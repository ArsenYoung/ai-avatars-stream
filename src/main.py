# src/main.py
import argparse
import os
from dotenv import load_dotenv

from src.obs_client import ObsClient, ObsConfig
from src.orchestrator import Orchestrator


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
    elif args.audio:
        os.environ.pop("TEXT_ONLY", None)
    if args.text_sleep is not None:
        os.environ["TEXT_ONLY_SLEEP_S"] = str(args.text_sleep)

    obs = ObsClient(ObsConfig(
        host=os.getenv("OBS_HOST", "127.0.0.1"),
        port=int(os.getenv("OBS_PORT", "4455")),
        password=os.getenv("OBS_PASSWORD", ""),
        scene_a=os.getenv("SCENE_A", "SCENE_A"),
        scene_b=os.getenv("SCENE_B", "SCENE_B"),
        scene_idle=os.getenv("SCENE_IDLE", "SCENE_IDLE"),
        audio_player=os.getenv("AUDIO_PLAYER", "AUDIO_PLAYER"),
        test_wav=os.getenv("TEST_WAV", "audio/test.wav"),
    ))
    if os.getenv("TEXT_ONLY", "").strip() != "1":
        obs.self_check()  # CP2: проверка сцен/источника + тестовый restart

    orch = Orchestrator(
        obs,
        scene_a=os.environ["SCENE_A"],
        scene_b=os.environ["SCENE_B"],
        scene_idle=os.environ["SCENE_IDLE"],
        audio_player=os.environ["AUDIO_PLAYER"],
        audio_dir=os.environ.get("AUDIO_DIR", "audio/preloaded"),
        transcript_path=os.environ.get("TRANSCRIPT_PATH", "transcripts/transcript.jsonl"),
        history_max=int(os.environ.get("HISTORY_MAX", "48")),
        min_queue_items=int(os.environ.get("MIN_QUEUE_ITEMS", "2")),
        poll_ms=int(os.environ.get("POLL_MS", "300")),
        idle_sleep_s=float(os.environ.get("IDLE_SLEEP_S", "1.0")),
    )
    orch.run_forever()


if __name__ == "__main__":
    main()
