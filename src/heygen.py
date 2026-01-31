import mimetypes
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests


@dataclass
class HeygenConfig:
    api_key: str
    upload_url: str = "https://upload.heygen.com/v1/asset"
    generate_url: str = "https://api.heygen.com/v2/video/generate"
    status_url: str = "https://api.heygen.com/v1/video_status.get"
    timeout_s: int = 120


def _first_key(d: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v:
            return str(v)
    return None


def _extract_asset_id(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    direct = _first_key(data, ("asset_id", "id"))
    if direct:
        return direct
    nested = data.get("data")
    if isinstance(nested, dict):
        return _first_key(nested, ("asset_id", "id"))
    return None


def _extract_video_id(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    direct = _first_key(data, ("video_id", "id"))
    if direct:
        return direct
    nested = data.get("data")
    if isinstance(nested, dict):
        return _first_key(nested, ("video_id", "id"))
    return None


def guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return "audio/wav"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".mp4":
        return "video/mp4"
    if ext == ".png":
        return "image/png"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    return "application/octet-stream"


class HeygenClient:
    def __init__(self, cfg: HeygenConfig):
        if not cfg.api_key:
            raise RuntimeError("HEYGEN_API_KEY is required")
        self.cfg = cfg

    def _headers(self, *, json: bool = False) -> Dict[str, str]:
        headers = {"X-Api-Key": self.cfg.api_key}
        if json:
            headers["Content-Type"] = "application/json"
        return headers

    def upload_asset(self, path: str) -> str:
        url = self.cfg.upload_url
        mime = guess_mime(path)
        headers = self._headers()
        headers["Content-Type"] = mime
        with open(path, "rb") as f:
            r = requests.post(url, headers=headers, data=f, timeout=self.cfg.timeout_s)
        if not r.ok:
            raise RuntimeError(f"HeyGen upload failed: {r.status_code} {r.text}")
        data = r.json()
        asset_id = _extract_asset_id(data)
        if not asset_id:
            raise RuntimeError(f"HeyGen upload response missing asset_id: {data}")
        return asset_id

    def generate_video(self, payload: Dict[str, Any]) -> str:
        r = requests.post(
            self.cfg.generate_url,
            headers=self._headers(json=True),
            json=payload,
            timeout=self.cfg.timeout_s,
        )
        if not r.ok:
            raise RuntimeError(f"HeyGen generate failed: {r.status_code} {r.text}")
        data = r.json()
        video_id = _extract_video_id(data)
        if not video_id:
            raise RuntimeError(f"HeyGen generate response missing video_id: {data}")
        return video_id

    def _build_character(
        self,
        *,
        character_type: str,
        character_id: str,
        avatar_style: Optional[str] = None,
    ) -> Dict[str, Any]:
        ctype = (character_type or "avatar").strip().lower()
        if not character_id:
            raise ValueError("character_id is required")
        if ctype == "avatar":
            character: Dict[str, Any] = {
                "type": "avatar",
                "avatar_id": character_id,
            }
            if avatar_style:
                character["avatar_style"] = avatar_style
            return character
        if ctype == "talking_photo":
            return {
                "type": "talking_photo",
                "talking_photo_id": character_id,
            }
        raise ValueError(f"Unsupported character_type: {character_type}")

    def generate_avatar_video(
        self,
        *,
        avatar_id: str,
        audio_asset_id: Optional[str] = None,
        audio_url: Optional[str] = None,
        width: int = 1280,
        height: int = 720,
        caption: bool = False,
        avatar_style: Optional[str] = None,
        character_type: str = "avatar",
    ) -> str:
        if (audio_asset_id is None) == (audio_url is None):
            raise ValueError("Provide exactly one of audio_asset_id or audio_url")

        voice: Dict[str, Any] = {"type": "audio"}
        if audio_asset_id is not None:
            voice["audio_asset_id"] = audio_asset_id
        if audio_url is not None:
            voice["audio_url"] = audio_url

        character = self._build_character(
            character_type=character_type,
            character_id=avatar_id,
            avatar_style=avatar_style,
        )

        payload = {
            "video_inputs": [
                {
                    "character": character,
                    "voice": voice,
                    "caption": caption,
                }
            ],
            "dimension": {"width": width, "height": height},
        }
        return self.generate_video(payload)

    def poll_video(self, video_id: str, *, timeout_s: int = 600, poll_s: float = 5.0) -> str:
        url = self.cfg.status_url
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = requests.get(
                url,
                headers=self._headers(),
                params={"video_id": video_id},
                timeout=self.cfg.timeout_s,
            )
            if not r.ok:
                raise RuntimeError(f"HeyGen status failed: {r.status_code} {r.text}")
            data = r.json()
            status = None
            if isinstance(data, dict):
                if isinstance(data.get("data"), dict):
                    status = data["data"].get("status")
                    video_url = data["data"].get("video_url")
                else:
                    status = data.get("status")
                    video_url = data.get("video_url")
            else:
                video_url = None
            if status == "completed":
                if video_url:
                    return video_url
                raise RuntimeError(f"HeyGen completed but video_url missing: {data}")
            if status == "failed":
                raise RuntimeError(f"HeyGen failed: {data}")
            time.sleep(poll_s)
        raise TimeoutError("HeyGen video not ready in time")

    def download(self, url: str, out_path: str) -> None:
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
