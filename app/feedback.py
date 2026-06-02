"""
feedback.py — парсинг фидбека через локальную Ollama (qwen2.5:14b).
Запись в feedback.json всегда происходит, даже если Ollama недоступна.
"""
import os
import json
import requests
from pydantic import BaseModel
from typing import Optional
from app.clap_engine import get_embedding

FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "feedback.json")
OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODEL  = "qwen2.5:14b"

class FeedbackItem(BaseModel):
    key: str
    mode: str
    text: str
    prompt: str = ""
    vibe_mismatch: bool = False

class ParsedFeedback(BaseModel):
    key: str
    mode: str
    liked: bool
    bpm_delta: Optional[int] = None
    preferred_mode: Optional[str] = None
    preferred_key: Optional[str] = None
    melody_density: Optional[str] = None
    bass_activity: Optional[str] = None
    chord_density: Optional[str] = None
    raw_text: str = ""

PARSE_PROMPT = """You are a music parameter extractor. The user just heard a MIDI track and gave feedback in Russian or English.

Track info:
- Key: {key}
- Mode: {mode}

User feedback: "{text}"

Extract parameters as JSON. Rules:
- liked: true if positive overall, false if negative
- bpm_delta: integer +/- (e.g. +10 if "too slow", -10 if "too fast"), null if not mentioned
- preferred_mode: "minor" or "major" if mentioned, null otherwise
- preferred_key: a musical key if mentioned, null otherwise
- melody_density:
    "dense" if melody has too many gaps/rests/pauses, feels interrupted, choppy (Russian: прерывистая, с паузами, обрывистая)
    "sparse" if melody feels too busy, too many notes
    null if fine
- melody_variety:
    "more" if melody feels repetitive, monotonous (Russian: репетативная, однообразная, монотонная, скучная)
    "less" if melody feels too chaotic, random (Russian: хаотичная, случайная)
    null if fine
- bass_activity: "less" if bass is too much, "more" if bass is weak/missing, null if fine
- chord_density: "less" if chords are too frequent, "more" if chords feel absent, null if fine
- chord_min_interval: integer semitones (e.g. 3 if "notes in chord too close"), null if not mentioned

Respond ONLY with valid JSON, no markdown, no explanation:
{{"liked": bool, "bpm_delta": int_or_null, "preferred_mode": str_or_null, "preferred_key": str_or_null, "melody_density": str_or_null, "melody_variety": str_or_null, "bass_activity": str_or_null, "chord_density": str_or_null, "chord_min_interval": int_or_null}}"""


def _keyword_fallback(text: str) -> dict:
    """Простой парсер по ключевым словам — никогда не падает."""
    t = text.lower()
    liked = any(w in t for w in [
        "хорошо", "круто", "нравится", "классно", "огонь", "кайф", "топ", "огонь",
        "good", "great", "nice", "like", "love", "fire", "yes", "да", "супер", "норм",
    ])
    disliked = any(w in t for w in [
        "плохо", "не нравится", "ужас", "отстой", "скучно", "однообразно",
        "bad", "boring", "terrible", "no", "нет", "слабо",
    ])
    if disliked:
        liked = False

    bpm_delta = None
    if any(w in t for w in ["медленно", "slow", "медленн"]):
        bpm_delta = +10
    elif any(w in t for w in ["быстро", "fast", "слишком быстр"]):
        bpm_delta = -10

    melody_variety = None
    if any(w in t for w in ["однообразн", "монотон", "скучн", "повторяет", "repetiti", "boring"]):
        melody_variety = "more"
    elif any(w in t for w in ["хаотич", "случайн", "chaotic", "random"]):
        melody_variety = "less"

    melody_density = None
    if any(w in t for w in ["прерывист", "пауз", "обрывист", "choppy"]):
        melody_density = "dense"
    elif any(w in t for w in ["слишком много нот", "загружен", "busy"]):
        melody_density = "sparse"

    bass_activity = None
    if any(w in t for w in ["бас слаб", "бас тих", "bass weak", "bass missing"]):
        bass_activity = "more"
    elif any(w in t for w in ["бас громк", "много баса", "bass too"]):
        bass_activity = "less"

    return {
        "liked": liked,
        "bpm_delta": bpm_delta,
        "melody_variety": melody_variety,
        "melody_density": melody_density,
        "bass_activity": bass_activity,
    }


def _parse_with_ollama(key: str, mode: str, text: str) -> dict:
    prompt = PARSE_PROMPT.format(key=key, mode=mode, text=text)
    resp = requests.post(OLLAMA_URL, json={
        "model":   OLLAMA_MODEL,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1},
    }, timeout=30)
    resp.raise_for_status()
    raw = resp.json().get("response", "{}").strip()
    # Убрать ```json ... ``` если модель добавила
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1][4:] if parts[1].startswith("json") else parts[1]
    return json.loads(raw.strip())


def parse_feedback(key: str, mode: str, text: str) -> dict:
    """Пробует Ollama, при ошибке — keyword fallback. Никогда не кидает исключение."""
    try:
        result = _parse_with_ollama(key, mode, text)
        print(f"[Feedback] Ollama OK liked={result.get('liked')}")
        return result
    except Exception as e:
        print(f"[Feedback] Ollama недоступна ({e}) — keyword fallback")
        result = _keyword_fallback(text)
        print(f"[Feedback] Keyword liked={result.get('liked')}")
        return result


def save_feedback(item: FeedbackItem) -> dict:
    parsed = parse_feedback(item.key, item.mode, item.text)

    prompt_emb = []
    try:
        prompt_emb = get_embedding(item.prompt) if item.prompt else []
    except Exception as e:
        print(f"[Feedback] CLAP embedding error: {e}")

    entry = {
        "key":                item.key,
        "mode":               item.mode,
        "raw_text":           item.text,
        "prompt":             item.prompt,
        "vibe_mismatch":      item.vibe_mismatch,
        "liked":              parsed.get("liked", True),
        "bpm_delta":          parsed.get("bpm_delta"),
        "preferred_mode":     parsed.get("preferred_mode"),
        "preferred_key":      parsed.get("preferred_key"),
        "melody_density":     parsed.get("melody_density"),
        "melody_variety":     parsed.get("melody_variety"),
        "bass_activity":      parsed.get("bass_activity"),
        "chord_density":      parsed.get("chord_density"),
        "chord_min_interval": parsed.get("chord_min_interval"),
        "prompt_embedding":   prompt_emb,
    }

    data = {"history": []}
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    data["history"].append(entry)

    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[Feedback] Сохранено #{len(data['history'])} — {item.key} {item.mode} | {entry}")
    return entry