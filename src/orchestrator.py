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
from src.heygen import guess_mime
from src.heygen_stream import HeygenStreamClient, HeygenStreamConfig, StreamSession, write_sessions_file
from src.retry import retry
from src.mode import resolve_avatar_mode


@dataclass
class Turn:
    turn_id: int
    speaker: str              # "A" | "B"
    text: str
    audio_file: str           # absolute path
    video_file: str = ""
    stream_session_id: str = ""
    stream_task_id: str = ""
    duration_est_s: float = 0.0
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
        def _env(*names: str, default: str = "") -> str:
            for name in names:
                val = os.getenv(name)
                if val is not None and str(val).strip() != "":
                    return str(val)
            return default

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
        self.playback_pad_s = float(_env("PLAYBACK_PAD_S", default="0.15"))
        self.media_start_timeout_s = float(_env("MEDIA_START_TIMEOUT_S", default="5.0"))
        self.media_start_retries = int(_env("MEDIA_START_RETRIES", default="2"))
        self.media_start_retry_sleep_s = float(_env("MEDIA_START_RETRY_SLEEP_S", default="0.2"))
        self.scene_switch_delay_s = float(_env("SCENE_SWITCH_DELAY_S", default="0.0"))
        self.highlight_pre_delay_s = float(_env("HIGHLIGHT_PRE_DELAY_S", default="0.15"))
        self.avatar_mode = resolve_avatar_mode()
        self.text_only = self.avatar_mode == "text"
        self.text_only_sleep_s = float(_env("TEXT_ONLY_SLEEP_S", default="0.5"))
        self.streaming_mode = self.avatar_mode == "heygen_stream"
        self.video_mode = self.avatar_mode == "heygen_video"
        self.video_dir = Path(os.getenv("VIDEO_DIR", "video/preloaded"))
        self.video_player_a = os.getenv("VIDEO_PLAYER_A", "MEDIA_A_MP4")
        self.video_player_b = os.getenv("VIDEO_PLAYER_B", "MEDIA_B_MP4")
        self.heygen = None
        self.heygen_dim_w = int(_env("HEYGEN_DIM_W", default="1280"))
        self.heygen_dim_h = int(_env("HEYGEN_DIM_H", default="720"))
        self.heygen_poll_s = float(_env("HEYGEN_POLL_S", default="5"))
        self.heygen_timeout_s = int(_env("HEYGEN_TIMEOUT_S", default="600"))
        self.heygen_max_retries = int(_env("HEYGEN_MAX_RETRIES", default="3"))
        self.heygen_status_max_retries = int(_env("HEYGEN_STATUS_MAX_RETRIES", default=str(self.heygen_max_retries)))
        self.heygen_upload_max_retries = int(_env("HEYGEN_UPLOAD_MAX_RETRIES", default=str(self.heygen_max_retries)))
        self.heygen_download_max_retries = int(_env("HEYGEN_DOWNLOAD_MAX_RETRIES", default=str(self.heygen_max_retries)))
        self.heygen_retry_base_delay_s = float(_env("HEYGEN_RETRY_BASE_DELAY_S", default="1.0"))
        self.heygen_retry_max_delay_s = float(_env("HEYGEN_RETRY_MAX_DELAY_S", default="12.0"))
        self.heygen_character_type = _env("HEYGEN_CHARACTER_TYPE", default="").strip().lower()
        self.heygen_avatar_id_a = _env("HEYGEN_AVATAR_ID_A", "HEYGEN_AVATAR_ID", default="")
        self.heygen_avatar_id_b = _env("HEYGEN_AVATAR_ID_B", "HEYGEN_AVATAR_ID", default="")
        self.heygen_talking_photo_id_a = (
            _env("HEYGEN_TALKING_PHOTO_ID_A", "HEYGEN_TALKING_PHOTO_ID", default="")
        )
        self.heygen_talking_photo_id_b = (
            _env("HEYGEN_TALKING_PHOTO_ID_B", "HEYGEN_TALKING_PHOTO_ID", default="")
        )
        if not self.heygen_character_type:
            if self.heygen_talking_photo_id_a or self.heygen_talking_photo_id_b:
                self.heygen_character_type = "talking_photo"
            else:
                self.heygen_character_type = "avatar"
        if self.heygen_character_type == "talking_photo":
            self.heygen_character_id_a = self.heygen_talking_photo_id_a or self.heygen_avatar_id_a
            self.heygen_character_id_b = self.heygen_talking_photo_id_b or self.heygen_avatar_id_b
        else:
            self.heygen_character_id_a = self.heygen_avatar_id_a
            self.heygen_character_id_b = self.heygen_avatar_id_b
        self.heygen_avatar_style = _env("HEYGEN_AVATAR_STYLE", default="normal").strip() or None

        # HeyGen Streaming (LiveAvatar)
        self.stream_client: Optional[HeygenStreamClient] = None
        self.stream_sessions: Dict[str, StreamSession] = {}
        self.stream_session_file = _env("STREAM_SESSION_FILE", default="stream_sessions.json")
        self.stream_quality = _env("HEYGEN_STREAM_QUALITY", default="high")
        self.stream_video_encoding = _env("HEYGEN_STREAM_VIDEO_ENCODING", default="H264")
        self.stream_voice_rate = float(_env("HEYGEN_STREAM_VOICE_RATE", default="1.0"))
        self.stream_disable_idle_timeout = _env("HEYGEN_STREAM_DISABLE_IDLE_TIMEOUT", default="1").strip() != "0"
        self.stream_task_type = _env("HEYGEN_STREAM_TASK_TYPE", default="repeat")
        self.stream_chars_per_sec = float(_env("STREAM_CHARS_PER_SEC", default="15"))
        self.stream_min_s = float(_env("STREAM_MIN_S", default="2.0"))
        self.stream_pad_s = float(_env("STREAM_PAD_S", default="0.2"))
        self.stream_auth_mode = _env("HEYGEN_STREAM_AUTH_MODE", default="api_key").strip().lower()
        self.stream_avatar_a = _env("HEYGEN_STREAM_AVATAR_A", "HEYGEN_AVATAR_NAME_A", default="")
        self.stream_avatar_b = _env("HEYGEN_STREAM_AVATAR_B", "HEYGEN_AVATAR_NAME_B", default="")
        self.stream_avatar_id_a = _env("HEYGEN_STREAM_AVATAR_ID_A", default="")
        self.stream_avatar_id_b = _env("HEYGEN_STREAM_AVATAR_ID_B", default="")
        self.stream_voice_id_a = _env("HEYGEN_VOICE_ID_A", "HEYGEN_VOICE_ID", default="")
        self.stream_voice_id_b = _env("HEYGEN_VOICE_ID_B", "HEYGEN_VOICE_ID", default="")
        self._stream_token: Optional[str] = None
        self.stream_api_key: str = ""

        self.client = OpenAI()
        self.llm_cfg = LLMConfig(model=_env("OPENAI_MODEL", "LLM_MODEL", default="gpt-4.1-mini"))
        self.tts_cfg = TTSConfig(
            model=_env("TTS_MODEL", default="gpt-4o-mini-tts"),
            fmt=_env("TTS_FORMAT", default="mp3"),
        )
        self.tts = TTS(self.client, self.tts_cfg)
        self.voice_a = _env("TTS_VOICE_A", "VOICE_A", default="alloy")
        self.voice_b = _env("TTS_VOICE_B", "VOICE_B", default="verse")
        self.prompt_version = _env("PROMPT_VERSION", default="v1")
        self.anchor_case = _env("ANCHOR_CASE", default="").strip()
        self.bridge_phrase = _env("BRIDGE_PHRASE", default="").strip()
        self.bridge_phrase_a = _env("BRIDGE_PHRASE_A", default="Коротко: продолжим с ключевого теста.").strip()
        self.bridge_phrase_b = _env("BRIDGE_PHRASE_B", default="Ок, давай сузим до одного проверяемого теста.").strip()
        self.tts_chars_per_sec = float(_env("TTS_CHARS_PER_SEC", default="15"))
        self.roundup_every_n = int(_env("ROUNDUP_EVERY_N", default="6"))
        self.steelman_every_n = int(_env("STEELMAN_EVERY_N", default="8"))
        self.steelman_a_offset = int(_env("STEELMAN_A_OFFSET", default="4"))
        self.steelman_b_offset = int(_env("STEELMAN_B_OFFSET", default="0"))
        self.stream_intro_turn_a = int(_env("STREAM_INTRO_TURN_A", default="1"))
        self.stream_intro_turn_b = int(_env("STREAM_INTRO_TURN_B", default="2"))
        self.max_turns = int(_env("MAX_TURNS", default="25"))

        self.summary_cfg = SummarizeConfig(
            model=_env("SUMMARY_MODEL", default=self.llm_cfg.model),
            every_n_turns=int(_env("SUMMARY_EVERY_N", "SUMMARY_EVERY_N_TURNS", default="6")),
        )
        self.running_summary = ""
        self.history: List[Dict[str, str]] = []

        self.topic_provider = TopicProvider(
            TopicConfig(
                topic_env=_env("TOPIC_ENV", default="TOPIC"),
                topic_file=_env("TOPIC_FILE", default="topic.txt"),
                reload_s=int(_env("TOPIC_RELOAD_S", default="180")),
            )
        )

        self._stop_event = threading.Event()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._last_scene: Optional[str] = None
        self._yt_thread: Optional[threading.Thread] = None
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
            "эпигенетич",
            "эпигенетическ",
            "сенесц",
            "митохондр",
            "протеостаз",
            "воспал",
            "иммун",
            "стволов",
            "метабол",
            "поврежден",
        ]
        self.test_cycle = [
            "epigenetic clocks",
            "senescence markers",
            "mitochondrial function",
            "proteostasis",
            "inflammaging/immune",
            "stem cell exhaustion",
            "metabolic rate",
        ]
        self.test_class_window = int(os.getenv("TEST_CLASS_WINDOW", "10"))
        self.test_class_max = int(os.getenv("TEST_CLASS_MAX", "2"))
        # Order matters: more specific classes first to avoid generic matches.
        self._test_class_specs = [
            ("epigenetic clocks", {"any": ["эпигенет", "epigenetic", "clock", "метилир", "метил"]}),
            ("senescence markers", {"any": ["сенесц", "senescence", "p16", "p21", "sasp"]}),
            ("mitochondrial function", {"any": ["митохонд", "mitochond", "ros", "oxidative", "окисл"]}),
            ("proteostasis", {"any": ["протеостаз", "proteostasis", "autophagy", "аутофаг"]}),
            ("inflammaging/immune", {"any": ["воспал", "inflamm", "immune", "иммун", "cytokine", "циток"]}),
            ("stem cell exhaustion", {"any": ["стволов", "stem cell", "stэм", "клеточ"]}),
            ("metabolic rate", {"any": ["метабол", "metabolic", "insulin", "igf", "mTOR", "rapamycin", "метформ"]}),
        ]
        self.test_switch_every_n = int(_env("TEST_SWITCH_EVERY_N", default="4"))
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
        self.ov_scene  = os.getenv("OVERLAY_SCENE", "SCENE_OVERLAY")
        self.ov_speaker = os.getenv("OVERLAY_SPEAKER", "TXT_SPEAKER")
        self.ov_topic   = os.getenv("OVERLAY_TOPIC", "TXT_TOPIC")
        self.ov_stage   = os.getenv("OVERLAY_STAGE", "TXT_STAGE")
        self.avatar_a = os.getenv("AVATAR_A_SOURCE", "AVATAR_A")
        self.avatar_b = os.getenv("AVATAR_B_SOURCE", "AVATAR_B")
        self.f_dim    = os.getenv("FILTER_DIM", "DIM")
        self.f_speak  = os.getenv("FILTER_SPEAK", "SPEAK")
        self.enable_highlight = (not self.video_mode) and bool(self.f_speak) and bool(self.f_dim)
        if self.enable_highlight:
            try:
                self.avatar_a_exists = bool(self.obs) and self.obs.source_exists(self.avatar_a)
                self.avatar_b_exists = bool(self.obs) and self.obs.source_exists(self.avatar_b)
                self.avatar_a_has_dim = self.avatar_a_exists and self.obs.has_filter(self.avatar_a, self.f_dim)
                self.avatar_a_has_speak = self.avatar_a_exists and self.obs.has_filter(self.avatar_a, self.f_speak)
                self.avatar_b_has_dim = self.avatar_b_exists and self.obs.has_filter(self.avatar_b, self.f_dim)
                self.avatar_b_has_speak = self.avatar_b_exists and self.obs.has_filter(self.avatar_b, self.f_speak)
            except Exception:
                self.avatar_a_exists = False
                self.avatar_b_exists = False
                self.avatar_a_has_dim = False
                self.avatar_a_has_speak = False
                self.avatar_b_has_dim = False
                self.avatar_b_has_speak = False
        else:
            self.avatar_a_exists = False
            self.avatar_b_exists = False
            self.avatar_a_has_dim = False
            self.avatar_a_has_speak = False
            self.avatar_b_has_dim = False
            self.avatar_b_has_speak = False

        if self.avatar_mode == "png":
            missing = []
            if not self.f_dim or not self.f_speak:
                missing.append("FILTER_DIM/FILTER_SPEAK must be set for PNG highlight")
            if not self.avatar_a_exists:
                missing.append(f"AVATAR_A_SOURCE '{self.avatar_a}' not found in OBS")
            if not self.avatar_b_exists:
                missing.append(f"AVATAR_B_SOURCE '{self.avatar_b}' not found in OBS")
            if self.avatar_a_exists and not self.avatar_a_has_dim:
                missing.append(f"Filter '{self.f_dim}' missing on {self.avatar_a}")
            if self.avatar_a_exists and not self.avatar_a_has_speak:
                missing.append(f"Filter '{self.f_speak}' missing on {self.avatar_a}")
            if self.avatar_b_exists and not self.avatar_b_has_dim:
                missing.append(f"Filter '{self.f_dim}' missing on {self.avatar_b}")
            if self.avatar_b_exists and not self.avatar_b_has_speak:
                missing.append(f"Filter '{self.f_speak}' missing on {self.avatar_b}")
            if missing:
                raise RuntimeError("OBS highlight setup error:\\n- " + "\\n- ".join(missing))
        api_key = _env("HEYGEN_API_KEY", default="").strip()
        if self.streaming_mode:
            if not api_key:
                raise RuntimeError("HEYGEN_API_KEY is required when AVATAR_MODE=heygen_stream (or HEYGEN_STREAMING=1)")
            if not ((self.stream_avatar_a or self.stream_avatar_id_a) and (self.stream_avatar_b or self.stream_avatar_id_b)):
                raise RuntimeError(
                    "HEYGEN_STREAM_AVATAR_A/B (or HEYGEN_AVATAR_NAME_A/B) is required for streaming mode."
                )
            self.stream_api_key = api_key
            self.stream_client = HeygenStreamClient(HeygenStreamConfig(api_key=api_key))
        elif self.video_mode:
            if not api_key:
                raise RuntimeError("HEYGEN_API_KEY is required when AVATAR_MODE=heygen_video (or VIDEO_MODE=1)")
            if self.heygen_character_type not in ("avatar", "talking_photo"):
                raise RuntimeError("HEYGEN_CHARACTER_TYPE must be avatar or talking_photo.")
            if self.heygen_character_type == "avatar":
                if self.heygen_avatar_id_a and os.path.exists(self.heygen_avatar_id_a):
                    raise RuntimeError(
                        "HEYGEN_AVATAR_ID_A points to a local file path; it must be an avatar_id from HeyGen."
                    )
                if self.heygen_avatar_id_b and os.path.exists(self.heygen_avatar_id_b):
                    raise RuntimeError(
                        "HEYGEN_AVATAR_ID_B points to a local file path; it must be an avatar_id from HeyGen."
                    )
                if not (self.heygen_character_id_a and self.heygen_character_id_b):
                    raise RuntimeError(
                        "HEYGEN_AVATAR_ID_A/B is required for Create Avatar Video (V2)."
                    )
            else:
                if self.heygen_character_id_a and os.path.exists(self.heygen_character_id_a):
                    raise RuntimeError(
                        "HEYGEN_TALKING_PHOTO_ID_A points to a local file path; it must be a talking_photo_id from HeyGen."
                    )
                if self.heygen_character_id_b and os.path.exists(self.heygen_character_id_b):
                    raise RuntimeError(
                        "HEYGEN_TALKING_PHOTO_ID_B points to a local file path; it must be a talking_photo_id from HeyGen."
                    )
                if not (self.heygen_character_id_a and self.heygen_character_id_b):
                    raise RuntimeError(
                        "HEYGEN_TALKING_PHOTO_ID_A/B is required for Create Talking Photo Video (V2)."
                    )
            self.video_dir.mkdir(parents=True, exist_ok=True)
            from src.heygen import HeygenClient, HeygenConfig
            self.heygen = HeygenClient(HeygenConfig(api_key=api_key))

        self._last_topic: Optional[str] = None
        self._last_stage: Optional[str] = None

        yt_enable = _env("YOUTUBE_CHAT_ENABLE", "YOUTUBE_TOPIC_ENABLE", default="0").strip() == "1"
        self._yt_thread = None
        if yt_enable:
            try:
                from src.youtube_chat import YouTubeChatConfig, YouTubeTopicWatcher
                cfg = YouTubeChatConfig(
                    api_key=_env("YOUTUBE_API_KEY", default=""),
                    client_id=_env("YOUTUBE_CLIENT_ID", default=""),
                    client_secret=_env("YOUTUBE_CLIENT_SECRET", default=""),
                    refresh_token=_env("YOUTUBE_REFRESH_TOKEN", default=""),
                    live_chat_id=_env("YOUTUBE_LIVE_CHAT_ID", default=""),
                    broadcast_id=_env("YOUTUBE_BROADCAST_ID", default=""),
                    command_prefix=_env("YOUTUBE_TOPIC_PREFIX", default="!topic"),
                    cooldown_s=int(_env("YOUTUBE_TOPIC_COOLDOWN_S", default="180")),
                    topic_ttl_s=int(_env("YOUTUBE_TOPIC_TTL_S", default="900")),
                    allowlist=_env("YOUTUBE_CHAT_ALLOWLIST", default=""),
                    mods_only=_env("YOUTUBE_CHAT_MODS_ONLY", default="1").strip() != "0",
                )

                def _on_topic(topic: str, author: dict) -> None:
                    name = author.get("displayName") or author.get("channelId") or "unknown"
                    self.topic_provider.set_override(topic, ttl_s=cfg.topic_ttl_s, source="youtube", author=name)
                    self._write_topic_event(topic, source="youtube", author=name)

                watcher = YouTubeTopicWatcher(cfg, on_topic=_on_topic)
                self._yt_thread = threading.Thread(target=watcher.run_forever, daemon=True)
                self._yt_thread.start()
            except Exception as e:
                print(f"[youtube] disabled: {e}")

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

    def _video_out_path(self, turn_id: int, speaker: str) -> str:
        fname = f"turn_{turn_id:05d}_{speaker}.mp4"
        return str((self.video_dir / fname).resolve())

    def _heygen_character_id_for_speaker(self, speaker: str) -> Optional[str]:
        if self.heygen_character_type == "talking_photo":
            if speaker == "A":
                return self.heygen_character_id_a or None
            return self.heygen_character_id_b or None
        if speaker == "A":
            return self.heygen_character_id_a or None
        return self.heygen_character_id_b or None

    def _ensure_stream_sessions(self) -> None:
        if not self.streaming_mode or not self.stream_client:
            return
        if self.stream_sessions.get("A") and self.stream_sessions.get("B"):
            return
        if not self._stream_token:
            if self.stream_auth_mode == "session_token":
                self._stream_token = self.stream_client.create_token()
            else:
                # Default: use API key as Bearer token for streaming.* endpoints
                self._stream_token = self.stream_api_key
        token = self._stream_token
        if not self.stream_sessions.get("A"):
            self.stream_sessions["A"] = self._create_stream_session(agent="A", token=token)
        if not self.stream_sessions.get("B"):
            self.stream_sessions["B"] = self._create_stream_session(agent="B", token=token)
        write_sessions_file(self.stream_session_file, self.stream_sessions)

    def _create_stream_session(self, *, agent: str, token: str) -> StreamSession:
        avatar_name = self.stream_avatar_a if agent == "A" else self.stream_avatar_b
        avatar_id = self.stream_avatar_id_a if agent == "A" else self.stream_avatar_id_b
        voice_id = self.stream_voice_id_a if agent == "A" else self.stream_voice_id_b
        info = self.stream_client.new_session(
            token=token,
            avatar_name=avatar_name or None,
            avatar_id=avatar_id or None,
            voice_id=voice_id or None,
            voice_rate=self.stream_voice_rate,
            quality=self.stream_quality,
            version="v2",
            video_encoding=self.stream_video_encoding,
            disable_idle_timeout=self.stream_disable_idle_timeout,
        )
        session_id = str(info.get("session_id") or info.get("sessionId") or "")
        url = str(info.get("url") or "")
        access_token = str(info.get("access_token") or info.get("accessToken") or "")
        if not (session_id and url and access_token):
            raise RuntimeError(f"HeyGen streaming.new missing session fields: {info}")
        self.stream_client.start_session(token=token, session_id=session_id)
        return StreamSession(
            agent=agent,
            session_id=session_id,
            url=url,
            access_token=access_token,
            token=token,
            avatar_name=avatar_name or avatar_id or "",
            voice_id=voice_id or None,
            created_at=time.time(),
        )

    def _estimate_stream_duration(self, text: str) -> float:
        if not text:
            return self.stream_min_s
        est = max(self.stream_min_s, len(text) / max(1.0, self.stream_chars_per_sec))
        return est + self.stream_pad_s

    def _render_video_for_turn(self, turn_id: int, speaker: str, audio_path: str) -> str:
        if not self.heygen:
            return ""
        character_id = self._heygen_character_id_for_speaker(speaker)
        if not character_id:
            raise RuntimeError("HEYGEN character id is missing")
        try:
            size = os.path.getsize(audio_path)
        except OSError:
            size = -1
        mime = guess_mime(audio_path)
        print(f"[heygen] upload audio: {audio_path} ({size} bytes, {mime})")
        def _upload() -> str:
            return self.heygen.upload_asset(audio_path)
        audio_asset_id = retry(
            _upload,
            name="heygen_upload",
            max_retries=self.heygen_upload_max_retries,
            base_delay_s=self.heygen_retry_base_delay_s,
            max_delay_s=self.heygen_retry_max_delay_s,
        )
        print(f"[heygen] audio asset_id: {audio_asset_id}")
        print(f"[heygen] character_type: {self.heygen_character_type}")
        print(f"[heygen] character_id: {character_id}")
        def _gen() -> str:
            return self.heygen.generate_avatar_video(
                avatar_id=character_id,
                audio_asset_id=audio_asset_id,
                width=self.heygen_dim_w,
                height=self.heygen_dim_h,
                avatar_style=self.heygen_avatar_style,
                character_type=self.heygen_character_type,
            )
        video_id = retry(
            _gen,
            name="heygen_generate",
            max_retries=self.heygen_max_retries,
            base_delay_s=self.heygen_retry_base_delay_s,
            max_delay_s=self.heygen_retry_max_delay_s,
        )
        print(f"[heygen] video_id: {video_id}")
        def _poll() -> str:
            return self.heygen.poll_video(
                video_id,
                timeout_s=self.heygen_timeout_s,
                poll_s=self.heygen_poll_s,
            )
        video_url = retry(
            _poll,
            name="heygen_status",
            max_retries=self.heygen_status_max_retries,
            base_delay_s=self.heygen_retry_base_delay_s,
            max_delay_s=self.heygen_retry_max_delay_s,
        )
        print(f"[heygen] video_url: {video_url}")
        out_path = self._video_out_path(turn_id, speaker)
        print(f"[heygen] download -> {out_path}")
        def _download() -> None:
            self.heygen.download(video_url, out_path)
        retry(
            _download,
            name="heygen_download",
            max_retries=self.heygen_download_max_retries,
            base_delay_s=self.heygen_retry_base_delay_s,
            max_delay_s=self.heygen_retry_max_delay_s,
        )
        return out_path

    def _write_transcript(self, turn: Turn) -> None:
        evt = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "turn_id": turn.turn_id,
            "speaker": turn.speaker,
            "text": turn.text,
            "audio_file": turn.audio_file,
            "video_file": turn.video_file,
            "stream_session_id": turn.stream_session_id,
            "stream_task_id": turn.stream_task_id,
            "duration_est_s": round(turn.duration_est_s, 3) if turn.duration_est_s else 0.0,
            "llm_latency": round(turn.llm_latency, 4),
            "tts_latency": round(turn.tts_latency, 4),
            "model": turn.model,
            "prompt_version": turn.prompt_version,
            "summary_len": turn.summary_len,
            "source": "heygen_stream" if self.streaming_mode else "llm_tts",
        }
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            f.flush()

    def _write_topic_event(self, topic: str, *, source: str, author: str) -> None:
        evt = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": "topic_change",
            "topic": topic,
            "source": source,
            "author": author,
        }
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            f.flush()

    def _set_overlay(self, *, speaker: Optional[str], topic: str, stage: str) -> None:
        self.obs.set_text(self.ov_topic, f"Topic: {topic}")
        self.obs.set_text(self.ov_stage, stage)
        if speaker:
            self.obs.set_text(self.ov_speaker, speaker)
            try:
                self.obs.set_scene_item_enabled(self.ov_scene, self.ov_speaker, True)
            except Exception:
                pass
        else:
            self.obs.set_text(self.ov_speaker, "")
            try:
                self.obs.set_scene_item_enabled(self.ov_scene, self.ov_speaker, False)
            except Exception:
                pass

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
                stderr=subprocess.DEVNULL,
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

    def _wait_playback_end(self, input_name: Optional[str] = None) -> None:
        # Polling fallback when ffprobe is not available
        if input_name is None:
            input_name = self.audio_player
        start_t = time.time()
        start_timeout_s = 5.0
        max_play_s = 60.0
        play_start_t = None

        while True:
            st = self.obs.get_media_status(input_name)
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
                if recent_classes.count("epigenetic clocks") >= self.test_class_max:
                    extra_rules.append(
                        "Смена плоскости: следующий тест не про эпигенетические часы; возьми другую метрику."
                    )
        if (not is_closing) and next_turn_id % 3 == 0:
            extra_rules.append("Задай короткий вопрос оппоненту.")
        if (not is_closing) and self.steelman_every_n > 0:
            if speaker == "A" and (next_turn_id % self.steelman_every_n == self.steelman_a_offset):
                extra_rules.append("Steelman: назови самый сильный аргумент гипотезы программируемого старения.")
            if speaker == "B" and (next_turn_id % self.steelman_every_n == self.steelman_b_offset):
                extra_rules.append("Steelman: назови самый сильный аргумент гипотезы накопления повреждений.")
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
                    f"Смени тип теста/метрики в рамках старения: {suggested}."
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

        if self.text_only or self.streaming_mode:
            audio_path = ""
            tts_latency = 0.0
            video_path = ""
            duration_est_s = 0.0
        else:
            voice = self.voice_a if speaker == "A" else self.voice_b
            audio_path = self._audio_out_path(next_turn_id, speaker)
            duration_est_s = 0.0
            try:
                tts_latency = self.tts.speak(text=text, voice=voice, out_path=audio_path)
            except Exception as e:
                print(f"[tts] error: {e}. using bridge phrase + no audio")
                bridge = self.bridge_phrase or (self.bridge_phrase_a if speaker == "A" else self.bridge_phrase_b)
                if bridge:
                    text = bridge
                audio_path = ""
                tts_latency = 0.0
                duration_est_s = max(2.0, len(text) / max(1.0, self.tts_chars_per_sec))
            video_path = ""
            if self.video_mode:
                cached_path = self._video_out_path(next_turn_id, speaker)
                if os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                    video_path = cached_path
                    print(f"[heygen] reuse cached video: {cached_path}")
                else:
                    try:
                        video_path = self._render_video_for_turn(next_turn_id, speaker, audio_path)
                    except Exception as e:
                        print(f"[heygen] error: {e}")
                        raise

        turn = Turn(
            turn_id=next_turn_id,
            speaker=speaker,
            text=text,
            audio_file=audio_path,
            video_file=video_path,
            llm_latency=llm_latency,
            tts_latency=tts_latency,
            model=self.llm_cfg.model,
            prompt_version=self.prompt_version,
            duration_est_s=duration_est_s,
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
                try:
                    topic = self._last_topic or self.topic_provider.get()
                    stage = self._last_stage or ("INTRO" if self.turn_seq <= 2 else "DISCUSSION")
                    self._set_overlay(speaker=None, topic=topic, stage=stage)
                except Exception as e:
                    print(f"[overlay] warn: {e}")
                self.obs.set_scene(self.scene_idle)
                self._last_scene = self.scene_idle
            time.sleep(self.idle_sleep_s)
            return None

        media_input = self.audio_player
        media_path = turn.audio_file
        if not self.text_only:
            next_scene = self._scene_for_speaker(turn.speaker)
            if self.video_mode:
                if not turn.video_file:
                    print("[heygen] warn: video missing; skipping playback (no audio)")
                    self._write_transcript(turn)
                    time.sleep(self.idle_sleep_s)
                    return turn
                media_input = self.video_player_a if turn.speaker == "A" else self.video_player_b
                media_path = turn.video_file
                # Preload the file before switching scenes to reduce black frames on activation.
                try:
                    self.obs.set_media_file(media_input, media_path)
                except Exception as e:
                    print(f"[obs] warn: set_media_file failed (preload): {e}")
            if self._last_scene != next_scene:
                self.obs.set_scene(next_scene)
                self._last_scene = next_scene
                if self.scene_switch_delay_s > 0:
                    time.sleep(self.scene_switch_delay_s)
            speaker_name = "Scientist" if turn.speaker == "A" else "Skeptic"
            stage = "INTRO" if turn.turn_id <= 2 else "DISCUSSION"
            topic = self.topic_provider.get() if hasattr(self, "topic_provider") else "—"
            try:
                self._set_overlay(speaker=speaker_name, topic=topic, stage=stage)
                self._last_topic = topic
                self._last_stage = stage
            except Exception as e:
                print(f"[overlay] warn: {e}")
            if (not self.video_mode) and (not self.streaming_mode) and media_path:
                try:
                    self.obs.set_media_file(media_input, media_path)
                except Exception as e:
                    print(f"[obs] warn: set_media_file failed: {e}")
            try:
                is_a = (turn.speaker == "A")

                if self.enable_highlight and (self.avatar_a_exists or self.avatar_b_exists):
                    # A
                    if self.avatar_a_exists and self.obs.has_filter(self.avatar_a, self.f_speak):
                        self.obs.set_filter_enabled(self.avatar_a, self.f_speak, is_a)
                    if self.avatar_a_exists and self.obs.has_filter(self.avatar_a, self.f_dim):
                        self.obs.set_filter_enabled(self.avatar_a, self.f_dim, not is_a)

                    # B
                    if self.avatar_b_exists and self.obs.has_filter(self.avatar_b, self.f_speak):
                        self.obs.set_filter_enabled(self.avatar_b, self.f_speak, not is_a)
                    if self.avatar_b_exists and self.obs.has_filter(self.avatar_b, self.f_dim):
                        self.obs.set_filter_enabled(self.avatar_b, self.f_dim, is_a)
            except Exception as e:
                print(f"[highlight] warn: {e}")
            if self.enable_highlight and (not self.video_mode) and (not self.streaming_mode):
                if self.highlight_pre_delay_s > 0:
                    time.sleep(self.highlight_pre_delay_s)
            if self.streaming_mode:
                self._ensure_stream_sessions()
                session = self.stream_sessions.get(turn.speaker)
                if not session or not session.session_id:
                    raise RuntimeError("HeyGen streaming session missing for speaker")
                resp = self.stream_client.send_task(
                    token=session.token,
                    session_id=session.session_id,
                    text=turn.text,
                    task_type=self.stream_task_type,
                )
                task_id = ""
                if isinstance(resp, dict):
                    if isinstance(resp.get("data"), dict):
                        task_id = str(resp["data"].get("task_id") or resp["data"].get("taskId") or "")
                    else:
                        task_id = str(resp.get("task_id") or resp.get("taskId") or "")
                turn.stream_session_id = session.session_id
                turn.stream_task_id = task_id
                turn.duration_est_s = self._estimate_stream_duration(turn.text)
                self._write_transcript(turn)
                time.sleep(turn.duration_est_s)
                return turn
            if (not self.video_mode) and (not self.streaming_mode) and not media_path:
                print("[tts] warn: no audio file; continuing with estimated timing")
                self._write_transcript(turn)
                time.sleep(max(self.idle_sleep_s, turn.duration_est_s or self.idle_sleep_s))
                return turn
            self.obs.restart_media(media_input)
            if self.video_mode:
                ok = self.obs.wait_media_playing(media_input, timeout_s=self.media_start_timeout_s)
                if not ok:
                    for _ in range(self.media_start_retries):
                        time.sleep(self.media_start_retry_sleep_s)
                        self.obs.restart_media(media_input)
                        if self.obs.wait_media_playing(media_input, timeout_s=self.media_start_timeout_s):
                            ok = True
                            break
                    if not ok:
                        print("[obs] warn: media did not start playing; continuing anyway")

        self._write_transcript(turn)
        if self.text_only:
            time.sleep(self.text_only_sleep_s)
        elif self._ffprobe:
            dur_path = turn.audio_file or media_path
            dur = self._get_audio_duration(dur_path)
            time.sleep(dur + self.playback_pad_s)
        else:
            self._wait_playback_end(media_input)
        return turn

    def run_forever(self) -> None:
        if self.streaming_mode:
            try:
                self._ensure_stream_sessions()
            except Exception as e:
                print(f"[heygen_stream] session init error: {e}")
                raise
        prebuffer_total = int(os.getenv("PREBUFFER_TOTAL_TURNS", "0"))
        prebuffer_n = int(os.getenv("PREBUFFER_TURNS_PER_SPEAKER", "0"))
        if prebuffer_total > 0:
            total = prebuffer_total
            print(f"[prefetch] prebuffering {total} turns total...")
        elif prebuffer_n > 0:
            total = prebuffer_n * 2
            print(f"[prefetch] prebuffering {prebuffer_n} turns per speaker ({total} total)...")
        else:
            total = 0
        if total > 0:
            max_errors = int(os.getenv("PREBUFFER_MAX_ERRORS", "10"))
            retry_sleep = float(os.getenv("PREBUFFER_RETRY_S", "3.0"))
            errors = 0
            generated = 0
            while generated < total and not self._stop_event.is_set():
                try:
                    self.prefetch_next()
                    generated += 1
                except Exception as e:
                    errors += 1
                    print(f"[prefetch] prebuffer error: {e}")
                    if errors >= max_errors:
                        print("[prefetch] too many errors; continue without full prebuffer")
                        break
                    time.sleep(retry_sleep)
            print("[prefetch] prebuffer complete ✅")
        auto_stream = os.getenv("OBS_AUTO_START_STREAM", "0").strip() == "1"
        apply_stream = os.getenv("OBS_STREAM_APPLY", "1").strip() != "0"
        if auto_stream and self.obs:
            try:
                if apply_stream:
                    applied = self.obs.apply_stream_settings_from_env()
                    if applied:
                        print("[obs] stream settings applied ✅")
                print("[obs] starting stream...")
                self.obs.start_stream()
                print("[obs] stream started ✅")
            except Exception as e:
                raise RuntimeError(f"OBS auto-stream failed: {e}")
        self.start_prefetch()
        while True:
            self.play_next()
