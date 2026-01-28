import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass
class TopicConfig:
    topic_env: str = "TOPIC"
    topic_file: str = "topic.txt"
    reload_s: int = 180

class TopicProvider:
    def __init__(self, cfg: TopicConfig):
        self.cfg = cfg
        self._last_check = 0.0
        self._last_mtime: Optional[float] = None
        self._cached: Optional[str] = None

    def get(self) -> str:
        # env имеет приоритет
        env_topic = os.getenv(self.cfg.topic_env)
        if env_topic:
            self._cached = env_topic.strip()
            return self._cached

        now = time.time()
        if self._cached and (now - self._last_check) < self.cfg.reload_s:
            return self._cached

        self._last_check = now
        p = Path(os.getenv("TOPIC_FILE", self.cfg.topic_file))
        if not p.exists():
            self._cached = self._cached or "Научная дискуссия: старение и долголетие"
            return self._cached

        mtime = p.stat().st_mtime
        if self._last_mtime is None or mtime != self._last_mtime:
            self._last_mtime = mtime
            self._cached = p.read_text(encoding="utf-8").strip() or self._cached

        return self._cached or "Научная дискуссия: старение и долголетие"
