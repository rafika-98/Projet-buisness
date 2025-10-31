import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Signal
from yt_dlp import YoutubeDL

from config import DEFAULT_CONFIG, load_config
from paths import (
    AUDIOS_DIR,
    OUT_DIR,
    VIDEOS_DIR,
    delete_dir_if_empty,
    get_audio_dir,
    get_video_dir,
    is_path_in_dir,
)

BROWSER_TRY_ORDER = ("edge", "chrome", "brave", "vivaldi", "opera", "chromium", "firefox")


class YtdlpLogger:
    def __init__(self, emit: Callable[[str], None]):
        self.emit = emit

    def debug(self, _msg: str) -> None:  # pragma: no cover - silencieux par défaut
        return

    def warning(self, msg: str) -> None:
        try:
            self.emit(f"[yt-dlp] {msg}")
        except Exception:
            pass

    def error(self, msg: str) -> None:
        try:
            self.emit(f"[yt-dlp] {msg}")
        except Exception:
            pass


def _apply_cookies_to_opts(opts: dict, cfg: dict) -> None:
    mode = (cfg.get("browser_cookies") or "auto").strip().lower()
    cookies = (cfg.get("cookies_path") or "").strip()

    opts.pop("cookiefile", None)
    opts.pop("cookiesfrombrowser", None)

    if mode == "none":
        return

    if mode == "cookiefile":
        if cookies:
            opts["cookiefile"] = cookies
        return

    if mode in BROWSER_TRY_ORDER:
        opts["cookiesfrombrowser"] = (mode, None, None, None)
        return

    order = _browser_fallback_order(cfg)
    browser = order[0] if order else BROWSER_TRY_ORDER[0]
    opts["cookiesfrombrowser"] = (browser, None, None, None)


def _is_dpapi_error(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    needles = ("dpapi", "decrypt", "encrypted_key", "os_crypt", "failed to decrypt")
    return any(k in msg for k in needles)


def _is_chrome_copy_error(exc: Exception) -> bool:
    msg = (str(exc) or "").lower()
    if not msg:
        return False
    return ("could not copy" in msg and "cookie" in msg and "database" in msg) or (
        "could not copy chrome cookie database" in msg
    )


def _browser_fallback_order(cfg: dict) -> List[str]:
    pref = (cfg.get("browser_cookies") or "auto").strip().lower()
    if pref in BROWSER_TRY_ORDER:
        return [pref]
    if pref == "auto":
        return list(BROWSER_TRY_ORDER)
    return []


def _backoff_sleep(attempt: int, base: float = 1.5, jitter: bool = True) -> None:
    delay = base ** attempt
    if jitter:
        delay += random.uniform(0, 0.5)
    time.sleep(delay)


def normalize_yt(u: str) -> str:
    try:
        if not u:
            return u
        u = re.sub(r'([?&])si=[^&]+&?', r'\1', u)
        u = re.sub(r'[?&]$', '', u)

        m = re.search(r'youtu\.be/([A-Za-z0-9_-]{6,})', u)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}"
        return u
    except Exception:
        return u


def normalize_tiktok(u: str) -> str:
    try:
        if not u:
            return u
        u = re.sub(r'([?&])(?:_r|_t|share_link_id|sender_device)=\w+&?', r'\1', u)
        u = re.sub(r'[?&]$', '', u)
        return u
    except Exception:
        return u


def normalize_url(u: str) -> str:
    if not u:
        return u
    low = u.lower()
    if "youtu" in low:
        return normalize_yt(u)
    if "tiktok.com" in low or "vm.tiktok.com" in low or "vt.tiktok.com" in low:
        return normalize_tiktok(u)
    return u


def extract_basic_info(url: str) -> dict:
    u = normalize_url(url)
    cfg = load_config()
    user_agent = (cfg.get("user_agent") or DEFAULT_CONFIG["user_agent"]).strip()
    base_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 15,
        "logger": YtdlpLogger(lambda _: None),
    }
    if user_agent:
        base_opts["http_headers"] = {"User-Agent": user_agent}

    cookies_path = (cfg.get("cookies_path") or "").strip()
    browser_pref = (cfg.get("browser_cookies") or "auto").strip().lower()

    def _extract(local_opts: Dict[str, Any]) -> dict:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with YoutubeDL(local_opts) as ydl:
                    info = ydl.extract_info(u, download=False)
                if info and info.get("entries"):
                    info = info["entries"][0]
                return info or {}
            except Exception as exc:
                last_exc = exc
                msg = (str(exc) or "").lower()
                if "429" in msg or "too many requests" in msg:
                    _backoff_sleep(attempt)
                    continue
                raise
        if last_exc:
            raise last_exc
        return {}

    if browser_pref == "cookiefile" and cookies_path:
        legacy_opts = dict(base_opts)
        legacy_opts["cookiefile"] = cookies_path
        legacy_opts.pop("cookiesfrombrowser", None)
        try:
            return _extract(legacy_opts)
        except Exception:
            pass

    if browser_pref == "none":
        opts_no_cookies = dict(base_opts)
        opts_no_cookies.pop("cookiefile", None)
        opts_no_cookies.pop("cookiesfrombrowser", None)
        return _extract(opts_no_cookies)

    last_error: Exception | None = None

    browsers = _browser_fallback_order(cfg)
    explicit_browser = browser_pref in BROWSER_TRY_ORDER
    for browser in browsers:
        local_opts = dict(base_opts)
        local_opts["cookiesfrombrowser"] = (browser, None, None, None)
        try:
            return _extract(local_opts)
        except Exception as exc:
            last_error = exc
            msg = (str(exc) or "").lower()
            if browser == "firefox" and ("pycryptodomex" in msg or "cryptodome" in msg):
                if explicit_browser:
                    raise RuntimeError(
                        "Lecture des cookies Firefox impossible : installez 'pycryptodomex' (pip install pycryptodomex)."
                    )
                continue
            if _is_dpapi_error(exc) or _is_chrome_copy_error(exc):
                continue
            raise

    if browser_pref == "auto" and cookies_path:
        legacy_opts = dict(base_opts)
        legacy_opts["cookiefile"] = cookies_path
        legacy_opts.pop("cookiesfrombrowser", None)
        try:
            return _extract(legacy_opts)
        except Exception as exc:
            last_error = exc

    opts_no_cookies = dict(base_opts)
    opts_no_cookies.pop("cookiefile", None)
    opts_no_cookies.pop("cookiesfrombrowser", None)
    try:
        return _extract(opts_no_cookies)
    except Exception as exc:
        if not (_is_dpapi_error(exc) or _is_chrome_copy_error(exc)):
            raise
        if last_error and not (_is_dpapi_error(last_error) or _is_chrome_copy_error(last_error)):
            raise last_error
        raise RuntimeError(
            "Impossible de récupérer les informations vidéo : les cookies navigateur sont indisponibles et la requête sans cookies a échoué."
        )

    return {}


@dataclass
class Task:
    url: str
    status: str = "En attente"
    filename: str = ""
    total: int = 0
    downloaded: int = 0
    speed: float = 0.0
    eta: Optional[int] = None
    video_id: Optional[str] = None
    selected_fmt: Optional[str] = None
    final_audio_path: Optional[str] = None
    final_video_path: Optional[str] = None
    platform: str = "youtube"
    source: str = "ui"
    chat_id: Optional[int] = None


class DownloadWorker(QThread):
    sig_progress = Signal(object, object, float, int, str)
    sig_status = Signal(str)
    sig_done = Signal(bool, str, dict)

    def __init__(self, task: Task, ydl_opts: dict, parent=None):
        super().__init__(parent)
        self.task = task
        self.ydl_opts = ydl_opts
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        captured = {"fn": ""}

        def hook(d):
            if self._stop:
                raise Exception("Interrompu par l’utilisateur")
            st = d.get("status")
            if st == "downloading":
                downloaded = int(d.get("downloaded_bytes") or 0)
                total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                speed = float(d.get("speed") or 0.0)
                eta = int(d.get("eta") or 0)
                fn = d.get("filename") or self.task.filename or ""
                self.sig_progress.emit(downloaded, total, speed, eta, fn)
            elif st == "finished":
                captured["fn"] = d.get("filename") or captured["fn"]
                self.sig_status.emit(f"Terminé : {captured['fn']}")

        opts = dict(self.ydl_opts)
        opts["progress_hooks"] = [hook]
        opts["quiet"] = True
        opts["no_warnings"] = True
        opts["logger"] = YtdlpLogger(lambda msg: self.sig_status.emit(msg))

        cfg = load_config()
        user_agent = (cfg.get("user_agent") or "").strip()
        headers: Dict[str, str] = {}
        if user_agent:
            headers = dict(opts.get("http_headers") or {})
            headers["User-Agent"] = user_agent
            opts["http_headers"] = headers

        try:
            url = normalize_url(self.task.url)
            cookies_path = (cfg.get("cookies_path") or "").strip()
            browser_pref = (cfg.get("browser_cookies") or "auto").strip().lower()

            base_opts = dict(opts)
            base_opts.pop("cookiefile", None)
            base_opts.pop("cookiesfrombrowser", None)

            def _download_with_opts(local_opts: Dict[str, Any]) -> Tuple[dict, int]:
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        with YoutubeDL(local_opts) as ydl:
                            info_inner = ydl.extract_info(url, download=True)
                            ret = getattr(ydl, "_download_retcode", 0) or 0
                        return info_inner, ret
                    except Exception as exc:
                        last_exc = exc
                        msg = (str(exc) or "").lower()
                        if "429" in msg or "too many requests" in msg:
                            self.sig_status.emit("yt-dlp : HTTP 429, nouvelle tentative…")
                            _backoff_sleep(attempt)
                            continue
                        raise
                if last_exc:
                    raise last_exc
                return {}, 0

            info: dict[str, Any] = {}
            retcode = 0
            dpapi_or_copy_issue = False
            last_error: Exception | None = None
            used_no_cookies = False
            success = False

            pycryptodomex_hint = "Lecture cookies Firefox impossible : installez 'pycryptodomex' (pip install pycryptodomex)."

            def try_cookiefile() -> bool:
                nonlocal info, retcode, opts, last_error
                if not cookies_path:
                    return False
                local_opts = dict(base_opts)
                local_opts["cookiefile"] = cookies_path
                local_opts.pop("cookiesfrombrowser", None)
                try:
                    info, retcode = _download_with_opts(local_opts)
                    opts = local_opts
                    return True
                except Exception as exc:
                    last_error = exc
                    return False

            def try_browser(browser: str, explicit: bool) -> bool:
                nonlocal info, retcode, opts, last_error, dpapi_or_copy_issue
                local_opts = dict(base_opts)
                local_opts["cookiesfrombrowser"] = (browser, None, None, None)
                try:
                    info, retcode = _download_with_opts(local_opts)
                    opts = local_opts
                    return True
                except Exception as exc:
                    last_error = exc
                    msg = (str(exc) or "").lower()
                    if browser == "firefox" and ("pycryptodomex" in msg or "cryptodome" in msg):
                        self.sig_status.emit(pycryptodomex_hint)
                        if explicit:
                            raise RuntimeError(pycryptodomex_hint)
                        return False
                    if _is_dpapi_error(exc) or _is_chrome_copy_error(exc):
                        dpapi_or_copy_issue = True
                        return False
                    raise

            def try_no_cookies() -> bool:
                nonlocal info, retcode, opts, last_error
                local_opts = dict(base_opts)
                local_opts.pop("cookiefile", None)
                local_opts.pop("cookiesfrombrowser", None)
                try:
                    info, retcode = _download_with_opts(local_opts)
                    opts = local_opts
                    return True
                except Exception as exc:
                    last_error = exc
                    return False

            if browser_pref == "cookiefile":
                success = try_cookiefile()
            elif browser_pref == "none":
                success = try_no_cookies()
                used_no_cookies = True
            else:
                browsers = _browser_fallback_order(cfg)
                explicit = browser_pref in BROWSER_TRY_ORDER
                for browser in browsers:
                    if try_browser(browser, explicit):
                        success = True
                        break

                if not success and browser_pref == "auto" and try_cookiefile():
                    success = True

            if not success and not used_no_cookies:
                if dpapi_or_copy_issue:
                    self.sig_status.emit(
                        "Cookies navigateur indisponibles (DPAPI/DB verrouillée). Passage en mode sans cookies."
                    )
                success = try_no_cookies()
                used_no_cookies = True

            if not success:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("Téléchargement impossible : toutes les stratégies de cookies ont échoué.")

            fn = captured["fn"]
            if not fn and info:
                try:
                    rd = (info.get("requested_downloads") or [])
                    if rd:
                        fn = rd[0].get("filepath") or rd[0].get("filename") or ""
                except Exception:
                    pass

            if not info:
                reused_info = {}
                if retcode == 0:
                    try:
                        reused_info = extract_basic_info(url)
                    except Exception:
                        reused_info = {}

                if reused_info and retcode == 0:
                    video_id = reused_info.get("id") or ""
                    existing = find_existing_outputs(video_id, self.task.platform)
                    if (not video_id) or (not existing.get("audio") and not existing.get("video")):
                        raise RuntimeError(
                            "Téléchargement déjà enregistré dans l’archive mais aucun fichier final n’a été retrouvé. "
                            "Supprime l’entrée correspondante dans archive.txt pour forcer un nouveau téléchargement."
                        )
                    if existing.get("audio"):
                        self.task.final_audio_path = existing["audio"]
                    if existing.get("video"):
                        self.task.final_video_path = existing["video"]
                    reuse_msg = existing.get("audio") or existing.get("video") or "Déjà téléchargé (archive)"
                    if video_id:
                        self.task.video_id = video_id
                    self.sig_status.emit("Déjà téléchargé (archive)")
                    self.sig_done.emit(True, reuse_msg, reused_info or {})
                    return

                raise RuntimeError("yt-dlp n’a renvoyé aucune information (URL invalide, vidéo privée ou cookies requis).")

            if fn:
                self.task.filename = fn

            self.sig_done.emit(True, fn or "Téléchargement terminé", info or {})
        except Exception as e:
            self.sig_done.emit(False, str(e), {})


class CommandWorker(QThread):
    sig_line = Signal(str)
    sig_done = Signal(int)

    def __init__(self, cmd: List[str], cwd: pathlib.Path | None = None, env: dict | None = None, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.cwd = str(cwd) if cwd else None
        self.env = env

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self.env,
                shell=False,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self.sig_line.emit(line.rstrip())
            proc.wait()
            self.sig_done.emit(proc.returncode or 0)
        except Exception as e:
            self.sig_line.emit(f"[ERREUR] {e}")
            self.sig_done.emit(1)


_RESERVED = '<>:"/\\|?*'


def sanitize_filename(name: str) -> str:
    safe = "".join("_" if ch in _RESERVED else ch for ch in name)
    safe = re.sub(r"\s+", " ", safe).strip()
    if len(safe) > 150:
        safe = safe[:150].rstrip()
    return safe or "file"


def _unique_path(dst: pathlib.Path) -> pathlib.Path:
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        cand = dst.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def find_existing_outputs(video_id: str, platform: Optional[str] = None) -> dict:
    found = {"audio": None, "video": None}
    if not video_id:
        return found

    token = f"[{video_id}]"

    def _pick_latest(paths: List[pathlib.Path]) -> Optional[pathlib.Path]:
        if not paths:
            return None
        try:
            return max(paths, key=lambda p: p.stat().st_mtime)
        except Exception:
            return paths[0]

    audio_exts = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
    video_exts = {".mp4", ".mkv", ".webm", ".mov"}

    audio_base = AUDIOS_DIR if not platform else get_audio_dir(platform)
    video_base = VIDEOS_DIR if not platform else get_video_dir(platform)

    try:
        audio_candidates = [
            p for p in audio_base.glob(f"*{token}*") if p.is_file() and p.suffix.lower() in audio_exts
        ]
        video_candidates = [
            p for p in video_base.glob(f"*{token}*") if p.is_file() and p.suffix.lower() in video_exts
        ]

        audio_path = _pick_latest(audio_candidates)
        video_path = _pick_latest(video_candidates)
        if audio_path:
            found["audio"] = str(audio_path)
        if video_path:
            found["video"] = str(video_path)
    except Exception:
        pass

    return found


def move_final_outputs(task: Task) -> dict:
    moved = {"audio": None, "video": None}
    if not task.video_id or not task.filename:
        return moved

    src_dir: Optional[pathlib.Path] = None

    try:
        platform = (task.platform or "").strip().lower()
        audio_dir = get_audio_dir(platform or "youtube")
        video_dir = get_video_dir(platform or "youtube")
        src_dir = pathlib.Path(task.filename).parent
        token = f"[{task.video_id}]"

        for p in list(src_dir.glob(f"*{token}*")):
            if not p.is_file():
                continue
            ext = p.suffix.lower()

            if ext in {".m4a", ".aac", ".wav", ".ogg", ".flac"}:
                continue

            if ext == ".mp4" and ".f" in p.stem:
                continue

            if ext not in (".mp4", ".mp3", ".mkv", ".webm", ".mov"):
                continue

            stem = p.stem
            token_segment = ""
            if "[" in stem and "]" in stem:
                start = stem.rfind("[")
                end = stem.rfind("]")
                if start >= 0 and end > start:
                    token_segment = stem[start : end + 1]

            if ext == ".mp3":
                base_dir = audio_dir
            else:
                base_dir = video_dir

            prefix = stem
            if token_segment:
                prefix = stem.replace(token_segment, "").strip()
            safe_prefix = sanitize_filename(prefix)
            if token_segment:
                safe_stem = (safe_prefix + (" " if safe_prefix else "") + token_segment).strip()
            else:
                safe_stem = safe_prefix
            safe_name = f"{safe_stem}{ext}"
            dst = _unique_path(base_dir / safe_name)

            try:
                p.replace(dst)
            except Exception:
                shutil.move(str(p), str(dst))

            if base_dir == video_dir:
                moved["video"] = str(dst)
                task.final_video_path = str(dst)
            else:
                moved["audio"] = str(dst)
                task.final_audio_path = str(dst)
    except Exception:
        pass

    try:
        if src_dir and is_path_in_dir(src_dir, OUT_DIR):
            delete_dir_if_empty(src_dir)
    except Exception:
        pass

    return moved


def cleanup_orphans_in_outputs(task: Task) -> None:
    if not task.video_id:
        return
    token = f"[{task.video_id}]"
    platform = (task.platform or "").strip().lower()
    audio_dir = get_audio_dir(platform or "youtube")
    try:
        for p in audio_dir.glob(f"*{token}*"):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext == ".m4a" or (ext == ".mp4" and ".f" in p.stem) or ext in {".aac", ".wav", ".ogg", ".flac"}:
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def ensure_audio(task: Task) -> Optional[str]:
    if task.final_audio_path and os.path.exists(task.final_audio_path):
        return task.final_audio_path
    if not task.final_video_path or not os.path.exists(task.final_video_path):
        return None
    if not shutil.which("ffmpeg"):
        return None

    src = pathlib.Path(task.final_video_path)
    base = src.stem
    safe_base = sanitize_filename(base)
    platform = (task.platform or "").strip().lower()
    audio_dir = get_audio_dir(platform or "youtube")
    dst = _unique_path(audio_dir / f"{safe_base}.mp3")

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-b:a",
                "192k",
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and dst.exists():
            task.final_audio_path = str(dst)
            return task.final_audio_path
    except Exception:
        pass
    return None


def human_size(n: Optional[float]) -> str:
    if not n or n <= 0:
        return "—"
    n = float(n)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} Po"


def human_rate(v: float) -> str:
    if not v:
        return "—/s"
    for unit in ("o/s", "Ko/s", "Mo/s", "Go/s"):
        if v < 1024.0:
            return f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} To/s"


def human_eta(s: Optional[int]) -> str:
    if not s:
        return "—"
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m:d}m {sec:02d}s"
    return f"{sec:d}s"


def pick_best_audio(formats: List[dict], mp4_friendly: bool) -> Optional[dict]:
    audios = [f for f in formats if f.get("vcodec") in (None, "none")]
    if mp4_friendly:
        preferred = [
            f
            for f in audios
            if (f.get("ext") in ("m4a", "mp4", "aac") or (f.get("acodec", "").startswith("mp4a.")))
        ]
        if preferred:
            return sorted(preferred, key=lambda x: x.get("tbr") or 0, reverse=True)[0]
    if audios:
        return sorted(audios, key=lambda x: x.get("tbr") or 0, reverse=True)[0]
    return None


def list_video_formats(formats: List[dict], mp4_friendly: bool) -> List[dict]:
    vids = [f for f in formats if f.get("acodec") in (None, "none") and f.get("vcodec") not in (None, "none")]
    if mp4_friendly:
        vids = [f for f in vids if (f.get("ext") == "mp4" or "avc1" in (f.get("vcodec") or ""))]
    return sorted(vids, key=lambda x: (x.get("height") or 0, x.get("tbr") or 0), reverse=True)


def estimate_size(stream: dict, duration: Optional[float]) -> Optional[float]:
    size = stream.get("filesize") or stream.get("filesize_approx")
    if size:
        return float(size)
    tbr = stream.get("tbr")
    if duration and tbr:
        return float(tbr) * 1000.0 / 8.0 * float(duration)
    return None
