# Notion Agent Gateway v2 — OpenAI-compatible server on Notion's free AI agent protocol.
# Reverse-engineered (Oct 2026):
#   POST /api/v3/createAgentThread        type=personal_agent, model=<slug>, reasoningEffort -> threadId
#   POST /api/v3/getThreadTranscript      poll patches -> entity kind=assistant_message content[].text
#   POST /api/v3/getPublicSpaceData       type=space-ids -> cell (needed for x-notion-cell-hint)
# CRITICAL: plain httpx gets 400 UserValidationError (TLS fingerprint). curl_cffi impersonate=chrome -> 200.
#
# Run:  python gateway.py   (env: HOST PORT NOTION_GW_KEY POOL_FILE)
# Test: curl http://127.0.0.1:3100/v1/chat/completions -H "Authorization: Bearer ***" \
#         -d '{"model":"opus-5.5","messages":[{"role":"user","content":"hi"}]}'
import json, os, re, time, uuid, threading
from contextlib import asynccontextmanager

from curl_cffi import requests as cq
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "3100"))
POOL_FILE = os.environ.get("POOL_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool.json"))
NOTION_URL = os.environ.get("NOTION_URL", "https://app.notion.com")
CLIENT_VERSION = os.environ.get("NOTION_CLIENT_VERSION", "23.13.20261008.1055")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
_gw_key = "NOTION_GW_KEY"
API_KEY = os.environ.get(_gw_key, "")

# Full Notion model registry captured via api/v3/getAvailableModels (Oct 2026).
# alias -> (internal slug). 36 models enabled on FREE plan; disabled = Business-only.
MODEL_MAP = {
    "gpt-6.1-sol": "omniberry-sundae", "sol": "omniberry-sundae",
    "gpt-5.6-terra": "orchid-muffin", "terra": "orchid-muffin",
    "gpt-6-luna": "omodi", "luna": "omodi",
    "opus-5.5": "albuquerque-quinn", "opus": "albuquerque-quinn", "albuquerque": "albuquerque-quinn",
    "grok-4.6": "soursop-shortcake",
    "gpt-5.6-luna": "olive-jellyroll",
    "gpt-5.6-sol": "orange-mousse",
    "gpt-5.2": "oatmeal-cookie",
    "gpt-5.4": "oval-kumquat-medium",
    "gpt-5.5": "opal-quince-medium",
    "gemini-3.5-flash": "vertex-gemini-3.5-flash",
    "gemini-3.6-flash": "vertex-gemini-3.6-flash",
    "gemini-3.7-flash": "grapefruit-zeppole",
    "sonnet-4.6": "almond-croissant-low",
    "sonnet-5": "angel-cake-high",
    "sonnet-5.5": "achira-donut",
    "haiku-5.5": "amla-jam",
    "opus-4.6": "avocado-froyo-medium",
    "opus-4.7": "apricot-sorbet-high",
    "opus-4.8": "ambrosia-tart-high",
    "opus-5": "agave-flan",
    "gpt-5.4-mini": "oregon-grape-medium",
    "gpt-5.4-nano": "otaheite-apple-medium",
    "kimi-k3": "fireworks-kimi-k3", "kimi": "fireworks-kimi-k3",
    "deepseek-v4-pro": "baseten-deepseek-v4-pro", "deepseek": "baseten-deepseek-v4-pro",
    "deepseek-v4-flash": "baseten-deepseek-v4-flash",
    "deepseek-v4.1-flash": "baseten-deepseek-v4.1-flash",
    "glm-5.2": "baseten-glm-5.2", "glm-5.3": "baseten-glm-5.3", "glm": "baseten-glm-5.3",
    "glm-5.3-flash": "baseten-glm-5.3-flash",
    "grok-4.3": "xigua-mochi-medium", "grok-4.5": "strawberry-whoopiepie",
    "grok-build-0.1": "xinomavro-cake",
    "gemini-3.1-pro": "galette-medium-thinking",
    "haiku-4.5": "anthropic-haiku-4.5",
    "gemini-3-flash": "gingerbread",
}
# Business-plan-only (isDisabled=true on free):
DISABLED_MODELS = {"gpt-6-astra": "orlando-quinn", "fable-5.1": "assam-chai", "fable-5": "acai-budino-high"}
REASONING_VALUES = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
# per-model allowed reasoningEffort (from getAvailableModels); missing key -> any
MODEL_EFFORTS = {
    "albuquerque-quinn": ["low","medium","high","xhigh","max"],
    "omniberry-sundae": ["none","low","medium","high","xhigh","max"],
    "fireworks-kimi-k3": ["low","high","max"],
    "baseten-deepseek-v4-pro": ["none","minimal","low","medium","high","max","xhigh"],
    "baseten-glm-5.2": ["none","high","max"],
    "baseten-glm-5.3": ["low","high","max"],
    "vertex-gemini-3.5-flash": ["low","medium","high"],
    "grapefruit-zeppole": ["low","medium","high"],
    "amla-jam": ["none","low","medium","high","xhigh","max"],
    "achira-donut": ["low","medium","high","xhigh","max"],
}

def clamp_effort(slug, effort):
    allowed = MODEL_EFFORTS.get(slug)
    if not allowed:
        return effort if effort in REASONING_VALUES else "medium"
    if effort in allowed:
        return effort
    # nearest fallback: prefer medium/low/high present in allowed
    for fb in ("medium", "low", "high", "max", allowed[0]):
        if fb in allowed:
            return fb
    return allowed[0]


def load_pool():
    with open(POOL_FILE, encoding="utf-8") as f:
        return json.load(f)


class Pool:
    """Round-robin with cooldown; thread-safe."""
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
            self._cache = load_pool()
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

    def mark_bad(self, acc, secs=300):
        key = acc.get("email") or acc.get("user_id", "")
        with self.lock:
            self.cooldown[key] = time.time() + secs


pool = Pool()


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
    return re.sub(r"\\([_*`~\[\]()#>+\-.!|{}])", r"\1", t)


def agent_chat(acc, prompt, model_slug, reasoning="medium", timeout_s=180, on_delta=None):
    """Send one prompt via Notion agent protocol, return assistant text."""
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
        raise RuntimeError(f"createAgentThread {r.status_code}: {r.text[:150]}")

    deadline = time.time() + timeout_s
    texts = []
    last_len = -1
    stable = 0
    while time.time() < deadline:
        time.sleep(4)
        try:
            r2 = s.post(f"{NOTION_URL}/api/v3/getThreadTranscript",
                        json={"spaceId": sid, "threadId": tid, "direction": "backward", "limit": 50},
                        headers=H, cookies=cookies)
        except Exception:
            continue
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
                if st in ("finished", "complete", "done", "idle", "failed", "error"):
                    done = st not in ("failed", "error")
                    if not done:
                        raise RuntimeError(f"agent session {st}")
            elif op == "put":
                e = p.get("entity") or {}
                if e.get("kind") == "assistant_message":
                    for blk in e.get("content", []) or []:
                        if blk.get("type") == "text" and blk.get("text"):
                            cur.append(_unescape_md(blk["text"]))
        if cur:
            joined = "\n".join(cur)
            if len(joined) == last_len:
                stable += 1
            else:
                stable = 0
                last_len = len(joined)
            texts = cur
            if done or stable >= 2:
                break
    if not texts:
        raise RuntimeError("no assistant message in transcript")
    out = "\n".join(texts)
    if on_delta:
        on_delta(out)
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(title="Notion Agent Gateway", version="2.0.0", lifespan=lifespan)


def _auth_ok(req: Request):
    if not API_KEY:
        return True
    h = req.headers.get("authorization", "")
    return h == f"Bearer {API_KEY}" or h == API_KEY


@app.get("/health")
def health():
    return {"status": "ok", "accounts": len(pool.accounts())}


@app.get("/v1/models")
def models(req: Request):
    if not _auth_ok(req):
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    return {"object": "list", "data": [
        {"id": k, "object": "model", "created": int(time.time()), "owned_by": "notion-agent"}
        for k in MODEL_MAP]}


@app.post("/v1/chat/completions")
def chat(body: dict, request: Request):
    if not _auth_ok(request):
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    model = body.get("model", "opus-5.5")
    slug = MODEL_MAP.get(model, model)
    msgs = body.get("messages", [])
    parts = []
    for m in msgs:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        parts.append(f"[{role}]\n{content}" if role != "user" else str(content))
    prompt = "\n\n".join(parts)
    reasoning = str(body.get("reasoning_effort", "medium")).lower()
    stream = bool(body.get("stream"))

    last_err = "no accounts in pool"
    for _ in range(3):
        acc = pool.next()
        if not acc:
            return JSONResponse({"error": {"message": "pool exhausted / all cooling down"}}, status_code=503)
        try:
            text = agent_chat(acc, prompt, slug, reasoning)
            now = int(time.time())
            rid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            resp = {
                "id": rid, "object": "chat.completion", "created": now, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": len(text) // 4,
                          "total_tokens": (len(prompt) + len(text)) // 4},
            }
            if stream:
                def gen():
                    chunk = {"id": rid, "object": "chat.completion.chunk", "created": now, "model": model,
                             "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    stop = {"id": rid, "object": "chat.completion.chunk", "created": now, "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(stop)}\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(gen(), media_type="text/event-stream")
            return resp
        except Exception as e:
            pool.mark_bad(acc, 180)
            last_err = f"{acc.get('email','?')}: {str(e)[:150]}"
    return JSONResponse({"error": {"message": f"all retries failed: {last_err}"}}, status_code=502)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
