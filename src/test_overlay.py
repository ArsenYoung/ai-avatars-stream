import os

from obs_client import ObsClient


def main() -> None:
    obs = ObsClient(
        host=os.getenv("OBS_HOST", "127.0.0.1"),
        port=int(os.getenv("OBS_PORT", "4455")),
        password=os.getenv("OBS_PASSWORD", ""),
    )
    obs.connect()

    obs.set_text("TXT_SPEAKER", "Scientist")
    obs.set_text("TXT_TOPIC", "Topic: Теории старения")
    obs.set_text("TXT_STAGE", "DISCUSSION")

    print("ok")


if __name__ == "__main__":
    main()
