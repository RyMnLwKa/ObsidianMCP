import asyncio

import websockets as ws
import signal
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
import anyio
from pydantic import ValidationError

from mcp.server import MCPServer
from mcp.server.stdio import stdio_server
import mcp_types as types
from mcp.shared.message import SessionMessage

from pathlib import Path
import os
import re
import io
import base64
from typing import Optional

from config import (
    MCP_TRANSPORT as TRANSPORT,
    OBSIDIAN_VAULT,
    ALLOWED_EXTENSIONS,
    MAX_FILE_CHARS,
    IGNORE_DIRS,
    MAX_IMAGE_DIMENSION,
    JPEG_QUALITY,
    MAX_SINGLE_IMAGE_BYTES,
    MAX_TOTAL_EMBED_BYTES,
    IMAGE_MIME_TYPES,
    IMAGE_SUFFIXES,
    PIL_AVAILABLE as _PIL_AVAILABLE,
    WS_HOST,
    WS_PORT,
    SEARCH_CONTEXT_BEFORE,
    SEARCH_CONTEXT_AFTER,
    SEARCH_MAX_CONTEXTS_PER_FILE,
    SEARCH_MAX_FILES_IN_OUTPUT,
)

mcp = MCPServer("ObsidianReader")
server = mcp._lowlevel_server

_image_cache: dict[tuple[str, str], Optional[Path]] = {}

def _resolve_safe(path: str, must_exist: bool = True) -> Path:
    full_path = (OBSIDIAN_VAULT / path).resolve() if path else OBSIDIAN_VAULT

    if not full_path.is_relative_to(OBSIDIAN_VAULT):
        raise ValueError(f"Access denied: '{path}' is outside vault")

    if must_exist and not full_path.exists():
        raise ValueError(f"Path does not exist: '{path}'")

    return full_path

def _is_ignored(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)

@mcp.tool()
def list_files(path: str = "") -> str:
    """
    List all files in a directory.

    Args:
        path: Path inside vault (optional, empty = root)

    Examples:
        obsidian://vault/list/files/          → all files in root
        obsidian://vault/list/files/Projects  → files in Projects
    """
    full_path = _resolve_safe(path)

    if not full_path.is_dir():
        raise ValueError(f"Not a directory: '{path}'")

    with os.scandir(full_path) as entries:
        files = [
            entry.name for entry in entries
            if entry.is_file() and entry.name.endswith(ALLOWED_EXTENSIONS)
        ]

    if not files:
        return f"No files found in '{path or 'root'}'"
    return f"Files in '{path or 'root'}': ({len(files)} total)\n" + "\n".join(sorted(files))


@mcp.tool()
def list_folders(path: str = "") -> str:
    """
    List all folders in a directory.

    Args:
        path: Path inside vault (optional, empty = root)

    Examples:
        obsidian://vault/list/folders/       → all folders in root
        obsidian://vault/list/folders/Notes  → folders in Notes
    """
    full_path = _resolve_safe(path)

    if not full_path.is_dir():
        raise ValueError(f"Not a directory: '{path}'")

    with os.scandir(full_path) as entries:
        folders = [
            entry.name for entry in entries
            if entry.is_dir() and not _is_ignored(Path(entry))
        ]

    if not folders:
        return f"No folders found in '{path or 'root'}'"
    return f"Folders in '{path or 'root'}': ({len(folders)} total)\n" + "\n".join(sorted(folders))

@mcp.tool()
def search_text(keytext : str, folder: str = "") -> str:
    """
    Search for text in all files within a folder (recursively).

    Args:
        keytext: Text to search for
        folder: Folder to search in (optional, empty = root)

    Returns:
        List of files containing the text with context
    """

    fixed_keytext = keytext.strip().lower()

    if not fixed_keytext:
        return "Please provide text to search for."

    full_path = _resolve_safe(folder)

    if not full_path.is_dir():
        return f"Not a directory: '{folder or 'root'}'"

    if _is_ignored(full_path):
        return f"Folder '{folder}' is in the ignore list"

    results = []
    total_files = 0

    for file_path in full_path.rglob("*"):
        if _is_ignored(file_path.parent):
            continue
        if file_path.suffix not in ALLOWED_EXTENSIONS:
            continue

        total_files += 1
        try:
            file_text = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
        content = file_text.lower()

        if fixed_keytext in content:
            contexts = []
            pos = 0
            counts = content.count(fixed_keytext)
            while True:
                index = content.find(fixed_keytext, pos)
                if index == -1:
                    break
                start = max(0, index - SEARCH_CONTEXT_BEFORE)
                end = min(len(file_text), index + SEARCH_CONTEXT_AFTER + len(fixed_keytext))
                context = file_text[start:end].replace('\n', ' ').strip()
                contexts.append(context)

                pos = index + 1

                if len(contexts) >= SEARCH_MAX_CONTEXTS_PER_FILE:
                    break

            results.append({
                "filename": file_path.name,
                "occurrences" : counts,
                "contexts": contexts
            })

    if not results:
        return f"Text '{keytext}' not found in '{folder or 'root'}'. Checked {total_files} files."

    sorted_results = sorted(results, key = lambda obj: obj['occurrences'], reverse = True)

    output = [
        f"Found '{keytext}' in {len(sorted_results)} files",
        f"In folder: '{folder or 'root'}'",
        f"Checked: {total_files} files",
        "",
    ]

    for i, res in enumerate(sorted_results[:SEARCH_MAX_FILES_IN_OUTPUT], 1):
        output.append(f"{i}. {res['filename']}")
        output.append(f"   Occurrences: {res['occurrences']}")
        if res['contexts']:
            output.append(f"   Context: ...{res['contexts'][0]}...")
            if res['occurrences'] > len(res['contexts']):
                output.append(f"   (showing first {len(res['contexts'])} of {res['occurrences']} occurrences)")
        output.append("")

    if len(sorted_results) > SEARCH_MAX_FILES_IN_OUTPUT:
        output.append(f"... and {len(sorted_results) - SEARCH_MAX_FILES_IN_OUTPUT} more files")

    return "\n".join(output)


@mcp.tool()
def write_file(filepath: str, content: str, mode: str = "w") -> str:
    """
    Write content to a file in the Obsidian vault.

    Args:
        filepath: Path to the file (e.g., "notes.md" or "Projects/report.md")
        content: Content to write to the file
        mode: Write mode - "w" for overwrite, "a" for append (default: "w")

    Returns:
        Success message or error
    """
    full_path = _resolve_safe(filepath, must_exist=False)

    if _is_ignored(full_path):
        return f"File '{filepath}' is in the ignore list"

    if not full_path.suffix:
        full_path = full_path.with_suffix(".md")

    if full_path.suffix not in ALLOWED_EXTENSIONS:
        return f"Unsupported file type: '{full_path.suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"

    if mode not in ["w", "a"]:
        return f"Invalid mode: '{mode}'. Use 'w' for overwrite or 'a' for append"

    original_length = len(content)

    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + f"\n\n... [truncated, {original_length} chars total]"

    try:
        with open(full_path, mode, encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return f"Error writing file '{filepath}': {e}"

    chars_written = len(content)
    message = f"File '{full_path.name}' written successfully. ({chars_written} chars)"

    if original_length > MAX_FILE_CHARS:
        message += f" (original was {original_length} chars, truncated to {MAX_FILE_CHARS})"

    return message

def _resolve_image_path(img_ref: str, base_path: Path) -> Optional[Path]:

    global _image_cache

    cache_key = (str(base_path), img_ref)

    if cache_key in _image_cache:
        cached_path = _image_cache[cache_key]
        if cached_path is None:
            return None
        if cached_path.exists() and cached_path.is_file():
            return cached_path
        del _image_cache[cache_key]

    direct_path = OBSIDIAN_VAULT / img_ref
    if direct_path.is_file() and direct_path.is_relative_to(OBSIDIAN_VAULT):
        _image_cache[cache_key] = direct_path
        return direct_path

    img_name = Path(img_ref).name
    name_path = OBSIDIAN_VAULT / img_name
    if name_path.is_file() and name_path.is_relative_to(OBSIDIAN_VAULT):
        _image_cache[cache_key] = name_path
        return name_path

    relative_path = base_path / img_ref
    if relative_path.is_file() and relative_path.is_relative_to(OBSIDIAN_VAULT):
        _image_cache[cache_key] = relative_path
        return relative_path

    stem = Path(img_ref).stem

    for path in OBSIDIAN_VAULT.rglob('*'):
        if path.is_file() and path.stem.lower() == stem.lower():
            if path.suffix.lower() in IMAGE_SUFFIXES:
                _image_cache[cache_key] = path
                return path

    _image_cache[cache_key] = None
    return None


def _compress_image_bytes(img_data: bytes, suffix: str) -> tuple[bytes, str]:

    original_mime = IMAGE_MIME_TYPES.get(suffix, 'application/octet-stream')

    if suffix == '.svg' or not _PIL_AVAILABLE:
        return img_data, original_mime

    try:
        img = Image.open(io.BytesIO(img_data))

        w, h = img.size
        if max(w, h) > MAX_IMAGE_DIMENSION:
            scale = MAX_IMAGE_DIMENSION / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        if suffix == '.gif' and getattr(img, "is_animated", False):
            return img_data, original_mime

        buf = io.BytesIO()
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), "image/png"
        else:
            img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue(), "image/jpeg"

    except Exception:
        return img_data, original_mime


def _split_content_with_images(content: str, base_path: Path) -> list["types.ContentBlock"]:

    pattern = re.compile(r'!\[\[([^\]]+)\]\]')
    blocks: list[types.ContentBlock] = []
    total_embedded_bytes = 0
    last_end = 0

    for match in pattern.finditer(content):
        # text before this image
        if match.start() > last_end:
            text_chunk = content[last_end:match.start()]
            if text_chunk:
                blocks.append(types.TextContent(type="text", text=text_chunk))

        img_ref = match.group(1).strip()
        full_path = _resolve_image_path(img_ref, base_path)

        if not full_path:
            blocks.append(types.TextContent(type="text", text=match.group(0)))
            last_end = match.end()
            continue

        try:
            with open(full_path, "rb") as f:
                raw_data = f.read()

            suffix = full_path.suffix.lower()
            img_data, mime = _compress_image_bytes(raw_data, suffix)

            original_wikilink = match.group(0)  # исходник типа ![[filename.png]]

            if len(img_data) > MAX_SINGLE_IMAGE_BYTES:
                blocks.append(types.TextContent(
                    type="text",
                    text=f"[Image {original_wikilink} skipped: too large to embed ({len(img_data) // 1024} KB)]"
                ))
            elif total_embedded_bytes + len(img_data) > MAX_TOTAL_EMBED_BYTES:
                blocks.append(types.TextContent(
                    type="text",
                    text=f"[Image {original_wikilink} skipped: embed budget exceeded for this file]"
                ))
            else:
                total_embedded_bytes += len(img_data)
                base64_data = base64.b64encode(img_data).decode('utf-8')
                blocks.append(types.TextContent(type="text", text=f"{original_wikilink} (shown below):"))
                blocks.append(types.ImageContent(type="image", data=base64_data, mime_type=mime)) #mimeType

        except Exception:
            blocks.append(types.TextContent(type="text", text=match.group(0)))

        last_end = match.end()

    if last_end < len(content):
        tail = content[last_end:]
        if tail:
            blocks.append(types.TextContent(type="text", text=tail))

    if not blocks:
        blocks.append(types.TextContent(type="text", text=content))

    return blocks


@mcp.tool()
def read_file(filepath: str, embed_images: bool = False):
    """
    Read a file from Obsidian vault.

    Args:
        filepath: Path to file (e.g., "notes.md" or "Projects/report.md")
        embed_images: If True, resolve Obsidian ![[image]] links and return
            them as real image content blocks (so the model can actually
            see them), interleaved with the surrounding text.

    Examples:
        obsidian://vault/file/notes.md
        obsidian://vault/file/Projects/report.md

    Returns:
        str when embed_images is False (or file has no images / isn't markdown).
        list[ContentBlock] (text + image blocks) when embed_images is True
        and images were found.
    """

    full_path = _resolve_safe(filepath)

    if _is_ignored(full_path):
        return f"File '{filepath}' is in the ignore list"

    if not full_path.is_file():
        raise ValueError(f"Not a file: '{filepath}'")

    if full_path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: '{filepath}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    try:
        content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"Error reading file '{filepath}': {e}")

    original_len = len(content)
    if original_len > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + f"\n\n... [truncated, {original_len} chars total]"

    if embed_images and full_path.suffix == '.md':
        blocks = _split_content_with_images(content, full_path.parent)

        if len(blocks) == 1 and isinstance(blocks[0], types.TextContent):
            return blocks[0].text
        return blocks

    return content


@mcp.prompt()
def file_structure_extract(filename: str, max_headers: int | None = None) -> str:
    """
    Extract hierarchical header tree from a Markdown file without content.
    Searches for the file anywhere in the Obsidian vault by name.

    Args:
        filename: Name of the Markdown file to locate (with or without extension).
        max_headers: Maximum number of headers to extract.

    Returns:
        Tree representation of document headers with indentation.
    """
    try:
        root = OBSIDIAN_VAULT # Path.cwd()

        if not root.exists():
            return f"Directory '{root}' does not exist"

        found_path = None
        if '.' not in filename:
            for path in root.rglob('*'):
                if (path.is_file() and
                    path.stem.lower() == filename.lower() and
                    path.is_relative_to(OBSIDIAN_VAULT) and
                    path.suffix.lower() in ALLOWED_EXTENSIONS):
                    found_path = path
                    break
        else:
            for path in root.rglob('*'):
                if (path.is_file() and
                    path.name.lower() == filename.lower() and
                    path.is_relative_to(OBSIDIAN_VAULT) and
                    path.suffix.lower() in ALLOWED_EXTENSIONS):
                    found_path = path
                    break

        if not found_path:
            return f"File '{filename}' not found in vault"

        try:
            content = found_path.read_text(encoding='utf-8')
        except Exception:
            return f"File '{filename}' cannot be read"

        headers = []
        lines = content.splitlines()

        for line in lines:
            match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headers.append({'level': level, 'title': title})

                if max_headers and len(headers) >= max_headers:
                    break

        result = f"Document Structure: {found_path.name}\n"
        stack = []

        for header in headers:
            level = header['level']
            title = header['title']

            while stack and stack[-1]['level'] >= level:
                stack.pop()

            indent = "  " * (level - 1)
            result += f"{indent}# {title}\n"
            stack.append({'level': level, 'title': title})

        return result

    except ValueError as e:
        return f"Error: {e}"


async def ws_handler(websocket):
    read_send, read_recv = anyio.create_memory_object_stream(256)  # websocket --(ws_reader)--> read_send  ==stream==> read_recv (скрыто read_recv.receive() внутри run()) --> server.run() читает отсюда ()
    write_send, write_recv = anyio.create_memory_object_stream(256)  #server.run() пишет сюда --> write_send (скрыто write_send.send() внутри run()) ==stream==> write_recv --(ws_writer)--> websocket

    async def ws_reader():
        try:
            async for raw in websocket:
                try:
                    msg = types.jsonrpc_message_adapter.validate_json(raw, by_name=False)
                except ValidationError as exc:
                    await read_send.send(exc)
                    continue
                session_message = SessionMessage(msg)
                await read_send.send(session_message)
        except ConnectionClosedError:
            pass
        finally:
            await read_send.aclose()

    async def ws_writer():
        try:
            async for session_message in write_recv:  # SessionMessage.message
                raw = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                await websocket.send(raw)
        except ConnectionClosed:
            pass

    async with asyncio.TaskGroup() as tg:
        tg.create_task(ws_reader())
        tg.create_task(ws_writer())

        await server.run(
            read_recv,
            write_send,
            server.create_initialization_options(),
        )

        #tg.cancel_scope.cancel()  при anyio

async def run_stdio():
    async with stdio_server() as (reader, writer):
        await server.run(
            reader,
            writer,
            server.create_initialization_options(),
        )

async def run_websocket():
    state = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, state.set)
    loop.add_signal_handler(signal.SIGTERM, state.set)
    async with ws.serve(ws_handler, WS_HOST, WS_PORT): #8765
        await state.wait()  #server.wait_closed  #await asyncio.Future()
        #await server.close(close_connections=False)

"""
async def main():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(run_websocket())
        tg.create_task(run_stdio())
"""

async def main():
    tasks = []
    #if TRANSPORT in ("ws", "both"):
    #    tasks.append(asyncio.create_task(run_websocket())) #gather по умолчанию не отменяет остальные корутины при падении одной, если явно не передать return_exceptions=True и не обработать это вручную
    if TRANSPORT == "stdio": # in ("stdio", "both")
        tasks.append(asyncio.create_task(run_stdio()))  #TaskGroup если одна из тасок упадёт с исключением — вторая будет отменена, и TaskGroup перевыкинет исключение наруж
    elif TRANSPORT == "ws":
        tasks.append(asyncio.create_task(run_websocket()))
    else:
        raise ValueError(f"Unknown MCP_TRANSPORT: {TRANSPORT!r}")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    #mcp.run(transport = "stdio")
    asyncio.run(main())