"""
Playwright-based scraper for R1 Data Studio report exports.

Automates: login to ruckus.cloud -> navigate to tenant Data Studio
-> find report in embedded Superset iframe -> export CSV + PDF.

Architecture:
  - ruckus.cloud login redirects to auth.ruckuswireless.com (Rails SSO)
  - Data Studio page embeds Apache Superset in an iframe
  - Superset iframe has dashboard list at /api/a4rc/explorer/dashboard/list/
  - Each dashboard has a "..." menu (trigger[0]) with Export CSV / Export PDF
  - JS chunks at /tenant/t/*.esm.js need route interception to load properly
"""
import asyncio
import io
import logging
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Frame,
    Playwright,
)

logger = logging.getLogger(__name__)

# ============================================================
# SELECTORS — Mapped from discovery sessions (v11-v13)
# ============================================================

# Login page (auth.ruckuswireless.com)
LOGIN_USERNAME = "#user_username"
LOGIN_PASSWORD = "#user_password"
LOGIN_SUBMIT = "#ruckus-one-submit-button"
LOGIN_MFA = 'input[name="otp"], input[name="mfa"], [data-testid="mfa"]'

# Superset iframe (embedded in Data Studio page)
SUPERSET_IFRAME = 'iframe[title="data-studio"]'

# Dashboard list (inside Superset iframe)
DASHBOARD_TABLE_ROW = "tr"

# Dashboard-level "..." menu (first .ant-dropdown-trigger which is a DIV, not SPAN)
DASHBOARD_MORE_MENU = "div.button-container .ant-dropdown-trigger"
# Fallback: first DIV dropdown trigger
DASHBOARD_MORE_MENU_FALLBACK = "div.ant-dropdown-trigger"

# Export menu items (appear after clicking the "..." menu)
MENU_EXPORT_CSV = '.ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item'
MENU_EXPORT_PDF = '.ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item'

# Timeouts (milliseconds)
NAVIGATION_TIMEOUT = 30_000
LOGIN_TIMEOUT = 15_000
IFRAME_TIMEOUT = 30_000
DASHBOARD_LOAD_TIMEOUT = 45_000
DOWNLOAD_TIMEOUT = 120_000

# Retry config
MAX_RETRIES_PER_TENANT = 2
RETRY_BACKOFF_SECONDS = 10

RUCKUS_CLOUD_URL = "https://ruckus.cloud"


@dataclass
class ExportResult:
    """Result of a single tenant export attempt."""
    success: bool
    csv_files: Optional[Dict[str, bytes]] = None  # {filename: csv_bytes}
    pdf_bytes: Optional[bytes] = None
    error: str = ""
    screenshot_bytes: Optional[bytes] = None
    duration_seconds: float = 0.0


@dataclass
class ScraperSession:
    """Manages a Playwright browser session for Data Studio exports."""
    username: str
    password: str
    _pw: Optional[Playwright] = field(default=None, repr=False)
    _browser: Optional[Browser] = field(default=None, repr=False)
    _context: Optional[BrowserContext] = field(default=None, repr=False)
    _page: Optional[Page] = field(default=None, repr=False)
    _logged_in: bool = False

    async def start(self):
        """Launch browser and create context."""
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            accept_downloads=True,
        )
        self._page = await self._context.new_page()

        # Intercept /tenant/t/ chunk requests — they get ERR_ABORTED without this
        async def handle_chunk(route):
            try:
                resp = await route.fetch()
                await route.fulfill(response=resp)
            except Exception:
                await route.abort()

        await self._page.route("**/tenant/t/**", handle_chunk)

        logger.info("Playwright browser session started")

    async def close(self):
        """Close browser and clean up."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        logger.info("Playwright browser session closed")

    async def login(self) -> tuple[bool, str]:
        """
        Log in to ruckus.cloud (redirects to auth.ruckuswireless.com).
        Returns (success, error_message).
        """
        page = self._page
        try:
            logger.info("Navigating to ruckus.cloud")
            await page.goto(RUCKUS_CLOUD_URL, wait_until="networkidle", timeout=NAVIGATION_TIMEOUT)
            logger.info(f"Landed on: {page.url}")

            # Check for MFA
            mfa_el = await page.query_selector(LOGIN_MFA)
            if mfa_el:
                return False, "MFA detected — not supported for automated exports."

            # Wait for login form
            await page.wait_for_selector(LOGIN_USERNAME, timeout=LOGIN_TIMEOUT)

            # Fill credentials
            logger.info("Filling login credentials")
            await page.fill(LOGIN_USERNAME, self.username)
            await page.fill(LOGIN_PASSWORD, self.password)

            # Submit and wait for redirect chain back to ruckus.cloud
            logger.info("Submitting login")
            try:
                async with page.expect_navigation(
                    url="**/ruckus.cloud/**",
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT,
                ):
                    await page.click(LOGIN_SUBMIT)
            except Exception as nav_err:
                logger.warning(f"Navigation wait: {nav_err}")
                await page.wait_for_timeout(3000)

            logger.info(f"Post-login URL: {page.url}")

            if "auth.ruckuswireless.com" in page.url:
                mfa_el = await page.query_selector(LOGIN_MFA)
                if mfa_el:
                    return False, "MFA required — not supported for automated exports."
                error_el = await page.query_selector(
                    '.error-message, .alert-danger, .alert-error, [role="alert"], .flash-error'
                )
                if error_el:
                    error_text = await error_el.text_content()
                    return False, f"Login failed: {error_text}"
                return False, f"Login may have failed — still on auth page: {page.url}"

            self._logged_in = True
            logger.info("Successfully logged in to ruckus.cloud")
            return True, ""

        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False, f"Login error: {str(e)}"

    async def _find_superset_frame(self) -> Optional[Frame]:
        """Find the Superset iframe among page frames."""
        for frame in self._page.frames:
            if "a4rc" in frame.url or "explorer" in frame.url:
                return frame
        return None

    async def _wait_for_superset_iframe(self, timeout_s: int = 45) -> Optional[Frame]:
        """Wait for the Superset iframe to appear and have dashboard table rows."""
        for _ in range(0, timeout_s, 3):
            await self._page.wait_for_timeout(3000)
            frame = await self._find_superset_frame()
            if frame:
                try:
                    # Wait for actual dashboard links (not just nav links)
                    has_dashboards = await frame.evaluate("""() => {
                        const links = document.querySelectorAll('a');
                        for (const a of links) {
                            if (a.href && a.href.includes('/superset/dashboard/')) return true;
                        }
                        return false;
                    }""")
                    if has_dashboards:
                        return frame
                except Exception:
                    pass
        return None

    async def _wait_for_dashboard_render(self, frame: Frame, timeout_s: int = 45) -> bool:
        """Wait for the Superset dashboard to finish rendering charts."""
        for _ in range(0, timeout_s, 3):
            await self._page.wait_for_timeout(3000)
            try:
                els = await frame.evaluate("() => document.querySelectorAll('*').length")
                if els > 400:
                    return True
            except Exception:
                return False
        return False

    async def _dismiss_popups(self, frame: Frame):
        """Dismiss any Pendo popups or modals."""
        for sel in [
            "button._pendo-close-guide",
            "button._pendo-button-primaryButton",
        ]:
            try:
                el = await frame.query_selector(sel)
                if el:
                    await el.click()
                    await self._page.wait_for_timeout(500)
            except Exception:
                pass
        # Also dismiss in main page
        for sel in [
            "button._pendo-close-guide",
            "button._pendo-button-primaryButton",
        ]:
            try:
                el = await self._page.query_selector(sel)
                if el:
                    await el.click()
                    await self._page.wait_for_timeout(500)
            except Exception:
                pass

    async def _click_menu_item(self, frame: Frame, text: str) -> bool:
        """Click a dropdown menu item by its text content inside the Superset frame."""
        items = await frame.query_selector_all(
            ".ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item"
        )
        for item in items:
            item_text = (await item.text_content() or "").strip()
            if item_text == text:
                await item.click()
                return True
        return False

    async def export_tenant_report(
        self,
        tenant_id: str,
        report_name: str,
    ) -> ExportResult:
        """
        Navigate to a tenant's Data Studio and export a report as CSV + PDF.
        Retries up to MAX_RETRIES_PER_TENANT times on failure.
        """
        start_time = datetime.utcnow()

        for attempt in range(MAX_RETRIES_PER_TENANT + 1):
            if attempt > 0:
                logger.info(f"Retry {attempt}/{MAX_RETRIES_PER_TENANT} for tenant {tenant_id}")
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)

            result = await self._try_export(tenant_id, report_name)
            result.duration_seconds = (datetime.utcnow() - start_time).total_seconds()

            if result.success:
                return result

            logger.warning(
                f"Export attempt {attempt + 1} failed for tenant {tenant_id}: {result.error}"
            )

        # All retries exhausted — capture debug screenshot
        result.screenshot_bytes = await self._capture_screenshot()
        return result

    async def _try_export(self, tenant_id: str, report_name: str) -> ExportResult:
        """Single attempt to export a report for a tenant."""
        page = self._page
        try:
            # ── Navigate to tenant Data Studio ──
            data_studio_url = f"{RUCKUS_CLOUD_URL}/{tenant_id}/t/dataStudio"
            logger.info(f"Navigating to Data Studio: {data_studio_url}")
            await page.goto(data_studio_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT)

            # ── Wait for Superset iframe ──
            logger.info("Waiting for Superset iframe")
            frame = await self._wait_for_superset_iframe()
            if not frame:
                return ExportResult(success=False, error="Superset iframe did not load")

            logger.info(f"Superset frame loaded: {frame.url[:80]}")

            # ── Find report by name in dashboard list ──
            logger.info(f"Looking for report: '{report_name}'")
            report_link = None
            for link in await frame.query_selector_all("a"):
                text = (await link.text_content() or "").strip()
                if text == report_name:
                    report_link = link
                    break

            if not report_link:
                # List available reports for debugging
                available = []
                for link in await frame.query_selector_all("a"):
                    text = (await link.text_content() or "").strip()
                    href = await link.get_attribute("href") or ""
                    if "/dashboard/" in href and text:
                        available.append(text)
                return ExportResult(
                    success=False,
                    error=f"Report '{report_name}' not found. Available: {available}",
                )

            # ── Click into the report ──
            logger.info(f"Opening report: '{report_name}'")
            await report_link.click()

            # Wait for dashboard to render
            if not await self._wait_for_dashboard_render(frame):
                # Re-find frame in case it changed
                frame = await self._find_superset_frame() or frame
                logger.warning("Dashboard may not have fully rendered")

            # Re-find frame after navigation
            frame = await self._find_superset_frame() or frame
            await self._dismiss_popups(frame)

            logger.info("Dashboard loaded, starting exports")

            # ── Export CSV (comes as a ZIP of individual chart CSVs) ──
            zip_bytes = await self._export_file(frame, "Export CSV", ".zip")
            csv_files = None
            if zip_bytes:
                csv_files = self._extract_csvs_from_zip(zip_bytes)
                logger.info(f"Extracted {len(csv_files)} CSV files from ZIP")

            # ── Export PDF ──
            pdf_bytes = await self._export_file(frame, "Export PDF", ".pdf")

            if not csv_files and not pdf_bytes:
                return ExportResult(
                    success=False,
                    error="Both CSV and PDF exports failed",
                )

            total_csv_bytes = sum(len(v) for v in csv_files.values()) if csv_files else 0
            logger.info(
                f"Export complete for tenant {tenant_id}: "
                f"{len(csv_files or {})} CSVs ({total_csv_bytes}B), "
                f"PDF={len(pdf_bytes) if pdf_bytes else 0}B"
            )
            return ExportResult(success=True, csv_files=csv_files, pdf_bytes=pdf_bytes)

        except Exception as e:
            logger.error(f"Export failed for tenant {tenant_id}: {e}")
            return ExportResult(success=False, error=str(e))

    async def _export_file(
        self, frame: Frame, menu_text: str, suffix: str
    ) -> Optional[bytes]:
        """
        Click the dashboard "..." menu, select an export option, and capture the download.
        Returns file bytes or None on failure.
        """
        page = self._page
        try:
            # Click the dashboard-level "..." menu (first DIV dropdown trigger)
            more_btn = await frame.query_selector(DASHBOARD_MORE_MENU)
            if not more_btn:
                more_btn = await frame.query_selector(DASHBOARD_MORE_MENU_FALLBACK)
            if not more_btn:
                logger.error(f"Dashboard '...' menu not found for {menu_text}")
                return None

            await more_btn.click()
            await page.wait_for_timeout(1000)

            # Click the export menu item and capture download
            async with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                clicked = await self._click_menu_item(frame, menu_text)
                if not clicked:
                    logger.error(f"Menu item '{menu_text}' not found in dropdown")
                    # Close menu
                    await page.keyboard.press("Escape")
                    return None

            download = await dl_info.value
            logger.info(f"Download started: {download.suggested_filename}")

            # Save to temp file, read bytes, clean up
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
                await download.save_as(tmp.name)
                file_bytes = Path(tmp.name).read_bytes()
            await download.delete()

            logger.info(f"{menu_text} download complete: {len(file_bytes)} bytes")
            return file_bytes

        except Exception as e:
            logger.error(f"{menu_text} export failed: {e}")
            # Close any open dropdown
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return None

    @staticmethod
    def _extract_csvs_from_zip(zip_bytes: bytes) -> Dict[str, bytes]:
        """Extract individual CSV files from the Superset export ZIP."""
        csv_files = {}
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".csv"):
                        csv_files[name] = zf.read(name)
                        logger.info(f"  Extracted: {name} ({len(csv_files[name])} bytes)")
        except zipfile.BadZipFile:
            # Not a ZIP — might be a raw CSV (shouldn't happen but handle it)
            logger.warning("Export was not a ZIP file, treating as raw CSV")
            csv_files["export.csv"] = zip_bytes
        return csv_files

    async def _capture_screenshot(self) -> Optional[bytes]:
        """Capture a screenshot of the current page state for debugging."""
        try:
            return await self._page.screenshot(full_page=True, type="png")
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return None


async def test_login(username: str, password: str) -> tuple[bool, str]:
    """
    Test web credentials by attempting login only (no export).
    Returns (success, error_message).
    """
    session = ScraperSession(username=username, password=password)
    try:
        await session.start()
        success, error = await session.login()
        return success, error
    except Exception as e:
        return False, f"Browser error: {str(e)}"
    finally:
        await session.close()
