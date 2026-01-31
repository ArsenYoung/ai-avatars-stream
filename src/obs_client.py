import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional

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
    def __init__(
        self,
        cfg: Optional[ObsConfig] = None,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
    ):
        if cfg is None:
            cfg = ObsConfig(
                host=host or os.getenv("OBS_HOST", "127.0.0.1"),
                port=int(port if port is not None else os.getenv("OBS_PORT", "4455")),
                password=password or os.getenv("OBS_PASSWORD", ""),
                scene_a=os.getenv("SCENE_A", "SCENE_A"),
                scene_b=os.getenv("SCENE_B", "SCENE_B"),
                scene_idle=os.getenv("SCENE_IDLE", "SCENE_IDLE"),
                audio_player=os.getenv("AUDIO_PLAYER", "AUDIO_PLAYER"),
                test_wav=os.getenv("TEST_WAV", "audio/test.wav"),
            )
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

    def set_text(self, input_name: str, text: str) -> None:
        # Для Text (FreeType 2) / Text (GDI+) обычно ключ "text"
        # Если источник читает текст из файла, "text" игнорируется — отключаем это.
        self.ws.set_input_settings(
            input_name,
            {"text": text, "read_from_file": False},
            overlay=True,
        )

    def set_filter_enabled(self, source_name: str, filter_name: str, enabled: bool) -> None:
        self.ws.set_source_filter_enabled(source_name, filter_name, enabled)

    def has_filter(self, source_name: str, filter_name: str) -> bool:
        try:
            resp = self.ws.get_source_filter_list(source_name)
        except Exception:
            return False
        items = getattr(resp, "filters", None)
        if items is None and isinstance(resp, dict):
            items = resp.get("filters")
        items = items or []
        for f in items:
            if isinstance(f, dict):
                name = f.get("filterName") or f.get("filter_name") or f.get("name")
            else:
                name = (
                    getattr(f, "filter_name", None)
                    or getattr(f, "filterName", None)
                    or getattr(f, "name", None)
                )
            if name == filter_name:
                return True
        return False

    def source_exists(self, source_name: str) -> bool:
        try:
            self.ws.get_input_settings(source_name)
            return True
        except Exception:
            return False

    def set_scene_item_enabled(self, scene_name: str, source_name: str, enabled: bool) -> None:
        item = self.ws.get_scene_item_id(scene_name, source_name)
        scene_item_id = getattr(item, "scene_item_id", None)
        if scene_item_id is None:
            raise RuntimeError(f"OBS scene item not found: scene={scene_name} source={source_name}")
        self.ws.set_scene_item_enabled(scene_name, scene_item_id, enabled)

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

    def start_stream(self) -> None:
        self.ws.start_stream()

    def set_stream_service_settings(self, service_type: str, settings: dict) -> None:
        if not hasattr(self.ws, "set_stream_service_settings"):
            raise RuntimeError("OBS WebSocket method set_stream_service_settings not available")
        try:
            self.ws.set_stream_service_settings(
                stream_service_type=service_type,
                stream_service_settings=settings,
            )
            return
        except TypeError:
            pass
        try:
            self.ws.set_stream_service_settings(service_type, settings)
            return
        except TypeError:
            pass
        self.ws.set_stream_service_settings(
            streamServiceType=service_type,
            streamServiceSettings=settings,
        )

    def apply_stream_settings_from_env(self) -> bool:
        service_type = os.getenv("OBS_STREAM_SERVICE_TYPE", "").strip()
        service = os.getenv("OBS_STREAM_SERVICE", "").strip()
        server = os.getenv("OBS_STREAM_SERVER", "").strip()
        key = os.getenv("OBS_STREAM_KEY", "").strip()
        use_auth = os.getenv("OBS_STREAM_USE_AUTH", "0").strip() == "1"
        username = os.getenv("OBS_STREAM_USERNAME", "").strip()
        password = os.getenv("OBS_STREAM_PASSWORD", "").strip()

        if not (service or server or key):
            return False
        if not key:
            raise RuntimeError("OBS_STREAM_KEY is required to apply stream settings")

        if not service_type:
            service_type = "rtmp_common" if service else "rtmp_custom"

        settings = {"key": key}
        if service_type == "rtmp_common":
            if not service:
                raise RuntimeError("OBS_STREAM_SERVICE is required for rtmp_common")
            settings["service"] = service
            if server:
                settings["server"] = server
        else:
            if not server:
                raise RuntimeError("OBS_STREAM_SERVER is required for rtmp_custom")
            settings["server"] = server
            settings["use_auth"] = bool(use_auth)
            if use_auth:
                settings["username"] = username
                settings["password"] = password

        self.set_stream_service_settings(service_type, settings)
        return True

    # --- self check ---
    def self_check(self, *, check_media: bool = True, strict: Optional[bool] = None) -> None:
        print("[obs] connecting...")
        self.connect()
        print("[obs] connected ✅")

        print("[obs] checking scenes/inputs...")
        self.ensure_scene_exists(self.cfg.scene_a)
        self.ensure_scene_exists(self.cfg.scene_b)
        self.ensure_scene_exists(self.cfg.scene_idle)
        if check_media:
            self.ensure_input_exists(self.cfg.audio_player)
        print("[obs] scenes/inputs exist ✅")

        if strict is None:
            strict = os.getenv("OBS_STRICT", "").strip() == "1"
        if strict:
            print("[obs] strict check enabled...")
            missing: list[str] = []

            def _check_input(env_key: str, default: str) -> str:
                name = (os.getenv(env_key, default) or "").strip()
                if not name:
                    missing.append(f"{env_key} is empty (set to OBS source name)")
                    return ""
                try:
                    self.ensure_input_exists(name)
                except Exception:
                    missing.append(
                        f"{env_key} -> missing OBS input '{name}'. "
                        f"Create/rename source to '{name}' or set {env_key}."
                    )
                return name

            def _check_filter(source_name: str, env_key: str, default: str) -> None:
                if not source_name:
                    return
                fname = (os.getenv(env_key, default) or "").strip()
                if not fname:
                    missing.append(f"{env_key} is empty (set to filter name on '{source_name}')")
                    return
                if not self.has_filter(source_name, fname):
                    missing.append(
                        f"{env_key} -> filter '{fname}' missing on source '{source_name}'. "
                        f"Add filter or set {env_key}."
                    )

            _check_input("OVERLAY_TOPIC", "TXT_TOPIC")
            _check_input("OVERLAY_STAGE", "TXT_STAGE")
            _check_input("OVERLAY_SPEAKER", "TXT_SPEAKER")

            avatar_a = _check_input("AVATAR_A_SOURCE", "AVATAR_A")
            avatar_b = _check_input("AVATAR_B_SOURCE", "AVATAR_B")

            _check_filter(avatar_a, "FILTER_DIM", "DIM")
            _check_filter(avatar_a, "FILTER_SPEAK", "SPEAK")
            _check_filter(avatar_b, "FILTER_DIM", "DIM")
            _check_filter(avatar_b, "FILTER_SPEAK", "SPEAK")

            if missing:
                msg = "OBS strict check failed:\\n- " + "\\n- ".join(missing)
                raise RuntimeError(msg)

        if check_media:
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
