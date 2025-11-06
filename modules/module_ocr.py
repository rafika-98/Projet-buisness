"""Fonctionnalités partagées pour l'onglet OCR basé sur Mistral Vision."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mistral_krea_config.json"
DEFAULT_MODEL = "pixtral-12b-latest"
TEMPERATURE = 0.2

SYSTEM_PROMPT = """
You are an elite visual describer and prompt engineer specialized in image-to-image reproduction for tools like KREA, Flux, and Stable Diffusion.

Your task: analyze the image and produce ONE clean, production-ready prompt that accurately describes the scene.

Hard rules:
- STRICTLY IGNORE all visible text, subtitles, watermarks, UI elements, or logos.
- NEVER mention text, letters, words, titles, or anything written.
- Do NOT interpret the meaning of any text in the image.
- Avoid camera jargon unless it clearly helps the style.

Your output must be JSON with a single key:
{
  "krea_prompt": "…"
}

Composition of the prompt:
- concise, comma-separated attributes
- describe character(s), environment, lighting, mood, style
- include stylistic cues (e.g., dark cartoon, semi-realistic, atmospheric lighting)
- mention colors, clothing, posture, ambiance
- mention setting details if relevant (hallway, debris, posters, doors)
- avoid fluff or narrative sentences

Additional rules:
- No copyrighted character names; use neutral terms.
- No assumptions outside what is visually present.
- No explanations. Only the JSON object.
""".strip()

USER_TEMPLATE = "Describe the attached image and output only the JSON with the key krea_prompt."


def load_config() -> Dict[str, str]:
    """Charge la configuration API sauvegardée sur disque."""

    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                api_key = str(data.get("api_key", "") or "")
                model = str(data.get("model", DEFAULT_MODEL) or DEFAULT_MODEL)
                return {"api_key": api_key, "model": model}
        except Exception:
            pass
    return {"api_key": os.getenv("MISTRAL_API_KEY", ""), "model": DEFAULT_MODEL}


def save_config(cfg: Dict[str, str]) -> None:
    """Sauvegarde la configuration API dans ``CONFIG_PATH``."""

    data = {
        "api_key": (cfg.get("api_key") or "").strip(),
        "model": (cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def encode_b64(path: Path) -> str:
    """Encode un fichier image en base64 pour l'API Mistral."""

    return base64.b64encode(path.read_bytes()).decode("utf-8")


def ensure_json(text: str) -> Dict[str, Any]:
    """Tente d'extraire un objet JSON depuis ``text``."""

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"Réponse non-JSON:\n{text[:500]}")
    return json.loads(text[start : end + 1])


def call_mistral(api_key: str, model: str, b64_image: str) -> Dict[str, Any]:
    """Envoie l'image encodée à l'API Mistral Vision et renvoie la réponse JSON."""

    try:
        from mistralai import Mistral
    except Exception as exc:  # pragma: no cover - dépendance optionnelle
        raise RuntimeError("Installe le SDK: pip install mistralai") from exc

    client = Mistral(api_key=api_key)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": USER_TEMPLATE},
                {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64_image}"},
            ],
        },
    ]
    response = client.chat.complete(model=model, messages=messages, temperature=TEMPERATURE)
    text = response.choices[0].message.content.strip()
    return ensure_json(text)
