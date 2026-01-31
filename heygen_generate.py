import os
import time
import requests

HEYGEN_API_KEY = os.environ["HEYGEN_API_KEY"]


def _build_character(character_type: str, character_id: str) -> dict:
    ctype = (character_type or "avatar").strip().lower()
    if not character_id:
        raise ValueError("character_id is required")
    if ctype == "talking_photo":
        return {"type": "talking_photo", "talking_photo_id": character_id}
    if ctype == "avatar":
        return {"type": "avatar", "avatar_id": character_id}
    raise ValueError(f"Unsupported character_type: {character_type}")


def generate_video(character_type: str, character_id: str, audio_asset_id: str) -> str:
    url = "https://api.heygen.com/v2/video/generate"
    headers = {"X-API-KEY": HEYGEN_API_KEY, "Content-Type": "application/json"}
    character = _build_character(character_type, character_id)

    payload = {
        "video_inputs": [
            {
                "character": character,
                "voice": {"type": "audio", "audio_asset_id": audio_asset_id},
                "caption": False,
            }
        ],
        "dimension": {"width": 1280, "height": 720},
    }

    r = requests.post(url, headers=headers, json=payload, timeout=120)
    print("generate response:", r.status_code, r.text)
    r.raise_for_status()
    data = r.json()
    return data.get("video_id") or data.get("data", {}).get("video_id")


def poll_video(video_id: str) -> str:
    url = "https://api.heygen.com/v1/video_status.get"
    headers = {"X-API-KEY": HEYGEN_API_KEY}

    for _ in range(120):
        r = requests.get(url, headers=headers, params={"video_id": video_id}, timeout=60)
        r.raise_for_status()
        data = r.json()
        print("status:", data)
        status = data.get("data", {}).get("status") or data.get("status")
        if status == "completed":
            return data.get("data", {}).get("video_url") or data.get("video_url")
        if status == "failed":
            raise RuntimeError(f"HeyGen failed: {data}")
        time.sleep(5)
    raise TimeoutError("HeyGen video not ready in time")


def download(url: str, out_path: str) -> None:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


if __name__ == "__main__":
    character_type = os.getenv("HEYGEN_CHARACTER_TYPE", "").strip().lower()
    talking_photo_id = os.getenv("HEYGEN_TALKING_PHOTO_ID", "")
    avatar_id = os.getenv("HEYGEN_AVATAR_ID", "")
    if not character_type:
        character_type = "talking_photo" if talking_photo_id else "avatar"
    character_id = talking_photo_id if character_type == "talking_photo" else avatar_id
    if not character_id:
        raise RuntimeError("HEYGEN_TALKING_PHOTO_ID or HEYGEN_AVATAR_ID is required")
    audio_asset_id = os.environ["HEYGEN_AUDIO_ASSET_ID"]

    vid = generate_video(character_type, character_id, audio_asset_id)
    video_url = poll_video(vid)
    download(video_url, "out.mp4")
    print("saved out.mp4")
