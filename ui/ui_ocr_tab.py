from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from modules.module_ocr import (
    DEFAULT_MODEL,
    call_mistral,
    encode_b64,
    load_config,
    save_config,
)


@dataclass
class PromptResult:
    index: int
    source: pathlib.Path
    prompt: str


class PromptWorker(QThread):
    sig_result = Signal(int, str, str)
    sig_error = Signal(int, str, str)
    sig_status = Signal(str)
    sig_done = Signal()

    def __init__(self, images: List[pathlib.Path], api_key: str, model: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.images = list(images)
        self.api_key = api_key
        self.model = model

    def run(self) -> None:  # pragma: no cover - thread behavior
        for idx, image_path in enumerate(self.images, start=1):
            self.sig_status.emit(f"Analyse {idx}/{len(self.images)}… {image_path.name}")
            try:
                b64 = encode_b64(image_path)
                data = call_mistral(self.api_key, self.model, b64)
                prompt = str(data.get("krea_prompt", "")).strip()
                self.sig_result.emit(idx, str(image_path), prompt)
            except Exception as exc:  # noqa: PERF203 - propagé au slot principal
                self.sig_error.emit(idx, str(image_path), str(exc))
        self.sig_done.emit()


class OcrTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._config = load_config()
        self._images: List[pathlib.Path] = []
        self._results: List[PromptResult] = []
        self._worker: Optional[PromptWorker] = None

        self.build_ui()
        self.load_settings()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_pick = QPushButton("Choisir images…")
        self.btn_pick.clicked.connect(self.on_pick_images)
        btn_row.addWidget(self.btn_pick)

        self.btn_run = QPushButton("Lancer")
        self.btn_run.clicked.connect(self.on_run)
        btn_row.addWidget(self.btn_run)

        self.btn_export = QPushButton("Exporter CSV…")
        self.btn_export.clicked.connect(self.on_export)
        btn_row.addWidget(self.btn_export)

        self.btn_clear = QPushButton("Vider")
        self.btn_clear.clicked.connect(self.clear_results)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)

        root.addLayout(btn_row)

        self.lab_selection = QLabel("Aucune image sélectionnée.")
        root.addWidget(self.lab_selection)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(6)
        self.results_layout.addStretch(1)
        self.scroll_area.setWidget(self.results_container)
        root.addWidget(self.scroll_area, 1)

        settings_box = QGroupBox("Paramètres API")
        settings_layout = QGridLayout(settings_box)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setHorizontalSpacing(8)
        settings_layout.setVerticalSpacing(6)

        settings_layout.addWidget(QLabel("MISTRAL_API_KEY"), 0, 0)
        self.edit_api = QLineEdit()
        self.edit_api.setEchoMode(QLineEdit.Password)
        settings_layout.addWidget(self.edit_api, 0, 1)

        settings_layout.addWidget(QLabel("Modèle vision"), 1, 0)
        self.edit_model = QLineEdit(DEFAULT_MODEL)
        settings_layout.addWidget(self.edit_model, 1, 1)

        self.btn_save = QPushButton("Enregistrer")
        self.btn_save.clicked.connect(self.on_save_settings)
        settings_layout.addWidget(self.btn_save, 2, 0, 1, 2)

        root.addWidget(settings_box)

        self.lab_status = QLabel("Prêt.")
        self.lab_status.setWordWrap(True)
        root.addWidget(self.lab_status)

    def load_settings(self) -> None:
        self.edit_api.setText(self._config.get("api_key", ""))
        self.edit_model.setText(self._config.get("model", DEFAULT_MODEL))

    def on_pick_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Sélectionnez des images",
            str(pathlib.Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp);;Tous les fichiers (*.*)",
        )
        if not files:
            return
        self._images = [pathlib.Path(f) for f in files]
        self.lab_selection.setText(f"{len(self._images)} image(s) sélectionnée(s).")
        self.set_status(f"{len(self._images)} image(s) prête(s).")

    def on_run(self) -> None:
        if not self._images:
            QMessageBox.warning(self, "Images", "Sélectionnez au moins une image.")
            return
        api_key = self.edit_api.text().strip() or self._config.get("api_key", "")
        model = self.edit_model.text().strip() or self._config.get("model", DEFAULT_MODEL)
        if not api_key:
            QMessageBox.warning(self, "API", "API Key manquante.")
            return
        self.save_runtime_config(api_key, model)
        self.clear_results()
        self.toggle_inputs(False)
        worker = PromptWorker(self._images, api_key, model, self)
        worker.sig_result.connect(self.on_worker_result)
        worker.sig_error.connect(self.on_worker_error)
        worker.sig_status.connect(self.set_status)
        worker.sig_done.connect(self.on_worker_done)
        self._worker = worker
        worker.start()

    def save_runtime_config(self, api_key: str, model: str) -> None:
        self._config = {"api_key": api_key, "model": model}
        save_config(self._config)

    def on_worker_result(self, index: int, source: str, prompt: str) -> None:
        path = pathlib.Path(source)
        result = PromptResult(index=index, source=path, prompt=prompt)
        self._results.append(result)
        self.add_prompt_row(result)

    def on_worker_error(self, index: int, source: str, message: str) -> None:
        result = PromptResult(index=index, source=pathlib.Path(source), prompt=f"[ERREUR] {message}")
        self._results.append(result)
        self.add_prompt_row(result)

    def on_worker_done(self) -> None:
        self.set_status("Terminé.")
        self.toggle_inputs(True)
        self._worker = None

    def add_prompt_row(self, result: PromptResult) -> None:
        count = self.results_layout.count()
        if count:
            last_item = self.results_layout.itemAt(count - 1)
            if last_item and last_item.spacerItem():
                item = self.results_layout.takeAt(count - 1)
                del item
        if len(self._results) > 1:
            separator = QFrame()
            separator.setFrameShape(QFrame.HLine)
            separator.setFrameShadow(QFrame.Sunken)
            self.results_layout.addWidget(separator)
        container = QWidget()
        row_layout = QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        label = QLabel(result.prompt or "(vide)")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        row_layout.addWidget(label, 1)

        btn_copy = QPushButton("Copy")
        btn_copy.setMaximumWidth(80)
        btn_copy.clicked.connect(
            lambda _, text=result.prompt, target=label: self.copy_to_clipboard(text, target)
        )
        row_layout.addWidget(btn_copy)

        self.results_layout.addWidget(container)
        self.results_layout.addStretch(1)

    def clear_results(self) -> None:
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if not item:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.spacerItem() is not None:
                # spacer items are automatically garbage collected
                pass
        self.results_layout.addStretch(1)
        self._results.clear()

    def on_export(self) -> None:
        if not self._results:
            QMessageBox.information(self, "Export", "Rien à exporter.")
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter prompts",
            "prompts.csv",
            "CSV (*.csv)",
        )
        if not target:
            return
        with open(target, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["source_image", "krea_prompt"])
            writer.writeheader()
            for result in self._results:
                writer.writerow({"source_image": str(result.source), "krea_prompt": result.prompt})
        self.set_status(f"CSV exporté → {target}")
        QMessageBox.information(self, "Export", f"Exporté : {target}")

    def on_save_settings(self) -> None:
        api_key = self.edit_api.text().strip()
        model = self.edit_model.text().strip() or DEFAULT_MODEL
        save_config({"api_key": api_key, "model": model})
        self._config = {"api_key": api_key, "model": model}
        self.set_status("Paramètres enregistrés.")
        QMessageBox.information(self, "Paramètres", "Paramètres enregistrés.")

    def copy_to_clipboard(self, text: str, target: Optional[QLabel] = None) -> None:
        app = QApplication.instance()
        clipboard = QApplication.clipboard() if app else None
        if clipboard:
            clipboard.setText(text or "")
            self.set_status("Prompt copié dans le presse-papier.")
            if target is not None:
                target.setStyleSheet("color: #2e7d32;")
        else:  # pragma: no cover - absence d'instance QApplication improbable
            self.set_status("Impossible de copier : presse-papier indisponible.")

    def toggle_inputs(self, enabled: bool) -> None:
        for widget in (self.btn_pick, self.btn_run, self.btn_export, self.btn_clear, self.btn_save):
            widget.setEnabled(enabled)

    def set_status(self, message: str) -> None:
        self.lab_status.setText(message)
