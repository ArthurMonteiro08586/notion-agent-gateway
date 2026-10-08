# Notion Agent Gateway v3 — OpenAI-compatible server on Notion's free AI agent protocol.
# Fixes over v2 (from roast):
#   [1] real streaming: transcript poll pushes deltas through asyncio.Queue (run_in_executor)
#   [2] async endpoint: blocking Notion I/O runs in threadpool, event loop free
#   [3] honest usage: no fake len//4 — reported as 0 with note, or char-based estimate opt-in
#   [4] model validation: unknown model -> 404, no passthrough into payload
#   [5] MODEL_EFFORTS auto-loaded from model_registry.json (all 39 models, not 10)
#   [6] cooldown only for real account faults (auth/429/403), not client timeouts
#   [7] hmac.compare_digest for API key; /health does not leak pool size without key
#   [8] premature-stable guard: require session status OR 4 stable polls (16s) not 2
#
# Run:  python gateway_v3.py   (env: HOST PORT NOTION_GW_KEY POOL_FILE REGISTRY_FILE)
# Test: python smoke_test.py
import asyncio, hmac, json, os, re, time, uuid, threading
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cq
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "3100"))
_here = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.environ.get("POOL_FILE", os.path.join(_here, "pool.json"))
REGISTRY_FILE = os.environ.get("REGISTRY_FILE", os.path.join(_here, "model_registry.json"))
NOTION_URL = os.environ.get("NOTION_URL", "https://app.notion.com")
CLIENT_VERSION = os.environ.get("NOTION_CLIENT_VERSION", "23.13.20261008.1055")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
API_KEY = os.environ.get("NOTION_GW_KEY", "")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "3"))
STABLE_REQUIRED = int(os.environ.get("STABLE_REQUIRED", "4"))  # ~12-16s of no growth

# ---------------------------------------------------------------- model registry
def _load_registry():
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            reg = json.load(f)
    except Exception:
        reg = {}
    efforts = {slug: v.get("efforts", []) for slug, v in reg.items() if not v.get("disabled")}
    names = {slug: v.get("name", slug) for slug, v in reg.items()}
    return efforts, names

MODEL_EFFORTS, MODEL_NAMES = _load_registry()

MODEL_MAP = {
    "gpt-6.1-sol": "omniberry-sundae", "sol": "omniberry-sundae",
    "gpt-5.6-terra": "orchid-muffin", "terra": "orchid-muffin",
    "gpt-6-luna": "omodi", "luna": "omodi",
    "opus-5.5": "albuquerque-quinn", "opus": "albuquerque-quinn",
    "grok-4.6": "soursop-shortcake", "grok-4.5": "strawberry-whoopiepie", "grok-4.3": "xigua-mochi-medium",
    "gpt-5.6-luna": "olive-jellyroll", "gpt-5.6-sol": "orange-mousse",
    "gpt-5.2": "oatmeal-cookie", "gpt-5.4": "oval-kumquat-medium", "gpt-5.5": "opal-quince-medium",
    "gpt-5.4-mini": "oregon-grape-medium", "gpt-5.4-nano": "otaheite-apple-medium",
    "gemini-3.5-flash": "vertex-gemini-3.5-flash", "gemini-3.6-flash": "vertex-gemini-3.6-flash",
    "gemini-3.7-flash": "grapefruit-zeppole", "gemini-3-flash": "gingerbread",
    "gemini-3.1-pro": "galette-medium-thinking",
    "sonnet-4.6": "almond-croissant-low", "sonnet-5": "angel-cake-high", "sonnet-5.5": "achira-donut",
    "haiku-5.5": "amla-jam", "haiku-4.5": "anthropic-haiku-4.5",
    "opus-4.6": "avocado-froyo-medium", "opus-4.7": "apricot-sorbet-high",
    "opus-4.8": "ambrosia-tart-high", "opus-5": "agave-flan",
    "kimi-k3": "fireworks-kimi-k3", "kimi": "fireworks-kimi-k3",
    "deepseek-v4-pro": "baseten-deepseek-v4-pro", "deepseek": "baseten-deepseek-v4-pro",
    "deepseek-v4-flash": "baseten-deepseek-v4-flash", "deepseek-v4.1-flash": "baseten-deepseek-v4.1-flash",
    "glm-5.2": "baseten-glm-5.2", "glm-5.3": "baseten-glm-5.3", "glm": "baseten-glm-5.3",
    "glm-5.3-flash": "baseten-glm-5.3-flash", "grok-build-0.1": "xinomavro-cake",
}
REASONING_VALUES = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

def clamp_effort(slug, effort):
    allowed = MODEL_EFFORTS.get(slug) or REASONING_VALUES
    if effort in allowed:
        return effort
    for fb in ("medium", "low", "high", "max", allowed[0]):
        if fb in allowed:
            return fb
    return allowed[0]

# ---------------------------------------------------------------- pool
class Pool:
    """Round-robin with typed cooldown; thread-safe; hot-reload on mtime."""
    def __init__(self):
        self.lock = threading.Lock()
        self.idx = 0
        self.cooldown = {}
        self._cache = None
        self._mtime = 0

    def accounts(self):
        try:
            m = os.path.getmtime(POOL_FILE)
        except OSError:
            return []
        if self._cache is None or m != self._mtime:
            with open(POOL_FILE, encoding="utf-8") as f:
                self._cache = json.load(f)
            self._mtime = m
        return self._cache

    def next(self):
        accs = self.accounts()
        if not accs:
            return None
        now = time.time()
        with self.lock:
            for _ in range(len(accs)):
                a = accs[self.idx % len(accs)]
                self.idx += 1
                key = a.get("email") or a.get("user_id", "")
                if self.cooldown.get(key, 0) < now:
                    return a
        return None

    def mark_bad(self, acc, secs):
        key = acc.get("email") or acc.get("user_id", "")
        with self.lock:
            self.cooldown[key] = time.time() + secs

pool = Pool()
EXEC = ThreadPoolExecutor(max_workers=32, thread_name_prefix="notion")

# ---------------------------------------------------------------- notion protocol
def _session():
    return cq.Session(impersonate="chrome", timeout=45)

def _headers(acc):
    h = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "notion-client-version": CLIENT_VERSION,
        "notion-audit-log-platform": "web",
        "x-notion-active-user-header": acc["user_id"],
        "x-notion-space-id": acc["space_id"],
        "Origin": NOTION_URL,
        "Referer": f"{NOTION_URL}/chat",
    }
    if acc.get("cell"):
        h["x-notion-cell-hint-source"] = "space-id-mapping"
        h["x-notion-cell-hint"] = acc["cell"]
    return h

def _cookies(acc):
    ck = dict(acc.get("cookies") or {})
    if not ck and acc.get("token_v2"):
        ck = {"token_v2": acc["token_v2"]}
    return ck

def _unescape_md(t):
    # only unescape markdown-escapes Notion adds; leave double-backslashes alone
    return re.sub(r"(?<!\\)\\([_*`~\[\]()#>+\-.!|{}])", r"\1", t)

class AccountFault(Exception):
    """Real account-level failure -> cooldown the account."""
class TransientFault(Exception):
    """Model/session/timeout issue -> retry another account, NO cooldown."""

def _classify(status, body):
    if status in (401, 403):
        return AccountFault(f"auth {status}")
    if status == 429:
        return AccountFault(f"rate-limited {status}")
    return TransientFault(f"http {status}: {body[:120]}")

def agent_chat_stream(acc, prompt, model_slug, reasoning, timeout_s, on_delta):
    """Blocking (thread) generator-style chat: calls on_delta(text_so_far) per poll."""
    s = _session()
    cookies = _cookies(acc)
    H = _headers(acc)
    sid = acc["space_id"]
    tid = str(uuid.uuid4())
    payload = {
        "type": "personal_agent",
        "spaceId": sid,
        "threadId": tid,
        "enableSuggestedEditsTools": True,
        "model": model_slug,
        "reasoningEffort": clamp_effort(model_slug, reasoning),
        "createdSource": "full_page_chat",
        "content": [{"type": "text", "text": [[prompt]]}],
        "policies": {"approval_mode": "review_for_me"},
        "agentMemorySettings": {"useMemories": False, "excludeChatFromMemories": True},
        "browserEnabled": False,
        "clientMessageId": str(uuid.uuid4()),
    }
    r = s.post(f"{NOTION_URL}/api/v3/createAgentThread", json=payload, headers=H, cookies=cookies)
    if r.status_code != 200:
        raise _classify(r.status_code, r.text)

    deadline = time.time() + timeout_s
    texts = []
    last_len = -1
    stable = 0
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            r2 = s.post(f"{NOTION_URL}/api/v3/getThreadTranscript",
                        json={"spaceId": sid, "threadId": tid, "direction": "backward", "limit": 50},
                        headers=H, cookies=cookies)
        except Exception:
            continue
        if r2.status_code == 401 or r2.status_code == 403:
            raise AccountFault(f"transcript auth {r2.status_code}")
        if r2.status_code != 200:
            continue
        try:
            j = r2.json()
        except Exception:
            continue
        cur = []
        done = False
        for p in j.get("patches", []) or []:
            op = p.get("op")
            if op == "session":
                st = (p.get("session") or {}).get("status", "")
                if st in ("failed", "error"):
                    raise TransientFault(f"agent session {st}")
                if st in ("finished", "complete", "done", "idle"):
                    done = True
            elif op == "put":
                e = p.get("entity") or {}
                if e.get("kind") == "assistant_message":
                    for blk in e.get("content", []) or []:
                        if blk.get("type") == "text" and blk.get("text"):
                            cur.append(_unescape_md(blk["text"]))
        if cur:
            joined = "\n".join(cur)
            if joined != "\n".join(texts):
                texts = cur
                on_delta(joined)
                stable = 0
                last_len = len(joined)
            elif len(joined) == last_len:
                stable += 1
            if done or stable >= STABLE_REQUIRED:
                break
    if not texts:
        raise TransientFault("no assistant message in transcript (timeout)")
    return "\n".join(texts)

# ---------------------------------------------------------------- fastapi app
app = FastAPI(title="Notion Agent Gateway", version="3.0.0")

def _auth_ok(req: Request):
    if not API_KEY:
        return True
    h = req.headers.get("authorization", "")
    cand = h[7:] if h.lower().startswith("bearer ") else h
    return hmac.compare_digest(cand, API_KEY)

@app.get("/health")
def health(request: Request):
    body = {"status": "ok"}
    if _auth_ok(request):
        body["accounts"] = len(pool.accounts())
        body["models"] = len(MODEL_MAP)
    return body

@app.get("/v1/models")
def models(request: Request):
    if not _auth_ok(request):
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    data = []
    seen = set()
    for alias, slug in MODEL_MAP.items():
        if slug in seen:
            continue
        seen.add(slug)
        data.append({"id": alias, "object": "model", "created": int(time.time()),
                     "owned_by": "notion-agent",
                     "display_name": MODEL_NAMES.get(slug, alias),
                     "reasoning_efforts": MODEL_EFFORTS.get(slug, [])})
    return {"object": "list", "data": data}

def _build_prompt(msgs):
    parts = []
    for m in msgs:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        parts.append(f"[{role}]\n{content}" if role != "user" else str(content))
    return "\n\n".join(parts)

@app.post("/v1/chat/completions")
async def chat(body: dict, request: Request):
    if not _auth_ok(request):
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    model = body.get("model", "opus-5.5")
    if model not in MODEL_MAP:
        return JSONResponse({"error": {"message": f"unknown model '{model}'",
                                       "type": "invalid_request_error",
                                       "available": sorted(MODEL_MAP)}}, status_code=404)
    slug = MODEL_MAP[model]
    prompt = _build_prompt(body.get("messages", []))
    reasoning = str(body.get("reasoning_effort", "medium")).lower()
    stream = bool(body.get("stream"))
    timeout_s = min(int(body.get("timeout_s", 300)), 600)
    rid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    now = int(time.time())
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    SENT = object()

    async def _run_one(acc):
        def on_delta(text):
            loop.call_soon_threadsafe(q.put_nowait, text)
        return await loop.run_in_executor(
            EXEC, agent_chat_stream, acc, prompt, slug, reasoning, timeout_s, on_delta)

    async def _attempt(max_tries=3):
        last_err = "no accounts in pool"
        for _ in range(max_tries):
            acc = pool.next()
            if not acc:
                raise RuntimeError("pool exhausted / all cooling down")
            try:
                return await _run_one(acc)
            except AccountFault as e:
                pool.mark_bad(acc, 300)
                last_err = f"{acc.get('email','?')}: {e}"
            except TransientFault as e:
                last_err = f"{acc.get('email','?')}: {e}"
        raise RuntimeError(f"all retries failed: {last_err}")

    if stream:
        async def gen():
            task = asyncio.create_task(_attempt())
            task.add_done_callback(lambda t: loop.call_soon_threadsafe(q.put_nowait, SENT))
            sent = ""
            while True:
                item = await q.get()
                if item is SENT:
                    break
                delta = item[len(sent):]
                sent = item
                if delta:
                    chunk = {"id": rid, "object": "chat.completion.chunk", "created": now,
                             "model": model,
                             "choices": [{"index": 0, "delta": {"content": delta},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            try:
                text = task.result()
            except Exception as e:
                err = {"id": rid, "object": "error", "error": {"message": str(e)}}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
            stop = {"id": rid, "object": "chat.completion.chunk", "created": now, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(stop, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        text = await _attempt()
    except Exception as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=502)
    return {
        "id": rid, "object": "chat.completion", "created": now, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        # honest usage: Notion API gives no token counts; chars reported instead
        "usage": {"prompt_chars": len(prompt), "completion_chars": len(text),
                  "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "note": "Notion does not expose token usage; chars provided"},
    }

if __name__ == "__main__":
    import uvicorn
    if not API_KEY and HOST != "127.0.0.1":
        raise SystemExit("refusing to bind non-loopback without NOTION_GW_KEY")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
