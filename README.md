# AI Avatars Stream — MVP

Проект: автономные AI‑агенты с аватарами ведут научную дискуссию в прямом эфире (OBS → RTMP). Цель — быстрый, стабильный MVP «за 1–2 дня», теперь с **HeyGen Streaming** и управлением темы через **YouTube chat**.

## Что уже сделано
- Чекпоинт 1 готов (OBS‑only, ручное аудио): базовая сцена, `AUDIO_PLAYER`, ручное проигрывание mp3 в OBS подтверждено.
- Зафиксирована архитектура MVP в `docs/arch.md`.
- Сформирован подробный план работ по чекпоинтам в `docs/plan.md`.
- Реализован OBS WebSocket клиент `src/obs_client.py` + `self_check()` (сцены, медиа‑файл, перезапуск).
- Реализован оркестратор `src/orchestrator.py`: очередь, prefetch, переключение сцен, запись транскрипта.
- Ожидание длительности трека синхронизировано через `ffprobe` (устойчиво к глюкам статуса OBS).
- Интегрированы LLM/TTS/summary/topic: `src/llm.py`, `src/tts.py`, `src/summarize.py`, `src/topic.py`, `src/retry.py`.
- Добавлены правила «стрим‑формата»: вступление, смена уровня, steelman, финальные раунды, закрытие, ограничение длины реплик.
- Добавлен HeyGen Streaming (LiveAvatar) + локальный viewer для OBS Browser Source (`src/heygen_stream.py`, `src/stream_server.py`, `web/agent.html`).
- Добавлен YouTube chat watcher для смены темы по команде `!topic` (`src/youtube_chat.py`).

## Текущий статус
- Чекпоинт 1: готов.
- Чекпоинт 2: готов (OBS WebSocket контроль сцен/медиа подтверждён).
- Чекпоинт 3: готов (очередь + prefetch + сцены + транскрипт).
- Чекпоинт 4: базовая интеграция готова; остаются таймауты/расширенное логирование и «вау‑эффекты».

## Содержимое репозитория
- `docs/arch.md` — архитектура MVP и принятые решения.
- `docs/plan.md` — план работ по чекпоинтам.
- `docs/tz.docx` — исходное ТЗ.
- `src/obs_client.py` — клиент OBS WebSocket и `self_check()`.
- `src/orchestrator.py` — оркестратор (mp3/HeyGen streaming).
- `src/llm.py`, `src/tts.py`, `src/summarize.py`, `src/topic.py` — модули CP4.
- `src/retry.py` — общий retry/backoff.
- `src/heygen_stream.py` — HeyGen Streaming API (create/start/task).
- `src/stream_server.py` — локальный web‑сервер для OBS Browser Source.
- `web/agent.html` — WebRTC viewer (LiveKit) для аватара.
- `src/youtube_chat.py` — YouTube chat → topic control.

## Дефолты MVP (зафиксировано)
- В mp3‑режиме OBS проигрывает аудио через **Media Source** (`AUDIO_PLAYER`).
- В streaming‑режиме OBS использует **Browser Source** (WebRTC LiveKit).
- `queue_floor=2`.
- Hot‑reload темы каждые 2–5 минут (`TOPIC`/`topic.txt`).
- Контекст: последние 10–12 реплик + краткий summary.
- Длина реплики: по умолчанию 1–2 предложения (`MAX_SENTENCES=2`).
- Длина эфира: 25 ходов с финальными раундами и закрытием (`MAX_TURNS=25`).

## Быстрый старт (HeyGen Streaming)
1) Заполни `.env` по образцу `.env.example`:
   - `STREAM_MODE=heygen`
   - `HEYGEN_API_KEY`
   - `HEYGEN_STREAM_AVATAR_A/B` (или `HEYGEN_STREAM_AVATAR_ID_A/B`)
   - `HEYGEN_STREAM_AUTH_MODE=api_key` (по умолчанию, как в документации)
2) Запусти:
   - `python -m src.stream_server` (или выставь `STREAM_SERVER=1` и запускай `src.main`)
   - `python -m src.main`
3) В OBS добавь **Browser Source**:
   - `http://127.0.0.1:8099/agent.html?agent=A`
   - `http://127.0.0.1:8099/agent.html?agent=B`

## Быстрый старт (mp4‑рендер, без стриминга)
1) В `.env`:
   - `STREAM_MODE=` (пусто)
   - `VIDEO_MODE=1`
   - `HEYGEN_CHARACTER_TYPE=avatar`
   - `HEYGEN_AVATAR_ID_A/B` (из `List All Avatars (V2)`)
   - `VIDEO_PLAYER_A=MEDIA_A_MP4`, `VIDEO_PLAYER_B=MEDIA_B_MP4`
   - `PREBUFFER_TURNS_PER_SPEAKER=3` (если хочешь заранее сгенерировать ролики)
   - `HEYGEN_MAX_RETRIES=3` (повторы при 5xx от HeyGen)
   - `HEYGEN_STATUS_MAX_RETRIES=3` (повторы при сбоях status)
2) В OBS добавь **Media Source**:
   - `MEDIA_A_MP4`, `MEDIA_B_MP4` (файл любой, будет заменяться скриптом)
3) Запуск:
   - `python -m src.main`

## YouTube chat → topic control
Включи `YOUTUBE_CHAT_ENABLE=1` и заполни:
- `YOUTUBE_BROADCAST_ID` или `YOUTUBE_LIVE_CHAT_ID`
- API key **или** OAuth (`CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN`)
Команда в чате: `!topic новая тема`

## Следующие шаги
См. `docs/plan_v2.md` — актуальные задачи и «вау‑эффекты» для сдачи.
