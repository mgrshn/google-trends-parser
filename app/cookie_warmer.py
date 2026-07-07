"""
Получает свежие Google cookies через headless Chromium (Playwright + stealth).
Используется один раз при старте сессии скрейпера, затем куки передаются в curl_cffi.
"""
import asyncio
import logging
import random

from playwright.async_api import async_playwright
from playwright_stealth import stealth, Stealth

log = logging.getLogger(__name__)

TRENDS_URL = "https://trends.google.com/trends/explore?q=weather&date=today+1-m"


async def get_google_cookies(proxy_url: str | None = None, timeout: float = 30.0) -> dict:
    """
    Запускает headless Chromium, заходит на Google Trends, возвращает куки.
    proxy_url: "http://user:pass@host:port" или None для прямого соединения.
    """
    proxy = None
    if proxy_url:
        # playwright ожидает {"server": "...", "username": "...", "password": "..."}
        from urllib.parse import urlparse
        p = urlparse(proxy_url)
        proxy = {
            "server": f"{p.scheme}://{p.hostname}:{p.port}",
            "username": p.username or "",
            "password": p.password or "",
        }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            proxy=proxy,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        try:
            log.info("Cookie warmer: navigating to Google Trends (proxy=%s)", proxy_url or "direct")

            # шаг 1: заходим на google.com — получаем базовые куки
            await page.goto("https://www.google.com/", timeout=int(timeout * 1000), wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # шаг 2: переходим на trends.google.com — тригерим AEC
            await page.goto(TRENDS_URL, timeout=int(timeout * 1000), wait_until="networkidle")
            await asyncio.sleep(random.uniform(2.0, 3.0))

            # имитируем поведение пользователя
            for _ in range(random.randint(3, 6)):
                await page.mouse.move(
                    random.randint(100, 1100),
                    random.randint(50, 700),
                )
                await asyncio.sleep(random.uniform(0.1, 0.4))

            # прокручиваем страницу
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.5)")
            await asyncio.sleep(random.uniform(1.0, 2.0))

            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            important = {k: v for k, v in cookie_dict.items()
                         if k in ("NID", "AEC", "SOCS", "__Secure-BUCKET", "OTZ", "SIDCC")}
            log.info("Cookie warmer: got %d cookies total, key cookies: %s",
                     len(cookie_dict), list(important.keys()))
            return cookie_dict

        except Exception as e:
            log.error("Cookie warmer failed (proxy=%s): %s", proxy_url or "direct", e)
            return {}
        finally:
            await context.close()
            await browser.close()