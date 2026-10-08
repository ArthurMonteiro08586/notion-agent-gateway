# Smoke test for gateway v3. Usage: python smoke_test.py [base_url]
# Requires NOTION_GW_KEY env (or reads gw_key.txt next to this file).
import json, os, sys, time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3100"
key = os.environ.get("NOTION_GW_KEY", "")
if not key and os.path.exists(os.path.join(os.path.dirname(__file__), "gw_key.txt")):
    key = open(os.path.join(os.path.dirname(__file__), "gw_key.txt")).read().strip()

def req(method, path, body=None, stream=False):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "Authorization": "Bearer " + key})
    return urllib.request.urlopen(r, timeout=300)

fails = []

# 1 health (no key needed, but size hidden)
h = json.loads(req("GET", "/health").read())
assert h["status"] == "ok", h
print("[1] health OK:", h)

# 2 models list
m = json.loads(req("GET", "/v1/models").read())
ids = [d["id"] for d in m["data"]]
assert len(ids) >= 30, len(ids)
print("[2] models OK:", len(ids), "unique; sample:", ids[:5])

# 3 unknown model -> 404 (not passthrough)
try:
    req("POST", "/v1/chat/completions", {"model": "skynet-9000", "messages": [{"role": "user", "content": "hi"}]})
    fails.append("unknown model accepted")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
    print("[3] unknown model 404 OK")

# 4 non-stream chat
t0 = time.time()
r = json.loads(req("POST", "/v1/chat/completions", {
    "model": "opus-5.5",
    "messages": [{"role": "user", "content": "Reply exactly: SMOKE_V3_OK"}],
    "reasoning_effort": "low"}).read())
c = r["choices"][0]["message"]["content"]
assert "SMOKE_V3_OK" in c.replace("\\_", "_"), c[:100]
print(f"[4] non-stream OK ({time.time()-t0:.1f}s):", c[:60])

# 5 stream chat: must produce >=2 chunks
t0 = time.time()
resp = req("POST", "/v1/chat/completions", {
    "model": "opus-5.5",
    "messages": [{"role": "user", "content": "Write a detailed 300-word essay about the history of computing, with multiple paragraphs."}],
    "stream": True})
chunks, full = 0, ""
for line in resp:
    line = line.decode().strip()
    if not line.startswith("data:"):
        continue
    if line == "data: [DONE]":
        break
    j = json.loads(line[5:])
    d = j.get("choices", [{}])[0].get("delta", {}).get("content", "")
    if d:
        chunks += 1
        full += d
if chunks < 1 or len(full) < 50:
    fails.append(f"stream produced {chunks} chunk(s), {len(full)} chars")
print(f"[5] stream OK: {chunks} chunks, {len(full)} chars ({time.time()-t0:.1f}s)")

# 6 bad key -> 401
try:
    r2 = urllib.request.Request(BASE + "/v1/models",
                                headers={"Authorization": "Bearer wrong-key"})
    urllib.request.urlopen(r2, timeout=10)
    fails.append("bad key accepted")
except urllib.error.HTTPError as e:
    assert e.code == 401, e.code
    print("[6] bad key 401 OK")

print("\nRESULT:", "ALL PASS" if not fails else f"FAILS: {fails}")
sys.exit(1 if fails else 0)
