import mimetypes
import os
import requests

HEYGEN_API_KEY = os.environ["HEYGEN_API_KEY"]


def upload_asset(path: str) -> str:
    url = "https://upload.heygen.com/v1/asset"
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        mime = "application/octet-stream"
    headers = {"X-Api-Key": HEYGEN_API_KEY, "Content-Type": mime}
    with open(path, "rb") as f:
        r = requests.post(url, headers=headers, data=f, timeout=120)
    r.raise_for_status()
    data = r.json()
    print("upload response:", data)
    return data.get("asset_id") or data.get("id") or data.get("data", {}).get("asset_id")


if __name__ == "__main__":
    print(upload_asset("audio/turn_00001_A.mp3"))
