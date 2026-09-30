from typing import Any, Dict, List, Optional
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
    def __init__(self, headless: bool = True, timeout_ms: int = 30000):
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

    def _dismiss_popups(self, page: Page) -> None:
        for selector in DISMISS_SELECTORS:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=500):
                    locator.click(timeout=1000)
                    break
            except Exception:
                continue
    
    def _extract_fields(self, html: str, base_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else ""

        links = []

        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href'].strip()
            if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
                links.append(urljoin(base_url, href))
        images = []

        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src')

            if src:
                images.append(
                    {
                        "url": urljoin(base_url, src.strip()),
                        "alt": img.get("alt", "").strip(),
                    }
                )

        for tag in soup.find_all(UNWANTED_TAGS):
            tag.decompose()

        clean_text = soup.get_text(separator='\n',strip=True)

        return {
            "title": title,
            "text": clean_text,
            "links": list(dict.fromkeys(links)),
            "images": images,
        } 


    def scrape_page(self, url: str) -> Dict[str, Any]:
        if not self._context:
            raise RuntimeError("Scraper must be started before calling scrape_page().")

        page: Optional[Page] = None

        try:
            page = self._context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)

            if response and response.status >= 400:
                return {
                    "url": url,
                    "status": "error",
                    "title": "",
                    "text": "",
                    "links": [],
                    "images": [],
                    "error": f"HTTP {response.status}: {response.status_text}",
                }

            self._dismiss_popups(page)

            content = page.content()
            extracted = self._extract_fields(content, base_url=url)

            return {
                "url": url,
                "status": "success",
                "title": extracted["title"],
                "text": extracted["text"],
                "links": extracted["links"],
                "images": extracted["images"],
                "error": None,
            }

        except Exception as e:
            return {
                "url": url,
                "status": "error",
                "title": "",
                "text": "",
                "links": [],
                "images": [],
                "error": str(e),
            }

        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

def scrape_url(url: str, headless: bool = True, timeout_ms: int = 30000) -> Dict[str, Any]:
    with WebScraper(headless=headless, timeout_ms=timeout_ms) as scraper:
        return scraper.scrape_page(url)


def scrape_urls(
    urls: List[str], headless: bool = True, timeout_ms: int = 30000
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    with WebScraper(headless=headless, timeout_ms=timeout_ms) as scraper:
        for url in urls:
            result = scraper.scrape_page(url)
            results.append(result)

    return results


if __name__ == "__main__":
    sample_urls = [
        "https://huggingface.co/",
        "https://quotes.toscrape.com/",
        "https://httpbin.org/status/404",
    ]

    scraped_data = scrape_urls(sample_urls)
    for item in scraped_data:
        print(f"[{item['status'].upper()}] {item['url']} - Title: {item['title']!r}")
        if item["error"]:
            print(f"  Error: {item['error']}")
        else:
            print(f"  Extracted {len(item['links'])} links, {len(item['images'])} images, {len(item['text'])} chars")