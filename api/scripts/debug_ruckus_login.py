"""
Debug script: Navigate to ruckus.cloud and capture a screenshot of the login page.
Run inside the backend container:
    python scripts/debug_ruckus_login.py
"""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        print("Navigating to ruckus.cloud...")
        await page.goto("https://ruckus.cloud", wait_until="networkidle", timeout=30000)

        # Capture the current URL (may redirect to SSO)
        print(f"Current URL: {page.url}")

        # Take screenshot
        await page.screenshot(path="/app/debug_login_page.png", full_page=True)
        print("Screenshot saved to /app/debug_login_page.png")

        # Dump all input elements on the page
        inputs = await page.query_selector_all("input")
        print(f"\nFound {len(inputs)} input elements:")
        for inp in inputs:
            name = await inp.get_attribute("name") or ""
            type_ = await inp.get_attribute("type") or ""
            id_ = await inp.get_attribute("id") or ""
            placeholder = await inp.get_attribute("placeholder") or ""
            print(f"  <input name='{name}' type='{type_}' id='{id_}' placeholder='{placeholder}'>")

        # Dump all buttons
        buttons = await page.query_selector_all("button")
        print(f"\nFound {len(buttons)} buttons:")
        for btn in buttons:
            text = await btn.text_content()
            type_ = await btn.get_attribute("type") or ""
            print(f"  <button type='{type_}'>{text.strip()}</button>")

        # Dump iframes (SSO login often uses iframes)
        frames = page.frames
        print(f"\nFound {len(frames)} frames:")
        for frame in frames:
            print(f"  Frame: {frame.url}")

        await browser.close()


asyncio.run(main())
