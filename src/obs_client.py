import os
import sys
import time
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv
from obsws_python import ReqClient

load_dotenv()


@dataclass
class ObsConfig:
    host: str
    port: int
    password: str

    scene_a: str
    scene_b: str
    scene_idle: str
    audio_player: str

    test_wav: str


class ObsClient:
    def __init__(self, cfg: ObsConfig):
        self.cfg = cfg
        self.ws = None

    def connect(self) -> None:
        # obsws-python (ReqClient) коннектится при создании клиента;
        # если пароль неверный — упадёт на первом запросе.
        try:
            self.ws = ReqClient(host=self.cfg.host, port=self.cfg.port, password=self.cfg.password)
            self.ws.get_version()
        except ConnectionRefusedError as e:
            raise RuntimeError(
                "OBS WebSocket недоступен. Запусти OBS и включи WebSocket "
                "(Tools → WebSocket Server Settings). Проверь OBS_HOST/OBS_PORT."
            ) from e
        except OSError as e:
            raise RuntimeError(
                "Не удалось подключиться к OBS WebSocket. Проверь настройки и доступность порта."
            ) from e

    def close(self) -> None:
        if self.ws:
            self.ws.disconnect()

    # --- helpers ---
    def list_scenes(self) -> List[str]:
        resp = self.ws.get_scene_list()
        return [s["sceneName"] for s in resp.scenes]

    def ensure_scene_exists(self, name: str) -> None:
        scenes = self.list_scenes()
        if name not in scenes:
            raise RuntimeError(f"OBS scene not found: {name}. Existing: {scenes}")

    def ensure_input_exists(self, input_name: str) -> None:
        try:
            self.ws.get_input_settings(input_name)
        except Exception as e:
            raise RuntimeError(f"OBS input not found: {input_name}. Error: {e}")

    # --- actions ---
    def set_scene(self, scene_name: str) -> None:
        self.ws.set_current_program_scene(scene_name)

    def set_media_file(self, input_name: str, file_path: str) -> None:
        # OBS expects an absolute path for local_file in many setups; safest is abs
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            raise RuntimeError(f"Media file does not exist: {abs_path}")

        # For Media Source, the setting key is typically "local_file"
        self.ws.set_input_settings(input_name, {"local_file": abs_path}, overlay=True)

    def restart_media(self, input_name: str) -> None:
        self.ws.trigger_media_input_action(input_name, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")

    def get_media_status(self, input_name: str) -> dict:
        st = self.ws.get_media_input_status(input_name)
        state = getattr(st, "media_state", None)
        if state is None:
            return {}
        s = str(state)
        if s.startswith("OBS_MEDIA_STATE_"):
            s = s.replace("OBS_MEDIA_STATE_", "").lower()
        else:
            s = s.lower()
        return {"state": s, "mediaState": s}

    def wait_media_playing(self, input_name: str, timeout_s: float = 5.0, poll_s: float = 0.2) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            st = self.ws.get_media_input_status(input_name)
            # states: "OBS_MEDIA_STATE_PLAYING", "OBS_MEDIA_STATE_ENDED", etc.
            state = getattr(st, "media_state", None)
            if state in ("OBS_MEDIA_STATE_PLAYING", "OBS_MEDIA_STATE_ENDED"):
                return True
            time.sleep(poll_s)
        return False

    # --- self check ---
    def self_check(self) -> None:
        print("[obs] connecting...")
        self.connect()
        print("[obs] connected ✅")

        print("[obs] checking scenes/inputs...")
        self.ensure_scene_exists(self.cfg.scene_a)
        self.ensure_scene_exists(self.cfg.scene_b)
        self.ensure_scene_exists(self.cfg.scene_idle)
        self.ensure_input_exists(self.cfg.audio_player)
        print("[obs] scenes/inputs exist ✅")

        print("[obs] switching to SCENE_A...")
        self.set_scene(self.cfg.scene_a)
        time.sleep(0.2)

        print(f"[obs] setting test wav: {self.cfg.test_wav}")
        self.set_media_file(self.cfg.audio_player, self.cfg.test_wav)

        print("[obs] restart media...")
        self.restart_media(self.cfg.audio_player)

        ok = self.wait_media_playing(self.cfg.audio_player, timeout_s=5.0)
        if not ok:
            st = self.ws.get_media_input_status(self.cfg.audio_player)
            raise RuntimeError(f"Media did not start playing. media_state={getattr(st, 'media_state', None)}")

        print("[obs] media playing ✅")

        print("[obs] switching to SCENE_B...")
        self.set_scene(self.cfg.scene_b)
        time.sleep(0.2)
        print("[obs] switched ✅")

        print("[obs] done ✅")


def load_config() -> ObsConfig:
    return ObsConfig(
        host=os.getenv("OBS_HOST", "127.0.0.1"),
        port=int(os.getenv("OBS_PORT", "4455")),
        password=os.getenv("OBS_PASSWORD", ""),

        scene_a=os.getenv("SCENE_A", "SCENE_A"),
        scene_b=os.getenv("SCENE_B", "SCENE_B"),
        scene_idle=os.getenv("SCENE_IDLE", "SCENE_IDLE"),
        audio_player=os.getenv("AUDIO_PLAYER", "AUDIO_PLAYER"),

        test_wav=os.getenv("TEST_WAV", "audio/test.wav"),
    )


if __name__ == "__main__":
    cfg = load_config()
    client = ObsClient(cfg)
    try:
        client.self_check()
    except Exception as e:
        print(f"[obs] error: {e}")
        sys.exit(1)
    finally:
        client.close()
