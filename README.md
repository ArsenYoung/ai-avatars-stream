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

## OBS preset (AI_Avatars_3_modes.json)
В проекте есть конфигурация OBS `obs/AI_Avatars_3_modes.json` со сценами/источниками:
- Сцены: `SCENE_PNG`, `SCENE_STREAM`, `SCENE_VIDEO_A`, `SCENE_VIDEO_B`, `SCENE_IDLE`, `SCENE_OVERLAY`
- Источники: `PNG_AVATAR_A/B`, `STREAM_AVATAR_A/B`, `MEDIA_A_MP4`, `MEDIA_B_MP4`, `AUDIO_PLAYER`, `TXT_*`

Переключение режимов — только через `.env` (OBS не трогаем).  
Режим выбирается одной переменной `AVATAR_MODE` (она имеет приоритет над legacy‑флагами).
Можно включить `OBS_STRICT=1`, чтобы падать сразу при несовпадении имен источников/фильтров.

## Quickstart: режим 1 (PNG + TTS, рекомендован)
1) Скопируй `.env.example` → `.env`
2) Убедись, что `AVATAR_MODE=png` и источники соответствуют OBS‑конфигу
3) Запуск: `python -m src.main`

Транскрипт пишется в `TRANSCRIPT_PATH`.

## Выбор режима (через .env)
Важно: HeyGen может требовать платного плана и имеет лимиты/квоты.

### `AVATAR_MODE=png` (PNG + TTS, рекомендован)
Использует сцены/источники по умолчанию из `.env.example`:
`SCENE_PNG`, `PNG_AVATAR_A/B`, `AUDIO_PLAYER`.

### `AVATAR_MODE=heygen_stream` (HeyGen Streaming)
Нужно переключить сцены/источники под стриминг:
`SCENE_STREAM`, `STREAM_AVATAR_A/B`.  
Также требуется `HEYGEN_API_KEY` и запуск `stream_server`.
В OBS Browser Source:
- `http://127.0.0.1:8099/agent.html?agent=A`
- `http://127.0.0.1:8099/agent.html?agent=B`

### `AVATAR_MODE=heygen_video` (HeyGen MP4)
Нужно переключить сцены под видео:
`SCENE_VIDEO_A`, `SCENE_VIDEO_B`, idle = `SCENE_IDLE`, источники `MEDIA_A_MP4`, `MEDIA_B_MP4`.  
Рекомендуется оставить тайминги `SCENE_SWITCH_DELAY_S` и `MEDIA_START_*`.
Чтобы сначала сгенерировать N видео, установи `PREBUFFER_TOTAL_TURNS=N` (например, 20).

### `AVATAR_MODE=text` (только текст)
Без OBS/TTS, только транскрипт.

Примечание: в текущем OBS конфиге нет `SCENE_VIDEO_IDLE`, поэтому idle‑сцена — `SCENE_IDLE`.

## Стрим на YouTube
1) Настрой трансляцию в OBS: выбери сервис YouTube и укажи Stream Key (или RTMPS URL).
2) Запусти проект как обычно (`python -m src.main`).
3) Опционально: `OBS_AUTO_START_STREAM=1` — автостарт стрима после prebuffer (если он включён).
4) Если хочешь настроить стрим из `.env`, задай:
   - `OBS_STREAM_SERVICE_TYPE=rtmp_common`
   - `OBS_STREAM_SERVICE=YouTube - RTMPS` (или другое имя сервиса из OBS)
   - `OBS_STREAM_KEY=...`
   - Опционально `OBS_STREAM_SERVER=...`
   - И включи `OBS_STREAM_APPLY=1`

## Смена темы через YouTube чат
1) `YOUTUBE_CHAT_ENABLE=1`
2) Укажи `YOUTUBE_BROADCAST_ID` или `YOUTUBE_LIVE_CHAT_ID`
3) Авторизация: либо `YOUTUBE_API_KEY`, либо OAuth (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`)
4) Команда в чате: `!topic новая тема` (префикс настраивается через `YOUTUBE_TOPIC_PREFIX`)

## FAQ
- Чёрный экран: проверь корректность `VIDEO_PLAYER_A/B` или `AUDIO_PLAYER`, увеличь `MEDIA_START_TIMEOUT_S`, `MEDIA_START_RETRIES`, добавь `SCENE_SWITCH_DELAY_S`.
- Первые медиа не стартуют: включи `PREBUFFER_TURNS_PER_SPEAKER=2` и проверь `MEDIA_START_*` тайминги.
- Нет подсветки: проверь `AVATAR_A_SOURCE/AVATAR_B_SOURCE` и фильтры `FILTER_DIM/FILTER_SPEAK` на обоих источниках, включи `OBS_STRICT=1`.
- Нет текста на оверлеях: проверь `OVERLAY_TOPIC/OVERLAY_STAGE/OVERLAY_SPEAKER` и соответствующие источники в OBS.
- В streaming режиме “No session yet”: нет активной сессии — проверь `HEYGEN_API_KEY`, аватары и что `stream_server` запущен.

## YouTube chat → topic control
Включи `YOUTUBE_CHAT_ENABLE=1` и заполни:
- `YOUTUBE_BROADCAST_ID` или `YOUTUBE_LIVE_CHAT_ID`
- API key **или** OAuth (`CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN`)
Команда в чате: `!topic новая тема`

## Следующие шаги
См. `docs/plan_v2.md` — актуальные задачи и «вау‑эффекты» для сдачи.
