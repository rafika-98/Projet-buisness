"""Fonctions utilitaires pour les téléchargements YouTube."""

from __future__ import annotations

import pathlib
import re
from typing import Dict, Optional

from yt_dlp import YoutubeDL

from core.download_core import (
    Task,
    extract_basic_info,
    move_final_outputs,
    normalize_url,
    sanitize_filename,
)
from paths import DOWNLOAD_ARCHIVE, get_audio_dir, get_video_dir

YOUTUBE_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/watch\?\S*?v=[^\s&]+|youtu\.be/[^\s/?#]+)[^\s]*)",
    re.IGNORECASE,
)

_DEFAULT_VIDEO_FORMAT = "bv*+ba/best"
_DEFAULT_AUDIO_FORMAT = "bestaudio/best"
_FOLDER_TMPL = "%(title).80s [%(id)s]"
_FILE_TMPL = "%(title).80s [%(id)s].%(ext)s"
_TITLE_MAX_LENGTH = 80
_HASHTAG_PATTERN = re.compile(r"(?:^|\s)[#＃][^#＃\s]+")


def _sanitize_title(value: Optional[str]) -> str:
    if not value:
        return ""

    cleaned = _HASHTAG_PATTERN.sub(" ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return ""

    if len(cleaned) > _TITLE_MAX_LENGTH:
        cleaned = cleaned[:_TITLE_MAX_LENGTH].rstrip()

    cleaned = sanitize_filename(cleaned)
    cleaned = cleaned.replace("%", "％")
    return cleaned


def _custom_outtmpl(platform: str, title: str) -> str:
    base_dir = get_video_dir(platform)
    safe_title = title or "file"
    folder = f"{safe_title} [%(id)s]"
    filename = f"{safe_title} [%(id)s].%(ext)s"
    return str(base_dir / folder / filename)


def _base_outtmpl(platform: str) -> str:
    base_dir = get_video_dir(platform)
    return str(base_dir / _FOLDER_TMPL / _FILE_TMPL)


def build_download_options(
    task: Task,
    *,
    audio_only: bool = False,
    format_override: Optional[str] = None,
) -> Dict[str, object]:
    """Construit les options yt-dlp en séparant proprement les cas vidéo vs audio."""

    default_fmt = _DEFAULT_AUDIO_FORMAT if audio_only else _DEFAULT_VIDEO_FORMAT
    fmt = format_override or task.selected_fmt or default_fmt
    outtmpl = _base_outtmpl("youtube")

    # S'assure que les dossiers existent (notamment pour la branche audio)
    get_audio_dir("youtube")

    opts: Dict[str, object] = {
        "outtmpl": outtmpl,
        "windowsfilenames": True,
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "download_archive": str(DOWNLOAD_ARCHIVE),
        "nooverwrites": True,
        "overwrites": False,
    }

    if audio_only:
        # Extraction audio uniquement (pas de merge_output_format ici)
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            {"key": "FFmpegMetadata"},
        ]
    # Vidéo : laisser yt-dlp choisir le meilleur conteneur final. Si l'appelant veut forcer
    # un conteneur spécifique, il peut ajouter merge_output_format à opts avant l'exécution.

    return opts


def _run_direct_download(url: str, opts: Dict[str, object], *, expect_audio: bool = False) -> pathlib.Path:
    task = Task(url=url, platform="youtube")
    local_opts = dict(opts)
    captured: Dict[str, Optional[str]] = {"filename": None}

    default_outtmpl = str(local_opts.get("outtmpl") or "")
    try:
        probe = extract_basic_info(url)
    except Exception:
        probe = {}

    custom_title = ""
    if isinstance(probe, dict):
        custom_title = _sanitize_title(probe.get("title"))

    if custom_title:
        local_opts["outtmpl"] = _custom_outtmpl("youtube", custom_title)
    elif default_outtmpl:
        local_opts["outtmpl"] = default_outtmpl

    def _hook(data: Dict[str, object]) -> None:
        if data.get("status") == "finished":
            filename = data.get("filename")
            if isinstance(filename, str):
                captured["filename"] = filename

    hooks = list(local_opts.get("progress_hooks") or [])
    hooks.append(_hook)
    local_opts["progress_hooks"] = hooks

    with YoutubeDL(local_opts) as ydl:
        try:
            info = ydl.extract_info(normalize_url(url), download=True)
        except Exception:
            # Fallback si on forçait MP4 : retente sans merge_output_format (laisser mkv si nécessaire)
            if local_opts.get("merge_output_format") == "mp4":
                local_opts.pop("merge_output_format", None)
                with YoutubeDL(local_opts) as ydl2:
                    info = ydl2.extract_info(normalize_url(url), download=True)
            else:
                raise

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
    """Télécharge une vidéo YouTube (meilleure qualité) et retourne le chemin final."""

    opts = build_download_options(Task(url=url, platform="youtube"), audio_only=False)
    return _run_direct_download(url, opts, expect_audio=False)


def download_youtube_audio(url: str) -> pathlib.Path:
    """Télécharge uniquement l’audio d’une vidéo YouTube et retourne le chemin final."""

    task = Task(url=url, platform="youtube")
    opts = build_download_options(task, audio_only=True, format_override=_DEFAULT_AUDIO_FORMAT)
    opts = dict(opts)
    opts["keepvideo"] = False
    return _run_direct_download(url, opts, expect_audio=True)
