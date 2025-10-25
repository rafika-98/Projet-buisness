import sys, subprocess, time, os, shutil, pathlib


def main():
    if len(sys.argv) < 4:
        print("Usage: updater.py <repo_root> <python_exe> <main_script>")
        return 2

    repo_root   = pathlib.Path(sys.argv[1])
    python_exe  = sys.argv[2]
    main_script = os.path.abspath(sys.argv[3])

    # Laisse le temps au process principal de se fermer et libérer les fichiers
    time.sleep(1.2)

    # Quelques tentatives pour s'assurer que le script principal n'est plus verrouillé
    for _ in range(5):
        try:
            with open(main_script, "rb"):
                pass
            break
        except Exception:
            time.sleep(0.6)

    git = shutil.which("git")
    ret = 0
    if not git:
        print("Git introuvable dans le PATH.")
        ret = 1
    else:
        print(f"[Updater] git pull origin main — cwd={repo_root}")
        proc = subprocess.run([git, "pull", "origin", "main"], cwd=str(repo_root), text=True)
        ret = proc.returncode or 0
        print(f"[Updater] git pull terminé (exit={ret})")

    # Relance l’application quoi qu’il arrive (même si la mise à jour échoue)
    try:
        subprocess.Popen([python_exe, main_script], close_fds=True)
        print("[Updater] Application relancée.")
    except Exception as e:
        print(f"[Updater] Relance impossible : {e}")
        return 3

    return ret


if __name__ == "__main__":
    raise SystemExit(main())
