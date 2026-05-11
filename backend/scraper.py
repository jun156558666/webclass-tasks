import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

USERNAME = os.getenv("HOSEI_USERNAME", "")
PASSWORD = os.getenv("HOSEI_PASSWORD", "")
DEBUG_DIR = Path(__file__).parent.parent / "debug"

HOPPII_LOGIN_URL = "https://hoppii2025.hosei.ac.jp/portal/u001/index.php"
SSO_URL = "https://hoppii2025.hosei.ac.jp/portal/sso/Parm.php?auth_mode=1&group_id="
WEBCLASS_BASE = "https://lms2025.hosei.ac.jp"

# JS to extract assignments from a course page
_EXTRACT_JS = """() => {
    const results = [];
    const sections = document.querySelectorAll('section.list-group-item, .list-group-item');
    sections.forEach(section => {
        const text = section.innerText || '';
        if (!text.includes('レポート')) return;

        // Extract title: first non-empty line that isn't a label word
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
        let title = '';
        for (const line of lines) {
            if (line === 'New') continue;
            if (line === 'レポート') break;
            if (line === '詳細' || line.startsWith('利用回数') || line.startsWith('利用可能期間')) continue;
            if (line) { title = line; break; }
        }

        // Deadline = end of availability period
        const dateMatch = text.match(/(\\d{4}\\/\\d{2}\\/\\d{2}\\s+\\d{2}:\\d{2})\\s*[-~～]\\s*(\\d{4}\\/\\d{2}\\/\\d{2}\\s+\\d{2}:\\d{2})/);
        const deadline = dateMatch ? dateMatch[2] : null;

        // URL: prefer do_contents link, fall back to any contents link
        const doLink = section.querySelector('a[href*="do_contents"]');
        const anyLink = section.querySelector('a[href*="/contents/"], a[href*="contents"]');
        const link = doLink || anyLink;
        const href = link ? link.getAttribute('href') : '';

        if (title) {
            results.push({ title, href, deadline });
        }
    });
    return results;
}"""


class WebClassScraper:
    def __init__(self):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None
        self.is_logged_in = False
        self.last_error: Optional[str] = None
        self.last_scraped_at: Optional[str] = None

    async def start(self):
        DEBUG_DIR.mkdir(exist_ok=True)
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        self.page = await self.context.new_page()
        logger.info("Browser started")

    async def stop(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    async def _ss(self, name: str):
        if self.page:
            p = DEBUG_DIR / f"{name}_{datetime.now().strftime('%H%M%S')}.png"
            try:
                await self.page.screenshot(path=str(p), timeout=10000)
            except Exception:
                pass
            logger.debug(f"Screenshot: {p.name}")

    # ------------------------------------------------------------------
    # Login + SSO
    # ------------------------------------------------------------------
    async def login(self) -> bool:
        try:
            logger.info("Navigating to hoppii login page...")
            await self.page.goto(HOPPII_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await self._ss("01_hoppii")

            await self.page.fill('input[name="j_username"]', USERNAME)
            await self.page.fill('input[name="j_password"]', PASSWORD)
            await self.page.click('button:text("ログイン"), input[type="submit"]')
            await self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            await self._ss("02_after_login")
            logger.info(f"After login URL: {self.page.url}")

            # Portal may show an image-based login button linking to login/login.php
            login_link = await self.page.query_selector('a[href*="login/login.php"]')
            if login_link:
                await login_link.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=30000)
                await self._ss("03_after_portal_login")
                logger.info(f"After portal login URL: {self.page.url}")

            # Navigate directly to SSO URL → auto-redirects into WebClass
            logger.info("Navigating via SSO URL...")
            await self.page.goto(SSO_URL, wait_until="domcontentloaded", timeout=30000)
            await self._ss("04_webclass_home")
            logger.info(f"WebClass URL: {self.page.url}")

            if "lms2025.hosei.ac.jp" not in self.page.url:
                raise RuntimeError(f"SSO did not reach WebClass. URL: {self.page.url}")

            self.is_logged_in = True
            logger.info("Login successful")
            return True

        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Login failed: {e}")
            await self._ss("err_login")
            return False

    # ------------------------------------------------------------------
    # Scrape assignments
    # ------------------------------------------------------------------
    async def get_assignments(self) -> list[dict]:
        assignments = []

        course_links = await self._find_courses()
        logger.info(f"Found {len(course_links)} courses")

        for course_name, course_url in course_links:
            try:
                found = await self._scrape_course(course_name, course_url)
                assignments.extend(found)
                logger.info(f"  '{course_name}': {len(found)} assignments")
            except Exception as e:
                logger.error(f"Error in course '{course_name}': {e}")

        logger.info(f"Total assignments: {len(assignments)}")
        return assignments

    async def _find_courses(self) -> list[tuple[str, str]]:
        courses = []
        seen = set()

        links = await self.page.query_selector_all('a[href*="course.php"]')
        for link in links:
            href = await link.get_attribute("href") or ""
            name = (await link.inner_text()).strip().splitlines()[0].replace("\xbb", "").strip()
            if ')' in name:
                name = name[:name.rfind(')') + 1]
            if href and name and href not in seen:
                seen.add(href)
                full = href if href.startswith("http") else WEBCLASS_BASE + href
                courses.append((name, full))

        if not courses:
            logger.warning("No courses found with course.php selector")

        return courses

    async def _scrape_course(self, course_name: str, course_url: str) -> list[dict]:
        await self.page.goto(course_url, wait_until="domcontentloaded", timeout=30000)

        # Refine course name from page heading if available
        heading = await self.page.query_selector(".course-name, .navbar-brand a.course-name")
        if heading:
            course_name = (await heading.inner_text()).strip().splitlines()[0].strip()
            if ')' in course_name:
                course_name = course_name[:course_name.rfind(')') + 1]

        raw_items = await self.page.evaluate(_EXTRACT_JS)

        results = []
        for item in raw_items:
            title = item.get("title", "").strip()
            href = item.get("href", "") or ""
            deadline = item.get("deadline")
            if not title:
                continue
            url = href if href.startswith("http") else (WEBCLASS_BASE + href if href else "")
            # ID は授業名+タイトル+締切で決定（URLはacs_トークンで変わるため除外）
            uid = hashlib.md5(f"{course_name}:{title}:{deadline or ''}".encode()).hexdigest()
            results.append({
                "id": uid,
                "course_name": course_name,
                "title": title,
                "deadline": deadline,
                "url": url,
            })

        return results

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def _ensure_browser(self):
        """コンテナ再起動などでブラウザが死んでいたら再起動する。"""
        try:
            if self.page and not self.page.is_closed():
                return
        except Exception:
            pass
        logger.info("Browser lost, restarting...")
        try:
            await self.stop()
        except Exception:
            pass
        await self.start()
        self.is_logged_in = False

    async def scrape(self) -> list[dict]:
        await self._ensure_browser()
        if not self.is_logged_in:
            if not await self.login():
                return []
        else:
            # WebClass ホームに直接移動してセッション確認
            try:
                await self.page.goto(
                    WEBCLASS_BASE + "/webclass/",
                    wait_until="domcontentloaded", timeout=20000
                )
                # ログインページに飛ばされた場合は再ログイン
                if "lms2025.hosei.ac.jp" not in self.page.url or "login" in self.page.url.lower():
                    logger.info("Session expired, re-logging in...")
                    self.is_logged_in = False
                    if not await self.login():
                        return []
            except Exception as e:
                logger.warning(f"Session check failed: {e}, re-logging in...")
                self.is_logged_in = False
                if not await self.login():
                    return []

        results = await self.get_assignments()
        self.last_scraped_at = datetime.now().isoformat()
        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_scraper: Optional[WebClassScraper] = None


async def get_scraper() -> WebClassScraper:
    global _scraper
    if _scraper is None:
        _scraper = WebClassScraper()
        await _scraper.start()
    return _scraper


async def shutdown_scraper():
    global _scraper
    if _scraper:
        await _scraper.stop()
        _scraper = None
