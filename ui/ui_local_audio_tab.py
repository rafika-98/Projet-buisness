from __future__ import annotations

import pathlib
from typing import Optional

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.download_core import human_size
from modules.module_local_audio import (
    DEFAULT_BITRATE,
    DEFAULT_VIDEO_EXTS,
    OUTPUT_DIR,
    convert_to_mp3,
    ensure_output_dir,
    ffmpeg_exists,
    is_supported_video,
)


class AudioConvertWorker(QThread):
    sig_done = Signal(str)
    sig_error = Signal(str)

    def __init__(
        self,
        input_path: pathlib.Path,
        bitrate: str,
        output_dir: pathlib.Path,
        ffmpeg_bin: str = "ffmpeg",
        overwrite: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.input_path = input_path
        self.bitrate = bitrate
        self.output_dir = output_dir
        self.ffmpeg_bin = ffmpeg_bin
        self.overwrite = overwrite

    def run(self) -> None:
        try:
            output = convert_to_mp3(
                self.input_path,
                bitrate=self.bitrate,
                ffmpeg_bin=self.ffmpeg_bin,
                output_dir=self.output_dir,
                overwrite=self.overwrite,
            )
        except Exception as exc:  # pragma: no cover - worker errors bubbled to UI
            self.sig_error.emit(str(exc))
            return
        self.sig_done.emit(str(output))


class LocalAudioTab(QWidget):
    sig_audio_ready = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

        self._output_dir = ensure_output_dir(OUTPUT_DIR)
        self._ffmpeg_bin = "ffmpeg"
        self._worker: Optional[AudioConvertWorker] = None

        self.build_ui()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        info = QLabel(
            "Sélectionne une vidéo locale et convertis-la en audio MP3.\n"
            "Le fichier sera enregistré dans le dossier ci-dessous."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        video_box = QGroupBox("Vidéo locale")
        video_layout = QVBoxLayout(video_box)
        video_layout.setContentsMargins(12, 12, 12, 12)
        video_layout.setSpacing(8)

        row_video = QHBoxLayout()
        row_video.setSpacing(8)
        self.edit_video = QLineEdit()
        self.edit_video.setPlaceholderText("Dépose une vidéo ou parcours…")
        row_video.addWidget(self.edit_video, 1)
        btn_pick = QPushButton("Parcourir…")
        btn_pick.clicked.connect(self.on_pick_video)
        row_video.addWidget(btn_pick)
        self.btn_pick = btn_pick
        video_layout.addLayout(row_video)

        self.lab_video_info = QLabel("Aucune vidéo sélectionnée.")
        self.lab_video_info.setWordWrap(True)
        video_layout.addWidget(self.lab_video_info)

        root.addWidget(video_box)

        options_box = QGroupBox("Options")
        options_layout = QHBoxLayout(options_box)
        options_layout.setContentsMargins(12, 12, 12, 12)
        options_layout.setSpacing(12)

        self.cmb_bitrate = QComboBox()
        self.cmb_bitrate.addItems(["128k", "160k", DEFAULT_BITRATE, "256k", "320k"])
        if DEFAULT_BITRATE in [self.cmb_bitrate.itemText(i) for i in range(self.cmb_bitrate.count())]:
            self.cmb_bitrate.setCurrentText(DEFAULT_BITRATE)
        options_layout.addWidget(QLabel("Débit audio"))
        options_layout.addWidget(self.cmb_bitrate)

        self.chk_overwrite = QCheckBox("Écraser si le MP3 existe déjà")
        self.chk_overwrite.setChecked(True)
        options_layout.addWidget(self.chk_overwrite)
        options_layout.addStretch(1)

        root.addWidget(options_box)

        output_box = QGroupBox("Dossier de sortie")
        output_layout = QHBoxLayout(output_box)
        output_layout.setContentsMargins(12, 12, 12, 12)
        output_layout.setSpacing(8)
        self.edit_output = QLineEdit(str(self._output_dir))
        self.edit_output.setReadOnly(True)
        output_layout.addWidget(self.edit_output, 1)

        btn_output = QPushButton("Changer…")
        btn_output.clicked.connect(self.on_change_output)
        output_layout.addWidget(btn_output)

        btn_open_output = QPushButton("Ouvrir")
        btn_open_output.clicked.connect(self.on_open_output)
        output_layout.addWidget(btn_open_output)

        root.addWidget(output_box)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_convert = QPushButton("Extraire l’audio en MP3")
        self.btn_convert.clicked.connect(self.on_convert_clicked)
        actions.addWidget(self.btn_convert)
        actions.addStretch(1)
        root.addLayout(actions)

        self.lab_status = QLabel("Prêt.")
        self.lab_status.setWordWrap(True)
        root.addWidget(self.lab_status)

        helper = QLabel(
            "Astuce : tu peux aussi glisser-déposer un fichier vidéo dans cet onglet."
        )
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #aaaaaa;")
        root.addWidget(helper)
        root.addStretch(1)

    # Drag & drop support
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # pragma: no cover - UI behavior
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # pragma: no cover - UI behavior
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.set_video_file(pathlib.Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
        event.ignore()

    def on_pick_video(self) -> None:
        filters = "Vidéos (" + " ".join(f"*{ext}" for ext in DEFAULT_VIDEO_EXTS) + ")"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir une vidéo",
            str(pathlib.Path.home()),
            f"{filters};;Tous les fichiers (*.*)",
        )
        if file_path:
            self.set_video_file(pathlib.Path(file_path))

    def set_video_file(self, path: pathlib.Path) -> None:
        self.edit_video.setText(str(path))
        if path.exists():
            size = human_size(path.stat().st_size)
            self.lab_video_info.setText(f"Fichier sélectionné : {path.name} ({size})")
        else:
            self.lab_video_info.setText("Fichier sélectionné introuvable.")

    def on_change_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier de sortie",
            str(self._output_dir),
        )
        if directory:
            self._output_dir = ensure_output_dir(pathlib.Path(directory))
            self.edit_output.setText(str(self._output_dir))

    def on_open_output(self) -> None:
        ensure_output_dir(self._output_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def on_convert_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Conversion en cours", "Une conversion est déjà en cours.")
            return

        file_text = self.edit_video.text().strip()
        if not file_text:
            QMessageBox.warning(self, "Aucune vidéo", "Sélectionne d’abord une vidéo à convertir.")
            return

        input_path = pathlib.Path(file_text)
        if not input_path.exists():
            QMessageBox.warning(self, "Fichier introuvable", f"Le fichier {input_path} est introuvable.")
            return

        if not is_supported_video(input_path):
            answer = QMessageBox.question(
                self,
                "Extension inconnue",
                (
                    "Cette extension n'est pas reconnue comme vidéo.\n"
                    "Veux-tu tenter la conversion quand même ?"
                ),
            )
            if answer != QMessageBox.Yes:
                return

        if not ffmpeg_exists(self._ffmpeg_bin):
            QMessageBox.critical(
                self,
                "ffmpeg manquant",
                (
                    "ffmpeg n'est pas disponible.\n"
                    "Installe-le et ajoute-le au PATH avant de relancer la conversion."
                ),
            )
            return

        bitrate = self.cmb_bitrate.currentText() or DEFAULT_BITRATE
        overwrite = self.chk_overwrite.isChecked()

        self._worker = AudioConvertWorker(
            input_path=input_path,
            bitrate=bitrate,
            output_dir=self._output_dir,
            ffmpeg_bin=self._ffmpeg_bin,
            overwrite=overwrite,
        )
        self._worker.sig_done.connect(self.on_worker_done)
        self._worker.sig_error.connect(self.on_worker_error)
        self._worker.finished.connect(self.on_worker_finished)

        self._set_busy(True)
        self.lab_status.setText("Conversion en cours…")
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.btn_convert.setEnabled(not busy)
        self.btn_pick.setEnabled(not busy)
        self.edit_video.setEnabled(not busy)

    def on_worker_done(self, output_file: str) -> None:
        self.lab_status.setText(f"✅ Audio exporté : {output_file}")
        QMessageBox.information(
            self,
            "Conversion terminée",
            f"Audio exporté avec succès :\n{output_file}",
        )
        self.sig_audio_ready.emit(output_file)

    def on_worker_error(self, message: str) -> None:
        self.lab_status.setText(f"❌ Erreur : {message}")
        QMessageBox.critical(self, "Erreur", message)

    def on_worker_finished(self) -> None:
        self._set_busy(False)
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
