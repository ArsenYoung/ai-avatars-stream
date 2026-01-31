import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Tuple

import requests


@dataclass
class YouTubeChatConfig:
    api_key: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    live_chat_id: str = ""
    broadcast_id: str = ""
    command_prefix: str = "!topic"
    cooldown_s: int = 180
    topic_ttl_s: int = 900
    allowlist: str = ""
    mods_only: bool = True
    poll_fallback_ms: int = 2000


class YouTubeChatClient:
    def __init__(self, cfg: YouTubeChatConfig):
        self.cfg = cfg
        self._access_token: Optional[str] = None
        self._access_token_expiry: float = 0.0

    def _refresh_token(self) -> Optional[str]:
        if not (self.cfg.client_id and self.cfg.client_secret and self.cfg.refresh_token):
            return None
        if self._access_token and time.time() < self._access_token_expiry - 60:
            return self._access_token

        url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "refresh_token": self.cfg.refresh_token,
            "grant_type": "refresh_token",
        }
        r = requests.post(url, data=payload, timeout=30)
        if not r.ok:
            raise RuntimeError(f"YouTube token refresh failed: {r.status_code} {r.text}")
        data = r.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise RuntimeError(f"YouTube token refresh missing access_token: {data}")
        self._access_token = token
        self._access_token_expiry = time.time() + expires_in
        return token

    def _auth_headers(self) -> Dict[str, str]:
        token = self._refresh_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def _request(self, url: str, params: Dict[str, str]) -> Dict:
        headers = self._auth_headers()
        if not headers and self.cfg.api_key:
            params = dict(params)
            params["key"] = self.cfg.api_key
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if not r.ok:
            raise RuntimeError(f"YouTube API error: {r.status_code} {r.text}")
        return r.json()

    def get_live_chat_id(self) -> str:
        if self.cfg.live_chat_id:
            return self.cfg.live_chat_id
        if not self.cfg.broadcast_id:
            raise RuntimeError("YOUTUBE_BROADCAST_ID or YOUTUBE_LIVE_CHAT_ID is required")
        url = "https://www.googleapis.com/youtube/v3/liveBroadcasts"
        data = self._request(url, {"part": "snippet", "id": self.cfg.broadcast_id})
        items = data.get("items") or []
        if not items:
            raise RuntimeError("YouTube liveBroadcasts.list returned empty items")
        snippet = items[0].get("snippet") or {}
        live_chat_id = snippet.get("liveChatId")
        if not live_chat_id:
            raise RuntimeError("liveChatId missing in broadcast snippet")
        return live_chat_id

    def list_messages(self, *, live_chat_id: str, page_token: Optional[str] = None) -> Tuple[list, Optional[str], int]:
        url = "https://www.googleapis.com/youtube/v3/liveChatMessages"
        params: Dict[str, str] = {
            "liveChatId": live_chat_id,
            "part": "snippet,authorDetails",
            "maxResults": "200",
        }
        if page_token:
            params["pageToken"] = page_token
        data = self._request(url, params)
        items = data.get("items") or []
        next_page_token = data.get("nextPageToken")
        poll_ms = int(data.get("pollingIntervalMillis") or self.cfg.poll_fallback_ms)
        return items, next_page_token, poll_ms


def _parse_allowlist(raw: str) -> set[str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return set(parts)


def _author_is_allowed(author: Dict, allowlist: set[str], mods_only: bool) -> bool:
    channel_id = author.get("channelId") or ""
    display = author.get("displayName") or ""
    if allowlist:
        return channel_id in allowlist or display in allowlist
    if mods_only:
        return bool(author.get("isChatOwner") or author.get("isChatModerator"))
    return True


def _extract_text(item: Dict) -> str:
    snippet = item.get("snippet") or {}
    return (snippet.get("displayMessage") or "").strip()


def _extract_author(item: Dict) -> Dict:
    return item.get("authorDetails") or {}


def _extract_message_id(item: Dict) -> str:
    return str(item.get("id") or "")


class YouTubeTopicWatcher:
    def __init__(
        self,
        cfg: YouTubeChatConfig,
        *,
        on_topic: Callable[[str, Dict], None],
        on_message: Optional[Callable[[Dict], None]] = None,
    ):
        self.cfg = cfg
        self._client = YouTubeChatClient(cfg)
        self._on_topic = on_topic
        self._on_message = on_message
        self._stop = False
        self._seen: set[str] = set()
        self._last_change = 0.0
        self._allowlist = _parse_allowlist(cfg.allowlist)

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        live_chat_id = self._client.get_live_chat_id()
        page_token = None
        while not self._stop:
            try:
                items, page_token, poll_ms = self._client.list_messages(
                    live_chat_id=live_chat_id, page_token=page_token
                )
                for item in items:
                    msg_id = _extract_message_id(item)
                    if not msg_id or msg_id in self._seen:
                        continue
                    self._seen.add(msg_id)
                    if len(self._seen) > 2000:
                        self._seen = set(list(self._seen)[-1000:])

                    if self._on_message:
                        self._on_message(item)

                    text = _extract_text(item)
                    if not text:
                        continue
                    author = _extract_author(item)
                    if not _author_is_allowed(author, self._allowlist, self.cfg.mods_only):
                        continue
                    prefix = self.cfg.command_prefix.lower()
                    if not text.lower().startswith(prefix):
                        continue
                    topic = text[len(prefix):].strip(" :")
                    if not topic:
                        continue
                    now = time.time()
                    if now - self._last_change < self.cfg.cooldown_s:
                        continue
                    self._last_change = now
                    self._on_topic(topic, author)
                time.sleep(poll_ms / 1000.0)
            except Exception:
                time.sleep(3.0)
