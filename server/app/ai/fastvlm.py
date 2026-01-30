import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageStat

try:
    import torch
    from transformers import pipeline
except Exception:  # pragma: no cover - optional deps
    torch = None
    pipeline = None


_MODEL = None


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if pipeline is None:
        return None
    model_name = os.getenv("FASTVLM_MODEL", "Salesforce/blip-image-captioning-base")
    device = -1
    if torch is not None and torch.cuda.is_available():
        device = 0
    _MODEL = pipeline("image-to-text", model=model_name, device=device)
    return _MODEL


def _caption_image(image: Image.Image) -> str:
    model = _load_model()
    if model is None:
        return ""
    try:
        result = model(image, max_new_tokens=32)
        if isinstance(result, list) and result:
            return result[0].get("generated_text", "")
    except Exception:
        return ""
    return ""


def _brightness(image: Image.Image) -> float:
    grayscale = image.convert("L")
    stat = ImageStat.Stat(grayscale)
    return float(stat.mean[0]) / 255.0


def _extract_tags(caption: str) -> List[str]:
    caption_lower = caption.lower()
    tags = set()
    keyword_map = {
        "friends": ["friends", "friend", "group"],
        "crowd": ["crowd", "people", "audience"],
        "dancing": ["dance", "dancing"],
        "selfie": ["selfie"],
        "dj": ["dj", "turntable"],
        "street": ["street", "road"],
        "club": ["club", "bar"],
        "lights": ["lights", "neon", "stage"],
        "drinks": ["drink", "beer", "cocktail"],
        "phone": ["phone", "mobile"],
    }
    for tag, keywords in keyword_map.items():
        if any(keyword in caption_lower for keyword in keywords):
            tags.add(tag)
    return sorted(tags)


def _shot_type(caption: str, tags: List[str]) -> str:
    caption_lower = caption.lower()
    if "selfie" in tags or "selfie" in caption_lower:
        return "selfie"
    if "close" in caption_lower and "up" in caption_lower:
        return "closeup"
    if "crowd" in tags or "street" in tags:
        return "wide"
    if "pov" in caption_lower:
        return "pov"
    return "other"


def _people_score(caption: str, tags: List[str]) -> int:
    caption_lower = caption.lower()
    if "selfie" in tags:
        return 2
    if "crowd" in tags:
        return 5
    if any(word in caption_lower for word in ["person", "people", "man", "woman", "boy", "girl"]):
        return 3
    return 0


def _energy_score(tags: List[str], brightness: float) -> int:
    score = 4
    if "dancing" in tags:
        score += 3
    if "crowd" in tags:
        score += 2
    if "lights" in tags:
        score += 1
    if "club" in tags:
        score += 1
    if brightness < 0.2:
        score -= 2
    return max(0, min(10, score))


def _highlight_score(energy: int, tags: List[str], people: int) -> int:
    score = energy
    if people > 0:
        score += 1
    if any(tag in tags for tag in ["selfie", "dj", "dancing", "crowd"]):
        score += 2
    return max(0, min(10, score))


def tag_frame(image_path: Path) -> Dict[str, Any]:
    image = Image.open(image_path).convert("RGB")
    caption = _caption_image(image)
    brightness = _brightness(image)
    tags = _extract_tags(caption)
    people = _people_score(caption, tags)
    energy = _energy_score(tags, brightness)
    highlight = _highlight_score(energy, tags, people)
    return {
        "scene": caption or "",
        "tags": tags,
        "shot_type": _shot_type(caption, tags),
        "energy": energy,
        "highlight": highlight,
        "people": people,
        "brightness": round(brightness, 3),
        "ai_used": bool(caption),
    }
