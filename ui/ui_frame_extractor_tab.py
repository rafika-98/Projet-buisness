from __future__ import annotations

import os
import pathlib
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from modules.module_frame_extractor import (
    FrameExtractionOptions,
    FrameExtractionWorker,
)


def _open_dir(path: pathlib.Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except AttributeError:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    except Exception:
        pass


class FrameExtractorTab(QWidget):
    sig_extraction_done = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self.worker: Optional[FrameExtractionWorker] = None
        self._last_video_dir: Optional[str] = None
        self._last_output_dir: Optional[str] = None
        self._current_output_dir: Optional[pathlib.Path] = None

        self.build_ui()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        video_box = QGroupBox("Vidéo source")
        video_layout = QVBoxLayout(video_box)
        video_layout.setContentsMargins(12, 12, 12, 12)
        video_layout.setSpacing(6)

        row_video = QHBoxLayout()
        row_video.setSpacing(8)
        self.edit_video = QLineEdit()
        self.edit_video.setPlaceholderText("Sélectionne une vidéo…")
        self.edit_video.textChanged.connect(self.on_video_changed)
        btn_pick_video = QPushButton("Parcourir…")
        btn_pick_video.clicked.connect(self.on_pick_video)
        row_video.addWidget(self.edit_video, 1)
        row_video.addWidget(btn_pick_video)
        video_layout.addLayout(row_video)

        self.lab_video_info = QLabel("Aucune vidéo sélectionnée.")
        self.lab_video_info.setWordWrap(True)
        video_layout.addWidget(self.lab_video_info)

        root.addWidget(video_box)

        output_box = QGroupBox("Dossier de sortie")
        output_layout = QHBoxLayout(output_box)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(8)
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText("Ex: C:/…/frames")
        self.edit_output.textChanged.connect(self.on_output_changed)
        btn_pick_output = QPushButton("Choisir…")
        btn_pick_output.clicked.connect(self.on_pick_output)
        output_layout.addWidget(self.edit_output, 1)
        output_layout.addWidget(btn_pick_output)
        root.addWidget(output_box)

        options_box = QGroupBox("Options")
        options_layout = QFormLayout(options_box)
        options_layout.setContentsMargins(12, 12, 12, 12)
        options_layout.setSpacing(6)

        self.edit_prefix = QLineEdit("frame_")
        options_layout.addRow("Préfixe des fichiers", self.edit_prefix)

        self.cmb_format = QComboBox()
        self.cmb_format.addItem("JPG (JPEG)", "jpg")
        self.cmb_format.addItem("PNG", "png")
        options_layout.addRow("Format", self.cmb_format)

        self.spin_step = QSpinBox()
        self.spin_step.setRange(1, 500)
        self.spin_step.setValue(1)
        self.spin_step.setSuffix(" image(s)")
        options_layout.addRow("Garder 1 image toutes les", self.spin_step)

        time_row = QHBoxLayout()
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(0.0, 100000.0)
        self.spin_start.setDecimals(2)
        self.spin_start.setSuffix(" s")
        self.spin_start.valueChanged.connect(self.on_time_changed)
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setRange(0.0, 100000.0)
        self.spin_end.setDecimals(2)
        self.spin_end.setSuffix(" s")
        self.spin_end.valueChanged.connect(self.on_time_changed)
        time_row.addWidget(QLabel("Début"))
        time_row.addWidget(self.spin_start)
        time_row.addWidget(QLabel("Fin"))
        time_row.addWidget(self.spin_end)
        options_layout.addRow("Fenêtre temporelle", time_row)

        self.chk_resize = QCheckBox("Redimensionner")
        self.chk_resize.stateChanged.connect(self.on_resize_toggled)
        resize_row = QHBoxLayout()
        self.spin_width = QSpinBox()
        self.spin_width.setRange(16, 4096)
        self.spin_width.setValue(1920)
        self.spin_width.setEnabled(False)
        self.spin_height = QSpinBox()
        self.spin_height.setRange(16, 4096)
        self.spin_height.setValue(1080)
        self.spin_height.setEnabled(False)
        resize_row.addWidget(self.spin_width)
        resize_row.addWidget(QLabel("×"))
        resize_row.addWidget(self.spin_height)
        resize_row.addStretch(1)
        resize_widget = QWidget()
        resize_widget.setLayout(resize_row)
        options_layout.addRow(self.chk_resize, resize_widget)

        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(10, 100)
        self.spin_quality.setValue(95)
        self.spin_quality.setSuffix(" %")
        options_layout.addRow("Qualité (JPG)", self.spin_quality)

        self.spin_preview = QSpinBox()
        self.spin_preview.setRange(1, 50)
        self.spin_preview.setValue(5)
        self.spin_preview.setSuffix(" image(s)")
        options_layout.addRow("Aperçu toutes les", self.spin_preview)

        root.addWidget(options_box)

        progress_box = QGroupBox("Progression")
        progress_layout = QVBoxLayout(progress_box)
        progress_layout.setContentsMargins(12, 12, 12, 12)
        progress_layout.setSpacing(6)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.lab_progress = QLabel("En attente…")
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.lab_progress)

        self.preview = QLabel("Aucun aperçu disponible")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(200)
        self.preview.setStyleSheet("border: 1px solid #444; background: #111; color: #888;")
        progress_layout.addWidget(self.preview)

        root.addWidget(progress_box)

        logs_box = QGroupBox("Journal")
        logs_layout = QVBoxLayout(logs_box)
        logs_layout.setContentsMargins(12, 12, 12, 12)
        logs_layout.setSpacing(6)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        logs_layout.addWidget(self.logs)
        root.addWidget(logs_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_start = QPushButton("Lancer l'extraction")
        self.btn_start.clicked.connect(self.start_extraction)
        self.btn_stop = QPushButton("Arrêter")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_extraction)
        self.btn_open_output = QPushButton("Ouvrir le dossier")
        self.btn_open_output.clicked.connect(self.open_output_dir)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_open_output)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        root.addStretch(1)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        elif event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths: list[str] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile() or url.toString()
                if local:
                    paths.append(local)
        elif event.mimeData().hasText():
            paths.extend(line.strip() for line in event.mimeData().text().splitlines() if line.strip())
        if paths:
            self.edit_video.setText(paths[0])
        event.acceptProposedAction()

    def on_video_changed(self, text: str) -> None:
        path = pathlib.Path(text).expanduser()
        if path.exists():
            self._last_video_dir = str(path.parent)
            default_output = path.parent / f"{path.stem}_frames"
            if not self.edit_output.text().strip():
                self.edit_output.setText(str(default_output))
            self.update_video_info(path)
        else:
            self.lab_video_info.setText("Aucune vidéo sélectionnée.")

    def update_video_info(self, path: pathlib.Path) -> None:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.lab_video_info.setText("Impossible de lire la vidéo.")
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = total_frames / fps if fps > 0 else 0.0
        cap.release()
        info = [f"Résolution : {width}×{height}" if width and height else "Résolution inconnue"]
        if fps > 0:
            info.append(f"FPS : {fps:.2f}")
        else:
            info.append("FPS inconnu")
        if duration > 0:
            minutes, seconds = divmod(duration, 60)
            info.append(f"Durée : {int(minutes)}m{int(seconds):02d}s")
        else:
            info.append("Durée inconnue")
        self.lab_video_info.setText(" | ".join(info))

    def on_output_changed(self, text: str) -> None:
        if text.strip():
            self._last_output_dir = text.strip()
        self._current_output_dir = pathlib.Path(text).expanduser()

    def on_pick_video(self) -> None:
        start_dir = self._last_video_dir or str(pathlib.Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Vidéo", start_dir, "Vidéos (*.mp4 *.mov *.mkv *.avi *.webm)")
        if path:
            self.edit_video.setText(path)

    def on_pick_output(self) -> None:
        start_dir = self._last_output_dir or (self._last_video_dir or str(pathlib.Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Dossier de sortie", start_dir)
        if path:
            self.edit_output.setText(path)

    def on_time_changed(self) -> None:
        if self.spin_end.value() > 0 and self.spin_start.value() > self.spin_end.value():
            self.spin_start.setValue(self.spin_end.value())

    def on_resize_toggled(self) -> None:
        enabled = self.chk_resize.isChecked()
        self.spin_width.setEnabled(enabled)
        self.spin_height.setEnabled(enabled)

    def _current_format(self) -> str:
        data = self.cmb_format.currentData()
        return str(data or "jpg")

    @Slot()
    def start_extraction(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Extraction", "Une extraction est déjà en cours.")
            return

        video_path = pathlib.Path(self.edit_video.text().strip()).expanduser()
        if not video_path.exists():
            QMessageBox.warning(self, "Vidéo manquante", "Sélectionne une vidéo valide.")
            return

        output_dir_text = self.edit_output.text().strip()
        if not output_dir_text:
            QMessageBox.warning(self, "Dossier manquant", "Sélectionne un dossier de sortie.")
            return

        output_dir = pathlib.Path(output_dir_text).expanduser()
        prefix = self.edit_prefix.text().strip() or "frame_"
        every_n = self.spin_step.value()
        start_time = self.spin_start.value()
        end_time = self.spin_end.value() or None
        resize_width = self.spin_width.value() if self.chk_resize.isChecked() else None
        resize_height = self.spin_height.value() if self.chk_resize.isChecked() else None
        if self.chk_resize.isChecked() and (not resize_width or not resize_height):
            QMessageBox.warning(self, "Dimensions", "Renseigne largeur et hauteur pour le redimensionnement.")
            return

        options = FrameExtractionOptions(
            video_path=str(video_path),
            output_dir=str(output_dir),
            prefix=prefix,
            image_format=self._current_format(),
            every_n=every_n,
            start_time=start_time,
            end_time=end_time,
            resize_width=resize_width,
            resize_height=resize_height,
            jpeg_quality=self.spin_quality.value(),
            preview_every=self.spin_preview.value(),
        )

        self.logs.clear()
        self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.lab_progress.setText("Préparation…")
        self.preview.setText("Aucun aperçu disponible")
        self.preview.setPixmap(QPixmap())

        worker = FrameExtractionWorker(options, self)
        self.worker = worker
        worker.sig_started.connect(self.on_worker_started)
        worker.sig_progress.connect(self.on_worker_progress)
        worker.sig_log.connect(self.logs.append)
        worker.sig_finished.connect(self.on_worker_finished)
        worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._current_output_dir = output_dir

    @Slot()
    def stop_extraction(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.request_abort()
            self.btn_stop.setEnabled(False)

    @Slot(int, float)
    def on_worker_started(self, total: int, duration: float) -> None:
        if total > 0:
            self.progress.setRange(0, total)
        else:
            self.progress.setRange(0, 0)
        if duration > 0:
            minutes, seconds = divmod(duration, 60)
            self.logs.append(f"Durée détectée : {int(minutes)}m{int(seconds):02d}s")
        else:
            self.logs.append("Durée vidéo inconnue (FPS non détecté)")

    @Slot(int, int, float, str, object)
    def on_worker_progress(self, saved: int, total: int, position: float, path: str, preview_frame: object) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(saved)
        else:
            self.progress.setRange(0, 0)
        self.lab_progress.setText(f"Images enregistrées : {saved} / {total or '?'} — {position:.2f}s")
        self.logs.append(f"✔ {path}")
        if isinstance(preview_frame, np.ndarray):
            self.update_preview(preview_frame)

    @Slot(bool, int, str)
    def on_worker_finished(self, ok: bool, saved: int, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if ok:
            self.lab_progress.setText(f"Terminé — {saved} image(s).")
            if saved:
                self.logs.append("Extraction terminée avec succès.")
            else:
                self.logs.append("Aucune image extraite. Vérifie les paramètres (intervalle, fenêtre temporelle…).")
            if self._current_output_dir:
                self.sig_extraction_done.emit(str(self._current_output_dir))
        else:
            if not message:
                message = "Extraction interrompue."
            self.lab_progress.setText(message)
            self.logs.append(message)
        self.worker = None

    def update_preview(self, frame: np.ndarray) -> None:
        if frame.size == 0:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        image = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.preview.width(),
            self.preview.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview.setPixmap(scaled)
        self.preview.setText("")

    def open_output_dir(self) -> None:
        if not self._current_output_dir:
            QMessageBox.information(self, "Dossier", "Lance une extraction pour définir un dossier de sortie.")
            return
        _open_dir(self._current_output_dir)


__all__ = ["FrameExtractorTab"]
