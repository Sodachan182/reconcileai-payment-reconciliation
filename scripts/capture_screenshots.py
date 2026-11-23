"""Browser QA + README screenshots for ReconcileAI (Playwright, Chromium).

Starts a private Streamlit instance on port 8502 with a temporary database, walks
through the main states, asserts key behaviour, and saves screenshots.

    .venv\\Scripts\\python.exe scripts\\capture_screenshots.py [--out assets/screenshots]

Prints a PASS/FAIL line per check and exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PORT = 8502
URL = f"http://127.0.0.1:{PORT}"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}", flush=True)


def wait_port(port: int, timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    raise RuntimeError("Streamlit did not start")


def settle(page: Page, extra_ms: int = 700) -> None:
    page.wait_for_selector('[data-testid="stApp"]', timeout=30000)
    page.wait_for_timeout(400)
    with contextlib.suppress(Exception):
        page.wait_for_function(
            "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=30000)
    page.wait_for_timeout(extra_ms)


def nav(page: Page, label: str) -> None:
    page.locator('[data-testid="stSidebarNavLink"]', has_text=label).first.click()
    settle(page)


def no_hscroll(page: Page) -> bool:
    return page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def click_first_row(page: Page, index: int = 0) -> None:
    grid = page.locator('[data-testid="stDataFrame"]').nth(index)
    box = grid.bounding_box()
    page.mouse.click(box["x"] + 14, box["y"] + 35 + 17)
    settle(page)


def tall_shot(page: Page, path: Path) -> None:
    """Full-height screenshot (Streamlit scrolls inside a container, not the document)."""
    vp = page.viewport_size
    h = page.evaluate(
        "() => Math.max(...[...document.querySelectorAll("
        "'[data-testid=stMain], [data-testid=stMainBlockContainer]')].map(e => e.scrollHeight), 900)")
    page.set_viewport_size({"width": vp["width"], "height": min(int(h) + 40, 4000)})
    page.wait_for_timeout(700)
    page.screenshot(path=str(path))
    page.set_viewport_size(vp)
    page.wait_for_timeout(300)


def search(page: Page, text: str) -> None:
    box = page.get_by_placeholder("ID, customer or description")
    box.fill(text)
    box.press("Enter")
    settle(page)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "assets" / "screenshots"))
    ap.add_argument("--qa-out", default=None, help="Where to save non-README viewport screenshots")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    qa_out = Path(args.qa_out) if args.qa_out else Path(tempfile.mkdtemp(prefix="reconcileai_qa_"))
    qa_out.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="reconcileai_ui_"))
    env = {**os.environ, "RECONCILEAI_DB": str(tmp / "qa.db"), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
         "--server.port", str(PORT), "--server.address", "127.0.0.1"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    console_errors: list[str] = []
    try:
        wait_port(PORT)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark",
                                      accept_downloads=True, device_scale_factor=1)
            page = ctx.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

            # 1. Empty workspace
            page.goto(URL)
            settle(page, 1500)
            check("empty state shows load-demo action", page.get_by_role("button", name="Load demo dataset").count() == 1)
            page.screenshot(path=str(out / "overview_empty.png"))

            # 2. Invalid spreadsheet on the import page
            nav(page, "Import data")
            bad = tmp / "bad_orders.csv"
            bad.write_text("Order ID,Customer Name,Amount,Order Date\nORD-1,Nguyễn Văn An,abc,2026-11-01\n"
                           "ORD-2,Trần Thị Bình,1.250.000,31/02/2026\nORD-3,,500000,2026-11-02\n"
                           "ORD-4,Lê Văn Cường,-5000,2026-11-02\nORD-5,Phạm Thu Hà,1250000.5,2026-11-03\n",
                           encoding="utf-8")
            page.locator('input[type="file"]').first.set_input_files(str(bad))
            settle(page, 1500)
            body = page.inner_text("body")
            check("invalid file: every bad row rejected and import blocked",
                  "5 row(s) will not be imported" in body and "No valid order rows were found" in body
                  and "Import " not in page.locator("button").all_inner_texts().__str__(), "")
            tall_shot(page, out / "import_validation.png")

            # 3. Valid upload, mapping suggestions
            page.locator('input[type="file"]').first.set_input_files(str(ROOT / "demo" / "data" / "orders.csv"))
            settle(page, 1500)
            body = page.inner_text("body")
            check("Vietnamese headers auto-mapped and validated", "Import 300 rows" in body, "")
            page.get_by_role("button", name="Import 300 rows").click()
            settle(page, 1000)
            check("orders import confirmed", "Imported 300 new rows" in page.inner_text("body"))
            page.get_by_role("button", name="Import 300 rows").click()
            settle(page, 1000)
            check("re-import is idempotent", "Imported 0 new rows; 300 were already" in page.inner_text("body"))

            # 4. Load demo dataset (adds the bank statement; orders already present are unchanged)
            page.get_by_role("button", name="Load demo dataset").click()
            settle(page, 3000)
            nav(page, "Overview")
            page.wait_for_timeout(5000)  # let the confirmation toast disappear before capturing
            body = page.inner_text("body")
            check("overview shows reconciliation equation", "Confirmed payments applied" in body and "Unresolved" in body)
            check("order statuses survived the UI import (285 in scope, 15 excluded)",
                  "Expected from 285 orders" in body and "15 cancelled or refunded" in body)
            check("overview has no horizontal scroll at 1440", no_hscroll(page))
            page.screenshot(path=str(out / "overview.png"))
            tall_shot(page, out / "overview_full.png")

            # 5. Reconciliation workspace
            nav(page, "Reconciliation")
            check("reconciliation tables rendered", page.locator('[data-testid="stDataFrame"]').count() >= 2)
            page.screenshot(path=str(out / "reconciliation.png"))
            search(page, "ORD2026-00040")
            click_first_row(page, 0)
            body = page.inner_text("body")
            check("ambiguous candidate shows competing candidates", "competing candidates" in body or "Competes with" in body)
            tall_shot(page, out / "reconciliation_ambiguous.png")
            search(page, "ORD2026-00013")
            click_first_row(page, 0)
            check("amount mismatch evidence shown", "Reference, amount differs" in page.inner_text("body"))
            tall_shot(page, out / "reconciliation_mismatch.png")
            page.get_by_placeholder("Why this decision?").first.fill("Customer confirmed short payment")
            page.get_by_role("button", name="Confirm").first.click()
            settle(page, 1200)
            body = page.inner_text("body")
            check("manual confirmation updates status", "Confirmed by reviewer" in body and "Partial payment" in body)
            tall_shot(page, out / "reconciliation_confirmed.png")

            # 6. Exceptions
            page.wait_for_timeout(5000)  # let the confirmation toast disappear
            nav(page, "Exceptions")
            check("exceptions table rendered", page.locator('[data-testid="stDataFrame"]').count() >= 1)
            page.screenshot(path=str(out / "exceptions.png"))

            # 7. Reports + real XLSX download
            nav(page, "Reports")
            body = page.inner_text("body")
            check("all integrity checks pass", "✗" not in body and body.count("✓") >= 9)
            with page.expect_download() as dl:
                page.get_by_role("button", name="Download Excel (.xlsx)").click()
            path = dl.value.path()
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(Path(path).read_bytes()))
            check("XLSX export has the 8 report sheets + integrity",
                  wb.sheetnames[:8] == ["Summary", "Matched", "Unmatched Orders", "Unmatched Transactions",
                                        "Amount Mismatches", "Duplicate Candidates", "Review Log", "Configuration"],
                  ", ".join(wb.sheetnames))
            check("review log contains the confirmation", wb["Review Log"].max_row >= 2)
            page.screenshot(path=str(out / "reports.png"))

            nav(page, "Settings")
            page.screenshot(path=str(out / "settings.png"))

            # 8. Viewports and light theme
            nav(page, "Overview")
            for w, h in [(1920, 1080), (1366, 768), (1024, 768), (390, 844)]:
                page.set_viewport_size({"width": w, "height": h})
                settle(page, 800)
                check(f"overview no page-level horizontal scroll at {w}x{h}", no_hscroll(page))
                page.screenshot(path=str(qa_out / f"overview_{w}x{h}.png"))
            page.set_viewport_size({"width": 1366, "height": 768})
            settle(page, 500)
            nav(page, "Reconciliation")
            for w, h in [(1366, 768), (390, 844)]:
                page.set_viewport_size({"width": w, "height": h})
                settle(page, 800)
                check(f"reconciliation no page-level horizontal scroll at {w}x{h}", no_hscroll(page))
                page.screenshot(path=str(qa_out / f"reconciliation_{w}x{h}.png"))
            ctx.close()

            light = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
            lp = light.new_page()
            lp.goto(URL)
            settle(lp, 2500)
            check("light theme renders", "Confirmed payments applied" in lp.inner_text("body"))
            lp.screenshot(path=str(out / "overview_light.png"))
            light.close()
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    real_errors = [e for e in console_errors if "favicon" not in e.lower()]
    check("no browser console errors", not real_errors, "; ".join(real_errors[:3]))
    print(f"\nScreenshots: {out}\nViewport QA screenshots: {qa_out}")
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
