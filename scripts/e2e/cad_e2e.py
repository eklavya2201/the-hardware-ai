"""Drive the local Synqit CAD end to end with headless Chrome and save screenshots.

Steps: open app -> sign up (local Supabase, throwaway account) -> submit a prompt
-> wait for the agent loop to finish -> screenshot the editor with the model.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("CAD_URL", "http://localhost:3000/cad")
OUT = Path(__file__).resolve().parent / "shots"
OUT.mkdir(exist_ok=True)
EMAIL = os.environ.get("CAD_EMAIL", f"clone-test-{int(time.time())}@local.dev")
PASSWORD = os.environ.get("CAD_PASSWORD", "Clone-Test-2026!")
PROMPT = os.environ.get("CAD_PROMPT", "A phone stand for a 70 mm wide phone, 15 degree tilt, with a cable slot at the back")
MODE = sys.argv[1] if len(sys.argv) > 1 else "explore"


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print("shot", p.name, page.url)


def dump(page, name):
    els = page.evaluate(
        """() => Array.from(document.querySelectorAll('a,button,input,textarea,select,[role=button]'))
            .filter(e => e.offsetParent !== null)
            .slice(0, 80)
            .map(e => ({tag: e.tagName, text: (e.innerText||e.value||e.placeholder||'').trim().slice(0,60),
                        href: e.getAttribute('href'), type: e.getAttribute('type'), name: e.getAttribute('name'),
                        ph: e.getAttribute('placeholder'), id: e.id}))"""
    )
    (OUT / f"{name}.json").write_text(json.dumps(els, indent=1))
    for e in els:
        print("  ", e["tag"], repr(e["text"])[:50], e.get("href") or "", e.get("ph") or "", e.get("name") or "")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    logs = []
    page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:300]}"))
    page.on("pageerror", lambda e: logs.append(f"[pageerror] {str(e)[:300]}"))
    page.goto(BASE, wait_until="networkidle", timeout=90000)
    time.sleep(2)
    shot(page, "01-landing")
    dump(page, "01-landing")

    if MODE == "explore":
        for l in logs[-15:]:
            print(l)
        browser.close()
        sys.exit(0)

    # ---- sign up ----
    page.goto(BASE + "/signup", wait_until="networkidle", timeout=60000)
    time.sleep(1)
    shot(page, "02-signup")
    dump(page, "02-signup")
    # prefer an email-based signup route if the page offers it
    if page.locator("a[href*='signup-email'], a:has-text('email')").count():
        page.locator("a[href*='signup-email'], a:has-text('email')").first.click()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        shot(page, "03-signup-email")
        dump(page, "03-signup-email")
    page.locator("input[type='email'], input[name='email']").first.fill(EMAIL)
    pw = page.locator("input[type='password']")
    pw.first.fill(PASSWORD)
    if pw.count() > 1:
        pw.nth(1).fill(PASSWORD)
    for sel in ["input[name*='name']", "input[placeholder*='name' i]"]:
        if page.locator(sel).count():
            page.locator(sel).first.fill("Clone Tester")
            break
    shot(page, "04-signup-filled")
    page.locator("button[type='submit'], button:has-text('Sign up'), button:has-text('Create')").first.click()
    time.sleep(4)
    page.wait_for_load_state("networkidle")
    shot(page, "05-after-signup")
    dump(page, "05-after-signup")
    print("url after signup:", page.url)

    # ---- local Supabase asks for email confirmation: confirm in the DB, then sign in ----
    if "confirm-email" in page.url:
        import subprocess
        subprocess.run(["docker", "exec", "supabase_db_synqit", "psql", "-U", "postgres", "-d", "postgres", "-Atc",
                        f"update auth.users set email_confirmed_at=coalesce(email_confirmed_at,now()) where email='{EMAIL}'"],
                       check=True, capture_output=True)
        page.goto(BASE + "/signin", wait_until="networkidle", timeout=60000)
        time.sleep(1.5)
        dump(page, "05b-signin")
        already_in = page.locator("button:has-text('Creations')").count() > 0
        if already_in:
            print("session already active after confirmation; skipping sign-in form")
        if not already_in and not page.locator("input[type='email'], input[name='email']").count():
            # Google button first; reveal the email form
            for sel in ["a:has-text('email')", "button:has-text('email')", "a[href*='email']"]:
                if page.locator(sel).count():
                    page.locator(sel).first.click()
                    time.sleep(1.5)
                    break
            dump(page, "05b2-signin-email")
        if not already_in:
            page.locator("input[type='email'], input[name='email']").first.fill(EMAIL)
            page.locator("input[type='password']").first.fill(PASSWORD)
            page.locator("button[type='submit'], button:has-text('Sign in'), button:has-text('Sign In')").first.click()
            time.sleep(4)
            page.wait_for_load_state("networkidle")
        shot(page, "05c-after-signin")
        print("url after signin:", page.url)

    # ---- prompt ----
    if "/signin" in page.url or "/signup" in page.url or "confirm-email" in page.url:
        print("still on auth page; console:")
        for l in logs[-20:]:
            print(l)
        browser.close()
        sys.exit(2)
    box = page.locator("textarea").first
    box.wait_for(timeout=30000)
    box.fill(PROMPT)
    shot(page, "06-prompt-typed")
    box.press("Enter")
    time.sleep(3)
    shot(page, "07-submitted")
    print("url:", page.url)
    # wait for the agent to finish: poll up to 6 minutes for the send box to be enabled again + a canvas
    t0 = time.time()
    last = ""
    while time.time() - t0 < 360:
        time.sleep(10)
        body = page.inner_text("body")[-1500:]
        if body != last:
            last = body
        n = int((time.time() - t0) // 10)
        if n % 3 == 0:
            shot(page, f"08-progress-{n:02d}")
        done = page.evaluate("""() => { const ta = document.querySelector('textarea'); const busy = document.querySelector('[data-loading], .animate-pulse, [aria-busy=true]'); return {ta: ta && !ta.disabled, busy: !!busy} }""")
        if "Done" in body or (done["ta"] and not done["busy"] and time.time() - t0 > 40):
            break
    shot(page, "09-final")
    Path(OUT / "09-final-text.txt").write_text(page.inner_text("body"))
    print("elapsed", round(time.time() - t0), "s")
    print("--- last console lines")
    for l in logs[-25:]:
        print(l)
    browser.close()
