import pathlib
from typing import Optional

from config import OUT_DIR

VIDEOS_DIR = OUT_DIR / "Videos"
AUDIOS_DIR = OUT_DIR / "Audios"
TRANSCRIPTION_DIR = OUT_DIR / "Transcription"
DOWNLOAD_ARCHIVE = OUT_DIR / "archive.txt"
DOWNLOAD_ARCHIVE_TT = OUT_DIR / "archive_tiktok.txt"


def ensure_directories() -> None:
    for directory in (OUT_DIR, VIDEOS_DIR, AUDIOS_DIR, TRANSCRIPTION_DIR):
        directory.mkdir(parents=True, exist_ok=True)


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
