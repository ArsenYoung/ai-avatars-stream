import time
from dataclasses import dataclass
from typing import List, Dict

from openai import OpenAI
from src.retry import retry

@dataclass
class SummarizeConfig:
    model: str
    every_n_turns: int = 6

def should_summarize(turn_count: int, cfg: SummarizeConfig) -> bool:
    return cfg.every_n_turns > 0 and (turn_count % cfg.every_n_turns == 0)

def summarize(client: OpenAI, cfg: SummarizeConfig, *, running_summary: str, history: List[Dict]) -> tuple[str, float]:
    """
    ВАЖНО: summary без новых фактов.
    history: [{speaker, text}, ...]
    """
    t0 = time.time()

    def _call() -> str:
        prompt = (
            "Сожми разговор в 2–4 предложения.\n"
            "Правила:\n"
            "- Только перефразируй уже сказанное, НЕ добавляй новых фактов.\n"
            "- Если в разговоре были гипотезы/сомнения — так и отметь.\n"
            "- Без ссылок.\n\n"
            f"Текущий summary:\n{running_summary or '(empty)'}\n\n"
            "Последние реплики:\n" +
            "\n".join([f"{h['speaker']}: {h['text']}" for h in history[-12:]])
        )
        resp = client.responses.create(model=cfg.model, input=prompt)
        return (resp.output_text or "").strip()

    out = retry(_call, name="summarize")
    return out, (time.time() - t0)
