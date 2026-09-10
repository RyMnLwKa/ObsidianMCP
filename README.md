# Obsidian MCP Server

MCP server for reading and writing files in an Obsidian vault. Supports two transports: **stdio** (the client launches the server as a subprocess) and **websocket** (the server runs separately, the client connects via `ws://`).

## Features

### Tools

| Tool | Description |
|---|---|
| `list_files(path="")` | List files in a directory (`.md`, `.txt`, `.json`) |
| `list_folders(path="")` | List subfolders in a directory (ignoring `.git`, `venv`, `__pycache__`, `.idea`, `.venv`) |
| `search_text(keytext, folder="")` | Recursive text search across files with context and sorting by number of occurrences |
| `write_file(filepath, content, mode="w")` | Write/append to a file (`w` — overwrite, `a` — append) |
| `read_file(filepath, embed_images=False)` | Read a file. When `embed_images=True`, resolves Obsidian links `![[image.png]]` and returns images as `ImageContent` blocks (base64), with resizing and a size budget |

### Prompts

| Prompt | Description |
|---|---|
| `file_structure_extract(filename, max_headers=None)` | Extracts the header tree from a Markdown file (searches for the file by name anywhere in the vault) |

### Image handling

- Supported formats (see `IMAGE_MIME_TYPES`): `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.svg`, `.webp`, `.tiff`, `.ico`
- Resize of the longer side down to `MAX_IMAGE_DIMENSION` = **1280 px** (if `Pillow` is installed)
- Conversion to JPEG (`JPEG_QUALITY` = 70) or PNG (when transparency is present)
- Limits: `MAX_SINGLE_IMAGE_BYTES` = **400 KB** per image, `MAX_TOTAL_EMBED_BYTES` = **1.5 MB** per single `read_file` call
- Links `![[...]]` are resolved in order: direct path → file name in the vault root → relative to the current file → search by stem across the whole vault
- Cache of resolved paths: `_image_cache: dict[tuple[str, str], Optional[Path]]`
- SVG and animated GIFs are returned as-is (no resizing)

### Text search

- Context window: `SEARCH_CONTEXT_BEFORE` = 75 characters before and `SEARCH_CONTEXT_AFTER` = 125 characters after the match
- At most `SEARCH_MAX_CONTEXTS_PER_FILE` = 15 fragments per file
- The final report contains no more than `SEARCH_MAX_FILES_IN_OUTPUT` = 20 files (top by number of occurrences)
- The total occurrence count is reported accurately even if fragments are truncated: `counts = content.count(keytext)` is computed before the loop

### Security

- All paths are validated through `_resolve_safe` — escaping `OBSIDIAN_VAULT` is forbidden (`Path.is_relative_to`)
- Ignored directories: `IGNORE_DIRS` = `.git`, `__pycache__`, `venv`, `.venv`, `.idea`
- Size limit on files read/written: `MAX_FILE_CHARS` = **500 000** characters

## Running

### WebSocket server

```bash
export OBSIDIAN_VAULT_PATH="/path/to/vault"
export MCP_TRANSPORT=ws
python3 server.py
```

Listens on `ws://MCP_WS_HOST:MCP_WS_PORT` (default `ws://localhost:8765`). Shuts down gracefully on `SIGINT` / `SIGTERM` via `loop.add_signal_handler`.

### stdio server

```bash
export OBSIDIAN_VAULT_PATH="/path/to/vault"
export MCP_TRANSPORT=stdio
python3 server.py
```

In this mode the server communicates with the client over stdin/stdout. It is usually not started manually, but by the client as a subprocess. For example, from Claude Desktop.