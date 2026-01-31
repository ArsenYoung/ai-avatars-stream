import os
import time
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI
from src.retry import retry

@dataclass
class TTSConfig:
    model: str
    fmt: str = "mp3"

class TTS:
    def __init__(self, client: OpenAI, cfg: TTSConfig):
        self.client = client
        self.cfg = cfg

    def speak(self, *, text: str, voice: str, out_path: str) -> float:
        """
        Пишем атомарно: tmp -> replace
        Возвращаем latency (sec)
        """
        t0 = time.time()
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")

        timeout_s = float(os.getenv("TTS_TIMEOUT_S", "45"))

        def _with_timeout(client: OpenAI, timeout_s: float) -> OpenAI:
            try:
                return client.with_options(timeout=timeout_s)
            except Exception:
                return client

        client_t = _with_timeout(self.client, timeout_s)

        def _call() -> None:
            try:
                # официальный паттерн streaming->file
                with client_t.audio.speech.with_streaming_response.create(
                    model=self.cfg.model,
                    voice=voice,
                    input=text,
                    response_format=self.cfg.fmt,
                    timeout=timeout_s,
                ) as r:
                    r.stream_to_file(tmp)
            except TypeError:
                with client_t.audio.speech.with_streaming_response.create(
                    model=self.cfg.model,
                    voice=voice,
                    input=text,
                    response_format=self.cfg.fmt,
                ) as r:
                    r.stream_to_file(tmp)

        retry(_call, name="tts")
        os.replace(tmp, out)
        return time.time() - t0
