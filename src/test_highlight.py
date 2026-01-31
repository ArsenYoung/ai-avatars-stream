import os
import time

from obs_client import ObsClient

A = os.getenv("AVATAR_A_SOURCE", "AVATAR_A")
B = os.getenv("AVATAR_B_SOURCE", "AVATAR_B")
DIM = os.getenv("FILTER_DIM", "DIM")
SPK = os.getenv("FILTER_SPEAK", "SPEAK")

obs = ObsClient(
    host=os.getenv("OBS_HOST", "127.0.0.1"),
    port=int(os.getenv("OBS_PORT", "4455")),
    password=os.getenv("OBS_PASSWORD", ""),
)
obs.connect()

# A speaks
obs.set_filter_enabled(A, SPK, True)
obs.set_filter_enabled(A, DIM, False)
obs.set_filter_enabled(B, SPK, False)
obs.set_filter_enabled(B, DIM, True)
time.sleep(2)

# B speaks
obs.set_filter_enabled(A, SPK, False)
obs.set_filter_enabled(A, DIM, True)
obs.set_filter_enabled(B, SPK, True)
obs.set_filter_enabled(B, DIM, False)

print("ok")
