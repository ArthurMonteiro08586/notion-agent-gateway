# Changelog

## v3.0.0 (2026-10-08)
Fixes from internal roast of v2:

1. **Real streaming** — transcript poll pushes deltas through `asyncio.Queue`
   (`run_in_executor`); SSE chunks carry incremental `delta.content` instead of
   one final blob pretending to be a stream.
2. **Async endpoint** — `/v1/chat/completions` is `async def`; blocking Notion I/O
   runs in a dedicated 32-thread pool, event loop stays responsive under
   concurrent IDE agents.
3. **Honest usage** — no more `len//4` token horoscope; reports real
   `prompt_chars`/`completion_chars` + zeroed token fields with a note
   (Notion API exposes no token counts).
4. **Model validation** — unknown model → `404` + list of available models.
   No arbitrary passthrough into the agent payload (was: any string → Notion
   400 → three accounts burned in cooldown).
5. **MODEL_EFFORTS from registry** — all 39 models loaded from
   `model_registry.json` at startup (was: 10 hardcoded).
6. **Typed faults** — `AccountFault` (401/403/429) → 300s cooldown;
   `TransientFault` (model overloaded, timeout, session failed) → retry next
   account WITHOUT cooldown. Pool no longer melts on client-side timeouts.
7. **Auth hygiene** — `hmac.compare_digest` for key check; `/health` hides pool
   size without key; refuses to bind non-loopback with empty `NOTION_GW_KEY`.
8. **Premature-stable guard** — break requires session status OR 4 stable polls
   (~12-16s, env `STABLE_REQUIRED`) instead of 2 polls (8s) which truncated
   long reasoning outputs.
9. **`_unescape_md`** — no longer eats escaped backslashes (`(?<!\\)` guard).
10. **smoke_test.py** — 6-check suite: health, models, unknown-404, non-stream
    roundtrip, stream chunking, bad-key-401.

## v2.0.0 (2026-10-08)
- curl_cffi `impersonate="chrome"` — the actual breakthrough (httpx = 400
  UserValidationError on createAgentThread; TLS fingerprint filtering).
- `omniberry-sundae` = GPT-6.1 Sol (live-captured slug).
- `getAvailableModels` dump → `model_registry.json` (39 models, 36 free).
- Pool: 70 accounts with full cookies + cell hints (`enrich_cell.py`).

## v1.0.0 (2026-10-08)
- First attempt, httpx-based. Dead on arrival: Notion TLS-fingerprints
  `createAgentThread`. Kept in git history only — do not run `gateway.py`.
