import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

def retry(
    fn: Callable[[], T],
    *,
    max_retries: int = 5,
    base_delay_s: float = 0.8,
    max_delay_s: float = 10.0,
    name: str = "op",
) -> T:
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            # простая экспонента + джиттер
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay = delay * (0.7 + random.random() * 0.6)
            print(f"[retry] {name} failed (attempt {attempt}/{max_retries}): {e}. sleep {delay:.1f}s")
            time.sleep(delay)
    raise last_err
