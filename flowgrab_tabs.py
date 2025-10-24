import os, subprocess, shutil, sys, pathlib

OUT_DIR = pathlib.Path(r"C:\Users\Lamine\Desktop\Projet final\Application\downloads")
OUT_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_ARCHIVE = OUT_DIR / "archive.txt"
from dataclasses import dataclass
from typing import Optional, List, Dict

from PySide6.QtCore import Qt, QThread, Signal, Slot, QUrl, QTimer
from PySide6.QtGui import QAction, QPalette, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QLabel, QComboBox,
    QProgressBar, QMessageBox, QGroupBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QAbstractItemView
)
from PySide6.QtWidgets import QTextEdit
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
                fn = d.get("filename", "")
                self.sig_status.emit(f"Terminé : {fn}")

        opts = dict(self.ydl_opts)
        opts["progress_hooks"] = [hook]

        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.task.url, download=True)
                fn = ydl.prepare_filename(info)
            self.sig_done.emit(True, fn, info or {})
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
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "retries": 2,
                "socket_timeout": 15,
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
            if info.get("entries"):
                info = info["entries"][0]
            self.sig_done.emit(self.url, info or {})
        except Exception as e:
            self.sig_error.emit(self.url, str(e))


# ---------------------- Thèmes ----------------------
def apply_dark_theme(app: QApplication):
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

# ---------------------- Onglet YouTube ----------------------
class YoutubeTab(QWidget):
    def __init__(self, app_ref, parent=None):
        super().__init__(parent)
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

    def build_ui(self):
        root = QVBoxLayout(self)

        # ----- URLs + Inspecteur -----
        urls_box = QGroupBox("URLs")
        urls_layout = QVBoxLayout(urls_box)

        add_line = QHBoxLayout()
        self.edit_url = QLineEdit()
        self.edit_url.setPlaceholderText("Colle une URL YouTube/playlist et presse Entrée pour l’ajouter")
        self.edit_url.returnPressed.connect(self.add_url)
        btn_add   = QPushButton("Ajouter");        btn_add.clicked.connect(self.add_url)
        btn_file  = QPushButton("Depuis .txt");    btn_file.clicked.connect(self.add_from_file)
        btn_clear_urls = QPushButton("Vider la liste"); btn_clear_urls.clicked.connect(self.clear_url_list)
        btn_open  = QPushButton("Ouvrir le dossier");   btn_open.clicked.connect(self.open_output_dir)
        add_line.addWidget(self.edit_url)
        add_line.addWidget(btn_add)
        add_line.addWidget(btn_file)
        add_line.addWidget(btn_clear_urls)
        add_line.addWidget(btn_open)
        urls_layout.addLayout(add_line)

        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.ActionsContextMenu)
        act_del = QAction("Supprimer la sélection", self); act_del.triggered.connect(self.delete_selected)
        self.list.addAction(act_del)
        self.list.currentItemChanged.connect(self.on_current_item_changed)
        urls_layout.addWidget(self.list)

        # Tableau formats
        self.tbl = QTableWidget(0, 10)
        self.tbl.setHorizontalHeaderLabels([
            "✔","ID video","Résolution","FPS","Ext/VC","Poids vidéo",
            "ID audio","Audio","Poids audio","Total estimé"
        ])
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.horizontalHeader().setStretchLastSection(True)
        self.tbl.itemDoubleClicked.connect(self.on_format_double_click)
        urls_layout.addWidget(self.tbl)

        # ----- Contrôles -----
        ctrl = QHBoxLayout()
        self.btn_start = QPushButton("Démarrer"); self.btn_start.clicked.connect(self.start_queue)
        self.btn_stop  = QPushButton("Stop"); self.btn_stop.clicked.connect(self.stop_current)
        ctrl.addWidget(self.btn_start); ctrl.addWidget(self.btn_stop)
        urls_layout.addLayout(ctrl)
        root.addWidget(urls_box)

        # ----- Statuts -----
        stat_line = QHBoxLayout()
        self.lab_name  = QLabel("Fichier : —")
        self.lab_speed = QLabel("Vitesse : —")
        self.lab_size  = QLabel("Taille : —")
        self.lab_eta   = QLabel("ETA : —")
        stat_line.addWidget(self.lab_name, 3)
        stat_line.addWidget(self.lab_speed, 1)
        stat_line.addWidget(self.lab_size, 1)
        stat_line.addWidget(self.lab_eta, 1)
        root.addLayout(stat_line)

        self.bar = QProgressBar(); self.bar.setRange(0, 100); self.bar.setValue(0)
        root.addWidget(self.bar)

        self.setMinimumWidth(1080)

    def open_output_dir(self):
        path = OUT_DIR
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Impossible d’ouvrir le dossier : {e}")

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
        if QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
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
        self.statusBar("Formats prêts")
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.btn_start.setEnabled(True)
        self.inspect_worker = None


    def on_inspect_error(self, seq: int, item: QListWidgetItem, url: str, msg: str):
        # ignorer si une requête plus récente a été lancée
        if seq != self.inspect_seq:
            return
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.btn_start.setEnabled(True)
        self.statusBar("Échec de l’analyse")
        QMessageBox.warning(self, "Erreur", f"Impossible d’inspecter : {msg}")
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
            "noplaylist": False,
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
            self.cleanup_residuals(task)
        else:
            task.status = "Erreur"
            item.setText(f"[Erreur] {task.url}")
            QMessageBox.warning(self, "Erreur", f"Échec du téléchargement :\n{msg}")
        self.bar.setValue(0)
        self.current_worker = None
        self.btn_start.setEnabled(True)
        QThread.msleep(200)
        self.start_queue()

    def statusBar(self, text: str):
        self.window().setWindowTitle(f"FlowGrab — {text}")

    def cleanup_residuals(self, task: Task):
        """
        Supprime les fichiers intermédiaires liés au même ID dans le **sous-dossier** du titre :
          - flux bruts (.webm, .m4a, etc.)
          - .fNNN.mp4 (vidéo intermédiaire)
        Conserve:
          - Titre [ID].mp4
          - Titre [ID].mp3
        """
        if not task.video_id:
            return

        subdir = OUT_DIR / f"{pathlib.Path(task.filename).parent.name}"
        if not subdir.exists():
            subdir = OUT_DIR

        token = f"[{task.video_id}]"
        for p in subdir.iterdir():
            try:
                if not p.is_file() or token not in p.name:
                    continue
                ext = p.suffix.lower()
                if ext == ".mp3":
                    continue
                if ext == ".mp4":
                    if ".f" in p.stem:
                        p.unlink()
                    continue
                p.unlink()
            except Exception:
                pass

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
    - Bouton 'Mettre à jour l’app' -> git pull origin main
    - Bouton 'Redémarrer l’app'    -> relance le process et quitte l’instance actuelle
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: CommandWorker | None = None
        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)

        theme_line = QHBoxLayout()
        theme_label = QLabel("Thème")
        self.cmb_theme = QComboBox()
        self.cmb_theme.addItems(["Clair", "Sombre"])
        self.cmb_theme.currentIndexChanged.connect(self.on_theme_change)
        theme_line.addWidget(theme_label)
        theme_line.addWidget(self.cmb_theme)
        theme_line.addStretch(1)
        root.addLayout(theme_line)

        # Ligne boutons
        line = QHBoxLayout()
        self.btn_update = QPushButton("Mettre à jour l’app (git pull origin main)")
        self.btn_restart = QPushButton("Redémarrer l’app")
        self.btn_update.clicked.connect(self.on_update_clicked)
        self.btn_restart.clicked.connect(self.on_restart_clicked)
        line.addWidget(self.btn_update)
        line.addWidget(self.btn_restart)
        root.addLayout(line)

        # Zone de logs
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Logs des opérations (git, etc.)...")
        root.addWidget(self.logs)

        # Info
        info = QLabel("Astuce : l’app cherchera la racine du dépôt (.git) en remontant depuis le dossier du script.")
        info.setWordWrap(True)
        root.addWidget(info)

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
        git_exe = shutil.which("git")
        if not git_exe:
            QMessageBox.warning(self, "Git introuvable", "Impossible de trouver 'git' dans le PATH.")
            return

        repo_root = self.find_git_root()
        if not repo_root:
            QMessageBox.warning(self, "Hors dépôt Git", "Aucun dossier '.git' trouvé en remontant depuis ce projet.")
            return

        self.append_log(f">>> cwd: {repo_root}")
        self.append_log(">>> git pull origin main")
        self.btn_update.setEnabled(False)

        self.worker = CommandWorker([git_exe, "pull", "origin", "main"], cwd=repo_root)
        self.worker.sig_line.connect(self.append_log)
        self.worker.sig_done.connect(self.on_update_done)
        self.worker.start()

    def on_update_done(self, code: int):
        self.append_log(f">>> Terminé (code retour = {code})")
        self.btn_update.setEnabled(True)
        if code != 0:
            QMessageBox.warning(self, "Échec mise à jour", "La commande git s'est terminée avec une erreur.\nConsulte les logs.")
        else:
            QMessageBox.information(self, "Mise à jour OK", "Pull terminé. Clique sur 'Redémarrer l’app' pour prendre en compte les changements.")

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
        tabs = QTabWidget()
        tabs.addTab(YoutubeTab(app_ref=QApplication.instance()), "YouTube")
        tabs.addTab(ComingSoonTab("À venir 2"), "À venir 2")
        tabs.addTab(ComingSoonTab("À venir 3"), "À venir 3")
        tabs.addTab(ComingSoonTab("À venir 4"), "À venir 4")
        tabs.addTab(SettingsTab(), "Paramètres généraux")
        root.addWidget(tabs)

# ---------------------- main ----------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_light_theme(app)  # par défaut clair ; change dans l'UI
    w = Main()
    w.show()
    sys.exit(app.exec())
