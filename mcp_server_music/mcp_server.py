# mcp_server_music/mcp_server.py
"""
MCP server nhạc LOCAL - CHỈ QUERY DỮ LIỆU
- Đọc file nhạc từ thư mục trên máy
- Trả về metadata (tên bài, file path)
- KHÔNG phát nhạc
"""

from __future__ import annotations

from typing import List, Dict, Any
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ================== CẤU HÌNH THƯ MỤC NHẠC ==================

MUSIC_DIR = Path(r"E:\python LLM\music")
MUSIC_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}

# ================== KHỞI TẠO MCP SERVER ==================

mcp = FastMCP(
    name="local_music_mcp_server",
    instructions=(
        "Server này đọc file nhạc từ thư mục local và trả về danh sách bài hát. "
        "Hãy dùng các tool 'list_local_music' hoặc 'search_local_music' "
        "khi người dùng muốn xem / tìm nhạc trên máy."
    ),
)

# ================== HÀM TIỆN ÍCH ==================

def _scan_all_music_files() -> List[Path]:
    """
    Duyệt đệ quy toàn bộ MUSIC_DIR và trả về list Path tới các file nhạc.
    """
    if not MUSIC_DIR.exists():
        return []

    files: List[Path] = []
    for path in MUSIC_DIR.rglob("*"):
        if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS:
            files.append(path)
    # Sắp xếp cho ổn định (theo tên)
    files.sort(key=lambda p: p.name.lower())
    return files

def _make_track_dict(idx: int, path: Path) -> Dict[str, Any]:
    """
    Tạo dict track từ 1 file Path.
    """
    return {
        "track_id": idx,
        "track_name": path.stem,
        "artist_name": None,
        "album_name": None,
        "file_path": str(path.resolve()),
    }

# ================== TOOLS ==================

@mcp.tool()
def list_local_music(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Liệt kê một số bài nhạc có trong thư mục MUSIC_DIR.

    Args:
        limit: số bài tối đa trả về (default 20)

    Returns:
        List các track dạng dict với file_path
    """
    files = _scan_all_music_files()

    if not files:
        return []

    limit = max(1, min(limit, 500))
    files = files[:limit]

    tracks: List[Dict[str, Any]] = []
    for idx, path in enumerate(files, start=1):
        tracks.append(_make_track_dict(idx, path))

    return tracks

@mcp.tool()
def search_local_music(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Tìm nhạc theo từ khóa trong tên file.

    Args:
        query: từ khóa tìm kiếm
        limit: số kết quả tối đa

    Returns:
        List các track đã lọc theo query
    """
    if not query or not query.strip():
        raise ValueError("query không được để trống")

    query_norm = query.strip().lower()
    files = _scan_all_music_files()

    if not files:
        return []

    matches: List[Path] = []
    for path in files:
        name_norm = path.stem.lower()
        if query_norm in name_norm:
            matches.append(path)

    if not matches:
        return []

    limit = max(1, min(limit, 500))
    matches = matches[:limit]

    tracks: List[Dict[str, Any]] = []
    for idx, path in enumerate(matches, start=1):
        tracks.append(_make_track_dict(idx, path))

    return tracks

if __name__ == "__main__":
    # Chạy MCP server qua stdio - CHỈ query dữ liệu
    mcp.run()