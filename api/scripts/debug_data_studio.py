"""
Debug script v13: Discover chart export menus in Superset dashboard.
Click dropdown triggers on individual charts to find CSV export.

Run inside the backend container:
    python scripts/debug_data_studio.py
"""
import asyncio
import os
from playwright.async_api import async_playwright

TENANT_ID = "4566d293776b4059a824edde6661da50"
REPORT_NAME = "Access Points"
USERNAME = os.environ.get("RUCKUS_WEB_USER", "")
PASSWORD = os.environ.get("RUCKUS_WEB_PASS", "")


async def login_and_goto_report(page):
    """Login and navigate to the target Superset dashboard."""
    # Login
    print("Logging in...")
    await page.goto("https://ruckus.cloud", wait_until="networkidle", timeout=30000)
    await page.wait_for_selector("#user_username", timeout=15000)
    await page.fill("#user_username", USERNAME)
    await page.fill("#user_password", PASSWORD)
    async with page.expect_navigation(url="**/ruckus.cloud/**", wait_until="domcontentloaded", timeout=30000):
        await page.click("#ruckus-one-submit-button")
    print(f"Login OK — {page.url}")

    # Chunk interception
    async def handle_chunk(route):
        try:
            resp = await route.fetch()
            await route.fulfill(response=resp)
        except Exception:
            await route.abort()
    await page.route("**/tenant/t/**", handle_chunk)

    # Navigate to Data Studio
    target = f"https://ruckus.cloud/{TENANT_ID}/t/dataStudio"
    print(f"Navigating to: {target}")
    await page.goto(target, wait_until="domcontentloaded", timeout=60000)

    # Wait for iframe
    print("Waiting for Superset iframe...")
    for sec in range(3, 30, 3):
        await page.wait_for_timeout(3000)
        if len(page.frames) >= 2:
            break

    # Find Superset frame
    superset = None
    for frame in page.frames:
        if "a4rc" in frame.url or "explorer" in frame.url:
            superset = frame
            break
    if not superset:
        print("ERROR: Superset iframe not found")
        return None

    # Wait for list
    await page.wait_for_timeout(3000)

    # Click report
    print(f"Clicking '{REPORT_NAME}'...")
    link = None
    for a in await superset.query_selector_all("a"):
        if (await a.text_content() or "").strip() == REPORT_NAME:
            link = a
            break
    if not link:
        print(f"ERROR: '{REPORT_NAME}' not found")
        return None
    await link.click()

    # Wait for dashboard to load
    print("Waiting for dashboard...")
    for sec in range(3, 45, 3):
        await page.wait_for_timeout(3000)
        try:
            els = await superset.evaluate("() => document.querySelectorAll('*').length")
            print(f"  {sec}s: {els} elements")
            if els > 400:
                break
        except Exception:
            break

    await page.wait_for_timeout(3000)

    # Re-find frame
    for frame in page.frames:
        if "a4rc" in frame.url:
            superset = frame
    return superset


async def main():
    if not USERNAME or not PASSWORD:
        print("Set RUCKUS_WEB_USER and RUCKUS_WEB_PASS environment variables")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-gpu", "--disable-dev-shm-usage",
                   "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        superset = await login_and_goto_report(page)
        if not superset:
            await browser.close()
            return

        # ── Look for Superset data-test attributes ──
        print(f"\n{'='*60}")
        print("  SUPERSET DATA-TEST ATTRIBUTES")
        print(f"{'='*60}")
        test_attrs = await superset.evaluate("""() => {
            const els = document.querySelectorAll('[data-test]');
            return Array.from(els).map(e => ({
                test: e.getAttribute('data-test'),
                tag: e.tagName,
                cls: (e.className || '').toString().substring(0, 50),
                text: (e.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
            }));
        }""")
        print(f"Found {len(test_attrs)} elements with data-test:")
        seen = set()
        for el in test_attrs:
            key = el['test']
            if key not in seen:
                seen.add(key)
                text = el['text'][:60] if el['text'] else ''
                print(f"  [{el['tag']}] data-test='{key}' text='{text}'")

        # ── Look for chart containers ──
        print(f"\n{'='*60}")
        print("  CHART CONTAINERS (slice headers)")
        print(f"{'='*60}")
        charts = await superset.evaluate("""() => {
            // Superset uses .dashboard-chart, .slice_container, or [data-test="chart-container"]
            const sels = [
                '[data-test="chart-container"]',
                '[data-test*="slice"]',
                '.dashboard-chart',
                '.slice_container',
                '.chart-container',
                '[class*="ChartHolder"]',
                '[class*="chart-slice"]',
                '[class*="DashboardChart"]',
            ];
            const results = {};
            for (const sel of sels) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    results[sel] = els.length;
                }
            }
            return results;
        }""")
        print("Chart container selectors found:")
        for sel, count in charts.items():
            print(f"  {sel}: {count}")

        # ── Find all dropdown triggers with context ──
        print(f"\n{'='*60}")
        print("  DROPDOWN TRIGGERS IN CONTEXT")
        print(f"{'='*60}")
        triggers = await superset.evaluate("""() => {
            const triggers = document.querySelectorAll('.ant-dropdown-trigger');
            return Array.from(triggers).map((t, i) => {
                // Get parent/sibling context
                const parent = t.parentElement;
                const grandparent = parent?.parentElement;
                const nearestText = (parent?.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 100);
                const dataTest = t.getAttribute('data-test')
                    || parent?.getAttribute('data-test')
                    || grandparent?.getAttribute('data-test') || '';
                return {
                    idx: i,
                    tag: t.tagName,
                    cls: (t.className || '').substring(0, 60),
                    text: (t.textContent || '').trim().substring(0, 40),
                    dataTest,
                    parentTag: parent?.tagName || '',
                    parentCls: (parent?.className || '').substring(0, 60),
                    nearestText: nearestText.substring(0, 80),
                };
            });
        }""")
        print(f"Found {len(triggers)} dropdown triggers:")
        for t in triggers:
            print(f"  [{t['idx']}] <{t['tag']} class='{t['cls'][:40]}'>")
            print(f"       text='{t['text']}' data-test='{t['dataTest']}'")
            print(f"       parent=<{t['parentTag']} class='{t['parentCls'][:40]}'>")
            print(f"       context='{t['nearestText'][:60]}'")

        # ── Click first dropdown trigger and capture menu ──
        print(f"\n{'='*60}")
        print("  CLICKING DROPDOWN TRIGGERS TO FIND EXPORT")
        print(f"{'='*60}")

        dropdown_els = await superset.query_selector_all(".ant-dropdown-trigger")
        for i, trigger in enumerate(dropdown_els[:5]):  # Try first 5
            print(f"\n--- Clicking trigger [{i}] ---")
            try:
                await trigger.click()
                await page.wait_for_timeout(1500)

                # Check for any visible dropdown/popover menu
                # Superset renders dropdowns in the body, not inside the trigger
                menu_items = await superset.evaluate("""() => {
                    // Check various dropdown/popover patterns
                    const sels = [
                        '.ant-dropdown:not(.ant-dropdown-hidden) .ant-dropdown-menu-item',
                        '.ant-dropdown:not(.ant-dropdown-hidden) li',
                        '.ant-popover:not(.ant-popover-hidden) .ant-popover-inner-content *',
                        '[class*="DropdownMenu"] [class*="MenuItem"]',
                        '[role="menu"] [role="menuitem"]',
                        '.ant-menu-vertical .ant-menu-item',
                    ];
                    const results = {};
                    for (const sel of sels) {
                        const els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            results[sel] = Array.from(els).map(e => ({
                                text: (e.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 100),
                                cls: (e.className || '').substring(0, 50),
                                dataTest: e.getAttribute('data-test') || '',
                            }));
                        }
                    }
                    return results;
                }""")

                if menu_items:
                    for sel, items in menu_items.items():
                        print(f"  {sel} ({len(items)}):")
                        for item in items:
                            print(f"    text='{item['text']}' cls='{item['cls'][:30]}' data-test='{item['dataTest']}'")
                else:
                    print("  No dropdown menu appeared")

                # Close the dropdown by pressing Escape
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)

            except Exception as e:
                print(f"  Error: {e}")

        # ── Also check for "..." buttons (kebab/more menus) ──
        print(f"\n{'='*60}")
        print("  KEBAB/MORE MENU BUTTONS")
        print(f"{'='*60}")
        kebabs = await superset.evaluate("""() => {
            const sels = [
                '[data-test*="more"]',
                '[aria-label*="more"]', '[aria-label*="More"]',
                '[aria-label*="action"]', '[aria-label*="Action"]',
                '[data-test*="header-action"]',
                '[class*="SliceHeaderControl"]',
                '[class*="header-control"]',
            ];
            const results = {};
            for (const sel of sels) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    results[sel] = Array.from(els).map(e => ({
                        tag: e.tagName,
                        text: (e.textContent || '').trim().substring(0, 60),
                        cls: (e.className || '').substring(0, 60),
                        dataTest: e.getAttribute('data-test') || '',
                    }));
                }
            }
            return results;
        }""")
        if kebabs:
            for sel, els in kebabs.items():
                print(f"\n{sel} ({len(els)}):")
                for el in els:
                    print(f"  <{el['tag']} data-test='{el['dataTest']}' class='{el['cls'][:40]}'>{el['text'][:40]}")
        else:
            print("No kebab/more buttons found")

        # ── Screenshot ──
        await page.screenshot(path="/app/debug_ds_export.png", full_page=True)
        print(f"\nScreenshot: /app/debug_ds_export.png")

        await browser.close()
        print("\nDone!")


asyncio.run(main())
