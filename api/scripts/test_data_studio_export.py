"""
Test the full Data Studio export flow end-to-end.
Uses the actual ScraperSession to login, navigate, and export.

Run inside the backend container:
    python scripts/test_data_studio_export.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.data_studio_scraper import ScraperSession

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s - %(message)s")

DEBUG_DIR = "/app/debug"
os.makedirs(DEBUG_DIR, exist_ok=True)

TENANT_ID = "1c396989c8d349bfa377d230c1be6d56"
REPORT_NAME = "[rt] AP Status"
USERNAME = os.environ.get("RUCKUS_WEB_USER", "")
PASSWORD = os.environ.get("RUCKUS_WEB_PASS", "")


async def main():
    if not USERNAME or not PASSWORD:
        print("Set RUCKUS_WEB_USER and RUCKUS_WEB_PASS environment variables")
        return

    session = ScraperSession(username=USERNAME, password=PASSWORD)
    try:
        await session.start()

        success, error = await session.login()
        if not success:
            print(f"LOGIN FAILED: {error}")
            return
        print("Login OK!\n")

        print(f"Exporting '{REPORT_NAME}' for tenant {TENANT_ID}...")
        result = await session.export_tenant_report(TENANT_ID, REPORT_NAME)

        csv_count = len(result.csv_files) if result.csv_files else 0
        csv_total = sum(len(v) for v in result.csv_files.values()) if result.csv_files else 0

        print(f"\n{'='*60}")
        print(f"  Success:  {result.success}")
        print(f"  Duration: {result.duration_seconds:.1f}s")
        print(f"  Error:    {result.error or '(none)'}")
        print(f"  CSVs:     {csv_count} files, {csv_total} bytes total")
        print(f"  PDF:      {len(result.pdf_bytes) if result.pdf_bytes else 0} bytes")
        print(f"{'='*60}")

        if result.csv_files:
            print(f"\nExtracted {csv_count} CSV files:")
            for name, data in result.csv_files.items():
                safe_name = name.replace("/", "_")
                path = f"{DEBUG_DIR}/test_export_{safe_name}"
                with open(path, "wb") as f:
                    f.write(data)
                lines = data.decode("utf-8", errors="replace").split("\n")
                row_count = len([l for l in lines if l.strip()]) - 1  # minus header
                print(f"\n  {name} ({len(data)} bytes, ~{row_count} rows)")
                for line in lines[:3]:
                    print(f"    {line[:120]}")
                print(f"  Saved to {path}")

        if result.pdf_bytes:
            with open(f"{DEBUG_DIR}/test_export.pdf", "wb") as f:
                f.write(result.pdf_bytes)
            print(f"\nPDF saved to {DEBUG_DIR}/test_export.pdf")

        if result.screenshot_bytes:
            with open(f"{DEBUG_DIR}/test_export_error.png", "wb") as f:
                f.write(result.screenshot_bytes)
            print(f"\nError screenshot saved to {DEBUG_DIR}/test_export_error.png")

    finally:
        await session.close()

    print("\nDone!")


asyncio.run(main())
