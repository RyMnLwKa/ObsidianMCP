import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DEFAULT_VAULT_PATH = os.getenv("DEFAULT_VAULT_PATH", "")
OBSIDIAN_VAULT: Path = Path(os.getenv("OBSIDIAN_VAULT_PATH", DEFAULT_VAULT_PATH)).expanduser().resolve()



MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "ws")



WS_HOST: str = os.getenv("MCP_WS_HOST", "localhost")
WS_PORT: int = int(os.getenv("MCP_WS_PORT", "8765"))
WS_URL: str = f"ws://{WS_HOST}:{WS_PORT}"



ALLOWED_EXTENSIONS: tuple[str, ...] = (".md", ".txt", ".json")
MAX_FILE_CHARS: int = 500_000
IGNORE_DIRS: set[str] = {".git", "__pycache__", "venv", ".venv", ".idea"}



MAX_IMAGE_DIMENSION : int = 1280      # размер в px большей стороны после ресайза
JPEG_QUALITY : int = 70               # качество после ресайза (от 0 до 100)
MAX_SINGLE_IMAGE_BYTES : int = 400_000    # порог размера изображения после ресайза. Если >=, то не эмбедим
MAX_TOTAL_EMBED_BYTES : int = 1_500_000   # порог сумарного размера base64 изображений на один вызов read_file



IMAGE_MIME_TYPES: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".bmp":  "image/bmp",
    ".svg":  "image/svg+xml",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".ico":  "image/x-icon",
}

IMAGE_SUFFIXES: set[str] = set(IMAGE_MIME_TYPES.keys())



SEARCH_CONTEXT_BEFORE: int = 75      # контекстное окно (до совпадения) для search_text
SEARCH_CONTEXT_AFTER: int = 125      # контекстное окно (после совпадения) для search_text
SEARCH_MAX_CONTEXTS_PER_FILE: int = 15
SEARCH_MAX_FILES_IN_OUTPUT: int = 20



STDIO_SERVER_COMMAND: str = "python3"
STDIO_SERVER_ARGS: list[str] = ["server.py"]



try:
    from PIL import Image
    PIL_AVAILABLE: bool = True
except ImportError:
    PIL_AVAILABLE: bool = False