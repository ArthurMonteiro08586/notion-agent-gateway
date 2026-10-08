# Notion Agent Gateway

Бесплатный OpenAI-compatible шлюз поверх AI-агента Notion. **Opus 5.5, GPT-6.1 Sol, Kimi K3, GPT-6 Luna, Sonnet 5.5, Haiku 5.5, Gemini 3.7 Flash, GPT-5.6 Terra, Grok 4.6, DeepSeek V4, GLM 5.2/5.3 — 26 моделей без карты на free-плане.**

Реверснутый протокол агент-чата Notion (октябрь 2026):

```
POST /api/v3/createAgentThread        type=personal_agent, model=<internal-slug> -> threadId
POST /api/v3/listenToThreadTranscript long-poll c cursor'ом -> patches stream + session.status
POST /api/v3/getThreadTranscript      history fallback
```

Внутренние слагi моделей (capture из живого UI):

Полный реестр — 39 моделей из `api/v3/getAvailableModels`, 36 доступны на free-плане:

| Model | internal slug | reasoning efforts |
|---|---|---|
| Opus 5.5 | `albuquerque-quinn` | low,medium,high,xhigh,max |
| GPT-6.1 Sol | `omniberry-sundae` | none..max |
| GPT-5.6 Terra | `orchid-muffin` | none..max |
| GPT-6 Luna | `omodi` | none..max |
| Grok 4.6 | `soursop-shortcake` | low..xhigh |
| Sonnet 5.5 | `achira-donut` | low..max |
| Haiku 5.5 | `amla-jam` | none..max |
| Opus 4.6/4.7/4.8/5 | `avocado-froyo/apricot-sorbet/ambrosia-tart/agave-flan` | - |
| Kimi K3 | `fireworks-kimi-k3` | low,high,max |
| DeepSeek V4 Pro/Flash | `baseten-deepseek-v4-*` | none..xhigh |
| GLM 5.2/5.3 | `baseten-glm-5.*` | - |
| Gemini 3.5/3.6/3.7 Flash | `vertex-gemini-*/grapefruit-zeppole` | low..high |
| GPT-5.2/5.4/5.5/6 Astra, Fable 5/5.1 | см. model_registry.json | Business-gate |

Business-only (isDisabled на free): GPT-6 Astra, Fable 5, Fable 5.1.

**Важно:** обычный httpx/curl получает `400 UserValidationError` на createAgentThread — Notion фильтрует по TLS-фингерпринту. Шлюз использует `curl_cffi` с `impersonate="chrome"`.

## Быстрый старт

```bash
pip install fastapi uvicorn curl_cffi
# 1) pool.json — массив аккаунтов (см. ниже)
# 2) запуск
PORT=3100 NOTION_GW_KEY=*** python gateway_v2.py
# 3) запрос
curl http://127.0.0.1:3100/v1/chat/completions \
  -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
  -d '{"model":"opus-5.5","messages":[{"role":"user","content":"Привет"}]}'
```

## pool.json

```json
[{
  "email": "user@example.com",
  "token_v2": "***",
  "user_id": "uuid",
  "space_id": "uuid",
  "space_view_id": "uuid",
  "plan": "personal_free"
}]
```

`token_v2` — сессионная кука Notion. Билдер: `build_pool2.py` собирает pool.json из Playwright storage-state файлов фермы (см. гайд).

## Фичи

- Round-robin по пулу + cooldown на аккаунт при ошибке (2 мин)
- `stream: true` — SSE-обёртка
- `reasoning_effort` — прокидывается в payload агента (`low|medium|high`)
- Health: `GET /health` → `{"status":"ok","accounts":N}`

## Подключение агентов/IDE

Любой OpenAI-совместимый клиент:

```
base_url = http://127.0.0.1:3100/v1
api_key  = <NOTION_GW_KEY>
model    = opus-5.5
```

- Hermes/OMP: custom provider c OpenAI-совместимым endpoint
- Cline/Roo Code, Continue, OpenClaw — тот же base_url
- curl/openai-python SDK — работают из коробки

## Структура

```
gateway_v2.py     — шлюз (FastAPI+curl_cffi, рабочий)
enrich_cell.py    — обогащение пула cell-hints
model_registry.json — дамп 39 моделей getAvailableModels
build_pool2.py    — сборщик pool.json из state-файлов фермы
farm_trial.py     — авторег аккаунтов (voidash catch-all + OTP)
capture_model.py  — капчер внутренних model-slug'ов из UI
capture_proto.py  — капчер полного протокола (req+resp)
GUIDE.md          — полный гайд: от рега до шлюза
```

## Дисклеймер

Только для своих аккаунтов и исследовательских целей.
