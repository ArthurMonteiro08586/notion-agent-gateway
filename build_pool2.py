# Build pool.json for Notion Agent Gateway from Playwright storage states.
# Fast mode: token_v2 + getSpaces once per state (single getSpaces call, no space-name lookup).
# Usage: python build_pool2.py [src_dir] [out_file]
import json, os, sys, time
import httpx

SRC = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/User/tmp/notion_autoreg"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "pool.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"

def sv(raw):
    v = raw.get("value", raw) if isinstance(raw, dict) else raw
    if isinstance(v, dict) and "value" in v and isinstance(v["value"], dict):
        return v["value"]
    return v

files = sorted(f for f in os.listdir(SRC) if f.endswith(".json") and ("state" in f))
print("states:", len(files))
pool, seen = [], set()
c = httpx.Client(timeout=25, trust_env=False, headers={"User-Agent": UA, "Content-Type": "application/json"})
ok = fail = 0
for fn in files:
    try:
        d = json.load(open(os.path.join(SRC, fn), encoding="utf-8"))
        cookies = {ck["name"]: ck["value"] for ck in d.get("cookies", [])}
        tv2 = cookies.get("token_v2")
        if not tv2 or tv2 in seen:
            continue
        seen.add(tv2)
        r = c.post("https://app.notion.com/api/v3/getSpaces", json={}, cookies={"token_v2": tv2})
        if r.status_code != 200:
            fail += 1
            time.sleep(1.5)
            continue
        gs = r.json()
        if not gs:
            fail += 1
            time.sleep(1.5)
            continue
        uid = next(iter(gs))
        rec = gs[uid]
        users = {k: sv(v) for k, v in rec.get("notion_user", {}).items()}
        spaces = {k: sv(v) for k, v in rec.get("space", {}).items()}
        views = {}
        for vid, vr in rec.get("space_view", {}).items():
            v = sv(vr)
            sid = v.get("space_id")
            if sid:
                views.setdefault(sid, vid)
        if not spaces:
            # pointer fallback from user_root
            for raw in rec.values():
                if not isinstance(raw, dict):
                    continue
                for uroot in raw.get("user_root", {}).values():
                    v = sv(uroot)
                    for ptr in v.get("space_view_pointers", []) or []:
                        sidp = str(ptr.get("spaceId") or "").strip()
                        if sidp:
                            spaces.setdefault(sidp, {"name": "", "plan_type": ""})
                            views.setdefault(sidp, str(ptr.get("id") or ""))
        if not spaces:
            fail += 1
            time.sleep(1.5)
            continue
        sid = next(iter(spaces))
        u = users.get(uid, {})
        # cell hint from getPublicSpaceData (required by createAgentThread)
        cell = ""
        try:
            gp = c.post("https://app.notion.com/api/v3/getPublicSpaceData",
                        json={"type": "space-ids", "spaceIds": [sid], "includeCounts": False})
            if gp.status_code == 200:
                cell = gp.json()["results"][0].get("cell", "")
        except Exception:
            pass
        pool.append({
            "email": u.get("email", fn.replace("state_", "").replace(".json", "")),
            "token_v2": tv2,
            "cookies": cookies,
            "cell": cell,
            "user_id": uid,
            "space_id": sid,
            "space_view_id": views.get(sid, ""),
            "space_name": spaces[sid].get("name", ""),
            "plan": spaces[sid].get("plan_type", ""),
            "source_file": fn,
        })
        ok += 1
        if ok % 10 == 0:
            json.dump(pool, open(OUT, "w", encoding="utf-8"), indent=1)
            print(f"  progress ok={ok} fail={fail}")
        time.sleep(0.7)
    except Exception as e:
        fail += 1
        print("ERR", fn, str(e)[:80])
        time.sleep(1)

json.dump(pool, open(OUT, "w", encoding="utf-8"), indent=1)
print(f"DONE pool={len(pool)} ok={ok} fail={fail} -> {OUT}")
