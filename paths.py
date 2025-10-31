import pathlib
from typing import Optional

from config import OUT_DIR

VIDEOS_DIR = OUT_DIR / "Videos"
AUDIOS_DIR = OUT_DIR / "Audios"
TRANSCRIPTION_DIR = OUT_DIR / "Transcription"
DOWNLOAD_ARCHIVE = OUT_DIR / "archive.txt"
DOWNLOAD_ARCHIVE_TT = OUT_DIR / "archive_tiktok.txt"

_PLATFORM_FOLDERS = {
    "youtube": ("Videos", "Youtube"),
    "tiktok": ("Videos", "Tiktok"),
}


def _resolve_platform_dir(base: pathlib.Path, platform: str, default_leaf: str) -> pathlib.Path:
    key = (platform or "").strip().lower()
    leaf = default_leaf
    if key:
        override = _PLATFORM_FOLDERS.get(key)
        if override and override[0] == base.name:
            leaf = override[1]
        elif override and override[0] != base.name:
            leaf = default_leaf
        else:
            leaf = platform.capitalize()
    path = base / leaf
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_video_dir(platform: str) -> pathlib.Path:
    return _resolve_platform_dir(VIDEOS_DIR, platform, platform.capitalize() or "Generic")


def get_audio_dir(platform: str) -> pathlib.Path:
    return _resolve_platform_dir(AUDIOS_DIR, platform, platform.capitalize() or "Generic")


def ensure_directories() -> None:
    for directory in (OUT_DIR, VIDEOS_DIR, AUDIOS_DIR, TRANSCRIPTION_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    for platform in ("youtube", "tiktok"):
        get_video_dir(platform)
        get_audio_dir(platform)


def is_path_in_dir(candidate: pathlib.Path, directory: pathlib.Path) -> bool:
    try:
        candidate.relative_to(directory)
        return True
    except ValueError:
        return False


def delete_dir_if_empty(path: pathlib.Path) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except Exception:
        pass


ensure_directories()
