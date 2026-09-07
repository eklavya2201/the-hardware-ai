import time
from playwright.sync_api import sync_playwright
CAD="http://localhost:3000/cad"; SYNQIT="http://localhost:8000/api"
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader","--enable-unsafe-swiftshader"])
    ctx=b.new_context(viewport={"width":1440,"height":900}); pg=ctx.new_page()
    def broken(): return pg.evaluate("() => Array.from(document.images).filter(i=>i.complete && i.naturalWidth===0).map(i=>i.getAttribute('src'))")
    pg.goto(CAD+"/", wait_until="networkidle"); time.sleep(3); pg.screenshot(path="shots/23-brand-landing.png"); print("landing broken imgs:", broken())
    pg.goto(CAD+"/signin", wait_until="networkidle"); time.sleep(3); pg.screenshot(path="shots/24-brand-signin.png"); print("signin broken imgs:", broken())
    ctx.request.post(SYNQIT+"/auth/login", data={"email":"sso-test-1788807141@local.dev","password":"Sso-Test-2026!"})
    pg.goto(SYNQIT+"/auth/handoff?return_to=http%3A%2F%2Flocalhost%3A3000%2Fcad%2Fsso", wait_until="networkidle"); time.sleep(6)
    pg.screenshot(path="shots/25-brand-signed-in.png"); print("signed-in url:", pg.url, "broken imgs:", broken())
    pg.goto(CAD+"/terms", wait_until="networkidle"); time.sleep(2); pg.screenshot(path="shots/26-brand-terms.png")
    b.close()
