# Capture internal model IDs: send a message in Notion chat UI, log request bodies to Notion API
import asyncio, json, sys
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"

async def main(state, model_label="Opus"):
    captured = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(user_agent=UA, viewport={"width":1600,"height":1000}, storage_state=state)
        pg = await ctx.new_page()

        async def on_request(req):
            if "notion" in req.url and req.method == "POST":
                try:
                    pd = req.post_data or ""
                    if "model" in pd.lower() or "aimodel" in pd.lower() or "complet" in req.url.lower() or "chat" in req.url.lower():
                        captured.append({"url": req.url, "body": pd[:2000]})
                except Exception:
                    pass
        pg.on("request", on_request)

        await pg.goto("https://app.notion.com/chat", wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(8000)
        for sel in ["button:has-text('Accept all')","button:has-text('Only allow necessary')"]:
            try:
                if await pg.locator(sel).first.is_visible(timeout=2000):
                    await pg.locator(sel).first.click(); break
            except Exception: pass
        await pg.wait_for_timeout(2000)
        # pick model
        try:
            await pg.locator('[aria-label="Choose AI model"]').first.click(timeout=8000)
            await pg.wait_for_timeout(2000)
            await pg.locator(f'[role="menuitemradio"]:has-text("{model_label}"), [role="menuitem"]:has-text("{model_label}")').first.click(timeout=5000, force=True)
            await pg.wait_for_timeout(1000)
            print("MODEL PICKED:", model_label)
        except Exception as e:
            print("pick fail:", str(e)[:80])
        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(1000)
        # send message
        box = pg.locator("[contenteditable='true']").first
        await box.click(timeout=5000)
        await box.fill("say OK")
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(15000)
        await b.close()

    print("CAPTURED:", len(captured))
    with open("model_capture.json","w",encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=1)
    for c in captured:
        u = c["url"].split("/")[-1][:40]
        body = c["body"]
        # extract model-ish fields
        import re
        ms = re.findall(r'"(?:model|aiModel|modelName|modelSlug|modelType)"\s*:\s*"([^"]+)"', body)
        print(u, "->", ms[:5] if ms else body[:150])

asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else "Opus"))
