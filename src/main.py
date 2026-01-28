# src/main.py
import os
from dotenv import load_dotenv

from src.obs_client import ObsClient, ObsConfig
from src.orchestrator import Orchestrator


def main():
    load_dotenv()

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
    obs.connect()
    obs.self_check()  # CP2: проверка сцен/источника + тестовый restart

    orch = Orchestrator(
        obs,
        scene_a=os.environ["SCENE_A"],
        scene_b=os.environ["SCENE_B"],
        scene_idle=os.environ["SCENE_IDLE"],
        audio_player=os.environ["AUDIO_PLAYER"],
        audio_dir=os.environ.get("AUDIO_DIR", "audio/preloaded"),
        transcript_path=os.environ.get("TRANSCRIPT_PATH", "transcripts/transcript.jsonl"),
        min_queue_items=int(os.environ.get("MIN_QUEUE_ITEMS", "2")),
        poll_ms=int(os.environ.get("POLL_MS", "300")),
        idle_sleep_s=float(os.environ.get("IDLE_SLEEP_S", "1.0")),
    )
    orch.run_forever()


if __name__ == "__main__":
    main()
