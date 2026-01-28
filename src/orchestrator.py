# src/orchestrator.py
from __future__ import annotations

import json
import os
import subprocess
import time
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class Turn:
    turn_id: int
    speaker: str              # "A" | "B"
    audio_file: str           # absolute path
    text: str = ""            # empty in CP3 (no LLM)


class Orchestrator:
    def __init__(self, obs, *, scene_a: str, scene_b: str, scene_idle: str, audio_player: str,
                 audio_dir: str, transcript_path: str,
                 min_queue_items: int = 2, poll_ms: int = 300, idle_sleep_s: float = 1.0):
        self.obs = obs
        self.scene_a = scene_a
        self.scene_b = scene_b
        self.scene_idle = scene_idle
        self.audio_player = audio_player

        self.audio_dir = Path(audio_dir)
        self.transcript_path = Path(transcript_path)
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)

        self.min_queue_items = min_queue_items
        self.poll_ms = poll_ms
        self.idle_sleep_s = idle_sleep_s

        self.queue: List[Turn] = []
        self.turn_seq = 0
        self._pool: List[Path] = []
        self._pool_idx = 0
        self._dur_cache: dict[str, float] = {}
        self._ffprobe = shutil.which("ffprobe")
        self.playback_pad_s = float(os.getenv("PLAYBACK_PAD_S", "0.15"))

    def load_pool(self) -> None:
        if not self.audio_dir.exists():
            raise RuntimeError(f"AUDIO_DIR not found: {self.audio_dir}")

        files = sorted([p for p in self.audio_dir.glob("*.mp3")])
        if not files:
            raise RuntimeError(f"No mp3 files in {self.audio_dir}")

        self._pool = files
        self._pool_idx = 0

    def _next_from_pool(self) -> Path:
        p = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % len(self._pool)
        return p

    def _infer_speaker(self, filename: str) -> str:
        name = filename.lower()
        if name.startswith("a_") or name.startswith("agent_a") or name.startswith("scientist"):
            return "A"
        if name.startswith("b_") or name.startswith("agent_b") or name.startswith("skeptic"):
            return "B"
        # fallback: alternate by turn id
        return "A" if (self.turn_seq % 2 == 1) else "B"

    def ensure_queue_floor(self) -> None:
        while len(self.queue) < self.min_queue_items:
            p = self._next_from_pool()
            self.turn_seq += 1
            speaker = self._infer_speaker(p.name)
            self.queue.append(Turn(
                turn_id=self.turn_seq,
                speaker=speaker,
                audio_file=str(p.resolve()),
                text=""  # CP3: no text
            ))

    def _scene_for_speaker(self, speaker: str) -> str:
        return self.scene_a if speaker == "A" else self.scene_b

    def _write_transcript(self, turn: Turn) -> None:
        evt = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "turn_id": turn.turn_id,
            "speaker": turn.speaker,
            "audio_file": turn.audio_file,
            "text": turn.text,
            "source": "preloaded",
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

    def _wait_playback_end(self) -> None:
        # Polling is robust for MVP: wait for playback to start, then for it to end
        start_t = time.time()
        start_timeout_s = 5.0
        max_play_s = 60.0
        play_start_t = None

        while True:
            st = self.obs.get_media_status(self.audio_player)
            state = (st or {}).get("mediaState") or (st or {}).get("state")  # depending on client wrapper
            norm = self._normalize_media_state(state)

            # Short clips can skip PLAYING and go straight to ENDED
            if play_start_t is None and norm in ("playing", "ended", "stopped"):
                play_start_t = time.time()

            if play_start_t is not None and norm in ("ended", "stopped"):
                return

            if play_start_t is None and (time.time() - start_t) >= start_timeout_s:
                raise RuntimeError("Media did not start playing within 5s. Check OBS input settings.")

            if play_start_t is not None and (time.time() - play_start_t) >= max_play_s:
                return

            time.sleep(self.poll_ms / 1000.0)

    def play_next(self) -> Optional[Turn]:
        if not self.queue:
            # bridging behavior: idle scene + wait, no A/B twitching
            self.obs.set_scene(self.scene_idle)
            time.sleep(self.idle_sleep_s)
            return None

        turn = self.queue.pop(0)

        # Switch scene first, then set file, then restart media
        self.obs.set_scene(self._scene_for_speaker(turn.speaker))
        self.obs.set_media_file(self.audio_player, turn.audio_file)
        self.obs.restart_media(self.audio_player)

        self._write_transcript(turn)
        if self._ffprobe:
            dur = self._get_audio_duration(turn.audio_file)
            time.sleep(dur + self.playback_pad_s)
        else:
            self._wait_playback_end()
        return turn

    def run_forever(self) -> None:
        self.load_pool()
        while True:
            self.ensure_queue_floor()
            self.play_next()
