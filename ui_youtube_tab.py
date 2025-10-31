import os
import pathlib
import re
import shutil
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config import OUT_DIR
from download_core import (
    Task,
    DownloadWorker,
    cleanup_orphans_in_outputs,
    ensure_audio,
    estimate_size,
    extract_basic_info,
    human_eta,
    human_rate,
    human_size,
    list_video_formats,
    move_final_outputs,
    pick_best_audio,
)
from module_tiktok import TIKTOK_REGEX, build_download_options as build_tiktok_options
from module_youtube import YOUTUBE_REGEX, build_download_options as build_youtube_options
from paths import delete_dir_if_empty, get_video_dir


class InspectWorker(QThread):
    sig_done = Signal(str, dict)
    sig_error = Signal(str, str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        try:
            info = extract_basic_info(self.url)
            self.sig_done.emit(self.url, info or {})
        except Exception as exc:
            self.sig_error.emit(self.url, str(exc))


def themed_icon(*names: str) -> QIcon:
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


def _is_list_item_valid(item: Optional[QListWidgetItem]) -> bool:
    try:
        from shiboken6 import isValid as _is_valid
    except Exception:
        def _is_valid(obj):  # type: ignore
            return obj is not None
    try:
        return bool(item) and _is_valid(item)
    except Exception:
        return False


class YoutubeTab(QWidget):
    sig_request_transcription = Signal(list)
    sig_audio_completed = Signal(object, str)

    def __init__(self, app_ref, parent=None, platform: str = "youtube"):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.app_ref = app_ref
        self.platform = (platform or "youtube").lower()
        self.queue: List[Task] = []
        self.current_worker: Optional[DownloadWorker] = None
        self.last_inspect_info: Dict[str, Any] = {}
        self.inspect_worker: Optional[InspectWorker] = None
        self.inspect_seq = 0
        self.inspect_debounce = QTimer(self)
        self.inspect_debounce.setSingleShot(True)
        self.inspect_debounce.setInterval(250)
        self.inspect_debounce.timeout.connect(self._inspect_current_after_debounce)
        self.build_ui()

    def _cursor_wait(self, on: bool) -> None:
        if on and QApplication.overrideCursor() is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        elif not on:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass

    def _open_dir(self, path: pathlib.Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except AttributeError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible d’ouvrir le dossier : {exc}")

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

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
        add_line.addWidget(self.edit_url, 1)
        add_line.addWidget(btn_add)
        add_line.addWidget(btn_paste)
        add_line.addWidget(btn_file)
        add_line.addWidget(btn_clear_urls)
        urls_layout.addLayout(add_line)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.currentItemChanged.connect(self.on_current_item_changed)
        urls_layout.addWidget(self.list)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        btn_start = QPushButton("Démarrer la file")
        icon_start = themed_icon("media-playback-start", "system-run")
        if not icon_start.isNull():
            btn_start.setIcon(icon_start)
        btn_start.clicked.connect(self.start_queue)
        btn_stop = QPushButton("Stop")
        icon_stop = themed_icon("media-playback-stop", "process-stop")
        if not icon_stop.isNull():
            btn_stop.setIcon(icon_stop)
        btn_stop.clicked.connect(self.stop_current)
        btn_open = QPushButton("Ouvrir dossier")
        icon_folder = themed_icon("folder", "system-file-manager")
        if not icon_folder.isNull():
            btn_open.setIcon(icon_folder)
        btn_open.clicked.connect(self.open_output_dir)
        ctrl.addWidget(btn_start)
        ctrl.addWidget(btn_stop)
        ctrl.addWidget(btn_open)
        ctrl.addStretch(1)
        urls_layout.addLayout(ctrl)

        formats_box = QGroupBox("Formats disponibles")
        formats_layout = QVBoxLayout(formats_box)
        formats_layout.setContentsMargins(12, 12, 12, 12)
        formats_layout.setSpacing(8)

        self.tbl = QTableWidget(0, 10)
        self.tbl.setHorizontalHeaderLabels(
            [
                "✔",
                "ID",
                "Résolution",
                "FPS",
                "Codec vidéo",
                "Taille vidéo",
                "ID audio",
                "Codec audio",
                "Taille audio",
                "Total",
            ]
        )
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl.doubleClicked.connect(self.on_format_double_click)
        formats_layout.addWidget(self.tbl)

        urls_layout.addWidget(formats_box)
        root.addWidget(urls_box)

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

        self.btn_start = btn_start
        self.btn_stop = btn_stop
        self.btn_open = btn_open
        self.setMinimumWidth(1080)

    def open_output_dir(self) -> None:
        try:
            target = get_video_dir(self.platform)
        except Exception:
            target = OUT_DIR
        self._open_dir(target)

    def clear_url_list(self) -> None:
        self.queue.clear()
        self.list.clear()

    def append_task(self, url: str) -> QListWidgetItem:
        task = Task(url=url, platform=self.platform)
        self.queue.append(task)
        item = QListWidgetItem(f"[En attente] {url}")
        item.setData(Qt.UserRole, task)
        self.list.addItem(item)
        return item

    def find_item_for_task(self, task: Task) -> Optional[QListWidgetItem]:
        for idx in range(self.list.count()):
            candidate = self.list.item(idx)
            if candidate and candidate.data(Qt.UserRole) is task:
                return candidate
        return None

    def _ensure_task_item(self, item: Optional[QListWidgetItem], task: Task) -> Optional[QListWidgetItem]:
        if _is_list_item_valid(item):
            return item
        return self.find_item_for_task(task)

    def add_url(self) -> None:
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

    def paste_clipboard(self) -> None:
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

    def add_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Fichier .txt", "", "Text (*.txt)")
        if not path:
            return
        new_items: List[QListWidgetItem] = []
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            url = line.strip()
            if not url:
                continue
            exists = False
            for i in range(self.list.count()):
                exist_task: Task = self.list.item(i).data(Qt.UserRole)
                if exist_task and exist_task.url == url:
                    exists = True
                    break
            if exists:
                continue
            new_items.append(self.append_task(url))
        if new_items:
            item = new_items[0]
            self.list.setCurrentItem(item)
            self.inspect_task_async(item)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls: List[str] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
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
        if event.mimeData().hasText():
            urls.append(event.mimeData().text())
        for raw in urls:
            if not raw:
                continue
            match = YOUTUBE_REGEX.search(raw.strip())
            if match:
                self.edit_url.setText(match.group(1))
                self.add_url()
        event.acceptProposedAction()

    def delete_selected(self) -> None:
        for it in self.list.selectedItems():
            task: Task = it.data(Qt.UserRole)
            if task in self.queue:
                self.queue.remove(task)
            self.list.takeItem(self.list.row(it))

    def on_current_item_changed(self, current: QListWidgetItem, _previous: QListWidgetItem) -> None:
        self.inspect_debounce.start()

    def _inspect_current_after_debounce(self) -> None:
        item = self.list.currentItem()
        if item:
            self.inspect_task_async(item)

    def inspect_task_async(self, item: QListWidgetItem) -> None:
        task: Task = item.data(Qt.UserRole)
        if not task or not task.url:
            return

        self.tbl.setRowCount(0)
        self.statusBar("Analyse des formats…")
        self._cursor_wait(True)
        self.btn_start.setEnabled(False)

        self.inspect_seq += 1
        seq = self.inspect_seq

        if self.inspect_worker and self.inspect_worker.isRunning():
            pass

        worker = InspectWorker(task.url, self)
        self.inspect_worker = worker
        worker.sig_done.connect(lambda url, info, s=seq: self.on_inspect_done(s, item, url, info))
        worker.sig_error.connect(lambda url, msg, s=seq: self.on_inspect_error(s, item, url, msg))
        worker.start()

    def on_inspect_done(self, seq: int, item: QListWidgetItem, url: str, info: dict) -> None:
        if seq != self.inspect_seq:
            return

        self.last_inspect_info = info or {}
        formats = self.last_inspect_info.get("formats") or []
        duration = self.last_inspect_info.get("duration")

        videos = list_video_formats(formats, mp4_friendly=True)
        abest = pick_best_audio(formats, mp4_friendly=True)

        self.tbl.setRowCount(0)
        task: Task = item.data(Qt.UserRole)

        for vf in videos:
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
        dur_txt = human_eta(int(duration)) if duration else ""
        self.statusBar(f"Formats prêts — {title} ({dur_txt})")
        self._cursor_wait(False)
        self.btn_start.setEnabled(True)
        self.inspect_worker = None

    def on_inspect_error(self, seq: int, _item: QListWidgetItem, _url: str, msg: str) -> None:
        if seq != self.inspect_seq:
            return
        self._cursor_wait(False)
        self.btn_start.setEnabled(True)
        self.statusBar("Échec de l’analyse")
        QMessageBox.warning(self, "Erreur", f"Impossible d’inspecter : {msg}")
        if "429" in msg or "Too Many Requests" in msg:
            QMessageBox.warning(
                self,
                "Limite atteinte",
                "YouTube a limité l’inspection (429). Réessaie dans ~1 minute.",
            )
        self.inspect_worker = None

    def on_format_double_click(self, it: QTableWidgetItem) -> None:
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
            dot_item = self.tbl.item(r, 0)
            if dot_item is None:
                dot_item = QTableWidgetItem("")
                self.tbl.setItem(r, 0, dot_item)
            dot_item.setText("")
            dot_item.setTextAlignment(Qt.AlignCenter)
            dot_item.setForeground(QColor())

        ok = self.tbl.item(row, 0)
        if ok is None:
            ok = QTableWidgetItem("")
            self.tbl.setItem(row, 0, ok)
        ok.setText("●")
        ok.setTextAlignment(Qt.AlignCenter)
        ok.setForeground(QColor(0, 170, 0))

        self.statusBar(f"Format choisi : {chosen}")

    def build_opts(self, task: Task) -> dict:
        return build_youtube_options(task)

    def start_queue(self) -> None:
        if self.list.count() == 0 and self.edit_url.text().strip():
            self.add_url()

        if self.current_worker and self.current_worker.isRunning():
            QMessageBox.information(self, "Déjà en cours", "Un téléchargement est déjà en cours.")
            return

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            QMessageBox.warning(
                self,
                "FFmpeg manquant",
                "Installe FFmpeg avant de télécharger (ex: winget install Gyan.FFmpeg).",
            )
            return

        next_task = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            task: Task = it.data(Qt.UserRole)
            if task.status in ("En attente", "Erreur"):
                next_task = (i, it, task)
                break
        if not next_task:
            QMessageBox.information(self, "Info", "Aucune tâche en attente.")
            return

        _, item, task = next_task
        task.status = "En cours"
        safe_item = self._ensure_task_item(item, task)
        if _is_list_item_valid(safe_item):
            safe_item.setText(f"[En cours] {task.url}")

        opts = self.build_opts(task)
        self.current_worker = DownloadWorker(task, opts, self)
        self.current_worker.sig_progress.connect(
            lambda d, tot, sp, eta, fn: self.on_progress(safe_item, task, d, tot, sp, eta, fn)
        )
        self.current_worker.sig_status.connect(self.statusBar)
        self.current_worker.sig_done.connect(lambda ok, msg, info: self.on_done(safe_item, task, ok, msg, info))
        self.btn_start.setEnabled(False)
        self.current_worker.start()

    def stop_current(self) -> None:
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.stop()

    @Slot()
    def on_progress(
        self,
        item: QListWidgetItem,
        task: Task,
        downloaded: int,
        total: int,
        speed: float,
        eta: int,
        filename: str,
    ) -> None:
        task.downloaded, task.total, task.speed, task.eta = downloaded, total, speed, eta
        if filename:
            task.filename = filename
        pct = int(downloaded * 100 / total) if total else 0
        self.bar.setValue(pct)
        name = pathlib.Path(task.filename).name if task.filename else "—"
        self.lab_name.setText(f"Fichier : {name}")
        self.lab_speed.setText(f"Vitesse : {human_rate(speed)}")
        self.lab_size.setText(f"Taille : {human_size(downloaded)} / {human_size(total)}")
        self.lab_eta.setText(f"ETA : {human_eta(eta)}")
        safe_item = self._ensure_task_item(item, task)
        if _is_list_item_valid(safe_item):
            safe_item.setText(f"[{pct:>3}%] {task.url}")

    @Slot()
    def on_done(self, item: QListWidgetItem, task: Task, ok: bool, msg: str, info: dict) -> None:
        safe_item = self._ensure_task_item(item, task)
        if ok:
            task.status = "Terminé"
            if _is_list_item_valid(safe_item):
                safe_item.setText(f"[Terminé] {task.url}")
            self.statusBar(f"Terminé : {msg}")
            task.video_id = (info or {}).get("id")
            moved = move_final_outputs(task)
            self.cleanup_residuals(task)
            cleanup_orphans_in_outputs(task)
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
            if _is_list_item_valid(safe_item):
                safe_item.setText(f"[Erreur] {task.url}")
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

    def statusBar(self, text: str) -> None:
        window = self.window()
        if window:
            window.setWindowTitle(f"FlowGrab — {text}")

    def cleanup_residuals(self, task: Task) -> None:
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
                if ext == ".mp4":
                    if ".f" in p.stem:
                        p.unlink()
                    continue
                if ext == ".mp3":
                    continue
                p.unlink()
            except Exception:
                pass


class TikTokTab(YoutubeTab):
    def __init__(self, app_ref, parent=None):
        super().__init__(app_ref, parent, platform="tiktok")

    def build_ui(self) -> None:
        super().build_ui()
        self.edit_url.setPlaceholderText("Colle une URL TikTok et presse Entrée pour l’ajouter")

    def add_url(self) -> None:
        url = self.edit_url.text().strip()
        if not url:
            return
        match = TIKTOK_REGEX.search(url)
        if not match:
            QMessageBox.information(self, "URL invalide", "Cette URL ne semble pas être une URL TikTok.")
            return
        url = match.group(1)
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

    def paste_clipboard(self) -> None:
        cb = QApplication.clipboard()
        if not cb:
            return
        text = (cb.text() or "").strip()
        if not text:
            return
        match = TIKTOK_REGEX.search(text)
        if match:
            self.edit_url.setText(match.group(1))
            self.add_url()

    def dropEvent(self, event) -> None:
        urls: List[str] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile() or url.toString()
                if path:
                    urls.append(path)
        if event.mimeData().hasText():
            urls.append(event.mimeData().text())
        for raw in urls:
            if not raw:
                continue
            match = TIKTOK_REGEX.search(raw.strip())
            if match:
                self.edit_url.setText(match.group(1))
                self.add_url()
        event.acceptProposedAction()

    def build_opts(self, task: Task) -> dict:
        return build_tiktok_options(task)
