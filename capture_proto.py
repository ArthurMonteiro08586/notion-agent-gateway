# Capture FULL agent chat protocol: request + response stream endpoints for createAgentThread flow
import asyncio, json, sys
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"

async def main(state):
    reqs, resps = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=UA, viewport={"width":1600,"height":1000}, storage_state=state)
        pg = await ctx.new_page()

        def interesting(u):
            return ("api/v3" in u) and not any(x in u for x in ["etClient","recordError","getSignedFileUrl","syncRecordValues","warmAgent"])

        async def on_request(req):
            if interesting(req.url):
                reqs.append({"url": req.url, "method": req.method, "body": (req.post_data or "")[:3000],
                             "headers": {k:v for k,v in req.headers.items() if k.lower() in ("content-type","notion-client-version","x-notion-client-version","cookie")[:3] or "notion" in k.lower()}})
        async def on_response(resp):
            if interesting(resp.url):
                try:
                    body = await resp.text()
                    resps.append({"url": resp.url, "status": resp.status, "ctype": resp.headers.get("content-type",""), "body": body[:3000]})
                except Exception:
                    resps.append({"url": resp.url, "status": resp.status, "body": "<no-body>"})
        pg.on("request", on_request)
        pg.on("response", on_response)

        await pg.goto("https://app.notion.com/chat", wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(8000)
        for sel in ["button:has-text('Accept all')","button:has-text('Only allow necessary')"]:
            try:
                if await pg.locator(sel).first.is_visible(timeout=2000):
                    await pg.locator(sel).first.click(); break
            except Exception: pass
        await pg.wait_for_timeout(2000)
        try:
            await pg.locator('[aria-label="Choose AI model"]').first.click(timeout=8000)
            await pg.wait_for_timeout(1500)
            await pg.locator('[role="menuitemradio"]:has-text("Opus"), [role="menuitem"]:has-text("Opus")').first.click(timeout=5000, force=True)
            await pg.wait_for_timeout(800)
        except Exception as e:
            print("pick fail:", str(e)[:60])
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(1000)
        box = pg.locator("[contenteditable='true']").first
        await box.click(timeout=5000)
        await box.fill("Reply with exactly: PROTOCOL_OK")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(25000)
        await b.close()

    json.dump({"requests": reqs, "responses": resps}, open("proto_capture.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print("REQUESTS:", len(reqs))
    for r in reqs:
        print(" REQ", r["method"], r["url"][:90])
    print("RESPONSES:", len(resps))
    for r in resps:
        print(" RES", r["status"], r["ctype"][:30], r["url"][:90])

asyncio.run(main(sys.argv[1]))
