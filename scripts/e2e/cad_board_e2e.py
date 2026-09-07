"""Synqit board -> Studio: sign in to Synqit (cookie), open the studio with ?project=,
expect the board brief auto-inserted, send the prompt, wait for the build, screenshot."""
import time, re
from playwright.sync_api import sync_playwright
SYNQIT="http://localhost:8000/api"; CAD="http://localhost:3000/cad"
with sync_playwright() as p:
    b=p.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader","--enable-unsafe-swiftshader"])
    ctx=b.new_context(viewport={"width":1440,"height":900}); pg=ctx.new_page()
    r=ctx.request.post(SYNQIT+"/auth/login", data={"email":"sso-test-1788807141@local.dev","password":"Sso-Test-2026!"}); print("synqit login", r.status)
    pg.goto(CAD+"/?project=d1-rain-alert-678c&embed=1", wait_until="networkidle"); time.sleep(6)
    ta=pg.locator("textarea").first; val=ta.input_value()
    print("auto-inserted:", "Synqit board" in val, "| prelude present:", "module synqit_board()" in val, "| chars:", len(val), "| sidebar hidden:", pg.locator("button:has-text('New Creation')").count()==0)
    pg.screenshot(path="shots/40-studio-embed-board-inserted.png")
    ta.fill("A two-part snap-fit enclosure for this board: 2 mm walls, standoffs on the mounting holes, openings for every connector, a removable lid with a 1 mm lip, and 4 mm rounded outer corners.\n\n"+val)
    ta.press("Enter"); t0=time.time(); last=""
    while time.time()-t0 < 420:
        time.sleep(10); body=pg.inner_text("body")
        if "Done" in body.split("rounded outer corners")[-1]: break
    print("elapsed", round(time.time()-t0),"s")
    pg.screenshot(path="shots/41-studio-enclosure.png")
    body=pg.inner_text("body"); m=re.findall(r"Model facts[^\n]{0,160}", body); print("facts shown in UI:", m[:2])
    print("assistant tail:", body.split("rounded outer corners")[-1][:400].replace("\n"," | "))
    b.close()
