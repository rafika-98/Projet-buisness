"""Frame extraction tools for the FlowGrab application."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
from PySide6.QtCore import QThread, Signal


@dataclass(slots=True)
class FrameExtractionOptions:
    """Configuration for a frame extraction session."""

    video_path: str
    output_dir: str
    prefix: str = "frame_"
    image_format: str = "jpg"
    every_n: int = 1
    start_time: float = 0.0
    end_time: Optional[float] = None
    resize_width: Optional[int] = None
    resize_height: Optional[int] = None
    jpeg_quality: int = 95
    preview_every: int = 1


class FrameExtractionWorker(QThread):
    """QThread worker responsible for extracting frames from a video."""

    sig_started = Signal(int, float)
    sig_progress = Signal(int, int, float, str, object)
    sig_log = Signal(str)
    sig_finished = Signal(bool, int, str)

    def __init__(self, options: FrameExtractionOptions, parent=None) -> None:
        super().__init__(parent)
        self.options = options
        self._abort_requested = False

    def request_abort(self) -> None:
        self._abort_requested = True

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    def run(self) -> None:  # noqa: C901 - complex but controlled
        opts = self.options
        cap = None
        try:
            video_path = Path(opts.video_path)
            if not video_path.exists():
                raise FileNotFoundError(f"Fichier vidéo introuvable : {video_path}")

            if opts.every_n < 1:
                raise ValueError("Le paramètre 'every_n' doit être supérieur ou égal à 1.")

            output_dir = Path(opts.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                raise RuntimeError(f"Impossible d'ouvrir la vidéo : {video_path}")

            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            if fps <= 0:
                fps = 0.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = total_frames / fps if fps > 0 else 0.0

            start_frame = 0
            if opts.start_time > 0 and fps > 0:
                start_frame = int(round(opts.start_time * fps))
            elif opts.start_time > 0 and fps == 0:
                self.sig_log.emit(
                    "FPS non détecté : le paramètre 'début' est ignoré et l'extraction commence au premier frame."
                )
            end_frame: Optional[int] = None
            if opts.end_time is not None and opts.end_time > 0:
                if fps > 0:
                    end_frame = int(round(opts.end_time * fps))
                else:
                    end_frame = None
                    self.sig_log.emit(
                        "FPS non détecté : le paramètre 'fin' est ignoré, extraction jusqu'à la fin de la vidéo."
                    )

            if total_frames > 0:
                if start_frame >= total_frames:
                    raise ValueError("Le temps de début dépasse la durée de la vidéo.")
                if end_frame is None or end_frame >= total_frames:
                    end_frame = total_frames - 1
                if end_frame < start_frame:
                    raise ValueError("Le temps de fin doit être supérieur au temps de début.")
                frames_to_iterate = end_frame - start_frame + 1
                total_to_save = (frames_to_iterate + opts.every_n - 1) // opts.every_n
            else:
                total_to_save = 0

            if start_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

            saved = 0
            processed = 0
            preview_next = opts.preview_every if opts.preview_every > 0 else 1

            self.sig_started.emit(total_to_save, duration)
            self.sig_log.emit(
                f"Extraction depuis {video_path.name} → dossier '{output_dir}' ({total_to_save or 'inconnu'} image(s) attendues)"
            )

            quality_args: list[int] = []
            fmt = opts.image_format.lower()
            if fmt in {"jpg", "jpeg"}:
                quality = max(10, min(100, int(opts.jpeg_quality or 95)))
                quality_args = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            elif fmt == "png":
                # valeur 0-9 (0 = sans compression, 9 = maximum)
                compression = 9 - max(0, min(9, int((opts.jpeg_quality or 95) / 11)))
                quality_args = [int(cv2.IMWRITE_PNG_COMPRESSION), compression]
            else:
                fmt = "jpg"

            frame_index = start_frame
            while True:
                if self._abort_requested:
                    self.sig_log.emit("Arrêt demandé, nettoyage…")
                    break

                if end_frame is not None and frame_index > end_frame:
                    break

                ok, frame = cap.read()
                if not ok:
                    break

                current_time = frame_index / fps if fps > 0 else cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

                if frame_index >= start_frame and (frame_index - start_frame) % opts.every_n == 0:
                    processed += 1
                    target_name = f"{opts.prefix}{saved:04d}.{fmt}"
                    target_path = output_dir / target_name

                    resized = frame
                    if opts.resize_width and opts.resize_height and opts.resize_width > 0 and opts.resize_height > 0:
                        resized = cv2.resize(frame, (opts.resize_width, opts.resize_height))

                    params = quality_args if quality_args else None
                    if params:
                        ok_write = cv2.imwrite(str(target_path), resized, params)
                    else:
                        ok_write = cv2.imwrite(str(target_path), resized)
                    if not ok_write:
                        raise IOError(f"Impossible d'enregistrer l'image : {target_path}")

                    saved += 1
                    preview_payload: object = None
                    if saved == 1 or processed >= preview_next:
                        preview_next = processed + (opts.preview_every or 1)
                        preview_payload = resized.copy()

                    self.sig_progress.emit(saved, total_to_save, float(current_time), str(target_path), preview_payload)

                frame_index += 1

            if self._abort_requested:
                self.sig_finished.emit(False, saved, "Extraction interrompue par l'utilisateur.")
            else:
                self.sig_finished.emit(True, saved, "Extraction terminée.")
        except Exception as exc:  # pragma: no cover - best effort logging
            self.sig_log.emit(f"[ERREUR] {exc}")
            self.sig_finished.emit(False, 0, str(exc))
        finally:
            if cap is not None:
                cap.release()
