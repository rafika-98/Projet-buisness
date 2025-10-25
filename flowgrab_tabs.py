import os, subprocess, shutil, sys, pathlib, mimetypes
import signal
import tempfile
import threading
import asyncio
import secrets
import re
import time
import random

OUT_DIR = pathlib.Path(r"C:\Users\Lamine\Desktop\Projet final\Application\downloads")
OUT_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR = OUT_DIR / "Videos"
AUDIOS_DIR = OUT_DIR / "Audios"
TRANSCRIPTION_DIR = OUT_DIR / "Transcription"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTION_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_ARCHIVE = OUT_DIR / "archive.txt"

from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover - typing uniquement
    from telegram.ext import Application

import telegram as tg  # pour la version

YOUTUBE_REGEX = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/watch\?\S*?v=[^\s&]+|youtu\.be/[^\s/?#]+)[^\s]*)",
    re.IGNORECASE,
)


def _ptb_major_minor() -> tuple[int, int]:
    try:
        parts = tg.__version__.split(".")[:2]
        return int(parts[0]), int(parts[1])
    except Exception:
        return 20, 0


def _backoff_sleep(attempt: int, base: float = 1.5, jitter: bool = True) -> None:
    delay = base ** attempt
    if jitter:
        delay += random.uniform(0, 0.5)
    time.sleep(delay)


def normalize_yt(u: str) -> str:
    """
    Normalise une URL YouTube :
    - supprime le paramètre ?si=... (inutile pour yt-dlp)
    - convertit youtu.be/<id> en https://www.youtube.com/watch?v=<id>
    """
    try:
        if not u:
            return u
        # retire ?si=... ou &si=...
        u = re.sub(r'([?&])si=[^&]+&?', r'\1', u)
        u = re.sub(r'[?&]$', '', u)

        m = re.search(r'youtu\.be/([A-Za-z0-9_-]{6,})', u)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}"
        return u
    except Exception:
        return u


def _is_path_in_dir(candidate: pathlib.Path, directory: pathlib.Path) -> bool:
    try:
        candidate.relative_to(directory)
        return True
    except ValueError:
        return False


def extract_basic_info(url: str) -> dict:
    """Récupère les métadonnées d'une vidéo sans lancer de téléchargement."""
    u = normalize_yt(url)
    cfg = load_config()
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "socket_timeout": 15,
        "http_headers": {"User-Agent": cfg.get("user_agent") or ""},
    }
    cookies = (cfg.get("cookies_path") or "").strip()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(u, download=False)
            if info and info.get("entries"):
                info = info["entries"][0]
            return info or {}
        except Exception as exc:  # pragma: no cover - dépend du réseau
            last_exc = exc
            msg = str(exc)
            if "429" in msg or "Too Many Requests" in msg:
                _backoff_sleep(attempt)
                continue
            break
    if last_exc:
        raise last_exc
    return {}

# PATCH START: config persistante
CONFIG_PATH = OUT_DIR / "flowgrab_config.json"

DEFAULT_CONFIG = {
    "webhook_path": "/webhook/Audio",
    "webhook_base": "",
    "webhook_full": "",
    "last_updated": "",
    "telegram_token": "",
    "telegram_mode": "auto",
    "telegram_port": 8081,
    "cookies_path": "",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _ensure_config_defaults(data: Optional[dict]) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(data, dict):
        for key, value in data.items():
            if value is None:
                continue
            cfg[key] = value
    mode = (cfg.get("telegram_mode") or "auto").lower()
    if mode not in ("auto", "polling", "webhook"):
        mode = "auto"
    cfg["telegram_mode"] = mode
    try:
        cfg["telegram_port"] = int(cfg.get("telegram_port") or DEFAULT_CONFIG["telegram_port"])
    except Exception:
        cfg["telegram_port"] = DEFAULT_CONFIG["telegram_port"]
    cfg["cookies_path"] = (cfg.get("cookies_path") or "").strip()
    cfg["user_agent"] = (cfg.get("user_agent") or DEFAULT_CONFIG["user_agent"]).strip()
    return cfg


def load_config() -> dict:
    try:
        import json
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return _ensure_config_defaults(data)
    except Exception:
        pass
    return _ensure_config_defaults(None)


def save_config(cfg: dict) -> None:
    try:
        import json, datetime
        merged = _ensure_config_defaults(cfg)
        merged["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
        CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
# PATCH END
from dataclasses import dataclass
from typing import Optional, List, Dict

from PySide6.QtCore import (
    Qt,
    QThread,
    Signal,
    Slot,
    QUrl,
    QTimer,
)
from PySide6.QtGui import QAction, QPalette, QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QLabel, QComboBox,
    QProgressBar, QMessageBox, QGroupBox, QTabWidget, QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QSpinBox,
)
from PySide6.QtWidgets import QTextEdit

try:
    from flask import Flask
except ImportError:  # pragma: no cover - dépend de l'environnement
    Flask = None  # type: ignore[assignment]

try:  # thème optionnel moderne
    import qdarktheme

    HAS_QDT = True
except Exception:  # pragma: no cover - dépend des packages installés
    HAS_QDT = False


_notification_server_started = False
_notification_parent_widget = None


def _send_windows_notification(message: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        from win10toast import ToastNotifier  # optionnel
        ToastNotifier().show_toast("FlowGrab", message, duration=5, threaded=True)
        return
    except Exception:
        pass
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([
            "cmd",
            "/c",
            "msg",
            "*",
            message,
        ], creationflags=creationflags)
    except Exception:
        pass


def start_notification_server(parent_widget=None) -> None:
    global _notification_server_started, _notification_parent_widget
    if _notification_server_started:
        return

    _notification_parent_widget = parent_widget

    if Flask is None:
        def warn_missing_flask():
            parent = _notification_parent_widget
            QMessageBox.warning(
                parent,
                "Flask manquant",
                "Impossible de démarrer le serveur de notification.\n"
                "Installe Flask avec 'pip install flask' pour activer les notifications.",
            )

        QTimer.singleShot(0, warn_missing_flask)
        _notification_server_started = True
        return

    from flask import request  # import ici pour éviter conflit avec l'import conditionnel global
    flask_app = Flask("flowgrab-notify")
    TOKEN = os.environ.get("FG_NOTIFY_TOKEN", "change_me")

    @flask_app.get("/notify-done")
    def notify_done():  # pragma: no cover - exécuté via requête HTTP
        if request.args.get("token") != TOKEN:
            return {"status": "forbidden"}, 403

        def _purge_transcription_segments_and_audio():
            try:
                if TRANSCRIPTION_DIR.exists():
                    for p in TRANSCRIPTION_DIR.glob("audio_partie_*.aac"):
                        try:
                            if p.is_file():
                                p.unlink()
                        except Exception:
                            pass
                    for p in TRANSCRIPTION_DIR.glob("*.mp3"):
                        try:
                            if p.is_file():
                                p.unlink()
                        except Exception:
                            pass

                horizon = time.time() - 3600
                if AUDIOS_DIR.exists():
                    for p in AUDIOS_DIR.iterdir():
                        if not p.is_file():
                            continue
                        if p.suffix.lower() not in {".mp3", ".m4a", ".wav", ".ogg", ".flac"}:
                            continue
                        try:
                            stat = p.stat()
                        except Exception:
                            continue
                        if stat.st_size <= 0:
                            continue
                        if stat.st_mtime >= horizon:
                            try:
                                p.unlink()
                            except Exception:
                                pass
            except Exception:
                pass

        def show_message_box():
            parent = _notification_parent_widget
            if parent is not None and hasattr(parent, "isVisible") and not parent.isVisible():
                parent = None
            if parent is None:
                parent = QApplication.activeWindow()
            QMessageBox.information(parent, "Notification N8N", "La transcription est terminée.")

        QTimer.singleShot(0, show_message_box)
        QTimer.singleShot(0, _purge_transcription_segments_and_audio)
        threading.Thread(target=_send_windows_notification, args=("La transcription est terminée.",), daemon=True).start()
        return {"status": "ok"}

    def run_flask():  # pragma: no cover - serveur en arrière-plan
        try:
            flask_app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)
        except Exception as exc:
            def warn_error():
                parent = _notification_parent_widget
                QMessageBox.warning(
                    parent,
                    "Serveur Flask",
                    f"Erreur lors du démarrage du serveur Flask : {exc}",
                )

            QTimer.singleShot(0, warn_error)

    threading.Thread(target=run_flask, daemon=True).start()
    _notification_server_started = True
from yt_dlp import YoutubeDL

# ---------------------- Modèle de tâche ----------------------
@dataclass
class Task:
    url: str
    status: str = "En attente"
    filename: str = ""
    total: int = 0
    downloaded: int = 0
    speed: float = 0.0
    eta: Optional[int] = None
    video_id: Optional[str] = None  # pour nettoyage
    selected_fmt: Optional[str] = None
    final_audio_path: Optional[str] = None
    final_video_path: Optional[str] = None
    # Telegram
    source: str = "ui"                 # "ui" | "telegram"
    chat_id: Optional[int] = None

# ---------------------- Worker de téléchargement ----------------------
class DownloadWorker(QThread):
    sig_progress = Signal(object, object, float, int, str)     # downloaded, total, speed, eta, filename
    sig_status   = Signal(str)                           # statut court
    sig_done     = Signal(bool, str, dict)               # ok, message/chemin, info dict

    def __init__(self, task: Task, ydl_opts: dict, parent=None):
        super().__init__(parent)
        self.task = task
        self.ydl_opts = ydl_opts
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        captured = {"fn": ""}

        def hook(d):
            if self._stop:
                raise Exception("Interrompu par l’utilisateur")
            st = d.get("status")
            if st == "downloading":
                downloaded = int(d.get("downloaded_bytes") or 0)
                total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                speed = float(d.get("speed") or 0.0)
                eta   = int(d.get("eta") or 0)
                fn    = d.get("filename") or self.task.filename or ""
                self.sig_progress.emit(downloaded, total, speed, eta, fn)
            elif st == "finished":
                captured["fn"] = d.get("filename") or captured["fn"]
                self.sig_status.emit(f"Terminé : {captured['fn']}")

        opts = dict(self.ydl_opts)
        opts["progress_hooks"] = [hook]

        cfg = load_config()
        user_agent = (cfg.get("user_agent") or "").strip()
        headers: dict[str, str] = {}
        if user_agent:
            headers = dict(opts.get("http_headers") or {})
            headers["User-Agent"] = user_agent
            opts["http_headers"] = headers
        cookies = (cfg.get("cookies_path") or "").strip()
        if cookies:
            opts["cookiefile"] = cookies

        try:
            url = normalize_yt(self.task.url)
            retcode = 0
            info: dict[str, Any] = {}
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    with YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        retcode = getattr(ydl, "_download_retcode", 0) or 0
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    msg = str(exc)
                    if "429" in msg or "Too Many Requests" in msg:
                        self.sig_status.emit("yt-dlp : HTTP 429, nouvelle tentative…")
                        _backoff_sleep(attempt)
                        continue
                    raise
            if last_exc:
                raise last_exc

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
                    existing = find_existing_outputs(video_id)
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
    sig_line = Signal(str)      # lignes de log
    sig_done = Signal(int)      # code retour

    def __init__(self, cmd: list[str], cwd: pathlib.Path | None = None, env: dict | None = None, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.cwd = str(cwd) if cwd else None
        self.env = env

    def run(self):
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


class InspectWorker(QThread):
    sig_done  = Signal(str, dict)   # url, info
    sig_error = Signal(str, str)    # url, message

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            info = extract_basic_info(self.url)
            self.sig_done.emit(self.url, info or {})
        except Exception as e:
            self.sig_error.emit(self.url, str(e))


class LongProcWorker(QThread):
    """Lance un processus long (ex: script PowerShell), stream les logs, et permet un stop propre."""

    sig_line = Signal(str)     # ligne de log
    sig_started = Signal(int)  # pid
    sig_done = Signal(int)     # code retour

    def __init__(self, args: list[str], env: dict | None = None, parent=None):
        super().__init__(parent)
        self.args = args
        self.env = env
        self.proc: subprocess.Popen | None = None

    def run(self):
        try:
            creationflags = 0
            if sys.platform.startswith("win"):
                # pour pouvoir envoyer CTRL_BREAK_EVENT et tuer l'arbre si besoin
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.proc = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=False,
                env=self.env,
                creationflags=creationflags,
            )
            assert self.proc.stdout is not None
            self.sig_started.emit(self.proc.pid or 0)
            for line in self.proc.stdout:
                self.sig_line.emit(line.rstrip())
            self.proc.wait()
            self.sig_done.emit(self.proc.returncode or 0)
        except Exception as e:
            self.sig_line.emit(f"[ERREUR] {e}")
            self.sig_done.emit(1)

    def stop(self):
        if not self.proc:
            return
        # PATCH START: stop propre + wait court
        try:
            if sys.platform.startswith("win"):
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            subprocess.run([
                "taskkill",
                "/PID",
                str(self.proc.pid),
                "/T",
                "/F",
            ], capture_output=True, text=True)
        except Exception:
            pass
        # PATCH END


def _apply_qdarktheme(app: QApplication, theme: str) -> bool:
    if not HAS_QDT:
        return False
    # Compatibilité avec qdarktheme>=2 (setup_theme) et <2 (load_stylesheet).
    setup = getattr(qdarktheme, "setup_theme", None)
    if callable(setup):
        setup(theme)
        return True
    loader = getattr(qdarktheme, "load_stylesheet", None)
    if callable(loader):
        app.setStyleSheet(loader(theme))
        return True
    return False


# ---------------------- Thèmes ----------------------
def apply_dark_theme(app: QApplication):
    if _apply_qdarktheme(app, "dark"):
        return
    app.setStyle("Fusion")
    palette = QPalette()
    bg = QColor(30, 30, 30)
    base = QColor(40, 40, 40)
    text = QColor(220, 220, 220)
    disabled = QColor(127, 127, 127)
    highlight = QColor(53, 132, 228)

    palette.setColor(QPalette.Window, bg)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, bg)
    palette.setColor(QPalette.ToolTipBase, base)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, base)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.Disabled, QPalette.Text, disabled)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    app.setPalette(palette)


def apply_light_theme(app: QApplication):
    if _apply_qdarktheme(app, "light"):
        return
    app.setStyle("Fusion")
    app.setPalette(QApplication.style().standardPalette())

# ---------------------- Utilitaires affichage ----------------------
def human_size(n: Optional[float]) -> str:
    if not n or n <= 0: return "—"
    n = float(n)
    for unit in ("o","Ko","Mo","Go","To"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} Po"

def human_rate(v: float) -> str:
    if not v: return "—/s"
    for unit in ("o/s","Ko/s","Mo/s","Go/s"):
        if v < 1024.0:
            return f"{v:.1f} {unit}"
        v /= 1024.0
    return f"{v:.1f} To/s"

def human_eta(s: Optional[int]) -> str:
    if not s: return "—"
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h: return f"{h:d}h {m:02d}m {sec:02d}s"
    if m: return f"{m:d}m {sec:02d}s"
    return f"{sec:d}s"

# ---------------------- Inspecteur de formats ----------------------
def pick_best_audio(formats: List[dict], mp4_friendly: bool) -> Optional[dict]:
    audios = [f for f in formats if f.get("vcodec") in (None, "none")]
    if mp4_friendly:
        preferred = [f for f in audios if (f.get("ext") in ("m4a","mp4","aac") or (f.get("acodec","" ).startswith("mp4a.")))]
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
    if size: return float(size)
    tbr = stream.get("tbr")  # kbps
    if duration and tbr:
        return float(tbr) * 1000.0 / 8.0 * float(duration)
    return None

# ---------------------- Telegram worker ----------------------
class TelegramWorker(QThread):
    # chat_id peut dépasser la taille d'un int 32 bits -> utiliser "object"
    sig_download_requested = Signal(str, str, object, str)  # url, fmt, chat_id, title
    sig_info = Signal(str)

    def __init__(self, app_config: dict, parent=None):
        super().__init__(parent)
        self.app_config = app_config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_evt: asyncio.Event | None = None
        self.app: "Application | None" = None
        self._pending_choices: Dict[str, Dict[str, Any]] = {}
        self.effective_mode = self._resolve_mode()
        # Map token -> chemin audio à transcrire (évite de dépasser la limite Telegram 64o sur callback_data)
        self._pending_transcriptions: Dict[str, str] = {}

    # ---- helpers ----
    def _resolve_mode(self) -> str:
        mode = (self.app_config.get("telegram_mode") or "auto").lower()
        base = (self.app_config.get("webhook_base") or "").strip()
        if mode == "auto":
            return "webhook" if base else "polling"
        if mode == "webhook" and not base:
            return "polling"
        if mode not in ("polling", "webhook"):
            return "polling"
        return mode

    def send_message(self, chat_id: int | str, text: str, reply_markup: Any = None) -> None:
        if not self._loop or not self.app:
            return

        try:
            chat_ref: int | str = int(chat_id)  # Telegram accepte les entiers Python arbitraires
        except (TypeError, ValueError):
            chat_ref = chat_id

        async def _send():
            try:
                await self.app.bot.send_message(chat_id=chat_ref, text=text, reply_markup=reply_markup)
            except Exception as exc:
                self.sig_info.emit(f"Envoi message Telegram impossible : {exc}")

        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_send()))

    def ask_transcription(self, chat_id: int | str, audio_path: str) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        token = secrets.token_urlsafe(8)  # court, sûr, << 64 octets
        self._pending_transcriptions[token] = audio_path
        name = os.path.basename(audio_path) or audio_path
        buttons = [
            [InlineKeyboardButton("📝 Oui, transcrire", callback_data=f"tr:yes:{token}")],
            [InlineKeyboardButton("⛔ Non", callback_data=f"tr:no:{token}")],
        ]
        self.send_message(chat_id, f"Transcrire l’audio téléchargé ?\n{name}", InlineKeyboardMarkup(buttons))

    # ---- yt-dlp helpers ----
    def _inspect_url(self, url: str) -> dict:
        u = normalize_yt(url)
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "socket_timeout": 15,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(u, download=False)
        if info and info.get("entries"):
            info = info["entries"][0]
        return info or {}

    def _build_options(self, info: dict) -> Tuple[str, List[Dict[str, Any]]]:
        formats = info.get("formats") or []
        duration = info.get("duration")
        videos = list_video_formats(formats, mp4_friendly=True)
        audio = pick_best_audio(formats, mp4_friendly=True)
        title = info.get("title") or info.get("fulltitle") or info.get("original_url") or "Lien YouTube"
        options: List[Dict[str, Any]] = []
        for vf in videos[:8]:
            vid_id = vf.get("format_id") or ""
            fmt = vid_id
            audio_id = ""
            audio_label = ""
            audio_size = None
            if audio:
                audio_id = audio.get("format_id") or ""
                if audio_id:
                    fmt = f"{vid_id}+{audio_id}"
                audio_label = f"{audio.get('ext','')}/{audio.get('acodec','')}"
                audio_size = estimate_size(audio, duration)
            res = f"{vf.get('height') or ''}p"
            fps = vf.get("fps")
            vc = f"{vf.get('ext','')}/{vf.get('vcodec','')}"
            vsize = estimate_size(vf, duration)
            total = (vsize or 0) + (audio_size or 0)
            parts = [res.strip() or "—", vc]
            if fps:
                parts.insert(1, f"{fps} fps")
            label = " • ".join([p for p in parts if p])
            approx = human_size(total) if total else "—"
            detail = label
            if audio_label:
                detail += f" • Audio {audio_label}"
            detail += f" • ≈ {approx}"
            options.append({
                "fmt": fmt,
                "label": detail,
            })
        return title, options

    # ---- PTB callbacks ----
    async def _cmd_start(self, update, context):
        msg = update.effective_message
        if msg:
            await msg.reply_text("Envoie-moi un lien YouTube pour lancer un téléchargement.")

    async def _handle_text(self, update, context):
        message = update.effective_message
        if not message:
            return
        text = (message.text or "").strip()
        match = YOUTUBE_REGEX.search(text)
        if not match:
            await message.reply_text("Je n’ai pas reconnu de lien YouTube. Envoie l’URL complète.")
            return
        url = normalize_yt(match.group(1))
        await message.reply_text("Analyse du lien…")
        loop = asyncio.get_running_loop()
        try:
            info = await loop.run_in_executor(None, self._inspect_url, url)
        except Exception as exc:
            self.sig_info.emit(f"Inspection Telegram échouée : {exc}")
            await message.reply_text("Impossible d’inspecter cette vidéo. Réessaie plus tard.")
            return

        title, options = self._build_options(info)
        if not options:
            await message.reply_text("Aucun format compatible trouvé pour cette vidéo.")
            return

        token = secrets.token_urlsafe(4)
        self._pending_choices[token] = {
            "url": url,
            "options": options,
            "title": title,
        }

        lines = [f"Formats disponibles pour « {title} » :", ""]
        for idx, opt in enumerate(options, start=1):
            lines.append(f"{idx}. {opt['label']}")
        lines.append("")
        lines.append("Choisis un format via les boutons ci-dessous.")

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        buttons: List[List[Any]] = []
        row: List[Any] = []
        for idx in range(len(options)):
            row.append(InlineKeyboardButton(str(idx + 1), callback_data=f"choose:{token}:{idx}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        markup = InlineKeyboardMarkup(buttons)
        await message.reply_text("\n".join(lines), reply_markup=markup)

    async def _handle_callback(self, update, context):
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        chat = query.message.chat if query.message else update.effective_chat
        chat_id = chat.id if chat else None

        if data.startswith("choose:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                await query.answer("Choix invalide.")
                return
            token, idx_str = parts[1], parts[2]
            entry = self._pending_choices.get(token)
            if not entry:
                await query.answer("Choix expiré.")
                try:
                    await query.edit_message_reply_markup(None)
                except Exception:
                    pass
                return
            try:
                idx = int(idx_str)
            except ValueError:
                await query.answer("Choix invalide.")
                return
            options = entry.get("options") or []
            if idx < 0 or idx >= len(options):
                await query.answer("Choix invalide.")
                return
            option = options[idx]
            if chat_id is None:
                await query.answer("Chat introuvable.")
                return
            await query.answer("Téléchargement en cours…", show_alert=False)
            try:
                await query.edit_message_reply_markup(None)
            except Exception:
                pass
            title = entry.get("title") or "Vidéo"
            fmt = option.get("fmt") or ""
            self.sig_download_requested.emit(entry.get("url", ""), fmt, chat_id, title)
            self.send_message(chat_id, f"Format sélectionné : {option.get('label','')}\nTéléchargement demandé…")
            self._pending_choices.pop(token, None)
        elif data.startswith("tr:yes"):
            parts = data.split(":", 2)
            tok = parts[2] if len(parts) == 3 else ""
            audio_path = self._pending_transcriptions.pop(tok, "")
            if not audio_path:
                await query.answer("Lien expiré. Renvoie la vidéo pour réessayer.", show_alert=True)
                return
            await self._handle_transcription_yes(query, chat_id, audio_path)
        elif data.startswith("tr:no"):
            await query.answer("OK", show_alert=False)
            try:
                await query.edit_message_reply_markup(None)
            except Exception:
                pass
            if chat_id is not None:
                self.send_message(chat_id, "Transcription annulée.")
        else:
            await query.answer("Commande inconnue.")

    async def _handle_transcription_yes(self, query, chat_id: Optional[int], audio_path: str):
        if chat_id is None:
            await query.answer("Chat introuvable.")
            return
        webhook_full = (self.app_config.get("webhook_full") or "").strip()
        if not webhook_full:
            await query.answer("Webhook non configuré.", show_alert=True)
            self.send_message(chat_id, "Configure le webhook dans l’app avant de lancer une transcription.")
            return
        await query.answer("Envoi en cours…", show_alert=False)
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(None, self._post_audio_to_webhook, webhook_full, audio_path)
        try:
            await query.edit_message_reply_markup(None)
        except Exception:
            pass
        if status == 0:
            self.send_message(chat_id, f"Transcription impossible : {body}")
            return
        snippet = body.strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "\n...[tronqué]..."
        msg = f"Transcription lancée ✅ (HTTP {status})"
        if snippet:
            msg += f"\n{snippet}"
        self.send_message(chat_id, msg)

    def _post_audio_to_webhook(self, url: str, audio_path: str) -> Tuple[int, str]:
        try:
            import requests
        except ImportError:
            return 0, "Le module requests est manquant. Installe-le depuis l’app."
        if not os.path.exists(audio_path):
            return 0, f"Fichier introuvable : {audio_path}"
        mime, _ = mimetypes.guess_type(audio_path)
        mime = mime or "application/octet-stream"
        basename = os.path.basename(audio_path)
        try:
            with open(audio_path, "rb") as handle:
                files = {"data": (basename, handle, mime)}
                resp = requests.post(url, files=files, timeout=(10, 600))
            body = resp.text or ""
            return resp.status_code, body
        except Exception as exc:
            return 0, str(exc)

    # ---- QThread API ----
    def run(self):
        token = (self.app_config.get("telegram_token") or "").strip()
        if not token:
            self.sig_info.emit("Token Telegram manquant : bot non démarré.")
            return

        if sys.platform.startswith("win"):
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except Exception:
                pass

        try:
            self._loop = asyncio.new_event_loop()
        except Exception as exc:
            self.sig_info.emit(f"Erreur bot Telegram : {exc}")
            return

        try:
            asyncio.set_event_loop(self._loop)
            self._stop_evt = asyncio.Event()
            mode = self._resolve_mode()
            base = (self.app_config.get("webhook_base") or "").strip()
            if mode == "webhook" and not base:
                self.sig_info.emit("URL webhook absente, bascule en mode polling.")
                mode = "polling"
            self.effective_mode = mode
            major, minor = _ptb_major_minor()
            self.sig_info.emit(f"python-telegram-bot v{major}.{minor}")

            if mode == "polling":
                self._loop.run_until_complete(self._serve_polling())
            else:
                port = int(self.app_config.get("telegram_port") or 8081)
                self._loop.run_until_complete(self._serve_webhook(base, port))
        except Exception as exc:
            self.sig_info.emit(f"Erreur bot Telegram : {exc}")
        finally:
            if self._loop:
                try:
                    self._loop.close()
                except Exception:
                    pass
            self._loop = None
            self._stop_evt = None
            self.app = None
            self._pending_choices.clear()
            self.sig_info.emit("Bot Telegram arrêté.")

    async def _build_app(self):
        try:
            from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
        except Exception as exc:
            raise RuntimeError(f"Import python-telegram-bot impossible : {exc}") from exc

        token = (self.app_config.get("telegram_token") or "").strip()
        app = Application.builder().token(token).build()
        self.app = app
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        app.add_handler(CallbackQueryHandler(self._handle_callback))
        return app

    async def _serve_polling(self):
        self.sig_info.emit("Bot Telegram en initialisation (polling)…")
        try:
            app = await self._build_app()
        except RuntimeError as exc:
            self.sig_info.emit(str(exc))
            return
        await app.initialize()
        await app.start()
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass

        if app.updater is None:
            self.sig_info.emit("Updater PTB indisponible : polling impossible.")
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        try:
            await app.updater.start_polling(drop_pending_updates=False)
        except Exception as exc:
            self.sig_info.emit(f"start_polling a échoué : {exc}")
            try:
                await app.updater.stop()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        self.sig_info.emit("Bot Telegram démarré en mode polling.")
        try:
            if self._stop_evt:
                await self._stop_evt.wait()
        finally:
            try:
                await app.updater.stop()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass

    async def _serve_webhook(self, base: str, port: int):
        self.sig_info.emit("Bot Telegram en initialisation (webhook)…")
        try:
            app = await self._build_app()
        except RuntimeError as exc:
            self.sig_info.emit(str(exc))
            return

        await app.initialize()
        await app.start()
        token = (self.app_config.get("telegram_token") or "").strip()
        path = f"tg/{token}"
        base = base.rstrip("/")
        webhook_url = f"{base}/{path}" if base else f"/{path}"

        try:
            from telegram import Update
            await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
            await app.start_webhook(
                listen="0.0.0.0",
                port=port,
                url_path=path,
                webhook_url=webhook_url,
                drop_pending_updates=True,
            )
        except Exception as exc:
            self.sig_info.emit(f"Impossible de démarrer le webhook : {exc}")
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass
            return

        self.sig_info.emit(f"Webhook : {webhook_url} (port {port})")
        self.sig_info.emit("Bot Telegram démarré en mode webhook.")
        try:
            if self._stop_evt:
                await self._stop_evt.wait()
        finally:
            try:
                await app.stop_webhook()
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            try:
                await app.shutdown()
            except Exception:
                pass

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)
# ---------------------- Utilitaires de fichiers ----------------------
_RESERVED = '<>:"/\\|?*'


def sanitize_filename(name: str) -> str:
    """Nettoie un nom de fichier pour le rendre compatible multiplateforme."""
    safe = "".join("_" if ch in _RESERVED else ch for ch in name)
    safe = re.sub(r"\s+", " ", safe).strip()
    if len(safe) > 150:
        safe = safe[:150].rstrip()
    return safe or "file"


def _unique_path(dst: pathlib.Path) -> pathlib.Path:
    """
    Retourne un chemin libre en ajoutant -1, -2, ... si 'dst' existe déjà.
    """
    if not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        cand = dst.with_name(f"{stem}-{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def ensure_audio(task: Task) -> Optional[str]:
    """Garantit la présence d'un MP3 exploitable pour la transcription."""

    if task.final_audio_path and os.path.exists(task.final_audio_path):
        return task.final_audio_path
    if not task.final_video_path or not os.path.exists(task.final_video_path):
        return None
    if not shutil.which("ffmpeg"):
        return None

    src = pathlib.Path(task.final_video_path)
    base = src.stem
    safe_base = sanitize_filename(base)
    dst = _unique_path(AUDIOS_DIR / f"{safe_base}.mp3")

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


def themed_icon(*names: str) -> QIcon:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def _is_merge_in_progress(repo: pathlib.Path) -> bool:
    return (repo / ".git" / "MERGE_HEAD").exists()


def find_existing_outputs(video_id: str) -> dict:
    """Cherche des fichiers audio/vidéo déjà produits pour l'ID donné."""
    found = {"audio": None, "video": None}
    if not video_id:
        return found

    token = f"[{video_id}]"

    def _pick_latest(paths: list[pathlib.Path]) -> Optional[pathlib.Path]:
        if not paths:
            return None
        try:
            return max(paths, key=lambda p: p.stat().st_mtime)
        except Exception:
            return paths[0]

    audio_exts = {".mp3", ".m4a", ".wav", ".ogg", ".flac"}
    video_exts = {".mp4", ".mkv", ".webm", ".mov"}

    try:
        audio_candidates = [
            p for p in AUDIOS_DIR.glob(f"*{token}*")
            if p.is_file() and p.suffix.lower() in audio_exts
        ]
        video_candidates = [
            p for p in VIDEOS_DIR.glob(f"*{token}*")
            if p.is_file() and p.suffix.lower() in video_exts
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
    """
    Déplace les fichiers finaux (.mp4, .mp3) du sous-dossier 'Titre [ID]'
    vers 'Videos' et 'Audios' (à plat). Gère les collisions de noms.
    """
    moved = {"audio": None, "video": None}
    if not task.video_id or not task.filename:
        return moved

    try:
        src_dir = pathlib.Path(task.filename).parent
        token = f"[{task.video_id}]"

        for p in list(src_dir.glob(f"*{token}*")):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in (".mp4", ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".mkv", ".webm"):
                continue

            stem = p.stem
            token_segment = ""
            if "[" in stem and "]" in stem:
                start = stem.rfind("[")
                end = stem.rfind("]")
                if start >= 0 and end > start:
                    token_segment = stem[start : end + 1]

            base_dir = AUDIOS_DIR
            if ext == ".mp4" and ".f" not in stem:
                base_dir = VIDEOS_DIR
            elif ext in (".mkv", ".webm"):
                base_dir = VIDEOS_DIR

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

            if base_dir == VIDEOS_DIR:
                moved["video"] = str(dst)
                task.final_video_path = str(dst)
            else:
                moved["audio"] = str(dst)
                task.final_audio_path = str(dst)
    except Exception:
        pass
    return moved


def delete_dir_if_empty(path: pathlib.Path):
    """
    Supprime 'path' s'il est vide (ignore erreurs).
    """
    try:
        if path.is_dir():
            # re-liste après les déplacements / nettoyages
            if not any(path.iterdir()):
                path.rmdir()
    except Exception:
        pass

# ---------------------- Onglet YouTube ----------------------

class YoutubeTab(QWidget):
    sig_request_transcription = Signal(list)
    # chat_id peut dépasser la taille d'un int 32 bits -> utiliser "object"
    sig_audio_completed = Signal(object, str)  # chat_id, audio_path

    def __init__(self, app_ref, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.app_ref = app_ref
        self.queue: List[Task] = []
        self.current_worker: Optional[DownloadWorker] = None
        self.last_inspect_info: Dict = {}
        self.inspect_worker = None
        self.inspect_seq = 0            # numéro de requête pour ignorer les réponses obsolètes
        self.inspect_debounce = QTimer(self)
        self.inspect_debounce.setSingleShot(True)
        self.inspect_debounce.setInterval(250)  # 250ms de debounce
        self.inspect_debounce.timeout.connect(self._inspect_current_after_debounce)
        self.build_ui()

    # PATCH START: helper curseur attente + usage
    def _cursor_wait(self, on: bool):
        if on and QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        elif not on:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass

    def _open_dir(self, path: pathlib.Path):
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Impossible d’ouvrir le dossier : {e}")
    # PATCH END

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ----- URLs + Inspecteur -----
        urls_box = QGroupBox("URLs")
        urls_layout = QVBoxLayout(urls_box)
        urls_layout.setContentsMargins(12, 12, 12, 12)
        urls_layout.setSpacing(8)

        add_line = QHBoxLayout()
        add_line.setSpacing(8)
        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText("Colle une URL YouTube/playlist et presse Entrée pour l’ajouter")
        self.edit_url.returnPressed.connect(self.add_url)
        btn_add = QPushButton("Ajouter")
        icon_add = themed_icon("list-add", "document-new")
        if not icon_add.isNull():
            btn_add.setIcon(icon_add)
        btn_add.clicked.connect(self.add_url)
        btn_paste = QPushButton("Coller URL")
        icon_paste = themed_icon("edit-paste")
        if not icon_paste.isNull():
            btn_paste.setIcon(icon_paste)
        btn_paste.clicked.connect(self.paste_clipboard)
        btn_file = QPushButton("Depuis .txt")
        icon_file = themed_icon("document-open", "text-x-generic")
        if not icon_file.isNull():
            btn_file.setIcon(icon_file)
        btn_file.clicked.connect(self.add_from_file)
        btn_clear_urls = QPushButton("Vider la liste")
        icon_clear = themed_icon("edit-clear", "user-trash")
        if not icon_clear.isNull():
            btn_clear_urls.setIcon(icon_clear)
        btn_clear_urls.clicked.connect(self.clear_url_list)
        btn_open = QPushButton("Ouvrir le dossier")
        icon_open = themed_icon("folder-open")
        if not icon_open.isNull():
            btn_open.setIcon(icon_open)
        btn_open.clicked.connect(self.open_output_dir)
        btn_open_v = QPushButton("Ouvrir Vidéos")
        if not icon_open.isNull():
            btn_open_v.setIcon(icon_open)
        btn_open_v.clicked.connect(lambda: self._open_dir(VIDEOS_DIR))
        btn_open_a = QPushButton("Ouvrir Audios")
        if not icon_open.isNull():
            btn_open_a.setIcon(icon_open)
        btn_open_a.clicked.connect(lambda: self._open_dir(AUDIOS_DIR))
        add_line.addWidget(self.edit_url, 1)
        add_line.addWidget(btn_add)
        add_line.addWidget(btn_paste)
        add_line.addWidget(btn_file)
        add_line.addWidget(btn_clear_urls)
        add_line.addWidget(btn_open)
        add_line.addWidget(btn_open_v)
        add_line.addWidget(btn_open_a)
        urls_layout.addLayout(add_line)

        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.ActionsContextMenu)
        act_del = QAction("Supprimer la sélection", self)
        act_del.triggered.connect(self.delete_selected)
        self.list.addAction(act_del)
        self.list.currentItemChanged.connect(self.on_current_item_changed)
        urls_layout.addWidget(self.list)

        # Tableau formats
        self.tbl = QTableWidget(0, 10)
        self.tbl.setHorizontalHeaderLabels(
            [
                "✔",
                "ID video",
                "Résolution",
                "FPS",
                "Ext/VC",
                "Poids vidéo",
                "ID audio",
                "Audio",
                "Poids audio",
                "Total estimé",
            ]
        )
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.itemDoubleClicked.connect(self.on_format_double_click)
        urls_layout.addWidget(self.tbl)

        # ----- Contrôles -----
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self.btn_start = QPushButton("Démarrer")
        icon_start = themed_icon("media-playback-start", "system-run")
        if not icon_start.isNull():
            self.btn_start.setIcon(icon_start)
        self.btn_start.clicked.connect(self.start_queue)
        self.btn_stop = QPushButton("Stop")
        icon_stop = themed_icon("media-playback-stop", "process-stop")
        if not icon_stop.isNull():
            self.btn_stop.setIcon(icon_stop)
        self.btn_stop.clicked.connect(self.stop_current)
        ctrl.addWidget(self.btn_start)
        ctrl.addWidget(self.btn_stop)
        ctrl.addStretch(1)
        urls_layout.addLayout(ctrl)
        root.addWidget(urls_box)

        # ----- Statuts -----
        stat_line = QHBoxLayout()
        stat_line.setSpacing(8)
        self.lab_name = QLabel("Fichier : —")
        self.lab_speed = QLabel("Vitesse : —")
        self.lab_size = QLabel("Taille : —")
        self.lab_eta = QLabel("ETA : —")
        stat_line.addWidget(self.lab_name, 3)
        stat_line.addWidget(self.lab_speed, 1)
        stat_line.addWidget(self.lab_size, 1)
        stat_line.addWidget(self.lab_eta, 1)
        root.addLayout(stat_line)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        root.addWidget(self.bar)

        self.setMinimumWidth(1080)

    def open_output_dir(self):
        path = OUT_DIR
        self._open_dir(path)

    def clear_url_list(self):
        self.queue.clear()
        self.list.clear()

    def append_task(self, url: str):
        t = Task(url=url)
        self.queue.append(t)
        item = QListWidgetItem(f"[En attente] {url}")
        item.setData(Qt.UserRole, t)
        self.list.addItem(item)
        return item

    def add_url(self):
        url = self.edit_url.text().strip()
        if not url:
            return
        for i in range(self.list.count()):
            exist_task: Task = self.list.item(i).data(Qt.UserRole)
            if exist_task and exist_task.url == url:
                QMessageBox.information(self, "Déjà présent", "Cette URL est déjà dans la liste.")
                self.edit_url.clear()
                return
        item = self.append_task(url)
        self.list.setCurrentItem(item)
        self.inspect_task_async(item)
        self.edit_url.clear()

    def paste_clipboard(self):
        cb = QApplication.clipboard()
        if not cb:
            return
        text = (cb.text() or "").strip()
        if not text:
            return
        match = YOUTUBE_REGEX.search(text)
        if match:
            self.edit_url.setText(match.group(1))
            self.add_url()

    def add_from_file(self):
        p, _ = QFileDialog.getOpenFileName(self, "Fichier .txt", "", "Text (*.txt)")
        if not p: return
        new_items: List[QListWidgetItem] = []
        for line in pathlib.Path(p).read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if not u: continue
            exists = False
            for i in range(self.list.count()):
                exist_task: Task = self.list.item(i).data(Qt.UserRole)
                if exist_task and exist_task.url == u:
                    exists = True
                    break
            if exists:
                continue
            new_items.append(self.append_task(u))
        if new_items:
            item = new_items[0]
            self.list.setCurrentItem(item)
            self.inspect_task_async(item)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls: list[str] = []
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                path = url.toLocalFile() or url.toString()
                if not path:
                    continue
                if path.lower().endswith(".txt"):
                    try:
                        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
                            if line.strip():
                                urls.append(line.strip())
                    except Exception:
                        pass
                else:
                    urls.append(path)
        if e.mimeData().hasText():
            urls.append(e.mimeData().text())
        for raw in urls:
            if not raw:
                continue
            match = YOUTUBE_REGEX.search(raw.strip())
            if match:
                self.edit_url.setText(match.group(1))
                self.add_url()
        e.acceptProposedAction()

    def delete_selected(self):
        for it in self.list.selectedItems():
            t: Task = it.data(Qt.UserRole)
            if t in self.queue: self.queue.remove(t)
            self.list.takeItem(self.list.row(it))

    # ---------- Inspecteur ----------
    def on_current_item_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        # Debounce pour éviter de spammer l’inspect quand on navigue vite
        self.inspect_debounce.start()

    def _inspect_current_after_debounce(self):
        item = self.list.currentItem()
        if item:
            self.inspect_task_async(item)

    def inspect_task_async(self, item: QListWidgetItem):
        """Démarre l'inspection en arrière-plan pour l'item donné."""
        task: Task = item.data(Qt.UserRole)
        if not task or not task.url:
            return

        # UI: état "Analyse…"
        self.tbl.setRowCount(0)
        self.statusBar("Analyse des formats…")
        self._cursor_wait(True)
        self.btn_start.setEnabled(False)

        # numéro de séquence pour ignorer les réponses tardives
        self.inspect_seq += 1
        seq = self.inspect_seq

        # tuer le worker précédent s'il existe (on n'a pas d'annulation "forte" sur yt-dlp, mais on évite de mélanger les signaux)
        if self.inspect_worker and self.inspect_worker.isRunning():
            pass

        w = InspectWorker(task.url, self)
        self.inspect_worker = w
        w.sig_done.connect(lambda url, info, s=seq: self.on_inspect_done(s, item, url, info))
        w.sig_error.connect(lambda url, msg, s=seq: self.on_inspect_error(s, item, url, msg))
        w.start()


    def on_inspect_done(self, seq: int, item: QListWidgetItem, url: str, info: dict):
        # ignorer si une requête plus récente a été lancée
        if seq != self.inspect_seq:
            return

        self.last_inspect_info = info or {}
        formats = self.last_inspect_info.get("formats") or []
        duration = self.last_inspect_info.get("duration")

        vlist = list_video_formats(formats, mp4_friendly=True)
        abest = pick_best_audio(formats, mp4_friendly=True)

        self.tbl.setRowCount(0)
        task: Task = item.data(Qt.UserRole)

        for vf in vlist:
            vid_id = vf.get("format_id") or ""
            res = f"{vf.get('height') or ''}p"
            fps = vf.get("fps") or ""
            vc = f"{vf.get('ext','')}/{vf.get('vcodec','')}"
            vsize = estimate_size(vf, duration)

            if abest:
                aid = abest.get("format_id") or ""
                aname = f"{abest.get('ext','')}/{abest.get('acodec','')}"
                asize = estimate_size(abest, duration)
            else:
                aid, aname, asize = "", "", None

            total = (vsize or 0) + (asize or 0)

            row = self.tbl.rowCount()
            self.tbl.insertRow(row)

            # Colonne 0 : point vert si format déjà choisi
            chosen = f"{vid_id}+{aid}" if aid else vid_id
            dot_item = QTableWidgetItem("●" if task and task.selected_fmt == chosen else "")
            dot_item.setTextAlignment(Qt.AlignCenter)
            if dot_item.text():
                dot_item.setForeground(QColor(0, 170, 0))
            self.tbl.setItem(row, 0, dot_item)

            values = [vid_id, res, str(fps), vc, human_size(vsize), aid, aname, human_size(asize), human_size(total)]
            for col, val in enumerate(values, start=1):
                self.tbl.setItem(row, col, QTableWidgetItem(val))

        self.tbl.resizeColumnsToContents()
        title = self.last_inspect_info.get("title") or "—"
        duration = self.last_inspect_info.get("duration") or 0
        dur_txt = human_eta(int(duration)) if duration else "—"
        self.statusBar(f"Formats prêts — {title} ({dur_txt})")
        self._cursor_wait(False)
        self.btn_start.setEnabled(True)
        self.inspect_worker = None


    def on_inspect_error(self, seq: int, item: QListWidgetItem, url: str, msg: str):
        # ignorer si une requête plus récente a été lancée
        if seq != self.inspect_seq:
            return
        self._cursor_wait(False)
        self.btn_start.setEnabled(True)
        self.statusBar("Échec de l’analyse")
        QMessageBox.warning(self, "Erreur", f"Impossible d’inspecter : {msg}")
        if "429" in msg or "Too Many Requests" in msg:
            QMessageBox.warning(self, "Limite atteinte",
                                "YouTube a limité l’inspection (429). Réessaie dans ~1 minute.")
        self.inspect_worker = None

    def on_format_double_click(self, it: QTableWidgetItem):
        row = it.row()
        item = self.list.currentItem()
        if not item or row < 0:
            return
        task: Task = item.data(Qt.UserRole)
        if not task:
            return

        vid_item = self.tbl.item(row, 1)
        if not vid_item:
            return
        vid = vid_item.text().strip()
        aid_item = self.tbl.item(row, 6)
        aid = aid_item.text().strip() if aid_item else ""
        chosen = f"{vid}+{aid}" if aid else vid
        task.selected_fmt = chosen

        for r in range(self.tbl.rowCount()):
            di = self.tbl.item(r, 0)
            if di is None:
                di = QTableWidgetItem("")
                di.setTextAlignment(Qt.AlignCenter)
                self.tbl.setItem(r, 0, di)
            else:
                di.setText("")
                di.setTextAlignment(Qt.AlignCenter)
                di.setForeground(QColor())

        ok = self.tbl.item(row, 0)
        if ok is None:
            ok = QTableWidgetItem("")
            self.tbl.setItem(row, 0, ok)
        ok.setText("●")
        ok.setTextAlignment(Qt.AlignCenter)
        ok.setForeground(QColor(0, 170, 0))

        self.statusBar(f"Format choisi : {chosen}")

    # ---------- Options yt-dlp ----------
    def build_opts(self, task: Task):
        outdir = OUT_DIR
        fmt = task.selected_fmt or "bestvideo[ext=mp4][vcodec*=avc1]+bestaudio[ext=m4a]/best[ext=mp4]"

        folder_tmpl = "%(title).200s [%(id)s]"
        file_tmpl = "%(title).200s [%(id)s].%(ext)s"
        outtmpl = str(outdir / folder_tmpl / file_tmpl)

        opts = {
            "outtmpl": outtmpl,
            "windowsfilenames": True,
            "format": fmt,
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
            ],
            # IMPORTANT: garder la vidéo après l'extraction audio
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
        return opts

    # ---------- File d’attente ----------
    def start_queue(self):
        if self.list.count() == 0 and self.edit_url.text().strip():
            self.add_url()

        if self.current_worker and self.current_worker.isRunning():
            QMessageBox.information(self, "Déjà en cours", "Un téléchargement est déjà en cours.")
            return

        # PATCH START: vérif FFmpeg
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            QMessageBox.warning(
                self,
                "FFmpeg manquant",
                "Installe FFmpeg avant de télécharger (ex: winget install Gyan.FFmpeg).",
            )
            return
        # PATCH END

        next_task = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            t: Task = it.data(Qt.UserRole)
            if t.status in ("En attente", "Erreur"):
                next_task = (i, it, t); break
        if not next_task:
            QMessageBox.information(self, "Info", "Aucune tâche en attente.")
            return

        _, item, task = next_task
        task.status = "En cours"
        item.setText(f"[En cours] {task.url}")

        opts = self.build_opts(task)
        self.current_worker = DownloadWorker(task, opts, self)
        self.current_worker.sig_progress.connect(lambda d, tot, sp, eta, fn: self.on_progress(item, task, d, tot, sp, eta, fn))
        self.current_worker.sig_status.connect(self.statusBar)
        self.current_worker.sig_done.connect(lambda ok, msg, info: self.on_done(item, task, ok, msg, info))
        self.btn_start.setEnabled(False)
        self.current_worker.start()

    def stop_current(self):
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.stop()

    @Slot()
    def on_progress(self, item: QListWidgetItem, task: Task, downloaded: int, total: int, speed: float, eta: int, filename: str):
        task.downloaded, task.total, task.speed, task.eta = downloaded, total, speed, eta
        if filename: task.filename = filename
        pct = int(downloaded * 100 / total) if total else 0
        self.bar.setValue(pct)
        name = pathlib.Path(task.filename).name if task.filename else "—"
        self.lab_name.setText(f"Fichier : {name}")
        self.lab_speed.setText(f"Vitesse : {human_rate(speed)}")
        self.lab_size.setText(f"Taille : {human_size(downloaded)} / {human_size(total)}")
        self.lab_eta.setText(f"ETA : {human_eta(eta)}")
        item.setText(f"[{pct:>3}%] {task.url}")

    @Slot()
    def on_done(self, item: QListWidgetItem, task: Task, ok: bool, msg: str, info: dict):
        if ok:
            task.status = "Terminé"
            item.setText(f"[Terminé] {task.url}")
            self.statusBar(f"Terminé : {msg}")
            task.video_id = (info or {}).get("id")
            moved = move_final_outputs(task)
            self.cleanup_residuals(task)
            try:
                if task.filename:
                    subdir = OUT_DIR / pathlib.Path(task.filename).parent.name
                    delete_dir_if_empty(subdir)
            except Exception:
                pass

            audio_path = moved.get("audio") or task.final_audio_path
            if not audio_path:
                audio_path = ensure_audio(task)
                if audio_path:
                    self.statusBar("Audio généré depuis la vidéo pour transcription")
            if task.source == "telegram" and task.chat_id and audio_path:
                self.sig_audio_completed.emit(task.chat_id, audio_path)
            elif audio_path:
                reply = QMessageBox.question(
                    self,
                    "Transcription",
                    "Voulez-vous transcrire l’audio téléchargé ?",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    self.sig_request_transcription.emit([audio_path])
        else:
            task.status = "Erreur"
            item.setText(f"[Erreur] {task.url}")
            if task.source == "telegram" and task.chat_id:
                main = self.window()
                worker = getattr(main, "telegram_worker", None)
                if worker:
                    worker.send_message(task.chat_id, f"Échec du téléchargement : {msg}")
            else:
                QMessageBox.warning(self, "Erreur", f"Échec du téléchargement :\n{msg}")
        self.bar.setValue(0)
        self.current_worker = None
        self.btn_start.setEnabled(True)
        QTimer.singleShot(200, self.start_queue)
        return

    def statusBar(self, text: str):
        self.window().setWindowTitle(f"FlowGrab — {text}")

    def cleanup_residuals(self, task: Task):
        """
        Supprime les fichiers intermédiaires dans le sous-dossier d'origine :
          - flux bruts (.webm, .m4a, etc.)
          - .fNNN.mp4 (vidéo intermédiaire)
        Conserve:
          - Titre [ID].mp4 (déjà déplacée)
          - Titre [ID].mp3 (déjà déplacée)
        """
        if not task.video_id or not task.filename:
            return

        subdir = pathlib.Path(task.filename).parent
        if not subdir.exists():
            return

        token = f"[{task.video_id}]"
        for p in list(subdir.iterdir()):
            try:
                if not p.is_file() or token not in p.name:
                    continue
                ext = p.suffix.lower()
                # les finaux ont été déplacés; on ne touche qu'aux intermédiaires
                if ext == ".mp4":
                    if ".f" in p.stem:
                        p.unlink()
                    continue
                if ext == ".mp3":
                    continue
                p.unlink()
            except Exception:
                pass


class ServeurTab(QWidget):
    """Onglet très simple avec deux boutons : Allumer / Éteindre.
    Allumer => lance PowerShell avec le script (cloudflared + n8n).
    Éteindre => arrête le process et son arbre.
    """

    sig_public_url = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: LongProcWorker | None = None
        self.ps_file: str | None = None
        self._pid: int | None = None
        self._last_public_url: str | None = None
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)

        # Ligne boutons + statut
        row = QHBoxLayout()
        self.btn_on = QPushButton("Allumer")
        self.btn_off = QPushButton("Éteindre")
        self.btn_off.setEnabled(False)
        self.btn_open = QPushButton("Ouvrir n8n")
        self.btn_open.setEnabled(False)
        self.lab_status = QLabel("Statut : inactif")
        self.btn_on.clicked.connect(self.start)
        self.btn_off.clicked.connect(self.stop)
        self.btn_open.clicked.connect(self.open_n8n)
        row.addWidget(self.btn_on)
        row.addWidget(self.btn_off)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        row.addWidget(self.lab_status)
        root.addLayout(row)

        # Logs
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs cloudflared / n8n…")
        root.addWidget(self.logs)

    # --- helpers UI ---
    def log(self, s: str):
        self.logs.append(s)
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", s)
        if m:
            url = m.group(0)
            self._last_public_url = url
            self.sig_public_url.emit(url)

    def open_n8n(self):
        url = self._last_public_url or "http://localhost:5678"
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    # --- start/stop ---
    def start(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Déjà en cours", "Le serveur est déjà allumé.")
            return

        # Vérifs rapides côté Python pour retour immédiat à l'utilisateur
        pwsh = shutil.which("powershell") or shutil.which("powershell.exe")
        if not pwsh:
            QMessageBox.warning(self, "PowerShell introuvable", "PowerShell est requis.")
            return
        if not shutil.which("cloudflared"):
            QMessageBox.warning(self, "cloudflared introuvable",
                                "Installe-le : winget install Cloudflare.cloudflared")
            return
        if not shutil.which("n8n"):
            QMessageBox.warning(self, "n8n introuvable",
                                "Installe-le : npm i -g n8n")
            return

        # Écrit le script PowerShell fourni par l'utilisateur dans un fichier temporaire
        ps_code = r'''
param([int]$Port = 5678)

$ErrorActionPreference = 'Stop'

# --- Vérifs rapides
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Error "cloudflared introuvable. Installe-le: winget install Cloudflare.cloudflared"
  exit 1
}
if (-not (Get-Command n8n -ErrorAction SilentlyContinue)) {
  Write-Error "n8n introuvable. Installe-le: npm i -g n8n"
  exit 1
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
  Write-Error "Le port $Port est déjà utilisé. Ferme l'autre instance ou choisis un autre port."
  exit 1
}

# --- 1) Démarre cloudflared en arrière-plan et loggue sa sortie (stdout/err séparés)
$logOut = Join-Path $env:TEMP "cloudflared_n8n_${Port}_out.log"
$logErr = Join-Path $env:TEMP "cloudflared_n8n_${Port}_err.log"
if (Test-Path $logOut) { Remove-Item $logOut -Force }
if (Test-Path $logErr) { Remove-Item $logErr -Force }

$cfArgs = @("tunnel","--url","http://localhost:$Port","--ha-connections","1","--protocol","quic")
$cfProc = Start-Process (Get-Command cloudflared).Source `
          -ArgumentList $cfArgs -NoNewWindow `
          -RedirectStandardOutput $logOut -RedirectStandardError $logErr -PassThru
Write-Host "cloudflared PID: $($cfProc.Id). Attente de l'URL publique…"

# --- 2) Récupère l'URL publique
$publicUrl = $null
$regex = [regex]'https://[a-z0-9-]+\.trycloudflare\.com'
for ($i=0; $i -lt 60; $i++) {  # ~30s max
  $content = ""
  if (Test-Path $logOut) { $content += (Get-Content $logOut -Raw) }
  if (Test-Path $logErr) { $content += "`n" + (Get-Content $logErr -Raw) }
  if ($content) {
    $m = $regex.Match($content)
    if ($m.Success) { $publicUrl = $m.Value; break }
  }
  Start-Sleep -Milliseconds 500
}

if ($publicUrl) {
  Write-Host "URL publique: $publicUrl"
  $env:WEBHOOK_URL         = $publicUrl
  $env:N8N_EDITOR_BASE_URL = $publicUrl
} else {
  Write-Warning "Impossible de lire l'URL publique. n8n sera accessible en local uniquement."
}

# --- 3) Exporte le port (ne PAS changer N8N_ENCRYPTION_KEY si tu as déjà lancé n8n avant)
$env:N8N_PORT = "$Port"

# --- 4) Lance n8n au premier plan
Write-Host "Démarrage n8n sur http://localhost:$Port ..."
& (Get-Command n8n).Source

# --- 5) A l'arrêt de n8n, coupe cloudflared proprement
Write-Host "n8n arrêté. Extinction de cloudflared…"
if ($cfProc -and -not $cfProc.HasExited) {
  try { Stop-Process -Id $cfProc.Id -Force -ErrorAction SilentlyContinue } catch {}
}
Write-Host "Terminé."
'''
        if self.ps_file and os.path.exists(self.ps_file):
            try:
                os.remove(self.ps_file)
            except Exception:
                pass
            self.ps_file = None
        fd, tmp = tempfile.mkstemp(prefix="fg_srv_", suffix=".ps1")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(ps_code)
        self.ps_file = tmp

        args = [
            pwsh,
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", tmp,
            "-Port", "5678",  # le besoin: 2 boutons sans autre option; port fixe
        ]

        self.log(f">>> Lancement serveur (port 5678)")
        self.lab_status.setText("Statut : démarrage…")
        self.btn_on.setEnabled(False)
        self.btn_off.setEnabled(True)
        self.btn_open.setEnabled(False)

        self.worker = LongProcWorker(args, env=os.environ.copy(), parent=self)
        self.worker.sig_started.connect(self.on_started)
        self.worker.sig_line.connect(self.log)
        self.worker.sig_done.connect(self.on_done)
        self.worker.start()

    def on_started(self, pid: int):
        self._pid = pid
        self.lab_status.setText(f"Statut : en cours (pid {pid})")
        self.log(f"[ps] démarré (pid {pid})")
        self.btn_open.setEnabled(True)

    def stop(self):
        self.lab_status.setText("Statut : arrêt…")
        self.log(">>> Extinction demandée…")
        self.btn_open.setEnabled(False)
        self._last_public_url = None
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        else:
            self.on_done(0)

    def on_done(self, code: int):
        self.log(f">>> Terminé (code={code})")
        self.lab_status.setText("Statut : inactif")
        self.btn_on.setEnabled(True)
        self.btn_off.setEnabled(False)
        self.btn_open.setEnabled(False)
        self._last_public_url = None
        self._pid = None
        self.worker = None
        if self.ps_file:
            try:
                os.remove(self.ps_file)
            except Exception:
                pass
            self.ps_file = None


# ---------------------- Onglet Transcription ----------------------
class MultiUploadWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, url: str, files: List[str], parent=None):
        super().__init__(parent)
        self.url = url
        self.files = list(files)

    def run(self):
        try:
            import requests
        except ImportError:
            self.sig_log.emit("Erreur : le module 'requests' est introuvable. Exécute `pip install requests`.")
            self.sig_done.emit(False)
            return

        if not self.files:
            self.sig_log.emit("Aucun fichier à envoyer.")
            self.sig_done.emit(False)
            return

        all_ok = True
        self.sig_log.emit(f">>> Envoi vers {self.url} — {len(self.files)} fichier(s)")
        session = requests.Session()
        for path in self.files:
            if not os.path.exists(path):
                self.sig_log.emit(f"[SKIP] Introuvable : {path}")
                all_ok = False
                continue

            mime, _ = mimetypes.guess_type(path)
            mime = mime or "application/octet-stream"
            basename = os.path.basename(path)
            self.sig_log.emit(
                f"POST {self.url}\n  -> {basename} (MIME={mime}) field='data'"
            )

            try:
                with open(path, "rb") as handle:
                    files = {"data": (basename, handle, mime)}
                    resp = session.post(self.url, files=files, timeout=(10, 600))
                self.sig_log.emit(f"HTTP {resp.status_code}")
                body = resp.text or ""
                if len(body) > 2000:
                    body = body[:2000] + "\n...[tronqué]..."
                if body.strip():
                    self.sig_log.emit(body)
                body_lower = body.lower()
                if resp.status_code == 404 and (
                    "not registered" in body_lower or "did you mean get" in body_lower
                ):
                    self.sig_log.emit(
                        "Indice : sur un webhook-test, clique sur 'Listen for test event' avant d'envoyer."
                    )
                if not (200 <= resp.status_code < 300):
                    all_ok = False
            except Exception as exc:
                all_ok = False
                self.sig_log.emit(f"[ERREUR réseau] {exc}")

        session.close()
        self.sig_log.emit(">>> Terminé.")
        self.sig_done.emit(all_ok)


class TranscriptionTab(QWidget):
    sig_url_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.selected_paths: set[str] = set()
        self.worker: MultiUploadWorker | None = None
        self.last_dir: str | None = None
        self._updating_url = False
        self._webhook_path = "/webhook/Audio"
        self.build_ui()
        self.update_send_button()

    # PATCH START: rename N8NTab -> TranscriptionTab (+ libellés)
    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        url_row.addWidget(QLabel("URL de transcription (n8n):"))
        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText("https://…trycloudflare.com/webhook/Audio")
        self.edit_url.textChanged.connect(self.on_url_changed)
        url_row.addWidget(self.edit_url, 1)
        root.addLayout(url_row)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self.btn_add = QPushButton("Ajouter fichier(s)…")
        icon_add = themed_icon("list-add", "document-open")
        if not icon_add.isNull():
            self.btn_add.setIcon(icon_add)
        self.btn_add.clicked.connect(self.on_add_files)
        self.btn_send = QPushButton()
        icon_send = themed_icon("mail-send", "document-send")
        if not icon_send.isNull():
            self.btn_send.setIcon(icon_send)
        self.btn_send.clicked.connect(self.on_send)
        self.btn_clear = QPushButton("Vider la liste")
        icon_clear = themed_icon("edit-clear", "user-trash")
        if not icon_clear.isNull():
            self.btn_clear.setIcon(icon_clear)
        self.btn_clear.clicked.connect(self.clear_selection_and_logs)
        actions_row.addWidget(self.btn_add)
        actions_row.addWidget(self.btn_send)
        actions_row.addWidget(self.btn_clear)
        actions_row.addStretch(1)
        root.addLayout(actions_row)

        self.list_sel = QListWidget()
        self.list_sel.itemDoubleClicked.connect(self.remove_selected_item)
        root.addWidget(self.list_sel, 1)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs webhook / réponses serveur…")
        root.addWidget(self.logs, 1)

        self.setMinimumSize(720, 480)
    # PATCH END

    # --- sélection ---
    def update_send_button(self):
        count = len(self.selected_paths)
        self.btn_send.setText(f"Envoyer ({count})")
        self.btn_send.setEnabled(count > 0 and self.worker is None)

    def add_to_selection(self, path: str):
        norm = os.path.abspath(path)
        if norm in self.selected_paths:
            return
        self.selected_paths.add(norm)
        item = QListWidgetItem(os.path.basename(norm) or norm)
        item.setToolTip(norm)
        item.setData(Qt.UserRole, norm)
        self.list_sel.addItem(item)
        self.update_send_button()

    def remove_selected_item(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path in self.selected_paths:
            self.selected_paths.remove(path)
        row = self.list_sel.row(item)
        self.list_sel.takeItem(row)
        self.update_send_button()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            return
        exts = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".mp4", ".mkv", ".webm"}
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isfile(path):
                if os.path.splitext(path)[1].lower() in exts:
                    self.add_to_selection(path)
        self.update_send_button()
        e.acceptProposedAction()

    def on_add_files(self):
        start_dir = self.last_dir or str(pathlib.Path.home())
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Choisir des fichiers",
            start_dir,
            "Audio/Video (*.mp3 *.m4a *.wav *.aac *.ogg *.flac *.mp4 *.mkv *.webm);;Tous les fichiers (*.*)",
        )
        if not files:
            return
        for path in files:
            self.add_to_selection(path)
        self.last_dir = os.path.dirname(files[-1]) or self.last_dir

    # --- envoi ---
    def on_send(self):
        if self.worker is not None:
            QMessageBox.information(self, "Envoi en cours", "Un upload est déjà en cours.")
            return

        url = (self.edit_url.text() or "").strip()
        if not url:
            QMessageBox.warning(self, "Manque URL", "Colle l’URL du webhook n8n.")
            return

        if not self.selected_paths:
            QMessageBox.information(self, "Rien à envoyer", "Sélectionne au moins un fichier.")
            return

        files: List[str] = []
        for idx in range(self.list_sel.count()):
            item = self.list_sel.item(idx)
            path = item.data(Qt.UserRole)
            if path:
                files.append(path)

        if not files:
            QMessageBox.information(self, "Rien à envoyer", "Sélectionne au moins un fichier.")
            return

        self.btn_send.setEnabled(False)
        self.worker = MultiUploadWorker(url, files, self)
        self.worker.sig_log.connect(self.logs.append)
        self.worker.sig_done.connect(self.on_sent_done)
        self.worker.start()
        token = os.environ.get("FG_NOTIFY_TOKEN", "change_me")
        self.logs.append("Exemple n8n → App : GET http://127.0.0.1:5050/notify-done?token=<FG_NOTIFY_TOKEN>")
        if token == "change_me":
            self.logs.append("Définis FG_NOTIFY_TOKEN dans tes variables d’environnement pour sécuriser la notification locale.")

    def on_sent_done(self, ok: bool):
        self.worker = None
        self.update_send_button()
        if ok:
            QMessageBox.information(self, "OK", "Tous les envois ont réussi.")
        else:
            QMessageBox.warning(
                self,
                "Terminé avec erreurs",
                "Au moins un fichier a échoué. Consulte les logs pour les détails.",
            )

    def clear_selection_and_logs(self):
        self.selected_paths.clear()
        self.list_sel.clear()
        self.logs.clear()
        self.update_send_button()

    # PATCH START: init + setters + envoi direct
    def init_from_config(self, cfg: dict):
        path = cfg.get("webhook_path") or "/webhook/Audio"
        full = cfg.get("webhook_full") or ""
        base = cfg.get("webhook_base") or ""
        if not full and base:
            full = base.rstrip("/") + path
        if full:
            self._set_url_text(full)
        self._webhook_path = path

    def set_webhook_full(self, full: str):
        cur = (self.edit_url.text() or "").strip()
        if not cur or "trycloudflare.com" in cur:
            self._set_url_text(full)

    def send_files_immediately(self, paths: list[str]):
        for p in paths:
            self.add_to_selection(p)
        if self.worker is None:
            self.on_send()
        else:
            self.logs.append("Upload déjà en cours, les fichiers sont ajoutés à la file.")
    # PATCH END

    def _set_url_text(self, text: str):
        self._updating_url = True
        try:
            self.edit_url.setText(text)
        finally:
            self._updating_url = False

    def on_url_changed(self, text: str):
        if self._updating_url:
            return
        self.sig_url_changed.emit(text)
# ---------------------- Onglets placeholders ----------------------
class ComingSoonTab(QWidget):
    def __init__(self, title="À venir", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lbl = QLabel(f"{title}\n\nBientôt disponible…")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)

class SettingsTab(QWidget):
    """
    Onglet Paramètres généraux :
    - Contrôle du thème
    - Section Telegram (token, mode, démarrage)
    - Outils de maintenance (git pull, redémarrage)
    """

    def __init__(self, app_ref=None, parent=None):
        super().__init__(parent)
        self.app_ref = app_ref
        self.worker: CommandWorker | None = None
        self._loading_cfg = False
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        theme_line = QHBoxLayout()
        theme_line.setSpacing(8)
        theme_label = QLabel("Thème")
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["Clair", "Sombre"])
        self.cmb_theme.currentIndexChanged.connect(self.on_theme_change)
        theme_line.addWidget(theme_label)
        theme_line.addWidget(self.cmb_theme)
        theme_line.addStretch(1)
        root.addLayout(theme_line)

        # Section Telegram
        grp_tg = QGroupBox("Telegram")
        tg_layout = QVBoxLayout(grp_tg)
        tg_layout.setContentsMargins(12, 12, 12, 12)
        tg_layout.setSpacing(8)

        row_token = QHBoxLayout()
        row_token.setSpacing(8)
        row_token.addWidget(QLabel("Token"))
        self.ed_token = QLineEdit()
        self.ed_token.setPlaceholderText("123456:ABC-DEF…")
        row_token.addWidget(self.ed_token, 1)
        tg_layout.addLayout(row_token)

        row_mode = QHBoxLayout()
        row_mode.setSpacing(8)
        row_mode.addWidget(QLabel("Mode"))
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Auto", "Polling", "Webhook"])
        row_mode.addWidget(self.cmb_mode)
        row_mode.addWidget(QLabel("Port"))
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(8081)
        row_mode.addWidget(self.spin_port)
        row_mode.addStretch(1)
        tg_layout.addLayout(row_mode)

        row_cookies = QHBoxLayout()
        row_cookies.setSpacing(8)
        row_cookies.addWidget(QLabel("Cookies.txt"))
        self.ed_cookies = QLineEdit()
        self.ed_cookies.setPlaceholderText("Chemin vers cookies.txt (optionnel)")
        row_cookies.addWidget(self.ed_cookies, 1)
        self.btn_cookies = QPushButton("Parcourir…")
        icon_file = themed_icon("document-open", "folder-open")
        if not icon_file.isNull():
            self.btn_cookies.setIcon(icon_file)
        self.btn_cookies.clicked.connect(self.on_pick_cookies)
        row_cookies.addWidget(self.btn_cookies)
        tg_layout.addLayout(row_cookies)

        row_user_agent = QHBoxLayout()
        row_user_agent.setSpacing(8)
        row_user_agent.addWidget(QLabel("User-Agent"))
        self.ed_user_agent = QLineEdit()
        self.ed_user_agent.setPlaceholderText("Mozilla/5.0 …")
        row_user_agent.addWidget(self.ed_user_agent, 1)
        tg_layout.addLayout(row_user_agent)

        row_ctrl = QHBoxLayout()
        row_ctrl.setSpacing(8)
        self.btn_tg_start = QPushButton("Démarrer bot")
        icon_start = themed_icon("media-playback-start", "system-run")
        if not icon_start.isNull():
            self.btn_tg_start.setIcon(icon_start)
        self.btn_tg_stop = QPushButton("Arrêter bot")
        icon_stop = themed_icon("media-playback-stop", "process-stop")
        if not icon_stop.isNull():
            self.btn_tg_stop.setIcon(icon_stop)
        self.btn_tg_stop.setEnabled(False)
        self.lab_tg = QLabel("Bot : inactif")
        row_ctrl.addWidget(self.btn_tg_start)
        row_ctrl.addWidget(self.btn_tg_stop)
        row_ctrl.addStretch(1)
        row_ctrl.addWidget(self.lab_tg)
        tg_layout.addLayout(row_ctrl)

        root.addWidget(grp_tg)

        # Ligne boutons maintenance
        line = QHBoxLayout()
        line.setSpacing(8)
        self.btn_update = QPushButton("Mettre à jour l’app (git pull origin main)")
        icon_update = themed_icon("view-refresh", "system-software-update")
        if not icon_update.isNull():
            self.btn_update.setIcon(icon_update)
        self.btn_restart = QPushButton("Redémarrer l’app")
        icon_restart = themed_icon("system-reboot", "application-exit")
        if not icon_restart.isNull():
            self.btn_restart.setIcon(icon_restart)
        self.btn_update.clicked.connect(self.on_update_clicked)
        self.btn_restart.clicked.connect(self.on_restart_clicked)
        line.addWidget(self.btn_update)
        line.addWidget(self.btn_restart)
        root.addLayout(line)

        self.lab_git_hint = QLabel("")
        self.lab_git_hint.setWordWrap(True)
        root.addWidget(self.lab_git_hint)

        git_grp = QGroupBox("Git – Outils de merge")
        git_layout = QHBoxLayout(git_grp)
        git_layout.setContentsMargins(12, 12, 12, 12)
        git_layout.setSpacing(8)
        self.btn_git_continue = QPushButton("Continuer le merge (guidé)")
        self.btn_git_continue.clicked.connect(self.on_git_continue_merge)
        self.btn_git_abort = QPushButton("Abort merge (git merge --abort)")
        self.btn_git_abort.clicked.connect(self.on_git_merge_abort)
        self.btn_git_stash_pull = QPushButton("Stash & Pull (rebase)")
        self.btn_git_stash_pull.clicked.connect(self.on_git_stash_pull)
        git_layout.addWidget(self.btn_git_continue)
        git_layout.addWidget(self.btn_git_abort)
        git_layout.addWidget(self.btn_git_stash_pull)
        root.addWidget(git_grp)

        # Zone de logs
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs des opérations (git, Telegram, etc.)...")
        root.addWidget(self.logs)

        # Info
        info = QLabel("Astuce : l’app cherchera la racine du dépôt (.git) en remontant depuis le dossier du script.")
        info.setWordWrap(True)
        root.addWidget(info)

        # Connexions config Telegram / yt-dlp
        self.ed_token.textChanged.connect(lambda s: self._save_cfg("telegram_token", s.strip()))
        self.cmb_mode.currentTextChanged.connect(lambda t: self._save_cfg("telegram_mode", (t or "auto").lower()))
        self.spin_port.valueChanged.connect(lambda v: self._save_cfg("telegram_port", int(v)))
        self.ed_cookies.textChanged.connect(lambda s: self._save_cfg("cookies_path", s.strip()))
        self.ed_user_agent.textChanged.connect(lambda s: self._save_cfg("user_agent", s.strip()))

        self.refresh_merge_state()

    def init_from_config(self, cfg: dict):
        self._loading_cfg = True
        try:
            token = cfg.get("telegram_token") or ""
            self.ed_token.setText(token)
            mode = (cfg.get("telegram_mode") or "auto").lower()
            nice = mode.capitalize()
            if nice not in ("Auto", "Polling", "Webhook"):
                nice = "Auto"
            self.cmb_mode.setCurrentText(nice)
            port = cfg.get("telegram_port") or DEFAULT_CONFIG["telegram_port"]
            try:
                self.spin_port.setValue(int(port))
            except Exception:
                self.spin_port.setValue(DEFAULT_CONFIG["telegram_port"])
            cookies = cfg.get("cookies_path") or ""
            self.ed_cookies.setText(cookies)
            user_agent = cfg.get("user_agent") or DEFAULT_CONFIG["user_agent"]
            self.ed_user_agent.setText(user_agent)
            self.set_telegram_idle()
        finally:
            self._loading_cfg = False
        self.refresh_merge_state()

    def _save_cfg(self, key: str, value: Any):
        if self._loading_cfg or not self.app_ref:
            return
        cfg = self.app_ref.app_config
        if key == "telegram_token":
            cfg[key] = value or ""
        elif key == "telegram_mode":
            cfg[key] = (value or "auto").lower()
        elif key == "telegram_port":
            try:
                cfg[key] = int(value)
            except Exception:
                cfg[key] = DEFAULT_CONFIG["telegram_port"]
        elif key == "cookies_path":
            cfg[key] = value or ""
        elif key == "user_agent":
            cfg[key] = value or DEFAULT_CONFIG["user_agent"]
        else:
            cfg[key] = value
        save_config(cfg)

    def set_telegram_running(self, mode: str):
        self.lab_tg.setText(f"Bot : en cours ({mode})")
        self.btn_tg_start.setEnabled(False)
        self.btn_tg_stop.setEnabled(True)

    def set_telegram_idle(self):
        self.lab_tg.setText("Bot : inactif")
        self.btn_tg_start.setEnabled(True)
        self.btn_tg_stop.setEnabled(False)

    def append_telegram_info(self, text: str):
        self.append_log(f"[Telegram] {text}")

    def on_pick_cookies(self):
        path, _ = QFileDialog.getOpenFileName(self, "Cookies.txt", "", "Text (*.txt);;Tous les fichiers (*.*)")
        if path:
            self.ed_cookies.setText(path)

    def refresh_merge_state(self):
        repo = self.find_git_root()
        in_merge = bool(repo and _is_merge_in_progress(repo))
        if in_merge:
            self.lab_git_hint.setText("Merge en cours détecté. Utilise les outils ci-dessous pour le résoudre.")
            self.btn_update.setEnabled(False)
        else:
            self.lab_git_hint.setText("")
            if not (self.worker and self.worker.isRunning()):
                self.btn_update.setEnabled(True)

    def _launch_git(self, args: list[str], cwd: pathlib.Path, next_cb: Callable[[int], None] | None = None) -> bool:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Git", "Une commande git est déjà en cours.")
            return False
        git = shutil.which("git")
        if not git:
            QMessageBox.warning(self, "Git", "Git introuvable dans le PATH.")
            return False
        self.append_log(f">>> git {' '.join(args)}")
        self.btn_update.setEnabled(False)
        worker = CommandWorker([git, *args], cwd=cwd)
        self.worker = worker
        worker.sig_line.connect(self.append_log)

        def done(code: int):
            self.append_log(f">>> (exit={code})")
            self.worker = None
            if next_cb:
                next_cb(code)
            else:
                self.refresh_merge_state()

        worker.sig_done.connect(done)
        worker.start()
        return True

    def _run_git_sequence(self, commands: list[list[str]], cwd: pathlib.Path):
        if not commands:
            self.refresh_merge_state()
            return

        first, *rest = commands

        def after(code: int):
            if code == 0 and rest:
                self._run_git_sequence(rest, cwd)
            else:
                self.refresh_merge_state()

        if not self._launch_git(first, cwd, next_cb=after):
            self.refresh_merge_state()

    def on_git_merge_abort(self):
        repo = self.find_git_root()
        if not repo:
            self.append_log("Pas de repo.")
            return
        if not _is_merge_in_progress(repo):
            QMessageBox.information(self, "Git", "Aucun merge en cours.")
            return
        self.append_log(f">>> cwd: {repo}")
        self._launch_git(["merge", "--abort"], repo)

    def on_git_continue_merge(self):
        repo = self.find_git_root()
        if not repo:
            self.append_log("Pas de repo.")
            return
        if not _is_merge_in_progress(repo):
            QMessageBox.information(self, "Git", "Aucun merge en cours.")
            return
        self.append_log(f">>> cwd: {repo}")
        if self._launch_git(["status"], repo):
            self.append_log("Conseil: résous les conflits, puis `git add -A` et `git commit`.\nUtilise 'Mettre à jour' ensuite.")

    def on_git_stash_pull(self):
        repo = self.find_git_root()
        if not repo:
            self.append_log("Pas de repo.")
            return
        self.append_log(f">>> cwd: {repo}")
        cmds = [
            ["stash", "push", "-u", "-m", "flowgrab-auto"],
            ["pull", "--rebase", "origin", "main"],
            ["stash", "pop"],
        ]
        self._run_git_sequence(cmds, repo)

    # ---------- Actions ----------
    def on_theme_change(self, _idx: int):
        app = QApplication.instance()
        if not app:
            return
        if self.cmb_theme.currentText() == "Sombre":
            apply_dark_theme(app)
        else:
            apply_light_theme(app)

    def on_update_clicked(self):
        repo_root = self.find_git_root()
        if not repo_root:
            QMessageBox.warning(self, "Hors dépôt Git", "Aucun dossier '.git' trouvé en remontant depuis ce projet.")
            return
        if _is_merge_in_progress(repo_root):
            QMessageBox.information(self, "Git", "Un merge est en cours. Utilise les outils dédiés avant de lancer git pull.")
            self.refresh_merge_state()
            return

        self.append_log(f">>> cwd: {repo_root}")

        def after(code: int):
            self.on_update_done(code)
            self.refresh_merge_state()

        if not self._launch_git(["pull", "origin", "main"], repo_root, next_cb=after):
            self.refresh_merge_state()

    def on_update_done(self, code: int):
        if code != 0:
            QMessageBox.warning(
                self,
                "Échec mise à jour",
                "La commande git s'est terminée avec une erreur.\nConsulte les logs.",
            )
        else:
            QMessageBox.information(
                self,
                "Mise à jour OK",
                "Pull terminé. Clique sur 'Redémarrer l’app' pour prendre en compte les changements.",
            )

    def on_restart_clicked(self):
        # Relance le même script avec les mêmes arguments
        try:
            subprocess.Popen([sys.executable, *sys.argv], close_fds=True)
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Impossible de redémarrer : {e}")
            return
        QApplication.instance().quit()

    # ---------- Utils ----------
    def append_log(self, text: str):
        self.logs.append(text)

    def find_git_root(self) -> Optional[pathlib.Path]:
        """
        Remonte depuis le dossier du script pour trouver un répertoire contenant '.git'.
        """
        p = pathlib.Path(__file__).resolve().parent
        for parent in [p, *p.parents]:
            if (parent / ".git").exists():
                return parent
        # dernier essai : si on exécute depuis un dossier qui a .git
        if (pathlib.Path.cwd() / ".git").exists():
            return pathlib.Path.cwd()
        return None

# ---------------------- Fenêtre principale ----------------------
class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FlowGrab — Video Downloader (yt-dlp)")
        root = QVBoxLayout(self)
        # PATCH START: tabs wiring + config + signaux
        self.app_config = load_config()
        self.telegram_worker: TelegramWorker | None = None

        self.youtube_tab = YoutubeTab(app_ref=QApplication.instance())
        self.transcription_tab = TranscriptionTab()
        self.serveur_tab = ServeurTab()
        self.settings_tab = SettingsTab(app_ref=self)

        tabs = QTabWidget()
        tabs.addTab(self.youtube_tab, "YouTube")
        tabs.addTab(self.transcription_tab, "Transcription")
        tabs.addTab(ComingSoonTab("À venir 2"), "À venir 2")
        tabs.addTab(ComingSoonTab("À venir 3"), "À venir 3")
        tabs.addTab(ComingSoonTab("À venir 4"), "À venir 4")
        tabs.addTab(self.settings_tab, "Paramètres généraux")
        tabs.addTab(self.serveur_tab, "Serveur")
        self.tabs = tabs
        root.addWidget(tabs)

        # Config JSON
        self.transcription_tab.init_from_config(self.app_config)
        self.settings_tab.init_from_config(self.app_config)

        # Signaux inter-onglets
        self.serveur_tab.sig_public_url.connect(self.on_cloudflare_public_url)       # base
        self.youtube_tab.sig_request_transcription.connect(self.on_transcription_request)
        self.youtube_tab.sig_audio_completed.connect(self.on_audio_ready_from_youtube)
        self.transcription_tab.sig_url_changed.connect(self.on_transcription_url_changed)

        # Paramètres Telegram
        self.settings_tab.btn_tg_start.clicked.connect(self.start_telegram)
        self.settings_tab.btn_tg_stop.clicked.connect(self.stop_telegram)
        # PATCH END

        start_notification_server(self)

    # PATCH START: slots Main pour webhook et transcription
    def on_cloudflare_public_url(self, base: str):
        path = self.app_config.get("webhook_path") or "/webhook/Audio"
        if not path.startswith("/"):
            path = "/" + path
        full = base.rstrip("/") + path
        self.app_config.update({"webhook_base": base, "webhook_full": full, "webhook_path": path})
        save_config(self.app_config)
        self.transcription_tab.set_webhook_full(full)

    def on_transcription_request(self, file_paths: list[str]):
        self.tabs.setCurrentWidget(self.transcription_tab)
        self.transcription_tab.send_files_immediately(file_paths)

    def on_transcription_url_changed(self, text: str):
        path = getattr(self.transcription_tab, "_webhook_path", "/webhook/Audio") or "/webhook/Audio"
        text = (text or "").strip()
        if path and not path.startswith("/"):
            path = "/" + path
        base = ""
        if text and path and path in text:
            idx = text.rfind(path)
            if idx >= 0:
                base = text[:idx]
        if not base:
            base = text.rstrip("/")
        self.app_config.update({
            "webhook_base": base.rstrip("/"),
            "webhook_full": text,
            "webhook_path": path,
        })
        save_config(self.app_config)
    # PATCH END

    # PATCH START: Telegram intégration
    def _effective_telegram_mode(self) -> str:
        mode = (self.app_config.get("telegram_mode") or "auto").lower()
        base = (self.app_config.get("webhook_base") or "").strip()
        if mode == "auto":
            return "webhook" if base else "polling"
        if mode not in ("polling", "webhook"):
            return "polling"
        if mode == "webhook" and not base:
            return "polling"
        return mode

    def start_telegram(self):
        token = (self.app_config.get("telegram_token") or "").strip()
        if not token:
            QMessageBox.warning(self, "Token manquant", "Renseigne le token du bot Telegram dans les paramètres.")
            return
        if self.telegram_worker and self.telegram_worker.isRunning():
            QMessageBox.information(self, "Bot actif", "Le bot Telegram est déjà démarré.")
            return
        worker = TelegramWorker(self.app_config)
        self.telegram_worker = worker
        worker.sig_download_requested.connect(self.on_tg_download_requested)
        worker.sig_info.connect(self.on_telegram_info)
        worker.finished.connect(self.on_telegram_finished)
        mode = worker.effective_mode or self._effective_telegram_mode()
        if self.settings_tab:
            self.settings_tab.set_telegram_running(mode)
            self.settings_tab.append_telegram_info(f"Démarrage bot ({mode})")
        worker.start()

    def stop_telegram(self):
        if not self.telegram_worker:
            if self.settings_tab:
                self.settings_tab.set_telegram_idle()
            return
        worker = self.telegram_worker
        if self.settings_tab:
            self.settings_tab.append_telegram_info("Arrêt du bot demandé…")
        worker.stop()
        worker.wait(5000)
        self.telegram_worker = None
        if self.settings_tab:
            self.settings_tab.set_telegram_idle()

    def on_telegram_finished(self):
        if self.settings_tab:
            self.settings_tab.set_telegram_idle()
        if self.telegram_worker and not self.telegram_worker.isRunning():
            self.telegram_worker = None

    def on_telegram_info(self, text: str):
        if self.settings_tab:
            self.settings_tab.append_telegram_info(text)

    def on_tg_download_requested(self, url: str, fmt: str, chat_id: int | str, title: str):
        try:
            chat_ref: int | str = int(chat_id)
        except (TypeError, ValueError):
            chat_ref = chat_id
        item = self.youtube_tab.append_task(url)
        task: Task = item.data(Qt.UserRole)
        task.selected_fmt = fmt
        task.source = "telegram"
        task.chat_id = chat_ref
        self.youtube_tab.statusBar(f"Téléchargement demandé par Telegram — {title}")
        self.youtube_tab.start_queue()
        if self.telegram_worker:
            self.telegram_worker.send_message(chat_ref, "Téléchargement lancé…")

    def on_audio_ready_from_youtube(self, chat_id: int | str, audio_path: str):
        if not self.telegram_worker:
            return
        try:
            chat_ref: int | str = int(chat_id)
        except (TypeError, ValueError):
            chat_ref = chat_id
        name = os.path.basename(audio_path) or audio_path
        self.telegram_worker.send_message(chat_ref, f"Téléchargement terminé ✅\n{name}")
        self.telegram_worker.ask_transcription(chat_ref, audio_path)
    # PATCH END

    def closeEvent(self, event):
        try:
            self.stop_telegram()
        finally:
            super().closeEvent(event)

# ---------------------- main ----------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_light_theme(app)  # par défaut clair ; change dans l'UI
    w = Main()
    w.show()
    sys.exit(app.exec())
