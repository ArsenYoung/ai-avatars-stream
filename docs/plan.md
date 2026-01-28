# План работ (по чекпоинтам)

## Чекпоинт 1 — OBS‑only (ручное аудио)
Задачи:
- ~~Зафиксировать дефолты: Media Source + `AUDIO_PLAYER`, `queue_floor=2`, hot‑reload темы 2–5 мин, контекст 10–12 реплик.~~
- ~~Создать структуру проекта: `src/`, `assets/`, `audio/`, `logs/`, `transcripts/`.~~
- ~~Подготовить `.env.example` с переменными (OBS/ключи/пути/модели/голоса).~~
- ~~Зафиксировать зависимости и запуск: `requirements.txt`/`pyproject.toml`, `python -m venv .venv`, `pip install -r requirements.txt`, `python -m src.main`.~~
- ~~Указать OBS 28+ (websocket встроен) и где включить WebSocket в настройках OBS.~~
- ~~Подготовить 2 PNG‑аватара и положить в `assets/`.~~
- ~~Настроить OBS: сцены `SCENE_A`, `SCENE_B`, `SCENE_IDLE`, источник `AUDIO_PLAYER` (Media Source), `AVATAR_A`, `AVATAR_B`.~~
- ~~Включить локальную запись в OBS (план B).~~
- ~~Сделать короткий тестовый mp3 (1–2 сек).~~
- ~~Проверка: вручную подставить mp3 в `AUDIO_PLAYER`, нажать play → в записи OBS есть звук.~~

## Чекпоинт 2 — OBS WebSocket (управление сценами/аудио)
Задачи:
- Реализовать `obs_client.py`: connect, `SetCurrentProgramScene`, `SetInputSettings`, `RestartMedia`.
- Добавить `self_check()`:
  - коннект к OBS WebSocket;
  - проверка сцен/источника (`SCENE_A/SCENE_B/SCENE_IDLE`, `AUDIO_PLAYER`);
  - тестовый `SetInputSettings` + `RestartMedia`;
  - дождаться `MediaInputPlaybackStarted` или `GetMediaInputStatus=playing`.
- Проверка: `obs_client.py` переключает `SCENE_A/SCENE_B` и перезапускает `AUDIO_PLAYER` с тестовым mp3.

## Чекпоинт 3 — Оркестратор без LLM/TTS
Задачи:
- Реализовать очередь и state machine в `orchestrator.py`:
  - `ensure_queue_floor(min_items=2)`;
  - `play_next()` (scene switch → set file → restart media);
  - `prefetch_next()`.
- Ожидание конца проигрывания: `MediaInputPlaybackEnded` или polling `GetMediaInputStatus` раз в 200–500мс.
- Реализовать bridging‑поведение:
  - если очередь пуста — ставить `SCENE_IDLE` и не переключать A/B;
  - короткая пауза без дёргания сцен.
- Реализовать запись транскрипта (JSONL базовый набор полей).
- Проверка: `orchestrator` гоняет заранее готовые mp3 5 минут, сцены синхронны, тишины нет.

## Чекпоинт 4 — Полный пайплайн (LLM + TTS)
Задачи:
- Реализовать `llm.py`:
  - роли Scientist/Skeptic;
  - лимит длины 6–12 сек (1–3 предложения);
  - анти‑повторы (последние 3 хода);
  - retry/backoff `max_retries=5`, `timeout_s=60`, логирование 429/5xx/timeout.
- Реализовать `tts.py`:
  - `speak(text, voice) -> mp3_path`;
  - atomic write `tmp.mp3` → `os.replace(tmp, final)`;
  - retry/backoff `max_retries=5`, `timeout_s=60`.
- Реализовать `summarize.py`:
  - обновление `running_summary` по длине history;
  - запрет на добавление новых фактов.
- Реализовать `topic.py`:
  - `TOPIC` env или `topic.txt`;
  - hot‑reload 2–5 минут.
- Расширить транскрипт: `timestamp`, `speaker`, `turn_id`, `llm_latency`, `tts_latency`, `model`, `audio_file`, `prompt_version`, `summary_len`.
- Проверка: LLM+TTS 10–15 минут, `transcript.jsonl` пишется и flushится, очередь не падает ниже 1.
- Финальная проверка демо: YouTube Live/Unlisted, локальная запись в OBS, план B готов.
