# src/main.py
import argparse
import os
from dotenv import load_dotenv

from src.obs_client import ObsClient, ObsConfig
from src.orchestrator import Orchestrator
from src.mode import resolve_avatar_mode, ALLOWED_MODES
from src.stream_server import StreamServer


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--text-only", action="store_true", help="Run without OBS/TTS, only transcript")
    mode.add_argument("--audio", action="store_true", help="Force audio mode (default)")
    p.add_argument("--text-sleep", type=float, default=None, help="Sleep between turns in text-only mode")
    return p.parse_args()


def main():
    load_dotenv()
    args = _parse_args()
    if args.text_only:
        os.environ["TEXT_ONLY"] = "1"
        os.environ["AVATAR_MODE"] = "text"
    elif args.audio:
        os.environ.pop("TEXT_ONLY", None)
        if os.environ.get("AVATAR_MODE", "").strip().lower() == "text":
            os.environ["AVATAR_MODE"] = "png"
    if args.text_sleep is not None:
        os.environ["TEXT_ONLY_SLEEP_S"] = str(args.text_sleep)

    raw_mode = (os.getenv("AVATAR_MODE", "") or "").strip().lower()
    if raw_mode and raw_mode not in ALLOWED_MODES:
        print(f"[mode] warn: unknown AVATAR_MODE={raw_mode!r}, falling back to legacy flags")
    resolved_mode = resolve_avatar_mode()
    print(f"[mode] Resolved mode: {resolved_mode}")

    obs = ObsClient(ObsConfig(
        host=_env("OBS_HOST", default="127.0.0.1") or "127.0.0.1",
        port=int(_env("OBS_PORT", default="4455") or "4455"),
        password=_env("OBS_PASSWORD", default="") or "",
        scene_a=_env("SCENE_A", "OBS_SCENE_A", default="SCENE_A") or "SCENE_A",
        scene_b=_env("SCENE_B", "OBS_SCENE_B", default="SCENE_B") or "SCENE_B",
        scene_idle=_env("SCENE_IDLE", "OBS_SCENE_IDLE", default="SCENE_IDLE") or "SCENE_IDLE",
        audio_player=_env("AUDIO_PLAYER", "OBS_AUDIO_INPUT", default="AUDIO_PLAYER") or "AUDIO_PLAYER",
        test_wav=_env("TEST_WAV", default="audio/test.wav") or "audio/test.wav",
    ))

    streaming = resolved_mode == "heygen_stream"
    video_mode = resolved_mode == "heygen_video"
    text_only = resolved_mode == "text"

    if not text_only:
        # streaming uses Browser Source; video_mode uses MP4 Media Sources (no AUDIO_PLAYER check)
        obs.self_check(check_media=(not streaming and not video_mode))
        if video_mode:
            # Ensure video Media Sources exist for mp4 playback
            video_player_a = _env("VIDEO_PLAYER_A", default="MEDIA_A_MP4") or "MEDIA_A_MP4"
            video_player_b = _env("VIDEO_PLAYER_B", default="MEDIA_B_MP4") or "MEDIA_B_MP4"
            obs.ensure_input_exists(video_player_a)
            obs.ensure_input_exists(video_player_b)

    if streaming and (_env("STREAM_SERVER", default="1") or "1") == "1":
        srv = StreamServer(
            host=_env("STREAM_SERVER_HOST", default="127.0.0.1") or "127.0.0.1",
            port=int(_env("STREAM_SERVER_PORT", default="8099") or "8099"),
            web_root=_env("STREAM_WEB_ROOT", default="web") or "web",
            session_file=_env("STREAM_SESSION_FILE", default="stream_sessions.json") or "stream_sessions.json",
        )
        srv.start()

    orch = Orchestrator(
        obs,
        scene_a=_env("SCENE_A", "OBS_SCENE_A", default="SCENE_A") or "SCENE_A",
        scene_b=_env("SCENE_B", "OBS_SCENE_B", default="SCENE_B") or "SCENE_B",
        scene_idle=_env("SCENE_IDLE", "OBS_SCENE_IDLE", default="SCENE_IDLE") or "SCENE_IDLE",
        audio_player=_env("AUDIO_PLAYER", "OBS_AUDIO_INPUT", default="AUDIO_PLAYER") or "AUDIO_PLAYER",
        audio_dir=os.environ.get("AUDIO_DIR", "audio/preloaded"),
        transcript_path=os.environ.get("TRANSCRIPT_PATH", "transcripts/transcript.jsonl"),
        history_max=int(_env("HISTORY_MAX", "HISTORY_MAX_TURNS", default="48") or "48"),
        min_queue_items=int(_env("MIN_QUEUE_ITEMS", "QUEUE_FLOOR", default="2") or "2"),
        poll_ms=int(_env("POLL_MS", default="300") or "300"),
        idle_sleep_s=float(_env("IDLE_SLEEP_S", default="1.0") or "1.0"),
    )
    orch.run_forever()


if __name__ == "__main__":
    main()
