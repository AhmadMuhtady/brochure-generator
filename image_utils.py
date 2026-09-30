import base64
import hashlib
import io
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(override=True)

import httpx
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("HERO_IMAGE_DIR", "output_images"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
POLLINATIONS_TIMEOUT = 90  # seconds; generation can be slow

JUNK_KEYWORDS = {
    "icon", "favicon", "badge", "avatar", "spacer", "pixel", "1x1",
    "track", "analytics", "sprite", "social", "facebook", "twitter",
    "instagram", "linkedin", "share", "arrow", "chevron", "btn"
}

UI_SCREENSHOT_KEYWORDS = {
    "screenshot", "preview", "dashboard", "mockup", "widget",
    "diagram", "docs", "documentation", "tutorial", "models-mobile",
    "app-preview", "ui-", "-ui", "interface"
}

LOGO_KEYWORDS = {"logo", "brand", "masthead"}
HERO_HINTS = {"hero", "banner", "header", "cover", "featured", "splash"}
DIMENSION_REGEX = re.compile(r"(\d+)x(\d+)", re.IGNORECASE)

_CACHED_OPENAI_IMAGE_MODEL: Optional[str] = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "cover"


def quick_url_heuristic(img: Dict[str, str]) -> str:
    url = img.get("url", "")
    alt = img.get("alt", "")
    parsed = urlparse(url)
    combined = f"{parsed.path} {alt}".lower()

    if parsed.path.lower().endswith(".svg"):
        return "logo" if any(k in combined for k in LOGO_KEYWORDS) else "junk"

    if any(k in combined for k in LOGO_KEYWORDS):
        return "logo"

    if any(k in combined for k in JUNK_KEYWORDS):
        return "junk"

    if any(k in combined for k in UI_SCREENSHOT_KEYWORDS):
        return "junk"

    match = DIMENSION_REGEX.search(parsed.path)
    if match:
        w, h = int(match.group(1)), int(match.group(2))
        if w < 100 or h < 100:
            return "junk"

    return "candidate"
def fetch_image_dimensions(client: httpx.Client, url: str, max_bytes: int = 65536) -> Optional[Tuple[int, int]]:
    try:
        with client.stream("GET", url, timeout=5.0, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                return None
            buffer = bytearray()
            for chunk in resp.iter_bytes(chunk_size=8192):
                buffer.extend(chunk)
                try:
                    with Image.open(io.BytesIO(buffer)) as im:
                        return im.size
                except Exception:
                    pass
                if len(buffer) >= max_bytes:
                    break
    except Exception:
        return None
    return None


def select_hero_and_logo(
    raw_images: List[Dict[str, str]],
    min_hero_width: int = 300,
    min_hero_height: int = 150,
) -> Dict[str, Optional[Dict[str, Any]]]:
    logos: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}) as client:
        for img in raw_images:
            heuristic_class = quick_url_heuristic(img)

            if heuristic_class == "junk":
                continue

            if heuristic_class == "logo":
                logos.append({
                    "url": img["url"],
                    "alt": img.get("alt", ""),
                    "format": "svg" if img["url"].lower().endswith(".svg") else "raster",
                })
                continue

            dims = fetch_image_dimensions(client, img["url"])
            if not dims:
                continue

            width, height = dims
            aspect_ratio = width / height if height > 0 else 0

            if width >= min_hero_width and height >= min_hero_height:
                aspect_multiplier = 1.5 if 1.2 <= aspect_ratio <= 2.2 else 1.0
                combined_name = f"{img['url']} {img.get('alt', '')}".lower()
                name_boost = 2.0 if any(h in combined_name for h in HERO_HINTS) else 1.0

                area = width * height
                entry = {
                    "url": img["url"],
                    "alt": img.get("alt", ""),
                    "width": width,
                    "height": height,
                    "area": area,
                    "score": area * aspect_multiplier * name_boost,
                }
                candidates.append(entry)

    hero = max(candidates, key=lambda c: c["score"]) if candidates else None
    logo = logos[0] if logos else None
    return {"hero": hero, "logo": logo}





