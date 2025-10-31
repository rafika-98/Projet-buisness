import os
import subprocess
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QListWidgetItem, QMessageBox

from paths import AUDIOS_DIR, TRANSCRIPTION_DIR

try:
    from shiboken6 import isValid as _shiboken_is_valid
except Exception:  # pragma: no cover
    def _shiboken_is_valid(obj):  # type: ignore[return-type]
        return obj is not None

try:
    from flask import Flask
except ImportError:  # pragma: no cover
    Flask = None  # type: ignore[assignment]

_notification_server_started = False
_notification_parent_widget = None


def _send_windows_notification(message: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        from win10toast import ToastNotifier

        ToastNotifier().show_toast("FlowGrab", message, duration=5, threaded=True)
        return
    except Exception:
        pass
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                "cmd",
                "/c",
                "msg",
                "*",
                message,
            ],
            creationflags=creationflags,
        )
    except Exception:
        pass


def is_list_item_valid(item: Optional[QListWidgetItem]) -> bool:
    try:
        return bool(item) and _shiboken_is_valid(item)
    except Exception:
        return False


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
                "Impossible de démarrer le serveur de notification.\nInstalle Flask avec 'pip install flask' pour activer les notifications.",
            )

        QTimer.singleShot(0, warn_missing_flask)
        _notification_server_started = True
        return

    from flask import request

    flask_app = Flask("flowgrab-notify")
    token = os.environ.get("FG_NOTIFY_TOKEN", "change_me")

    @flask_app.get("/notify-done")
    def notify_done():  # pragma: no cover
        if request.args.get("token") != token:
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

    def run_flask():  # pragma: no cover
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
