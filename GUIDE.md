# GUIDE — Notion free Opus 5.5: от рега до шлюза в своём IDE

Полный цикл: регистрация пула аккаунтов → реверс протокола → свой OpenAI-совместимый шлюз → подключение любого агента/IDE. Всё бесплатно, без карты.

## 0. Что получаешь

- 26 моделей в Notion AI на free-плане: **Opus 5.5** (`albuquerque-quinn`), **GPT-6.1 Sol**, Kimi K3, GPT-6 Luna, Sonnet 5.5, Haiku 5.5, Gemini 3.7 Flash, GPT-5.6 Terra, Grok 4.6, DeepSeek V4 Pro/Flash, GLM 5.2/5.3 и др.
- Явного счётчика сообщений нет; стресс-тест 30 запросов подряд на Opus — без единого блока
- Резонинг: отдельного тумблера thinking НЕТ, но в протоколе есть поле `reasoningEffort` (low/medium/high) — шлюз его прокидывает
- Через шлюз это обычный OpenAI endpoint — подключается к Hermes, OMP, Cline, Roo Code, Continue, openai-python

## 1. Регистрация аккаунтов (farm_trial.py)

Почта: voidash.bond catch-all (свой домен + IMAP). Скрипт:

```bash
PYTHONPATH="" python311 farm_trial.py 10 Opus
```

Флоу: signup → OTP из voidash API → онбординг (For personal life → Continue) → AI-чат → выбор модели → сохранение storage state в `state_<mailbox>.json`. Выход: `farm_trial_accounts.jsonl` (email, code, status, model_active).

Питфолы:
- Onboarding-кнопка Continue иногда не кликается — жми через JS-контейнер
- Gmail-алиасы Notion канонизирует (плюсы срезаются) — только catch-all домены
- После онбординга НЕ делай reload /chat — сносит сессию
- 20+ акков за прогон — нормально, ошибки ~5-15% (таймауты онбординга)

## 1.5. ГЛАВНЫЙ ПИТФОЛ: TLS-фингерпринт

`createAgentThread` отдаёт `400 UserValidationError` на любой запрос из httpx/requests/curl — Notion фильтрует по JA3/TLS-фингерпринту клиента, куки и хедеры не помогают. Решение: **curl_cffi с `impersonate="chrome"`** — сразу 200. Это стоило дня дебага: те же куки, тот же payload, httpx=400, curl_cffi=200.

Второй питфол: `reasoningEffort` валидируется ПО МОДЕЛИ. Kimi K3 принимает только low/high/max; medium → 400 ValidationError. Реестр усилий — в getAvailableModels (modelConfiguration.supportedReasoningEfforts).

## 2. Реверс протокола (capture_model.py, capture_proto.py)

Ставишь Playwright-слушатель на все POST в api/v3, шлёшь сообщение в чате, ловишь:

- `createAgentThread` — создание треда + первое сообщение. Ключевые поля: `type: "personal_agent"`, `model: "albuquerque-quinn"`, `reasoningEffort: "medium"`, `content: [{"type":"text","text":[["твой текст"]]}]`, `threadId`/`clientMessageId` — свои uuid
- Ответ приходит в `getThreadTranscript` (poll): patches[] → op=put → entity.kind="assistant_message" → content[].text (markdown-экранирован: `FULL\_CYCLE\_OK` — нужен unescape)
- `listenToThreadTranscript` — long-poll вариант того же
- cell-hint: `getPublicSpaceData {type:"space-ids"}` → results[0].cell (например prod-space-usw2-0004)
- Хедеры обязательны: `notion-client-version`, `x-notion-active-user-header` (user_id), `x-notion-space-id`, кука `token_v2`

Internal slug Opus 5.5 = `albuquerque-quinn` (город+имя — схема Notion). GPT-6.1 Sol = `gpt-6.1-sol`.

## 3. Сбор пула (build_pool2.py)

Из каждого `state_*.json` вытаскиваешь `token_v2` + один вызов `getSpaces` → user_id, space_id, space_view_id. Выход `pool.json`.

Питфолы:
- getSpaces рателмитит: тонкие 200-ответы (~759 байт без space) и 429 → sleep 0.7-25s между акками, ретраи
- Структура ответа: recordMap с ДВОЙНЫМ `value.value` — разворачивай дважды
- Эндпоинт `www.notion.so` (НЕ .com — 404)

## 4. Шлюз (gateway_v3.py)

FastAPI + **curl_cffi** (обязательно!). v3: async endpoint, дельты транскрипта пушатся в SSE сразу как растут, unknown model → 404, AccountFault (401/403/429) → cooldown, TransientFault → ротация БЕЗ cooldown, usage честный (chars, токены Notion не отдаёт). Примечание: Notion присылает assistant_message целыми патчами — дельт мало на коротких ответах, это ограничение протокола, не шлюза.. Round-robin по пулу 70 акков, cooldown битых 3 мин, stream-обёртка, reasoning_effort с per-model clamp. 36 моделей, алиасы: opus-5.5, sol, kimi, deepseek, glm...

```bash
PORT=3100 NOTION_GW_KEY=*** python gateway_v3.py
curl -s http://127.0.0.1:3100/v1/chat/completions \
  -H "Authorization: Bearer ***" -H "Content-Type: application/json" \
  -d '{"model":"opus-5.5","messages":[{"role":"user","content":"hi"}],"reasoning_effort":"high"}'
```

## 5. Подключение к IDE/агенту

В любом OpenAI-совместимом клиенте:

```
base_url: http://127.0.0.1:3100/v1
api_key:  <твой NOTION_GW_KEY>
model:    opus-5.5
```

- **Cline / Roo Code (VS Code)**: API Provider = OpenAI Compatible
- **Continue**: provider openai, apiBase как выше
- **Hermes/OMP**: custom provider → base_url шлюза (agent видит комп как обычно — шлюз только LLM-бэкенд)
- **openai-python**: `OpenAI(base_url="http://127.0.0.1:3100/v1", api_key=***

Агент видит твой комп как обычно — шлюз лишь подменяет LLM-бэкенд. Opus 5.5 бесплатно в IDE.

## 6. Лимиты и реальность

- Free-план: Business-триал БЕЗ карты сейчас не выдаётся (проверено 160+ свежих акков, кампании business_reverse_14d/stacked_business_trial, sessionStorage-сиды, US-прокси — везде только карточный модал $24/мес). Это серверный AB-гейт.
- НО: Opus 5.5 работает и на free — триал не нужен
- Явного лимита сообщений в UI нет; 30 подряд — чисто. Fair-use существует, поэтому пул из N акков + ротация = практически безлимит
- Один акк = один воркспейс; треда-изолированность на стороне Notion

## 7. Крон-волны

Волна рега 10 акков/сутки ловит AB-гейт на триал автоматически (cron `notion-trial-wave`, 04:00, отчёт в TG). Если `modal_has_trial: true` — алерт с почтой акка.
