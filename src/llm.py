import os
import re
import time
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

from openai import OpenAI
from src.retry import retry

SCIENTIST_SYSTEM = """Ты Scientist. Говоришь живо, по‑человечески, но точно.
Формат ответа: 1–2 предложения (6–10 секунд речи). 3 предложения — только если иначе теряется смысл.
Стиль: ясная мысль → один конкретный аргумент → в конце ОБЯЗАТЕЛЬНО тестируемое
предсказание/сигнал (в стиле: «если верно X, то при Y увидим Z»).
Тема: биология старения (эпигенетические часы, сенесцентные клетки, митохондрии,
протеостаз, воспаление/иммунное старение, истощение стволовых клеток, метаболические пути).
Не ограничивайся «надо измерить»: называй ожидаемый знак/направление эффекта
(сильнее/слабее/смещается/ускоряется), без цифр.
Всегда формулируй конкурирующие гипотезы явно (например, «программируемое старение vs накопление повреждений»).
Начинай с конкретного кейса/примера, избегай общих фраз вида «теории должны…».
Не используй «умные термины», если они не участвуют в тесте/предсказании.
Не используй слова «точно», «однозначно», «обязательно» — вместо этого
«сильно поддержит», «смещает баланс», «увеличивает правдоподобие».
Если утверждение зависит от подтипа механизма (например, мТОR‑зависимо/независимо) — говори условно.
Не давай медицинских рекомендаций; если не уверен — помечай как гипотезу/неуверенно.
Разрешены короткие разговорные связки (1–2 слова), но без канцелярита.
Не повторяй идеи из последних 3 реплик."""
SKEPTIC_SYSTEM = """Ты Skeptic. Критично проверяешь тезисы, но без токсичности.
Формат ответа: 1–2 предложения (6–10 секунд речи). 3 предложения — только если иначе теряется смысл.
Дай контраргумент/сомнение + альтернативное объяснение ИЛИ контр‑тест.
В конце ОБЯЗАТЕЛЬНО: как различить (какой тест/наблюдение отделит X от Z).
Тема: биология старения (эпигенетические часы, сенесценция, митохондрии, протеостаз,
воспаление/иммунное старение, стволовые клетки, метаболизм).
Не ограничивайся «надо измерить»: называй ожидаемый знак/направление эффекта,
без цифр.
Всегда формулируй конкурирующие гипотезы явно (например, «программируемое старение vs накопление повреждений»).
Начинай с конкретного кейса/примера, избегай общих фраз.
Не используй слова «точно», «однозначно», «обязательно» — вместо этого
«сильно поддержит», «смещает баланс», «увеличивает правдоподобие».
Не давай медицинских рекомендаций; если не уверен — помечай как гипотезу/неуверенно.
Разрешены короткие разговорные связки (1–2 слова), но без канцелярита.
Не повторяй идеи из последних 3 реплик."""

@dataclass
class LLMConfig:
    model: str

def _build_input(
    system: str,
    topic: str,
    running_summary: str,
    history: List[Dict],
    *,
    anchor_case: str = "",
    turn_id: Optional[int] = None,
    extra_rules: Optional[List[str]] = None,
) -> List[Dict]:
    last = history[-12:]
    context = "\n".join([f"{h['speaker']}: {h['text']}" for h in last]) if last else "(пока пусто)"
    parts = [f"Тема дискуссии: {topic}"]
    if anchor_case:
        parts.append(f"Якорный кейс: {anchor_case}")
    if turn_id is not None:
        parts.append(f"Номер хода: {turn_id}")
    if extra_rules:
        rules = "\n".join([f"- {r}" for r in extra_rules])
        parts.append(f"Доп. правила этого хода:\n{rules}")
    parts.append(f"Running summary (может быть пустым): {running_summary or '(empty)'}")
    parts.append(f"Последние реплики:\n{context}")
    parts.append("Твой следующий ход:")
    user = "\n\n".join(parts)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

_SENT_RE = re.compile(r"[^.!?…]+(?:[.!?…]+|$)")


def _limit_sentences(text: str, max_sentences: int) -> str:
    if max_sentences <= 0:
        return text.strip()
    parts = [m.group(0).strip() for m in _SENT_RE.finditer(text or "") if m.group(0).strip()]
    if not parts:
        return (text or "").strip()
    return " ".join(parts[:max_sentences]).strip()

def generate_turn(
    client: OpenAI,
    cfg: LLMConfig,
    *,
    speaker: str,  # "A" (Scientist) | "B" (Skeptic)
    topic: str,
    running_summary: str,
    history: List[Dict],
    anchor_case: str = "",
    turn_id: Optional[int] = None,
    extra_rules: Optional[List[str]] = None,
) -> Tuple[str, float]:
    t0 = time.time()
    system = SCIENTIST_SYSTEM if speaker == "A" else SKEPTIC_SYSTEM

    def _call() -> str:
        resp = client.responses.create(
            model=cfg.model,
            input=_build_input(
                system,
                topic,
                running_summary,
                history,
                anchor_case=anchor_case,
                turn_id=turn_id,
                extra_rules=extra_rules,
            ),
        )
        return (resp.output_text or "").strip()

    text = retry(_call, name=f"llm_{speaker}")
    max_sentences = int(os.getenv("MAX_SENTENCES", "2"))
    text = _limit_sentences(text, max_sentences)
    return text, (time.time() - t0)
