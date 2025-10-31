"""Fonctions utilitaires pour les téléchargements YouTube."""

from __future__ import annotations

import pathlib
import re
from typing import Dict, Optional

from yt_dlp import YoutubeDL

from download_core import Task, move_final_outputs, normalize_url
from paths import DOWNLOAD_ARCHIVE, get_audio_dir, get_video_dir

YOUTUBE_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/watch\?\S*?v=[^\s&]+|youtu\.be/[^\s/?#]+)[^\s]*)",
    re.IGNORECASE,
)

_DEFAULT_VIDEO_FORMAT = "bestvideo[ext=mp4][vcodec*=avc1]+bestaudio[ext=m4a]/best[ext=mp4]"
_DEFAULT_AUDIO_FORMAT = "bestaudio/best"
_FOLDER_TMPL = "%(title).200s [%(id)s]"
_FILE_TMPL = "%(title).200s [%(id)s].%(ext)s"


def _base_outtmpl(platform: str) -> str:
    base_dir = get_video_dir(platform)
    return str(base_dir / _FOLDER_TMPL / _FILE_TMPL)


def build_download_options(task: Task, *, format_override: Optional[str] = None) -> Dict[str, object]:
    """Construit les options yt-dlp pour une tâche YouTube."""

    fmt = format_override or task.selected_fmt or _DEFAULT_VIDEO_FORMAT
    outtmpl = _base_outtmpl("youtube")
    # S'assure que les dossiers cibles existent
    get_audio_dir("youtube")

    return {
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "format": fmt,
        "merge_output_format": "mp4",
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
        "keepvideo": True,
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "noplaylist": True,
        "download_archive": str(DOWNLOAD_ARCHIVE),
        "nooverwrites": True,
        "overwrites": False,
    }


def _run_direct_download(url: str, opts: Dict[str, object], *, expect_audio: bool = False) -> pathlib.Path:
    task = Task(url=url, platform="youtube")
    local_opts = dict(opts)
    captured: Dict[str, Optional[str]] = {"filename": None}

    def _hook(data: Dict[str, object]) -> None:
        if data.get("status") == "finished":
            filename = data.get("filename")
            if isinstance(filename, str):
                captured["filename"] = filename

    hooks = list(local_opts.get("progress_hooks") or [])
    hooks.append(_hook)
    local_opts["progress_hooks"] = hooks

    with YoutubeDL(local_opts) as ydl:
        info = ydl.extract_info(normalize_url(url), download=True)

    if info and info.get("entries"):
        info = info["entries"][0]

    filepath: Optional[str] = None
    if isinstance(info, dict):
        task.video_id = info.get("id")
        requested = info.get("requested_downloads") or []
        for entry in requested:
            filename = entry.get("filepath") or entry.get("filename")
            if filename:
                filepath = filename
                break
        if not filepath:
            filename = info.get("_filename") or info.get("filepath")
            if isinstance(filename, str):
                filepath = filename

    if not filepath:
        filepath = captured["filename"]

    if not filepath:
        raise RuntimeError("Impossible de déterminer le fichier téléchargé pour YouTube.")

    task.filename = filepath
    moved = move_final_outputs(task)

    target = moved.get("audio") if expect_audio else moved.get("video")
    if expect_audio and not target:
        target = moved.get("audio")
    if not target:
        raise RuntimeError("Téléchargement YouTube terminé mais aucun fichier final n’a été trouvé.")
    return pathlib.Path(target)


def download_youtube_video(url: str) -> pathlib.Path:
    """Télécharge une vidéo YouTube et retourne le chemin final."""

    opts = build_download_options(Task(url=url, platform="youtube"))
    return _run_direct_download(url, opts, expect_audio=False)


def download_youtube_audio(url: str) -> pathlib.Path:
    """Télécharge uniquement l’audio d’une vidéo YouTube."""

    task = Task(url=url, platform="youtube")
    opts = build_download_options(task, format_override=_DEFAULT_AUDIO_FORMAT)
    opts = dict(opts)
    opts["keepvideo"] = False
    opts["format"] = _DEFAULT_AUDIO_FORMAT
    return _run_direct_download(url, opts, expect_audio=True)
