from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright




DISMISS_SELECTORS = [
    'button[id*="cookie" i]',
    'button[class*="cookie" i]',
    'button[id*="consent" i]',
    'button[class*="consent" i]',
    'button[aria-label*="accept" i]',
    'button[aria-label*="close" i]',
    'button:has-text("Accept")',
    'button:has-text("Accept All")',
    'button:has-text("Agree")',
    'button:has-text("I agree")',
    'button:has-text("Allow all")',
    'button:has-text("Dismiss")',
    'button:has-text("Close")',
]

UNWANTED_TAGS = [
    "script",
    "style",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    "svg",
    "iframe",
]

class WebScraper:
    def __init__(self,headless: bool = False,timeout_ms: int = 30000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def start(self) -> None:
        if not self._browser:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._context.set_default_timeout(self.timeout_ms)

    def close(self) -> None:
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "WebScraper":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
