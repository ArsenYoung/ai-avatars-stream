# src/orchestrator.py
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, List, Optional

from openai import OpenAI

from src.llm import LLMConfig, generate_turn
from src.summarize import SummarizeConfig, should_summarize, summarize
from src.topic import TopicConfig, TopicProvider
from src.tts import TTSConfig, TTS


@dataclass
class Turn:
    turn_id: int
    speaker: str              # "A" | "B"
    text: str
    audio_file: str           # absolute path
    llm_latency: float = 0.0
    tts_latency: float = 0.0
    model: str = ""
    prompt_version: str = ""
    summary_len: int = 0


class Orchestrator:
    def __init__(
        self,
        obs,
        *,
        scene_a: str,
        scene_b: str,
        scene_idle: str,
        audio_player: str,
        audio_dir: str,
        transcript_path: str,
        history_max: int = 48,
        min_queue_items: int = 2,
        poll_ms: int = 300,
        idle_sleep_s: float = 1.0,
    ):
        self.obs = obs
        self.scene_a = scene_a
        self.scene_b = scene_b
        self.scene_idle = scene_idle
        self.audio_player = audio_player

        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = Path(transcript_path)
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)

        self.history_max = history_max
        self.min_queue_items = min_queue_items
        self.poll_ms = poll_ms
        self.idle_sleep_s = idle_sleep_s

        self.queue: Deque[Turn] = deque()
        self._queue_lock = threading.Lock()
        self.turn_seq = 0

        self._dur_cache: dict[str, float] = {}
        self._ffprobe = shutil.which("ffprobe")
        self.playback_pad_s = float(os.getenv("PLAYBACK_PAD_S", "0.15"))
        self.text_only = os.getenv("TEXT_ONLY", "").strip() == "1"
        self.text_only_sleep_s = float(os.getenv("TEXT_ONLY_SLEEP_S", "0.5"))

        self.client = OpenAI()
        self.llm_cfg = LLMConfig(model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
        self.tts_cfg = TTSConfig(
            model=os.getenv("TTS_MODEL", "gpt-4o-mini-tts"),
            fmt=os.getenv("TTS_FORMAT", "mp3"),
        )
        self.tts = TTS(self.client, self.tts_cfg)
        self.voice_a = os.getenv("TTS_VOICE_A", "alloy")
        self.voice_b = os.getenv("TTS_VOICE_B", "verse")
        self.prompt_version = os.getenv("PROMPT_VERSION", "v1")
        self.anchor_case = os.getenv("ANCHOR_CASE", "").strip()
        self.roundup_every_n = int(os.getenv("ROUNDUP_EVERY_N", "6"))
        self.steelman_every_n = int(os.getenv("STEELMAN_EVERY_N", "8"))
        self.steelman_a_offset = int(os.getenv("STEELMAN_A_OFFSET", "4"))
        self.steelman_b_offset = int(os.getenv("STEELMAN_B_OFFSET", "0"))
        self.stream_intro_turn_a = int(os.getenv("STREAM_INTRO_TURN_A", "1"))
        self.stream_intro_turn_b = int(os.getenv("STREAM_INTRO_TURN_B", "2"))
        self.max_turns = int(os.getenv("MAX_TURNS", "25"))

        self.summary_cfg = SummarizeConfig(
            model=os.getenv("SUMMARY_MODEL", self.llm_cfg.model),
            every_n_turns=int(os.getenv("SUMMARY_EVERY_N", "6")),
        )
        self.running_summary = ""
        self.history: List[Dict[str, str]] = []

        self.topic_provider = TopicProvider(
            TopicConfig(
                topic_env=os.getenv("TOPIC_ENV", "TOPIC"),
                topic_file=os.getenv("TOPIC_FILE", "topic.txt"),
                reload_s=int(os.getenv("TOPIC_RELOAD_S", "180")),
            )
        )

        self._stop_event = threading.Event()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._last_scene: Optional[str] = None
        self._method_keywords = [
            "экранир",
            "контрол",
            "шум",
            "помех",
            "погреш",
            "конфунд",
            "сред",
            "вакуум",
            "изоля",
        ]
        self._repeat_terms = [
            "линзир",
            "системат",
            "многочаст",
            "газ",
            "рентген",
            "потенциал",
            "смещ",
            "центр масс",
            "центра масс",
        ]
        self.test_cycle = [
            "rotation curves",
            "weak lensing",
            "satellite dynamics",
            "cluster collisions",
            "CMB+BAO",
            "direct detection",
            "structure growth",
        ]
        self.test_class_window = int(os.getenv("TEST_CLASS_WINDOW", "10"))
        self.test_class_max = int(os.getenv("TEST_CLASS_MAX", "2"))
        # Order matters: more specific classes first to avoid weak lensing capturing clusters.
        self._test_class_specs = [
            ("cluster collisions", {"any": ["скоплен", "cluster", "bullet", "el gordo", "столкнов"]}),
            ("weak lensing", {"any": ["линзир", "weak lensing", "слабое линзирование"]}),
            ("rotation curves", {"all": ["крив", "вращ"], "any": ["rotation curve"]}),
            ("satellite dynamics", {"any": ["спутник", "сателлит", "subhalo", "substructure", "субструктур", "орбит"]}),
            ("CMB+BAO", {"any": ["cmb", "bao", "акустич", "реликт", "микроволнов"]}),
            ("direct detection", {"any": ["direct detection", "прямое обнаруж", "прямого обнаруж", "ксенон", "xenon", "argon", "dama", "lux", "supercdms"]}),
            ("structure growth", {"any": ["рост структур", "growth of structure", "sigma_8", "sigma8", "fs8", "кластеризац", "формирован"]}),
        ]
        self.test_switch_every_n = int(os.getenv("TEST_SWITCH_EVERY_N", "4"))
        if self.max_turns > 0:
            default_final_a = max(1, self.max_turns - 2)
            if default_final_a % 2 == 0:
                default_final_a = max(1, default_final_a - 1)
            default_final_b = max(2, self.max_turns - 1)
            if default_final_b % 2 == 1:
                default_final_b = max(2, default_final_b - 1)
            default_closing = self.max_turns
        else:
            default_final_a = 21
            default_final_b = 22
            default_closing = default_final_b + 1
        self.final_round_a = int(os.getenv("FINAL_ROUND_A", str(default_final_a)))
        self.final_round_b = int(os.getenv("FINAL_ROUND_B", str(default_final_b)))
        self.stream_closing_turn = int(
            os.getenv("STREAM_CLOSING_TURN", str(default_closing))
        )
        self.obs_interp_block = int(os.getenv("OBS_INTERP_BLOCK", "4"))

    def _queue_len(self) -> int:
        with self._queue_lock:
            return len(self.queue)

    def _next_speaker(self, turn_id: int) -> str:
        return "A" if (turn_id % 2 == 1) else "B"

    def _scene_for_speaker(self, speaker: str) -> str:
        return self.scene_a if speaker == "A" else self.scene_b

    def _audio_out_path(self, turn_id: int, speaker: str) -> str:
        fname = f"turn_{turn_id:05d}_{speaker}.{self.tts_cfg.fmt}"
        return str((self.audio_dir / fname).resolve())

    def _write_transcript(self, turn: Turn) -> None:
        evt = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "turn_id": turn.turn_id,
            "speaker": turn.speaker,
            "text": turn.text,
            "audio_file": turn.audio_file,
            "llm_latency": round(turn.llm_latency, 4),
            "tts_latency": round(turn.tts_latency, 4),
            "model": turn.model,
            "prompt_version": turn.prompt_version,
            "summary_len": turn.summary_len,
            "source": "llm_tts",
        }
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            f.flush()

    def _normalize_media_state(self, state) -> Optional[str]:
        if state is None:
            return None
        s = str(state).strip().lower()
        if "obs_media_state_" in s:
            s = s.replace("obs_media_state_", "")
        if "playing" in s:
            return "playing"
        if "ended" in s:
            return "ended"
        if "stopped" in s:
            return "stopped"
        if "paused" in s:
            return "paused"
        return s or None

    def _get_audio_duration(self, path: str) -> float:
        if path in self._dur_cache:
            return self._dur_cache[path]
        if not self._ffprobe:
            return 0.0
        try:
            out = subprocess.check_output(
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                text=True,
            ).strip()
            dur = float(out) if out else 0.0
        except Exception:
            dur = 0.0
        dur = max(0.3, dur)
        self._dur_cache[path] = dur
        return dur

    def _is_methodology_loop(self, history: List[Dict[str, str]]) -> bool:
        if len(history) < 2:
            return False
        last = history[-1]["text"].lower()
        prev = history[-2]["text"].lower()
        return (
            any(k in last for k in self._method_keywords)
            and any(k in prev for k in self._method_keywords)
        )

    def _is_term_repeat_loop(self, history: List[Dict[str, str]]) -> bool:
        if len(history) < 2:
            return False
        last = history[-1]["text"].lower()
        prev = history[-2]["text"].lower()
        return any(t in last and t in prev for t in self._repeat_terms)

    def _used_discriminator_phrase(self, history: List[Dict[str, str]]) -> bool:
        if not history:
            return False
        last = history[-1]["text"].lower()
        return ("различает" in last) and ("потому что" in last)

    def _classify_test(self, text: str) -> Optional[str]:
        t = text.lower()
        for label, spec in self._test_class_specs:
            all_req = spec.get("all", [])
            any_req = spec.get("any", [])
            if all_req and not all(k in t for k in all_req):
                continue
            if any_req and not any(k in t for k in any_req):
                continue
            return label
        return None

    def _recent_test_classes(self, history: List[Dict[str, str]], window: int) -> List[str]:
        if not history:
            return []
        recent = history[-window:] if window > 0 else history
        classes: List[str] = []
        for h in recent:
            c = self._classify_test(h["text"])
            if c:
                classes.append(c)
        return classes

    def _wait_playback_end(self) -> None:
        # Polling fallback when ffprobe is not available
        start_t = time.time()
        start_timeout_s = 5.0
        max_play_s = 60.0
        play_start_t = None

        while True:
            st = self.obs.get_media_status(self.audio_player)
            state = (st or {}).get("mediaState") or (st or {}).get("state")
            norm = self._normalize_media_state(state)

            if play_start_t is None and norm in ("playing", "ended", "stopped"):
                play_start_t = time.time()

            if play_start_t is not None and norm in ("ended", "stopped"):
                return

            if play_start_t is None and (time.time() - start_t) >= start_timeout_s:
                raise RuntimeError("Media did not start playing within 5s. Check OBS input settings.")

            if play_start_t is not None and (time.time() - play_start_t) >= max_play_s:
                return

            time.sleep(self.poll_ms / 1000.0)

    def prefetch_next(self) -> None:
        with self._queue_lock:
            next_turn_id = self.turn_seq + 1
            speaker = self._next_speaker(next_turn_id)
            history_snapshot = list(self.history)
            if self.history_max > 0:
                history_snapshot = history_snapshot[-self.history_max:]
            summary_snapshot = self.running_summary
        if self.max_turns > 0 and next_turn_id > self.max_turns:
            self._stop_event.set()
            return
        extra_rules: List[str] = []
        is_closing = next_turn_id == self.stream_closing_turn
        if (not is_closing) and self.roundup_every_n > 0 and (next_turn_id % self.roundup_every_n == 0):
            extra_rules.append(
                "Смена уровня: 1–2 предложения, три коротких элемента: "
                "(1) что сейчас сильнее поддержано наблюдениями, "
                "(2) что могло бы перевернуть, "
                "(3) следующий самый решающий тест."
            )
        if next_turn_id == self.stream_intro_turn_a and speaker == "A":
            extra_rules.append(
                "Начало стрима: короткое приветствие + тема + формат дискуссии."
            )
        if next_turn_id == self.stream_intro_turn_b and speaker == "B":
            extra_rules.append(
                "Старт обсуждения: кратко обозначь позиции сторон и задай первый содержательный вызов."
            )
        if self._is_methodology_loop(history_snapshot):
            extra_rules.append(
                "Анти‑петля: дай конкретный ожидаемый сигнал ИЛИ назови минимум данных для выбора модели."
            )
        if self._is_term_repeat_loop(history_snapshot):
            extra_rules.append(
                "Не повторяй мотив «линзирование/смещения/центр масс/газ/многочастотность» два хода подряд — "
                "переключись на другой класс теста и добавь новый измеримый критерий."
            )
        if self._used_discriminator_phrase(history_snapshot):
            extra_rules.append(
                "Не используй шаблон «Наблюдение X различает A и B, потому что…» в этом ходе — "
                "перефразируй естественно."
            )
        blocked_classes: List[str] = []
        if self.test_class_window > 0 and self.test_class_max > 0:
            recent_classes = self._recent_test_classes(history_snapshot, self.test_class_window)
            if recent_classes:
                blocked_classes = sorted(
                    {c for c in recent_classes if recent_classes.count(c) >= self.test_class_max}
                )
                if blocked_classes:
                    allowed = [c for c, _ in self._test_class_specs if c not in blocked_classes]
                    rule = (
                        f"Класс теста нельзя использовать чаще {self.test_class_max} раз за "
                        f"{self.test_class_window} ходов. Сейчас заблокированы: "
                        f"{', '.join(blocked_classes)}."
                    )
                    if allowed:
                        rule += f" Выбери другой класс: {', '.join(allowed)}."
                    extra_rules.append(rule)
                if recent_classes.count("cluster collisions") >= self.test_class_max:
                    extra_rules.append(
                        "Смена плоскости: следующий тест не про скопления/кластеры (включая столкновения)."
                    )
        if (not is_closing) and next_turn_id % 3 == 0:
            extra_rules.append("Задай короткий вопрос оппоненту.")
        if (not is_closing) and self.steelman_every_n > 0:
            if speaker == "A" and (next_turn_id % self.steelman_every_n == self.steelman_a_offset):
                extra_rules.append("Steelman: скажи фразой «Самая сильная боль MOND — …».")
            if speaker == "B" and (next_turn_id % self.steelman_every_n == self.steelman_b_offset):
                extra_rules.append("Steelman: скажи фразой «Самый сильный аргумент ΛCDM — …».")
        if (not is_closing) and self.test_switch_every_n > 0 and next_turn_id > 1:
            if next_turn_id % self.test_switch_every_n == 1:
                idx = (next_turn_id // self.test_switch_every_n) % len(self.test_cycle)
                suggested = self.test_cycle[idx]
                if blocked_classes and suggested in blocked_classes:
                    for i in range(len(self.test_cycle)):
                        cand = self.test_cycle[(idx + i) % len(self.test_cycle)]
                        if cand not in blocked_classes:
                            suggested = cand
                            break
                extra_rules.append(
                    f"Смени тип теста (но сохрани DM vs MOND): {suggested}."
                )
        if self.obs_interp_block > 0:
            block_idx = (next_turn_id - 1) // self.obs_interp_block
            if block_idx % 2 == 0:
                extra_rules.append(
                    "Сейчас фокус на наблюдениях и сигнатурах (конкретные тесты, минимум интерпретаций)."
                )
            else:
                extra_rules.append(
                    "Сейчас фокус на интерпретации и последствиях (как результат меняет баланс, но с тестом)."
                )
        if next_turn_id == self.final_round_a and speaker == "A":
            extra_rules.append(
                "Финальный раунд A: что на сегодня сильнее и почему — 2 коротких пункта."
            )
        if next_turn_id == self.final_round_b and speaker == "B":
            extra_rules.append(
                "Финальный раунд B: steelman позиции A + что бы тебя переубедило — 2 пункта."
            )
        if next_turn_id == self.stream_closing_turn:
            extra_rules.append(
                "Закрытие стрима: короткий итог + благодарность зрителям + прощание и явное завершение. "
                "Уложись в 2 предложения."
            )

        topic = self.topic_provider.get()
        text, llm_latency = generate_turn(
            self.client,
            self.llm_cfg,
            speaker=speaker,
            topic=topic,
            running_summary=summary_snapshot,
            history=history_snapshot,
            anchor_case=self.anchor_case,
            turn_id=next_turn_id,
            extra_rules=extra_rules or None,
        )

        if self.text_only:
            audio_path = ""
            tts_latency = 0.0
        else:
            voice = self.voice_a if speaker == "A" else self.voice_b
            audio_path = self._audio_out_path(next_turn_id, speaker)
            tts_latency = self.tts.speak(text=text, voice=voice, out_path=audio_path)

        turn = Turn(
            turn_id=next_turn_id,
            speaker=speaker,
            text=text,
            audio_file=audio_path,
            llm_latency=llm_latency,
            tts_latency=tts_latency,
            model=self.llm_cfg.model,
            prompt_version=self.prompt_version,
        )

        with self._queue_lock:
            self.turn_seq = next_turn_id
            self.queue.append(turn)
            self.history.append({"speaker": speaker, "text": text})
            if self.history_max > 0 and len(self.history) > self.history_max:
                self.history = self.history[-self.history_max:]
            history_for_sum = list(self.history)
            summary_before = self.running_summary
            turn_count = len(self.history)

        if should_summarize(turn_count, self.summary_cfg):
            new_summary, _ = summarize(
                self.client,
                self.summary_cfg,
                running_summary=summary_before,
                history=history_for_sum,
            )
            with self._queue_lock:
                if new_summary:
                    self.running_summary = new_summary
            turn.summary_len = len(self.running_summary)
        else:
            turn.summary_len = len(summary_before)

    def _prefetch_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._queue_len() < self.min_queue_items:
                try:
                    self.prefetch_next()
                except Exception as e:
                    print(f"[prefetch] error: {e}")
                    time.sleep(1.0)
            else:
                time.sleep(0.2)

    def start_prefetch(self) -> None:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return
        self._prefetch_thread = threading.Thread(target=self._prefetch_loop, daemon=True)
        self._prefetch_thread.start()

    def play_next(self) -> Optional[Turn]:
        with self._queue_lock:
            if not self.queue:
                turn = None
            else:
                turn = self.queue.popleft()

        if turn is None:
            if not self.text_only and self._last_scene != self.scene_idle:
                self.obs.set_scene(self.scene_idle)
                self._last_scene = self.scene_idle
            time.sleep(self.idle_sleep_s)
            return None

        if not self.text_only:
            next_scene = self._scene_for_speaker(turn.speaker)
            if self._last_scene != next_scene:
                self.obs.set_scene(next_scene)
                self._last_scene = next_scene
            self.obs.set_media_file(self.audio_player, turn.audio_file)
            self.obs.restart_media(self.audio_player)

        self._write_transcript(turn)
        if self.text_only:
            time.sleep(self.text_only_sleep_s)
        elif self._ffprobe:
            dur = self._get_audio_duration(turn.audio_file)
            time.sleep(dur + self.playback_pad_s)
        else:
            self._wait_playback_end()
        return turn

    def run_forever(self) -> None:
        self.start_prefetch()
        while True:
            self.play_next()
