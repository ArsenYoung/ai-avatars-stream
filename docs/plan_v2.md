# План V2 — HeyGen Streaming + YouTube chat (локальный запуск)

## Цель V2
- 2 автономных агента с визуальными аватарами.
- Реальный live‑стрим через HeyGen Streaming (WebRTC) → OBS → YouTube.
- Управление темой через чат YouTube.

## Что уже есть (база)
- Оркестратор с очередью, prefetch, памятью, summary, транскриптом (`src/orchestrator.py`).
- LLM/TTS пайплайн (OpenAI) + OBS управление сценами (`src/obs_client.py`).
- Интеграция HeyGen **generate** (рендер mp4) в `VIDEO_MODE=1` — НЕ streaming.

## Что требуется изменить/добавить

### 1) Режимы запуска и конфиги (обязательный техдолг)
- Привести env‑ключи к единому контракту (сейчас есть расхождения: `OPENAI_MODEL` vs `LLM_MODEL`, `TTS_VOICE_*` vs `VOICE_*`, `SCENE_*` vs `OBS_SCENE_*`).
- Добавить явный режим: `STREAM_MODE=heygen` (или `HEYGEN_STREAMING=1`) и оставить `VIDEO_MODE` как fallback.
- Обновить `.env.example` + README с реальными именами переменных и путём запуска.

### 2) HeyGen Streaming (новый пайплайн вместо mp4)
**Цель:** каждый агент — отдельная live‑сессия HeyGen, которую OBS захватывает как Browser Source.

Задачи:
- Добавить модуль `src/heygen_stream.py`:
  - создание streaming‑сессии (avatar/talking_photo, voice, размер);
  - отправка задач «произнеси текст»;
  - stop/reconnect + retry/backoff.
- Решить «кто подключается к WebRTC»:
  - **Вариант A (предпочтительный):** локальная HTML‑страница с JS SDK HeyGen, которая принимает `session` и подключается к WebRTC.
  - Оркестратор создаёт сессию через API, пишет данные (token/sdp/room) в локальный JSON; страница читает JSON и подключается.
- Добавить мини‑сервер (FastAPI/Flask) для:
  - раздачи `web/agent_a.html`, `web/agent_b.html`;
  - выдачи session‑data без раскрытия API‑ключа в браузер;
  - проброса команд speak (если SDK требует call из браузера).

**OBS изменения:**
- Заменить `AVATAR_A/AVATAR_B` на Browser Source, указывающие на локальные страницы.
- Сцены `SCENE_A/SCENE_B/SCENE_IDLE` оставить, но контент в них — WebRTC‑видео.

### 3) Оркестратор под streaming
- Разделить этапы:
  - LLM → текст (prefetch) **без** TTS/MP3;
  - «play» = отправка текста в HeyGen streaming для активного агента.
- Добавить ожидание окончания реплики:
  - если API даёт события/статус «speaking/ended» — ждать их;
  - иначе fallback: вычислять длительность по длине текста (chars_per_sec).
- Вести транскрипт с новыми полями:
  - `stream_session_id`, `stream_task_id`, `voice_id`, `duration_est`.
- Поддержка двух агентов: отдельные session_id и отдельные Browser Source.
- Добавить graceful‑reconnect при разрыве WebRTC (ре‑создание сессии).

### 4) YouTube Chat → Topic Control
**Цель:** тема меняется по сообщению чата, без ручного вмешательства.

Задачи:
- Добавить `src/youtube_chat.py`:
  - OAuth (YouTube Data API v3) и polling live chat;
  - фильтр команд, например `!topic <тема>`;
  - cooldown (например 2–5 минут) и allowlist модераторов.
- Расширить `TopicProvider`:
  - метод `set(topic, source, ts)` и приоритет chat‑темы над file/env;
  - TTL темы из чата (например 10–15 мин), потом возврат к дефолту.
- Логировать смену темы в `transcripts/` (с метками времени).

### 5) Обновление промптов под «старение/биология»
- Переписать `SCIENTIST_SYSTEM` и `SKEPTIC_SYSTEM` под научные темы биологии/старения.
- Добавить «личности» (скептик/оптимист/учёный), как в ТЗ.
- Сохранить ограничения длины и «не выдумывать источники».

### 6) Ранбук запуска (локально)
1) Запустить локальный web‑сервер (`python -m src.web` или `uvicorn src.web:app`).
2) Открыть локальные страницы (agent A/B) в OBS Browser Source.
3) Запустить оркестратор в streaming‑режиме.
4) Проверить, что live‑реплики идут по очереди и OBS пишет/стримит.

## Приоритеты (по порядку)
1) Streaming‑пайплайн (HeyGen live + OBS Browser Source).
2) Оркестратор: очередь → speak‑task, без mp3.
3) YouTube chat → topic control.
4) Апдейт промптов под биологию/старение.
5) Документация/README/.env.example.

## Definition of Done
- Два live‑агента в OBS, каждый — HeyGen streaming (WebRTC) с lip‑sync.
- Автономный диалог 20–30 минут без ручного вмешательства.
- Тема меняется командой в YouTube чате и фиксируется в транскрипте.
- Есть запись/VOD или активный live‑линк.
