"""Utilitaires pour extraire l'audio MP3 depuis une vidéo locale.

Ce module reprend le script autonome fourni pour l'« avenir 4 » en le
transformant en composant réutilisable par l'application. Il propose un
API Python, une interface CLI et, si Tkinter est disponible, une petite
interface graphique permettant de sélectionner un fichier vidéo.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

from core.download_core import sanitize_filename
from paths import get_audio_dir

# Dossier de sortie par défaut : ``Audios/Youtube`` dans OUT_DIR
OUTPUT_DIR = get_audio_dir("youtube")
DEFAULT_BITRATE = "192k"
DEFAULT_VIDEO_EXTS = (
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".3gp",
)


class FFMpegError(RuntimeError):
    """Erreur personnalisée renvoyée lorsque ffmpeg échoue."""


def ensure_output_dir(output_dir: Path | None = None) -> Path:
    """S'assure que le dossier de sortie existe et le renvoie."""

    directory = output_dir or OUTPUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def ffmpeg_exists(ffmpeg_bin: str = "ffmpeg") -> bool:
    """Retourne ``True`` si ffmpeg est disponible dans le PATH."""

    try:
        subprocess.run([ffmpeg_bin, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except FileNotFoundError:
        return False


def is_supported_video(path: Path, allowed_exts: Iterable[str] | None = None) -> bool:
    """Indique si un fichier possède une extension vidéo connue."""

    exts = tuple((ext or "").lower() for ext in (allowed_exts or DEFAULT_VIDEO_EXTS))
    return path.suffix.lower() in exts


def build_output_path(input_path: Path, output_dir: Path | None = None) -> Path:
    """Construit le chemin de sortie MP3 pour ``input_path``."""

    directory = ensure_output_dir(output_dir)
    safe_name = sanitize_filename(input_path.stem) or "audio"
    safe_name = safe_name.replace(":", " -").replace("|", "-")
    return directory / f"{safe_name}.mp3"


def convert_to_mp3(
    input_file: Path,
    *,
    bitrate: str = DEFAULT_BITRATE,
    ffmpeg_bin: str = "ffmpeg",
    output_dir: Path | None = None,
    overwrite: bool = True,
) -> Path:
    """Extrait l'audio d'une vidéo locale au format MP3.

    :raises FileNotFoundError: si ``input_file`` est absent.
    :raises FFMpegError: si ffmpeg renvoie un code de retour non nul.
    """

    if not input_file.exists():
        raise FileNotFoundError(f"Fichier introuvable : {input_file}")

    ensure_output_dir(output_dir)
    output_file = build_output_path(input_file, output_dir)

    cmd = [
        ffmpeg_bin,
        "-y" if overwrite else "-n",
        "-i",
        str(input_file),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_file),
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise FFMpegError(f"Échec ffmpeg.\n\n--- STDERR ---\n{proc.stderr}")

    return output_file


def _load_tk() -> tuple[object, object, object]:  # pragma: no cover - interface graphique optionnelle
    import tkinter as tk  # type: ignore
    from tkinter import filedialog, messagebox  # type: ignore

    return tk, filedialog, messagebox


def run_tk_app() -> None:  # pragma: no cover - interface graphique optionnelle
    """Lance l'interface Tkinter pour choisir une vidéo et extraire l'audio."""

    try:
        tk, filedialog, messagebox = _load_tk()
    except Exception as exc:  # pragma: no cover - Tkinter indisponible
        raise RuntimeError("Tkinter est indisponible sur cet environnement") from exc

    if not ffmpeg_exists():
        messagebox.showerror(
            "ffmpeg manquant",
            (
                "ffmpeg n'est pas installé ou pas dans le PATH.\n\n"
                "1) Télécharge-le sur https://ffmpeg.org/download.html\n"
                "2) Ajoute-le au PATH de Windows\n"
                "3) Relance ce module."
            ),
        )
        return

    class _App:
        def __init__(self) -> None:
            self.root = tk.Tk()
            self.root.title("Extracteur Audio MP3 depuis une Vidéo")
            self.root.geometry("520x160")
            self.root.resizable(False, False)

            info = tk.Label(
                self.root,
                text=(
                    "Sélectionne une vidéo locale.\n"
                    "L'audio sera exporté en MP3 ici :\n"
                    f"{OUTPUT_DIR}"
                ),
                justify="center",
            )
            info.pack(pady=12)

            self.btn = tk.Button(
                self.root,
                text="Choisir une vidéo et extraire l’audio",
                command=self.pick_and_convert,
                height=2,
            )
            self.btn.pack(pady=6)

            self.status_var = tk.StringVar(value="En attente…")
            status = tk.Label(self.root, textvariable=self.status_var, fg="#555")
            status.pack(pady=4)

        def pick_and_convert(self) -> None:
            file_path = filedialog.askopenfilename(
                title="Choisis une vidéo",
                filetypes=[
                    ("Vidéos", " ".join(f"*{ext}" for ext in DEFAULT_VIDEO_EXTS)),
                    ("Tous les fichiers", "*.*"),
                ],
            )
            if not file_path:
                return

            input_file = Path(file_path)
            if not is_supported_video(input_file):
                if not messagebox.askyesno(
                    "Extension inconnue",
                    (
                        f"L'extension {input_file.suffix} n'est pas reconnue comme vidéo.\n"
                        "Vouloir tenter l'extraction quand même ?"
                    ),
                ):
                    return

            try:
                self.btn.config(state="disabled")
                self.status_var.set("Conversion en cours…")
                self.root.update_idletasks()

                out = convert_to_mp3(input_file)

                self.status_var.set(f"✅ Audio exporté : {out}")
                messagebox.showinfo("Terminé", f"Audio exporté avec succès :\n{out}")
            except Exception as exc:  # pragma: no cover - interactions utilisateur
                self.status_var.set("❌ Erreur")
                messagebox.showerror("Erreur", str(exc))
            finally:
                self.btn.config(state="normal")

        def run(self) -> None:
            self.root.mainloop()

    _App().run()


def main_cli(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée CLI : ``python -m modules.module_local_audio <video>``."""

    parser = argparse.ArgumentParser(description="Extraction MP3 locale via ffmpeg")
    parser.add_argument("video", nargs="?", help="Chemin vers la vidéo à convertir")
    parser.add_argument("--bitrate", default=DEFAULT_BITRATE, help="Débit audio cible (ex: 128k, 192k)")
    parser.add_argument("--no-overwrite", action="store_true", help="Ne pas écraser les fichiers existants")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Dossier de sortie (par défaut: Audios/Youtube)",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="Binaire ffmpeg à utiliser (par défaut: ffmpeg)",
    )
    parser.add_argument("--gui", action="store_true", help="Ouvrir l'interface graphique Tkinter")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.gui or not args.video:
        run_tk_app()
        return 0

    if not ffmpeg_exists(args.ffmpeg):
        print("Erreur : ffmpeg n'est pas installé ou pas dans le PATH.", file=sys.stderr)
        return 1

    try:
        out = convert_to_mp3(
            Path(args.video),
            bitrate=args.bitrate,
            ffmpeg_bin=args.ffmpeg,
            output_dir=args.output_dir,
            overwrite=not args.no_overwrite,
        )
    except FileNotFoundError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except FFMpegError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(f"OK : {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exécution directe
    sys.exit(main_cli())
