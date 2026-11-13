"""The exact answer formats (JSON schemas) the local AI must follow for each kind of question."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}

SENTIMENT = _obj({"score": NUM, "catalyst": BOOL, "summary": STR}, ["score", "catalyst", "summary"])
DECISION = _obj({"action": {"type": "string", "enum": ["buy", "short", "skip"]}, "confidence": NUM,
                 "reasoning": STR}, ["action", "confidence", "reasoning"])
SWING_REVIEW = _obj({"approve": BOOL, "reason": STR}, ["approve", "reason"])
BRIEFING = _obj({
    "outlook": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
    "summary": STR,
    "focus": {"type": "array", "items": _obj({"symbol": STR, "bias": STR, "reason": STR}, ["symbol", "reason"])},
    "risks": {"type": "array", "items": STR},
}, ["outlook", "summary", "focus", "risks"])
AFTER_CLOSE = _obj({
    "summary": STR,
    "watch_tomorrow": {"type": "array", "items": _obj({"symbol": STR, "reason": STR}, ["symbol", "reason"])},
}, ["summary", "watch_tomorrow"])
LESSONS = _obj({"review": STR, "new_lessons": {"type": "array", "items": STR},
                "retire_lessons": {"type": "array", "items": STR}}, ["review", "new_lessons"])
# AI trader test, version 2: the evening plan and the morning score
PREP = _obj({"situation": STR, "good_news_would_be": STR, "bad_news_would_be": STR, "already_expected": STR},
            ["situation", "good_news_would_be", "bad_news_would_be", "already_expected"])
SCORE = _obj({"score": NUM, "reasoning": STR}, ["score", "reasoning"])
