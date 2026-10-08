# Enrich pool.json with cell hints from getPublicSpaceData
import json, time, httpx
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36"
p = json.load(open("pool.json", encoding="utf-8"))
c = httpx.Client(timeout=25, trust_env=False, headers={"User-Agent": UA, "Content-Type": "application/json"})
n = 0
for a in p:
    if a.get("cell"):
        continue
    try:
        r = c.post("https://app.notion.com/api/v3/getPublicSpaceData",
                   json={"type": "space-ids", "spaceIds": [a["space_id"]], "includeCounts": False},
                   cookies={"token_v2": a["token_v2"]})
        if r.status_code == 200 and r.json().get("results"):
            a["cell"] = r.json()["results"][0].get("cell", "")
            n += 1
    except Exception as e:
        print("ERR", a.get("email", "?")[:30], str(e)[:60])
    time.sleep(0.8)
json.dump(p, open("pool.json", "w", encoding="utf-8"), indent=1)
print("enriched:", n, "of", len(p))
