# Архитектура решения: минимальный, но зачётный MVP

## Цель
- Два автономных агента обсуждают научную тему голосом в прямом эфире.
- Визуальные аватары: достаточно статичных изображений.
- Контекст диалога сохраняется, транскрипт доступен.

## Принципы упрощения
- Один Python‑скрипт вместо многоуровневых сервисов.
- Без multi‑agent фреймворков — только `history` и чередование реплик.
- Без lip‑sync: при воспроизведении аудио активный аватар подсвечивается автоматически.
- OBS отвечает только за визуальную сцену и RTMP.
- Подсветка/переключение сцен — автоматом через OBS WebSocket или аудиореактивные фильтры.
- Основной аудио‑тракт фиксирован: OBS сам проигрывает mp3 через Media Source.
- Фолбэк: virtual audio device → OBS Audio Input Capture.

## Компоненты

### 1) Оркестрация диалога (Python)
- Хранит `conversation_history` (список реплик).
- Хранит `running_summary` (2–4 предложения) и обновляет при разрастании контекста.
- По очереди вызывает агента A и агента B.
- Держит очередь и pre‑generate следующий ход, пока играет текущий.
- Следит за `queue_floor` (минимум 2 готовые реплики).
- Сохраняет транскрипт в файл (txt/json).
- Тема берётся из `TOPIC` (env) или `topic.txt` (проверка и hot‑reload раз в 2–5 минут).
- Опционально: источник темы из чата (YouTube API), если есть время.

Псевдологика (mini state machine):
```
history, summary, queue = [], "", []
while True:
    ensure_queue_floor(queue, summary, history, min_items=2)
    play_next(queue)              # OBS plays mp3 + scene switch
    prefetch_next(queue, summary, history)
```

### 2) LLM‑агенты
- Agent A — Scientist (аналитик).
- Agent B — Skeptic (критик).
- Один провайдер: OpenAI или Claude.
- Retry/backoff для LLM: `max_retries=5`, exponential backoff, `timeout_s=60`, логирование причин ретраев (429/5xx/timeout).
- В system prompt:
  - «Если не уверен — отмечай как гипотезу».
  - «Не давай клинических рекомендаций».
  - «Ссылайся на типы источников без выдуманных ссылок».
  - «Длина реплики: 6–12 секунд (1–3 предложения)».
- Ритм дискуссии: hypothesis → evidence → experiment, каждые 4 хода Scientist предлагает тест, Skeptic критикует.
- Формат хода:
  - Scientist: тезис → 1 аргумент → предложение проверки/эксперимента.
  - Skeptic: 1 контраргумент → что измерить/какие confounders.
- Анти‑повторы: «не повторяй идеи из последних 3 ходов».

### 3) Голос (TTS)
- OpenAI TTS или ElevenLabs.
- На выходе: `agent_a_001.mp3`, `agent_b_001.mp3`.
- Генерация с буфером: следующий файл готовится, пока играет текущий.
- Если очередь пуста: короткая пауза без переключений или нейтральный bridging‑фрагмент.
- Retry/backoff: `max_retries=5`, exponential backoff, `timeout_s=60`, логирование причин ретраев (429/5xx/timeout).

### 4) Визуал (OBS)
- Два PNG‑аватара в split‑screen.
- При воспроизведении аудио активный аватар подсвечивается или чуть увеличивается автоматически.
- Лип‑синк не требуется.
- Вариант 1 (без плагинов): две сцены (A speaking / B speaking), скрипт переключает через OBS WebSocket.
- Вариант 2: аудиореактивный фильтр, привязанный к `AUDIO_PLAYER`.
- OBS naming convention (минимальный контракт):
  - Scenes: `SCENE_A`, `SCENE_B`, `SCENE_IDLE`.
  - Sources: `AVATAR_A`, `AVATAR_B`, `AUDIO_PLAYER`.
  - WebSocket: `OBS_HOST`, `OBS_PORT`, `OBS_PASSWORD`.

## Self-check перед стартом
Перед запуском скрипт делает проверку: подключается к OBS WebSocket, проверяет наличие сцен `SCENE_A/SCENE_B/SCENE_IDLE` и источника `AUDIO_PLAYER`, затем задаёт короткий тестовый mp3 через `SetInputSettings` и выполняет `RestartMedia`; если в микшере OBS нет уровня или команды не проходят — старт блокируется до исправления.

### 5) Стриминг
- OBS → YouTube Live (RTMP).
- Python‑скрипт запускается отдельно и по очереди пишет mp3, синхронизируя сцену.
- Аудио проигрывает OBS: Media Source; сцена переключается детерминированно:
  - `SetCurrentProgramScene(SCENE_A|SCENE_B)`
  - `SetInputSettings(AUDIO_PLAYER, file=next.mp3)`
  - `RestartMedia(AUDIO_PLAYER)`
- План B: unlisted‑стрим + локальная запись в OBS (VOD при сбоях Live).
- Фолбэк аудио‑тракт: вывод звука в virtual audio device, OBS захватывает его как Audio Input Capture (Linux: PulseAudio/PipeWire sink; Win: VB-Audio; macOS: BlackHole).
- Фолбэк проигрывания из Python: `ffplay`/`mpv` с явным device/sink.

## Поток данных
1. Тема (`TOPIC` env или `topic.txt`) → Python‑скрипт.
2. Agent A генерирует реплику → TTS → `agent_a_###.mp3`.
3. Agent B генерирует реплику → TTS → `agent_b_###.mp3`.
4. OBS (Media Source) проигрывает mp3, скрипт переключает сцену до `RestartMedia`.
5. Параллельно pre‑generate следующий ход (очередь + queue_floor=2).
6. Транскрипт сохраняется локально с метаданными.

## Контекст диалога
- `conversation_history` содержит последние 10–12 реплик.
- `running_summary` (2–4 предложения) обновляется при разрастании контекста (по длине history).
- В prompt передаётся `summary + last 10–12 turns`.
- Summary‑prompt: «только перефразируй сказанное, не добавляй новых фактов; если не уверен — пропусти».

## Автономность (ключевая формулировка для описания)
Agents autonomously generate discussion turns based on shared conversation memory without human intervention.

## Риски и минимизация
- Если TTS медленный — pre‑generate следующий ход и держать очередь.
- Если OBS сложен — сцены A/B и автоматическое переключение через WebSocket.
- Если звук не попадает в стрим — проверить Media Source и аудио‑микшер OBS; для фолбэка — virtual audio device + Audio Input Capture.
- Если YouTube Live даёт сбои — unlisted‑стрим + локальная запись в OBS.

## Итоговые артефакты
- Ссылка на стрим или запись.
- Короткое описание сборки (Python + LLM + TTS + OBS).
- Транскрипт диалога с provenance: timestamp, speaker_id, turn_id, latency (LLM/TTS), model name.
