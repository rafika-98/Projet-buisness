"""Fonctions utilitaires pour les téléchargements TikTok."""

from __future__ import annotations

import os
import pathlib
import re
from datetime import datetime
from typing import Dict, Optional

from yt_dlp import YoutubeDL

from core.download_core import Task, move_final_outputs, normalize_url
from paths import DOWNLOAD_ARCHIVE_TT, get_audio_dir, get_video_dir

TIKTOK_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:tiktok\.com/.+?/video/\d+|vt\.tiktok\.com/\S+|vm\.tiktok\.com/\S+))",
    re.IGNORECASE,
)

_DEFAULT_VIDEO_FORMAT = "mp4/bestaudio/best"
_DEFAULT_AUDIO_FORMAT = "bestaudio/best"


def _timestamped_outtmpl(base_dir: pathlib.Path, prefix: str) -> str:
    base_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    # On conserve l’identifiant dans le nom pour les traitements aval (archives/cleanups)
    return str(base_dir / f"{prefix}_{date_str} [%(id)s].%(ext)s")


def _prepare_common_opts(outtmpl: str, *, fmt: str) -> Dict[str, object]:
    return {
        "outtmpl": outtmpl,
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "continuedl": True,
        "download_archive": str(DOWNLOAD_ARCHIVE_TT),
        "nooverwrites": True,
        "overwrites": False,
    }


def build_download_options(task: Task, *, format_override: Optional[str] = None) -> Dict[str, object]:
    """Construit les options yt-dlp pour une tâche TikTok."""

    fmt = format_override or task.selected_fmt or _DEFAULT_VIDEO_FORMAT
    outtmpl = _timestamped_outtmpl(get_video_dir("tiktok"), "tiktok")
    get_audio_dir("tiktok")
    return _prepare_common_opts(outtmpl, fmt=fmt)


def _finalize_download(task: Task, info: dict, expect_audio: bool) -> pathlib.Path:
    task.video_id = (info or {}).get("id")
    moved = move_final_outputs(task)
    target = moved.get("audio") if expect_audio else moved.get("video")
    if expect_audio and not target:
        target = moved.get("audio")
    if not target:
        raise RuntimeError("Téléchargement TikTok terminé mais aucun fichier final n’a été trouvé.")
    return pathlib.Path(target)


def _direct_download(url: str, opts: Dict[str, object], *, expect_audio: bool = False) -> pathlib.Path:
    task = Task(url=url, platform="tiktok")
    local_opts = dict(opts)

    def _hook(data: Dict[str, object]) -> None:
        if data.get("status") == "finished":
            filename = data.get("filename")
            if isinstance(filename, str):
                task.filename = filename

    hooks = list(local_opts.get("progress_hooks") or [])
    hooks.append(_hook)
    local_opts["progress_hooks"] = hooks

    with YoutubeDL(local_opts) as ydl:
        info = ydl.extract_info(normalize_url(url), download=True)
        if info and info.get("entries"):
            info = info["entries"][0]
        if not task.filename:
            try:
                task.filename = ydl.prepare_filename(info)
            except Exception:
                task.filename = ""

    if not task.filename:
        raise RuntimeError("Impossible de déterminer le fichier téléchargé pour TikTok.")

    # Harmonise l’extension si yt-dlp a remuxé le flux
    if not task.filename.lower().endswith(".mp4"):
        base, _ = os.path.splitext(task.filename)
        candidate = f"{base}.mp4"
        if os.path.exists(candidate):
            task.filename = candidate

    return _finalize_download(task, info if isinstance(info, dict) else {}, expect_audio)


def download_tiktok_video(url: str, output_dir: Optional[str | os.PathLike[str]] = None) -> pathlib.Path:
    """Télécharge une vidéo TikTok et retourne le chemin final."""

    base_dir = pathlib.Path(output_dir) if output_dir else get_video_dir("tiktok")
    outtmpl = _timestamped_outtmpl(base_dir, "tiktok")
    opts = _prepare_common_opts(outtmpl, fmt=_DEFAULT_VIDEO_FORMAT)
    return _direct_download(url, opts, expect_audio=False)


def download_tiktok_audio(url: str, output_dir: Optional[str | os.PathLike[str]] = None) -> pathlib.Path:
    """Télécharge uniquement l’audio d’une vidéo TikTok."""

    base_dir = pathlib.Path(output_dir) if output_dir else get_audio_dir("tiktok")
    outtmpl = _timestamped_outtmpl(base_dir, "tiktok_audio")
    opts = _prepare_common_opts(outtmpl, fmt=_DEFAULT_AUDIO_FORMAT)
    opts = dict(opts)
    opts["keepvideo"] = False
    return _direct_download(url, opts, expect_audio=True)
