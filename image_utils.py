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


def extract_visual_themes(title: str, context_text: str) -> str:
    """
    Synthesizes title and scraped body text into a clean visual theme
    suitable for an editorial hero banner.
    """
    sample = f"{title} {context_text[:1500]}".lower()

    if any(k in sample for k in ["law", "legal", "attorney", "counsel", "litigation"]):
        return "modern law firm, elegant wood accents, minimalist legal library, warm natural lighting"
    if any(k in sample for k in ["bakery", "pastry", "coffee", "cafe", "artisan bread"]):
        return "artisan bakery counter, rustic wooden tables, warm sunlight, freshly baked sourdough"
    if any(k in sample for k in ["medical", "clinic", "health", "doctor", "pharma"]):
        return "contemporary medical clinic, clean bright architectural interior, serene daylight"
    if any(k in sample for k in ["finance", "fintech", "banking", "wealth", "investment"]):
        return "modern high-rise executive financial office, glass architecture, subtle morning glow"
    if any(k in sample for k in ["software", "ai", "machine learning", "cloud", "developer"]):
        return "futuristic high-tech workspace, subtle glowing network interfaces, sleek minimalist desk"

    clean_lines = [
        line.strip() for line in context_text.splitlines() 
        if len(line.strip()) > 30 and not line.strip().startswith(("http", "{", "<"))
    ]
    summary_hint = clean_lines[0][:100] if clean_lines else title[:80]
    return f"clean modern editorial photography representing: {summary_hint}"


def build_hero_prompt(theme: str) -> str:
    """Single prompt shared across providers to ensure consistent framing."""
    return (
        f"Wide editorial hero photograph evoking {theme}. "
        "Main subject placed on the right third, softly blurred background, "
        "large calm empty area on the left with smooth, uncluttered surfaces. "
        "Soft cinematic lighting, shallow depth of field, muted premium color palette, "
        "photorealistic, high-end magazine photography."
    )


def get_contextual_stock_image(theme: str) -> str:
    category_photos = {
        "legal": "photo-1589829545856-d10d557cf95f",
        "bakery": "photo-1509440159596-0249088772ff",
        "clinic": "photo-1519494026892-80bbd2d6fd0d",
        "finance": "photo-1486406146926-c627a92ad1ab",
        "tech": "photo-1518770660439-4636190af475",
        "default": "photo-1497366216548-37526070297c",
    }
    lowered = theme.lower()
    selected_id = category_photos["default"]
    for key, photo_id in category_photos.items():
        if key in lowered:
            selected_id = photo_id
            break
    return f"https://images.unsplash.com/{selected_id}?auto=format&fit=crop&w=1600&q=80"


def discover_available_image_model(client: Any) -> str:
    global _CACHED_OPENAI_IMAGE_MODEL
    if _CACHED_OPENAI_IMAGE_MODEL:
        return _CACHED_OPENAI_IMAGE_MODEL

    try:
        available_models = [m.id for m in client.models.list().data]
        preferred_candidates = [
            OPENAI_IMAGE_MODEL,
            "gpt-image-1",
            "gpt-image-1-mini",
            "gpt-image",
            "dall-e-3",
            "dall-e-2",
        ]
        for candidate in preferred_candidates:
            if candidate in available_models:
                _CACHED_OPENAI_IMAGE_MODEL = candidate
                return candidate

        matching = [m for m in available_models if "image" in m.lower()]
        if matching:
            _CACHED_OPENAI_IMAGE_MODEL = matching[0]
            return _CACHED_OPENAI_IMAGE_MODEL
    except Exception as e:
        logger.warning(f"Could not list OpenAI models: {e}")

    return OPENAI_IMAGE_MODEL


def generate_ai_cover(page_title: str, context_text: str = "") -> str:
    """
    Generates a wide hero image tailored to the company's content.
    Returns a local file path or fallback stock image URL.
    """
    clean_title = page_title.strip()[:80] or "Company Overview"
    theme = extract_visual_themes(clean_title, context_text)
    prompt = build_hero_prompt(theme)

    key = hashlib.sha256(f"{clean_title}|{theme}".encode()).hexdigest()[:12]
    base_name = f"{_slug(clean_title)}-{key}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cached = next(OUTPUT_DIR.glob(f"{base_name}.*"), None)
    if cached:
        print(f"Reusing cached hero image: {cached}")
        return str(cached)


    if os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            client = OpenAI()
            model_to_use = discover_available_image_model(client)
            print(f"Calling OpenAI image generation with model '{model_to_use}'...")

            resp = client.images.generate(
                model=model_to_use,
                prompt=prompt + " No text, lettering, signage, or logos anywhere in the image.",
                size="1024x1024" if "dall-e-2" in model_to_use else "1536x1024",
                quality="medium" if "gpt-image" in model_to_use else "standard",
                n=1,
            )
            item = resp.data[0]


            if getattr(item, "b64_json", None):
                out = OUTPUT_DIR / f"{base_name}.png"
                out.write_bytes(base64.b64decode(item.b64_json))
                return str(out)

            
            if getattr(item, "url", None):
                saved_path = download_and_save_image(item.url, base_name)
                if saved_path:
                    return saved_path
                raise IOError(f"Failed to download image from OpenAI URL: {item.url}")


    try:
        seed = int(key[:8], 16)
        params = urllib.parse.urlencode({
            "width": 1600,
            "height": 896,
            "model": "flux",
            "nologo": "true",
            "seed": seed,
        })
        url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?{params}"
        print(f"Generating AI image via Pollinations for theme: '{theme[:50]}'...")

        req = urllib.request.Request(url, headers={"User-Agent": "hero-cover/1.0"})
        with urllib.request.urlopen(req, timeout=POLLINATIONS_TIMEOUT) as r:
            content_type = r.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                raise ValueError(f"Unexpected content type: {content_type!r}")
            data = r.read()

        ext = "png" if "png" in content_type else "jpg"
        out = OUTPUT_DIR / f"{base_name}.{ext}"
        out.write_bytes(data)
        return str(out)
    except Exception as e:
        print(f"[!] Pollinations generation failed: {e}. Falling back to stock...")


    return get_contextual_stock_image(theme)







