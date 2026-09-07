"""Second pass on the local CADAM clone: sign in, reopen the conversation, send a
follow-up edit, wait for the agent, screenshot, then download the STL."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CADAM_URL", "http://localhost:3000/cad")
OUT = Path(__file__).resolve().parent / "shots"
EMAIL = sys.argv[1]
PASSWORD = os.environ.get("CADAM_PASSWORD", "Clone-Test-2026!")
EDITOR = sys.argv[2]
FOLLOWUP = os.environ.get("CADAM_FOLLOWUP", "Make the walls 3 mm thick, add three vertical ventilation slots on the back support, and round all outer vertical edges with a 4 mm radius")


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print("shot", p.name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:200]}") if m.type in ("error", "warning") else None)
    page.goto(BASE + "/signin", wait_until="networkidle", timeout=60000)
    time.sleep(1)
    if not page.locator("input[type='email']").count():
        for sel in ["a:has-text('email')", "button:has-text('email')", "a[href*='email']"]:
            if page.locator(sel).count():
                page.locator(sel).first.click(); time.sleep(1.5); break
    shot(page, "10-signin-form")
    page.locator("input[type='email']").first.fill(EMAIL)
    page.locator("input[type='password']").first.fill(PASSWORD)
    page.get_by_role("button", name="Sign In", exact=True).click()
    time.sleep(5)
    shot(page, "10b-after-signin-click")
    print("after click url:", page.url)
    print("page says:", page.inner_text("body")[:400].replace("\n", " | "))
    page.goto(EDITOR, wait_until="networkidle", timeout=60000)
    time.sleep(4)
    shot(page, "11-reopened")
    box = page.locator("textarea").first
    box.wait_for(timeout=30000)
    box.fill(FOLLOWUP)
    box.press("Enter")
    t0 = time.time()
    n_tool = 0
    while time.time() - t0 < 300:
        time.sleep(10)
        n = int((time.time() - t0) // 10)
        if n % 3 == 0:
            shot(page, f"12-iter-progress-{n:02d}")
        txt = page.inner_text("body")
        if txt.count("Done") >= 2 or ("Done" in txt.split(FOLLOWUP)[-1] if FOLLOWUP in txt else False):
            break
    print("iteration elapsed", round(time.time() - t0), "s")
    shot(page, "13-iter-final")
    Path(OUT / "13-iter-final-text.txt").write_text(page.inner_text("body"), encoding="utf-8")
    # STL download
    try:
        with page.expect_download(timeout=60000) as dl:
            page.locator("button:has-text('STL')").first.click()
        d = dl.value
        dest = OUT / (d.suggested_filename or "model.stl")
        d.save_as(str(dest))
        print("downloaded", dest.name, dest.stat().st_size, "bytes")
    except Exception as exc:  # noqa: BLE001
        print("STL download failed:", str(exc)[:200])
        shot(page, "14-stl-attempt")
    for l in logs[-10:]:
        print(l)
    browser.close()
