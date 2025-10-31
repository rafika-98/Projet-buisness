import mimetypes
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any, Callable, List, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEFAULT_CONFIG, load_config, save_config
from core.download_core import CommandWorker, Task
from flask_notify import start_notification_server
from workers.telegram_worker import TelegramWorker
from ui.ui_frame_extractor_tab import FrameExtractorTab
from ui.ui_youtube_tab import TikTokTab, YoutubeTab, themed_icon

try:  # thème optionnel moderne
    import qdarktheme

    HAS_QDT = True
except Exception:  # pragma: no cover
    HAS_QDT = False


def _apply_qdarktheme(app: QApplication, theme: str) -> bool:
    if not HAS_QDT:
        return False
    setup = getattr(qdarktheme, "setup_theme", None)
    if callable(setup):
        setup(theme)
        return True
    loader = getattr(qdarktheme, "load_stylesheet", None)
    if callable(loader):
        app.setStyleSheet(loader(theme))
        return True
    return False


def apply_dark_theme(app: QApplication) -> None:
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


def apply_light_theme(app: QApplication) -> None:
    if _apply_qdarktheme(app, "light"):
        return
    app.setStyle("Fusion")
    app.setPalette(QApplication.style().standardPalette())


class LongProcWorker(QThread):
    sig_line = Signal(str)
    sig_started = Signal(int)
    sig_done = Signal(int)

    def __init__(self, args: List[str], env: dict | None = None, parent=None):
        super().__init__(parent)
        self.args = args
        self.env = env
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        try:
            creationflags = 0
            if sys.platform.startswith("win"):
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
        except Exception as exc:
            self.sig_line.emit(f"[ERREUR] {exc}")
            self.sig_done.emit(1)

    def stop(self) -> None:
        if not self.proc:
            return
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
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True, text=True
            )
        except Exception:
            pass


class MultiUploadWorker(QThread):
    sig_log = Signal(str)
    sig_done = Signal(bool)

    def __init__(self, url: str, files: List[str], parent=None):
        super().__init__(parent)
        self.url = url
        self.files = list(files)

    def run(self) -> None:
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


def _is_merge_in_progress(repo: pathlib.Path) -> bool:
    return (repo / ".git" / "MERGE_HEAD").exists()



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

    def build_ui(self) -> None:
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

        lists_row = QHBoxLayout()
        lists_row.setSpacing(8)
        self.list_sel = QListWidget()
        self.list_sel.setSelectionMode(QListWidget.ExtendedSelection)
        lists_row.addWidget(self.list_sel, 1)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        lists_row.addWidget(self.logs, 1)
        root.addLayout(lists_row)

        self.update_send_button()

    def update_send_button(self) -> None:
        self.btn_send.setText(f"Envoyer ({self.list_sel.count()})")
        self.btn_send.setEnabled(self.list_sel.count() > 0 and self.worker is None)

    def add_to_selection(self, path: str) -> None:
        if not os.path.exists(path):
            QMessageBox.warning(self, "Introuvable", f"Fichier introuvable : {path}")
            return
        if path in self.selected_paths:
            return
        self.selected_paths.add(path)
        item = QListWidgetItem(os.path.basename(path) or path)
        item.setData(Qt.UserRole, path)
        self.list_sel.addItem(item)
        self.update_send_button()

    def on_add_files(self) -> None:
        start_dir = self.last_dir or str(pathlib.Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Fichiers audio", start_dir, "Audio (*.mp3 *.m4a *.wav *.ogg *.flac)")
        if not paths:
            return
        for path in paths:
            self.add_to_selection(path)
        self.last_dir = os.path.dirname(paths[0])

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile() or url.toString()
                if path:
                    self.add_to_selection(path)
        if event.mimeData().hasText():
            for raw in event.mimeData().text().splitlines():
                if raw.strip():
                    self.add_to_selection(raw.strip())
        event.acceptProposedAction()

    def on_send(self) -> None:
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

    def on_sent_done(self, ok: bool) -> None:
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

    def clear_selection_and_logs(self) -> None:
        self.selected_paths.clear()
        self.list_sel.clear()
        self.logs.clear()
        self.update_send_button()

    def init_from_config(self, cfg: dict) -> None:
        path = cfg.get("webhook_path") or "/webhook/Audio"
        full = cfg.get("webhook_full") or ""
        base = cfg.get("webhook_base") or ""
        if not full and base:
            full = base.rstrip("/") + path
        if full:
            self._set_url_text(full)
        self._webhook_path = path

    def set_webhook_full(self, full: str) -> None:
        cur = (self.edit_url.text() or "").strip()
        if not cur or "trycloudflare.com" in cur:
            self._set_url_text(full)

    def send_files_immediately(self, paths: list[str]) -> None:
        for path in paths:
            self.add_to_selection(path)
        if self.worker is None:
            self.on_send()
        else:
            self.logs.append("Upload déjà en cours, les fichiers sont ajoutés à la file.")

    def _set_url_text(self, text: str) -> None:
        self._updating_url = True
        try:
            self.edit_url.setText(text)
        finally:
            self._updating_url = False

    def on_url_changed(self, text: str) -> None:
        if self._updating_url:
            return
        self.sig_url_changed.emit(text)


class ServeurTab(QWidget):
    sig_public_url = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: LongProcWorker | None = None
        self.ps_file: str | None = None
        self._pid: int | None = None
        self._last_public_url: Optional[str] = None
        self.build_ui()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self.lab_status = QLabel("Statut : inactif")
        root.addWidget(self.lab_status)

        self.btn_on = QPushButton("Allumer serveur")
        icon_on = themed_icon("media-playback-start", "system-run")
        if not icon_on.isNull():
            self.btn_on.setIcon(icon_on)
        self.btn_on.clicked.connect(self.start)

        self.btn_off = QPushButton("Éteindre")
        icon_off = themed_icon("media-playback-stop", "process-stop")
        if not icon_off.isNull():
            self.btn_off.setIcon(icon_off)
        self.btn_off.clicked.connect(self.stop)
        self.btn_off.setEnabled(False)

        self.btn_open = QPushButton("Ouvrir n8n")
        icon_open = themed_icon("internet-web-browser", "applications-internet")
        if not icon_open.isNull():
            self.btn_open.setIcon(icon_open)
        self.btn_open.clicked.connect(self.open_public_url)
        self.btn_open.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self.btn_on)
        btn_row.addWidget(self.btn_off)
        btn_row.addWidget(self.btn_open)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        root.addWidget(self.logs)

    def log(self, text: str) -> None:
        self.logs.append(text)
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
        if match:
            url = match.group(0)
            self._last_public_url = url
            self.sig_public_url.emit(url)

    def open_public_url(self) -> None:
        url = self._last_public_url or "http://localhost:5678"
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass

    def start(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Déjà en cours", "Le serveur est déjà allumé.")
            return

        pwsh = shutil.which("powershell") or shutil.which("powershell.exe")
        if not pwsh:
            QMessageBox.warning(self, "PowerShell introuvable", "PowerShell est requis.")
            return
        if not shutil.which("cloudflared"):
            QMessageBox.warning(
                self,
                "cloudflared introuvable",
                "Installe-le : winget install Cloudflare.cloudflared",
            )
            return
        if not shutil.which("n8n"):
            QMessageBox.warning(self, "n8n introuvable", "Installe-le : npm i -g n8n")
            return

        ps_code = r"""
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
"""

        if self.ps_file and os.path.exists(self.ps_file):
            try:
                os.remove(self.ps_file)
            except Exception:
                pass
            self.ps_file = None
        fd, tmp = tempfile.mkstemp(prefix="fg_srv_", suffix=".ps1")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(ps_code)
        self.ps_file = tmp

        args = [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            tmp,
            "-Port",
            "5678",
        ]

        self.log(">>> Lancement serveur (port 5678)")
        self.lab_status.setText("Statut : démarrage…")
        self.btn_on.setEnabled(False)
        self.btn_off.setEnabled(True)
        self.btn_open.setEnabled(False)

        self.worker = LongProcWorker(args, env=os.environ.copy(), parent=self)
        self.worker.sig_started.connect(self.on_started)
        self.worker.sig_line.connect(self.log)
        self.worker.sig_done.connect(self.on_done)
        self.worker.start()

    def on_started(self, pid: int) -> None:
        self._pid = pid
        self.lab_status.setText(f"Statut : en cours (pid {pid})")
        self.log(f"[ps] démarré (pid {pid})")
        self.btn_open.setEnabled(True)

    def stop(self) -> None:
        self.lab_status.setText("Statut : arrêt…")
        self.log(">>> Extinction demandée…")
        self.btn_open.setEnabled(False)
        self._last_public_url = None
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        else:
            self.on_done(0)

    def on_done(self, code: int) -> None:
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



class ComingSoonTab(QWidget):
    def __init__(self, title="À venir", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lbl = QLabel(f"{title}\n\nBientôt disponible…")
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)


class SettingsTab(QWidget):
    def __init__(self, app_ref=None, parent=None):
        super().__init__(parent)
        self.app_ref = app_ref
        self.worker: CommandWorker | None = None
        self._loading_cfg = False
        self._browser_combo_values: list[tuple[str, str]] = []
        self.build_ui()

    def build_ui(self) -> None:
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
        self.cmb_theme.setCurrentText("Sombre")
        theme_label.setVisible(False)
        self.cmb_theme.setVisible(False)

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
        self.lbl_mode = QLabel("Mode")
        row_mode.addWidget(self.lbl_mode)
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Polling"])
        self.cmb_mode.setCurrentText("Polling")
        row_mode.addWidget(self.cmb_mode)
        self.lbl_port = QLabel("Port")
        row_mode.addWidget(self.lbl_port)
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(8081)
        row_mode.addWidget(self.spin_port)
        row_mode.addStretch(1)
        tg_layout.addLayout(row_mode)

        self.lbl_mode.setVisible(False)
        self.cmb_mode.setVisible(False)
        self.lbl_port.setVisible(False)
        self.spin_port.setVisible(False)

        row_browser = QHBoxLayout()
        row_browser.setSpacing(8)
        self.lbl_browser_cookies = QLabel("Source cookies")
        row_browser.addWidget(self.lbl_browser_cookies)
        self.cmb_browser_cookies = QComboBox()
        self._browser_combo_values = [
            ("Auto (fallback)", "auto"),
            ("Edge", "edge"),
            ("Chrome", "chrome"),
            ("Firefox", "firefox"),
            ("Brave", "brave"),
            ("Vivaldi", "vivaldi"),
            ("Opera", "opera"),
            ("Chromium", "chromium"),
            ("cookies.txt", "cookiefile"),
            ("Aucun (sans cookies)", "none"),
        ]
        for label, value in self._browser_combo_values:
            self.cmb_browser_cookies.addItem(label, value)
        row_browser.addWidget(self.cmb_browser_cookies, 1)
        row_browser.addStretch(1)
        tg_layout.addLayout(row_browser)

        row_cookies = QHBoxLayout()
        row_cookies.setSpacing(8)
        self.lbl_cookies = QLabel("Cookies.txt")
        row_cookies.addWidget(self.lbl_cookies)
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
        self.lbl_user_agent = QLabel("User-Agent")
        row_user_agent.addWidget(self.lbl_user_agent)
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

        line = QHBoxLayout()
        line.setSpacing(8)
        self.btn_update = QPushButton("Mettre à jour l’app (redémarrage auto)")
        icon_update = themed_icon("view-refresh", "system-software-update")
        if not icon_update.isNull():
            self.btn_update.setIcon(icon_update)
        self.btn_restart = QPushButton("Redémarrer l’app")
        icon_restart = themed_icon("system-restart", "system-log-out")
        if not icon_restart.isNull():
            self.btn_restart.setIcon(icon_restart)
        self.btn_update.clicked.connect(self.on_update_clicked)
        self.btn_restart.clicked.connect(self.on_restart_clicked)
        line.addWidget(self.btn_update)
        line.addWidget(self.btn_restart)
        root.addLayout(line)

        self.lab_git_hint = QLabel("")
        root.addWidget(self.lab_git_hint)

        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        root.addWidget(self.logs)

        self.btn_git_abort = QPushButton("Annuler merge en cours")
        self.btn_git_abort.clicked.connect(self.on_git_merge_abort)
        self.btn_git_continue = QPushButton("Aide merge : git status")
        self.btn_git_continue.clicked.connect(self.on_git_continue_merge)
        self.btn_git_update = QPushButton("Stash + Pull + Pop")
        self.btn_git_update.clicked.connect(self.on_git_stash_pull)

        git_row = QHBoxLayout()
        git_row.setSpacing(8)
        git_row.addWidget(self.btn_git_abort)
        git_row.addWidget(self.btn_git_continue)
        git_row.addWidget(self.btn_git_update)
        git_row.addStretch(1)
        root.addLayout(git_row)

        self.cmb_browser_cookies.currentIndexChanged.connect(self.on_browser_choice_changed)
        self.ed_token.textChanged.connect(lambda text: self._save_cfg("telegram_token", text))
        self.ed_cookies.textChanged.connect(lambda text: self._save_cfg("cookies_path", text))
        self.ed_user_agent.textChanged.connect(lambda text: self._save_cfg("user_agent", text))

    def append_log(self, text: str) -> None:
        self.logs.append(text)

    def init_from_config(self, cfg: dict) -> None:
        self._loading_cfg = True
        try:
            self.ed_token.setText(cfg.get("telegram_token", ""))
            mode = (cfg.get("browser_cookies") or "auto").strip().lower()
            idx = 0
            for i, (_label, value) in enumerate(self._browser_combo_values):
                if value == mode:
                    idx = i
                    break
            self.cmb_browser_cookies.setCurrentIndex(idx)
            self.ed_cookies.setText(cfg.get("cookies_path", ""))
            self.ed_user_agent.setText(cfg.get("user_agent", DEFAULT_CONFIG["user_agent"]))
        finally:
            self._loading_cfg = False
        self.refresh_merge_state()

    def _save_cfg(self, key: str, value: Any) -> None:
        if self._loading_cfg or not self.app_ref:
            return
        cfg = self.app_ref.app_config
        if key == "telegram_port":
            cfg[key] = int(value)
        elif key == "browser_cookies":
            cfg[key] = value or "auto"
        else:
            cfg[key] = value or ""
        save_config(cfg)

    def on_browser_choice_changed(self) -> None:
        mode = self.cmb_browser_cookies.currentData(Qt.UserRole) or "auto"
        self._update_cookie_inputs_state(str(mode))
        if self._loading_cfg:
            return
        self._save_cfg("browser_cookies", str(mode))

    def _update_cookie_inputs_state(self, mode: str) -> None:
        enable_cookie_file = mode == "cookiefile"
        for widget in (self.lbl_cookies, self.ed_cookies, self.btn_cookies):
            widget.setEnabled(enable_cookie_file)
        self.ed_cookies.setReadOnly(not enable_cookie_file)

    def set_telegram_running(self, mode: str) -> None:
        self.lab_tg.setText(f"Bot : en cours ({mode})")
        self.btn_tg_start.setEnabled(False)
        self.btn_tg_stop.setEnabled(True)

    def set_telegram_idle(self) -> None:
        self.lab_tg.setText("Bot : inactif")
        self.btn_tg_start.setEnabled(True)
        self.btn_tg_stop.setEnabled(False)

    def append_telegram_info(self, text: str) -> None:
        self.append_log(f"[Telegram] {text}")

    def on_pick_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Cookies.txt", "", "Text (*.txt);;Tous les fichiers (*.*)")
        if path:
            self.ed_cookies.setText(path)

    def refresh_merge_state(self) -> None:
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

        def done(code: int) -> None:
            self.append_log(f">>> (exit={code})")
            self.worker = None
            if next_cb:
                next_cb(code)
            else:
                self.refresh_merge_state()

        worker.sig_done.connect(done)
        worker.start()
        return True

    def _run_git_sequence(self, commands: list[list[str]], cwd: pathlib.Path) -> None:
        if not commands:
            self.refresh_merge_state()
            return
        first, *rest = commands

        def after(code: int) -> None:
            if code == 0 and rest:
                self._run_git_sequence(rest, cwd)
            else:
                self.refresh_merge_state()

        if not self._launch_git(first, cwd, next_cb=after):
            self.refresh_merge_state()

    def on_git_merge_abort(self) -> None:
        repo = self.find_git_root()
        if not repo:
            self.append_log("Pas de repo.")
            return
        if not _is_merge_in_progress(repo):
            QMessageBox.information(self, "Git", "Aucun merge en cours.")
            return
        self.append_log(f">>> cwd: {repo}")
        self._launch_git(["merge", "--abort"], repo)

    def on_git_continue_merge(self) -> None:
        repo = self.find_git_root()
        if not repo:
            self.append_log("Pas de repo.")
            return
        if not _is_merge_in_progress(repo):
            QMessageBox.information(self, "Git", "Aucun merge en cours.")
            return
        self.append_log(f">>> cwd: {repo}")
        if self._launch_git(["status"], repo):
            self.append_log(
                "Conseil: résous les conflits, puis `git add -A` et `git commit`.\nUtilise 'Mettre à jour' ensuite."
            )

    def on_git_stash_pull(self) -> None:
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

    def on_theme_change(self, _idx: int) -> None:
        app = QApplication.instance()
        if not app:
            return
        apply_dark_theme(app)
        if self.cmb_theme.currentText() != "Sombre":
            self.cmb_theme.blockSignals(True)
            self.cmb_theme.setCurrentText("Sombre")
            self.cmb_theme.blockSignals(False)

    def on_update_clicked(self) -> None:
        repo_root = self.find_git_root()
        if not repo_root:
            QMessageBox.warning(self, "Hors dépôt Git", "Aucun dossier '.git' trouvé en remontant depuis ce projet.")
            return
        if _is_merge_in_progress(repo_root):
            QMessageBox.information(self, "Git", "Un merge est en cours. Résous-le avant de mettre à jour.")
            self.refresh_merge_state()
            return
        updater_py = pathlib.Path(__file__).resolve().parent / "scripts" / "updater.py"
        if not updater_py.exists():
            QMessageBox.warning(self, "Updater manquant", f"Fichier introuvable : {updater_py}")
            return
        python_exe = sys.executable
        main_script = os.path.abspath(sys.argv[0])
        QMessageBox.information(
            self,
            "Mise à jour",
            "L’application va se fermer, appliquer la mise à jour (git pull) puis redémarrer automatiquement.",
        )
        try:
            subprocess.Popen(
                [python_exe, "-u", str(updater_py), str(repo_root), python_exe, main_script],
                close_fds=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible de lancer l’updater : {exc}")
            return
        app = QApplication.instance()
        if app:
            app.quit()

    def on_restart_clicked(self) -> None:
        try:
            subprocess.Popen([sys.executable, *sys.argv], close_fds=True)
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible de redémarrer : {exc}")
            return
        QApplication.instance().quit()

    def find_git_root(self) -> Optional[pathlib.Path]:
        p = pathlib.Path(__file__).resolve().parent
        for parent in [p, *p.parents]:
            if (parent / ".git").exists():
                return parent
        if (pathlib.Path.cwd() / ".git").exists():
            return pathlib.Path.cwd()
        return None


class Main(QWidget):
    def __init__(self):
        super().__init__()

        def _is_elevated_win() -> bool:
            if not sys.platform.startswith("win"):
                return False
            try:
                import ctypes

                return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
            except Exception:
                return False

        if _is_elevated_win():
            QMessageBox.information(
                self,
                "Conseil",
                "L’application tourne en mode administrateur. Si les cookies Chromium ne se déchiffrent pas (DPAPI), "
                "relance l’app sans élévation ou force 'browser_cookies' = 'firefox' dans flowgrab_config.json.",
            )
        self.setWindowTitle("FlowGrab — Video Downloader (yt-dlp)")
        root = QVBoxLayout(self)

        self.app_config = load_config()
        self.telegram_worker: TelegramWorker | None = None

        self.youtube_tab = YoutubeTab(app_ref=QApplication.instance())
        self.tiktok_tab = TikTokTab(app_ref=QApplication.instance())
        self.frame_extractor_tab = FrameExtractorTab()
        self.transcription_tab = TranscriptionTab()
        self.serveur_tab = ServeurTab()
        self.settings_tab = SettingsTab(app_ref=self)

        tabs = QTabWidget()
        tabs.addTab(self.youtube_tab, "YouTube")
        tabs.addTab(self.tiktok_tab, "TikTok")
        tabs.addTab(self.transcription_tab, "Transcription")
        tabs.addTab(self.frame_extractor_tab, "Création Frame")
        tabs.addTab(ComingSoonTab("À venir 4"), "À venir 4")
        tabs.addTab(ComingSoonTab("À venir 5"), "À venir 5")
        tabs.addTab(ComingSoonTab("À venir 6"), "À venir 6")
        tabs.addTab(ComingSoonTab("À venir 7"), "À venir 7")
        tabs.addTab(ComingSoonTab("À venir 8"), "À venir 8")
        tabs.addTab(self.settings_tab, "Paramètres généraux")
        tabs.addTab(self.serveur_tab, "Serveur")
        self.tabs = tabs
        root.addWidget(tabs)

        self.transcription_tab.init_from_config(self.app_config)
        self.settings_tab.init_from_config(self.app_config)

        self.serveur_tab.sig_public_url.connect(self.on_cloudflare_public_url)
        self.youtube_tab.sig_request_transcription.connect(self.on_transcription_request)
        self.youtube_tab.sig_audio_completed.connect(self.on_audio_ready_from_youtube)
        self.tiktok_tab.sig_request_transcription.connect(self.on_transcription_request)
        self.tiktok_tab.sig_audio_completed.connect(self.on_audio_ready_from_youtube)
        self.transcription_tab.sig_url_changed.connect(self.on_transcription_url_changed)

        self.settings_tab.btn_tg_start.clicked.connect(self.start_telegram)
        self.settings_tab.btn_tg_stop.clicked.connect(self.stop_telegram)

        start_notification_server(self)

    def on_cloudflare_public_url(self, base: str) -> None:
        path = self.app_config.get("webhook_path") or "/webhook/Audio"
        if not path.startswith("/"):
            path = "/" + path
        full = base.rstrip("/") + path
        self.app_config.update({"webhook_base": base, "webhook_full": full, "webhook_path": path})
        save_config(self.app_config)
        self.transcription_tab.set_webhook_full(full)

    def on_transcription_request(self, file_paths: list[str]) -> None:
        self.tabs.setCurrentWidget(self.transcription_tab)
        self.transcription_tab.send_files_immediately(file_paths)

    def on_transcription_url_changed(self, text: str) -> None:
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
        self.app_config.update(
            {
                "webhook_base": base.rstrip("/"),
                "webhook_full": text,
                "webhook_path": path,
            }
        )
        save_config(self.app_config)

    def _effective_telegram_mode(self) -> str:
        return "polling"

    def start_telegram(self) -> None:
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

    def stop_telegram(self) -> None:
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

    def on_telegram_finished(self) -> None:
        if self.settings_tab:
            self.settings_tab.set_telegram_idle()
        if self.telegram_worker and not self.telegram_worker.isRunning():
            self.telegram_worker = None

    def on_telegram_info(self, text: str) -> None:
        if self.settings_tab:
            self.settings_tab.append_telegram_info(text)

    def on_tg_download_requested(self, url: str, fmt: str, chat_id: int | str, title: str) -> None:
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

    def on_audio_ready_from_youtube(self, chat_id: int | str, audio_path: str) -> None:
        if not self.telegram_worker:
            return
        try:
            chat_ref: int | str = int(chat_id)
        except (TypeError, ValueError):
            chat_ref = chat_id
        name = os.path.basename(audio_path) or audio_path
        self.telegram_worker.send_message(chat_ref, f"Téléchargement terminé ✅\n{name}")
        self.telegram_worker.ask_transcription(chat_ref, audio_path)

    def closeEvent(self, event) -> None:
        try:
            self.stop_telegram()
        finally:
            super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    w = Main()
    w.show()
    sys.exit(app.exec())
