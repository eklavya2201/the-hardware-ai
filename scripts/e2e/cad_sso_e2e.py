"""SSO handoff test: sign in to the LOCAL Synqit backend (cookie), then open the
handoff URL and expect to land in Synqit CAD signed in as the same user."""
import time, json
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent / "shots"; OUT.mkdir(exist_ok=True)
SYNQIT = "http://localhost:8000/api"; CAD = "http://localhost:3000/cad"
EMAIL = f"sso-test-{int(time.time())}@local.dev"; PW = "Sso-Test-2026!"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}); page = ctx.new_page()
    errs = []; page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    # rebranded public pages first
    page.goto(CAD + "/", wait_until="networkidle"); time.sleep(2); page.screenshot(path=str(OUT / "20-synqit-landing.png"))
    page.goto(CAD + "/signin", wait_until="networkidle"); time.sleep(3); page.screenshot(path=str(OUT / "21-synqit-signin.png"))
    print("signin buttons:", page.locator("button, a").all_inner_texts()[:12])
    # 1. create + sign in a Synqit user through its own API (cookie lands in this context)
    r = ctx.request.post(SYNQIT + "/auth/signup", data={"name": "SSO Tester", "email": EMAIL, "password": PW})
    print("synqit signup:", r.status, r.text()[:120])
    me = ctx.request.get(SYNQIT + "/auth/me"); print("synqit /auth/me:", me.status, me.text()[:120])
    # 2. handoff
    page.goto(SYNQIT + "/auth/handoff?return_to=" + "http%3A%2F%2Flocalhost%3A3000%2Fcad%2Fsso", wait_until="networkidle")
    time.sleep(6)
    print("after handoff url:", page.url)
    page.screenshot(path=str(OUT / "22-after-sso.png"))
    body = page.inner_text("body")
    print("signed in as sso user:", EMAIL in body, "| creations menu:", "Creations" in body)
    print("console errors:", errs[-5:])
    b.close()
