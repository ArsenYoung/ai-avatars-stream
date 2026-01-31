import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests


def _first_key(d: Dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v:
            return str(v)
    return None


def _extract_token(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    direct = _first_key(data, ("token", "access_token"))
    if direct:
        return direct
    nested = data.get("data")
    if isinstance(nested, dict):
        return _first_key(nested, ("token", "access_token"))
    return None


def _extract_session_info(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("data"), dict):
        return data["data"]
    return data


@dataclass
class HeygenStreamConfig:
    api_key: str
    api_base: str = "https://api.heygen.com"
    timeout_s: int = 120


@dataclass
class StreamSession:
    agent: str
    session_id: str
    url: str
    access_token: str
    token: str
    avatar_name: str
    voice_id: Optional[str]
    created_at: float

    def public_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "session_id": self.session_id,
            "url": self.url,
            "access_token": self.access_token,
            "avatar_name": self.avatar_name,
            "voice_id": self.voice_id,
            "created_at": self.created_at,
        }


def write_sessions_file(path: str, sessions: Dict[str, StreamSession]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": time.time(),
        "sessions": {k: v.public_dict() for k, v in sessions.items()},
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


class HeygenStreamClient:
    def __init__(self, cfg: HeygenStreamConfig):
        if not cfg.api_key:
            raise RuntimeError("HEYGEN_API_KEY is required")
        self.cfg = cfg

    def _headers(self, *, json_body: bool = False, token: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["X-Api-Key"] = self.cfg.api_key
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def create_token(self) -> str:
        url = f"{self.cfg.api_base}/v1/streaming.create_token"
        r = requests.post(url, headers=self._headers(json_body=True), json={}, timeout=self.cfg.timeout_s)
        if not r.ok:
            raise RuntimeError(f"HeyGen create_token failed: {r.status_code} {r.text}")
        data = r.json()
        token = _extract_token(data)
        if not token:
            raise RuntimeError(f"HeyGen create_token missing token: {data}")
        return token

    def new_session(
        self,
        *,
        token: str,
        avatar_name: Optional[str] = None,
        avatar_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        voice_rate: float = 1.0,
        quality: str = "high",
        version: str = "v2",
        video_encoding: str = "H264",
        disable_idle_timeout: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.cfg.api_base}/v1/streaming.new"
        payload: Dict[str, Any] = {
            "quality": quality,
            "version": version,
            "video_encoding": video_encoding,
            "disable_idle_timeout": disable_idle_timeout,
        }
        if avatar_name:
            payload["avatar_name"] = avatar_name
        elif avatar_id:
            payload["avatar_id"] = avatar_id
        else:
            raise ValueError("avatar_name or avatar_id is required for streaming.new")

        if voice_id:
            payload["voice"] = {"voice_id": voice_id, "rate": voice_rate}

        r = requests.post(
            url,
            headers=self._headers(json_body=True, token=token),
            json=payload,
            timeout=self.cfg.timeout_s,
        )
        if not r.ok:
            raise RuntimeError(f"HeyGen streaming.new failed: {r.status_code} {r.text}")
        return _extract_session_info(r.json())

    def start_session(self, *, token: str, session_id: str) -> None:
        url = f"{self.cfg.api_base}/v1/streaming.start"
        r = requests.post(
            url,
            headers=self._headers(json_body=True, token=token),
            json={"session_id": session_id},
            timeout=self.cfg.timeout_s,
        )
        if not r.ok:
            raise RuntimeError(f"HeyGen streaming.start failed: {r.status_code} {r.text}")

    def stop_session(self, *, token: str, session_id: str) -> None:
        url = f"{self.cfg.api_base}/v1/streaming.stop"
        r = requests.post(
            url,
            headers=self._headers(json_body=True, token=token),
            json={"session_id": session_id},
            timeout=self.cfg.timeout_s,
        )
        if not r.ok:
            raise RuntimeError(f"HeyGen streaming.stop failed: {r.status_code} {r.text}")

    def send_task(
        self,
        *,
        token: str,
        session_id: str,
        text: str,
        task_type: str = "repeat",
    ) -> Dict[str, Any]:
        url = f"{self.cfg.api_base}/v1/streaming.task"
        payload = {"session_id": session_id, "text": text, "task_type": task_type}
        r = requests.post(
            url,
            headers=self._headers(json_body=True, token=token),
            json=payload,
            timeout=self.cfg.timeout_s,
        )
        if not r.ok:
            raise RuntimeError(f"HeyGen streaming.task failed: {r.status_code} {r.text}")
        return r.json()
