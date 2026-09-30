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


