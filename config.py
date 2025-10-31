import json
import pathlib
from datetime import datetime, timezone
from typing import Optional

OUT_DIR = pathlib.Path(r"C:\\Users\\Lamine\\Desktop\\Projet final\\Application\\downloads")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = OUT_DIR / "flowgrab_config.json"

DEFAULT_CONFIG = {
    "webhook_path": "/webhook/Audio",
    "webhook_base": "",
    "webhook_full": "",
    "last_updated": "",
    "telegram_token": "",
    "telegram_mode": "polling",
    "telegram_port": 8081,
    "cookies_path": "",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "browser_cookies": "auto",
}


def _ensure_config_defaults(data: Optional[dict]) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(data, dict):
        for k, v in data.items():
            if v is not None:
                cfg[k] = v

    bc = (cfg.get("browser_cookies") or "auto").strip().lower()
    allowed = {
        "auto",
        "edge",
        "chrome",
        "firefox",
        "brave",
        "vivaldi",
        "opera",
        "chromium",
        "cookiefile",
        "none",
    }
    cfg["browser_cookies"] = bc if bc in allowed else "auto"

    return cfg


def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return _ensure_config_defaults(data)
    except Exception:
        pass
    return _ensure_config_defaults(None)


def save_config(cfg: dict) -> None:
    try:
        merged = _ensure_config_defaults(cfg)
        merged["last_updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
