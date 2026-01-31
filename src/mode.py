from __future__ import annotations

import os
from typing import Mapping

ALLOWED_MODES = {"png", "heygen_stream", "heygen_video", "text"}


def resolve_avatar_mode(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    raw = (env.get("AVATAR_MODE", "") or "").strip().lower()
    if raw in {"", "auto"}:
        raw = ""
    if raw in ALLOWED_MODES:
        return raw

    text_only = (env.get("TEXT_ONLY", "") or "").strip() == "1"
    stream_mode = (env.get("STREAM_MODE", "") or "").strip().lower()
    streaming = (env.get("HEYGEN_STREAMING", "") or "").strip() == "1" or stream_mode in {
        "heygen",
        "stream",
        "streaming",
    }
    video = (env.get("VIDEO_MODE", "") or "").strip() == "1"

    if text_only:
        return "text"
    if streaming:
        return "heygen_stream"
    if video:
        return "heygen_video"
    return "png"
