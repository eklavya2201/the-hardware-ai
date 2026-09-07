import sys, time
from playwright.sync_api import sync_playwright
BASE="http://localhost:3000/cadam"; EMAIL=sys.argv[1]; EDITOR=sys.argv[2]; OUT=sys.argv[3]
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader","--enable-unsafe-swiftshader"])
    pg=b.new_context(viewport={"width":1440,"height":900}).new_page()
    pg.goto(BASE+"/signin", wait_until="networkidle"); time.sleep(1)
    pg.locator("input[type='email']").first.fill(EMAIL); pg.locator("input[type='password']").first.fill("Clone-Test-2026!")
    pg.get_by_role("button", name="Sign In", exact=True).click(); time.sleep(4)
    pg.goto(EDITOR, wait_until="networkidle"); time.sleep(15)
    pg.screenshot(path=OUT); print("saved", OUT)
    b.close()
