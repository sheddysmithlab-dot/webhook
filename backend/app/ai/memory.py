"""SQL-backed agent memory. Learns slang/corrections from live chats. No secrets/PII."""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from ..models import AiAgentMemory, Chat
from ..services import utcnow

log = logging.getLogger("infradealer.ai.memory")

SEED = [
    ("slang", "madal", "model"),
    ("slang", "modal", "model"),
    ("slang", "kimat", "price"),
    ("slang", "keemat", "price"),
    ("slang", "qimat", "price"),
    ("slang", "ranig", "running"),
    ("slang", "runing", "running"),
    ("slang", "bima", "insurance"),
    ("slang", "beema", "insurance"),
    ("slang", "tex", "tax"),
    ("slang", "quvtar", "quarter tax"),
    ("slang", "quarter", "quarter tax"),
    ("slang", "fotu", "photo"),
    ("slang", "foto", "photo"),
    ("slang", "pics", "photo"),
    ("slang", "prize", "price"),
    ("slang", "prise", "price"),
    ("slang", "indor", "indore"),
    ("slang", "banglore", "bangalore"),
    ("slang", "hydrabad", "hyderabad"),
    ("slang", "gadii", "gadi"),
    ("correction", "repeat_question", "Do not ask the same field again if it is already in state."),
    ("correction", "photos_enough", "If customer says these are the only photos, stop asking for more photos."),
]

_REPEAT = re.compile(
    r"baar baar|ek hi sawal|same question|pehli? (baat|baar)|already (told|said|sent)|pehle bata",
    re.I,
)
_PHOTOS_DONE = re.compile(
    r"bas yahi|yahi photo|aur nahi|itni hi|nahi h(e|ai) bas|bhej di (he|hai|hain)|only (these|this) photo",
    re.I,
)


def seed_memory(db: Session) -> None:
    existing = {((r.kind or "") + "|" + (r.cue or "")) for r in db.query(AiAgentMemory).all()}
    for kind, cue, meaning in SEED:
        key = f"{kind}|{cue}"
        if key in existing:
            continue
        db.add(AiAgentMemory(kind=kind, cue=cue, meaning=meaning, source="seed", hits=1))
    db.flush()


def load_reps(db: Session) -> list[tuple[str, str]]:
    rows = (
        db.query(AiAgentMemory)
        .filter(AiAgentMemory.kind == "slang")
        .order_by(AiAgentMemory.hits.desc(), AiAgentMemory.id.desc())
        .limit(80)
        .all()
    )
    out = []
    for row in rows:
        cue = (row.cue or "").strip().lower()
        meaning = (row.meaning or "").strip().lower()
        if cue and meaning and cue != meaning:
            out.append((cue, meaning))
    return out


def prompt_block(db: Session) -> str:
    rows = (
        db.query(AiAgentMemory)
        .order_by(AiAgentMemory.hits.desc(), AiAgentMemory.id.desc())
        .limit(24)
        .all()
    )
    if not rows:
        return ""
    lines = []
    for row in rows:
        lines.append(f"- {row.kind}: {row.cue} → {row.meaning}")
    return (
        "LEARNED FROM PAST CHATS (SQL memory, follow these):\n"
        + "\n".join(lines)
        + "\nNever re-ask a field already present in CURRENT_STATE. "
        "If customer says photos are enough / already sent, stop asking photos. "
        "If they say you asked the same question, apologise once and move to the NEXT missing field."
    )


def remember(db: Session, kind: str, cue: str, meaning: str, fields: dict | None = None, source: str = "rule") -> None:
    cue = (cue or "").strip().lower()[:120]
    meaning = (meaning or "").strip()[:300]
    kind = (kind or "fact")[:24]
    if not cue or not meaning:
        return
    if re.fullmatch(r"\d{10}", cue) or "sk-" in meaning.lower() or "otp" in cue:
        return
    row = (
        db.query(AiAgentMemory)
        .filter(AiAgentMemory.kind == kind, AiAgentMemory.cue == cue)
        .first()
    )
    if row:
        row.hits = (row.hits or 0) + 1
        row.last_used_at = utcnow()
        if meaning and meaning != row.meaning:
            row.meaning = meaning
        return
    db.add(
        AiAgentMemory(
            kind=kind,
            cue=cue,
            meaning=meaning,
            fields_json=json.dumps(fields or {}, ensure_ascii=False),
            source=source[:24],
            hits=1,
            last_used_at=utcnow(),
        )
    )


def harvest_turn(db: Session, text: str, fields: dict, last_ask: str) -> None:
    seed_memory(db)
    msg = (text or "").strip()
    low = msg.lower()
    if not msg:
        return
    if _REPEAT.search(low):
        remember(db, "correction", last_ask or "repeat_question", "Do not repeat this question.")
    if _PHOTOS_DONE.search(low):
        remember(db, "correction", "photos_enough", "Customer said photos are complete. Stop asking more photos.")
    for m in re.finditer(r"\b(tata|eicher|mahindra|jcb|leyland)(\d{3,4})\b", low):
        remember(db, "mapping", m.group(0), f"{m.group(1).title()} {m.group(2)}", fields)
    if fields.get("model") and fields.get("brand"):
        remember(db, "fact", f"{fields['brand']} {fields['model']}".lower(), "known vehicle", fields)
    glued = re.findall(r"\b[a-z]{4,10}\d{3,4}\b", low)
    for token in glued:
        remember(db, "slang", token, re.sub(r"(\D+)(\d+)", r"\1 \2", token), source="rule")


def photos_complete(text: str) -> bool:
    return bool(_PHOTOS_DONE.search(text or ""))


def customer_annoyed(text: str) -> bool:
    return bool(_REPEAT.search(text or ""))


def last_outbound_body(db: Session, conversation_id: str) -> str:
    rows = recent_outbound_bodies(db, conversation_id, 1)
    return rows[0] if rows else ""


def recent_outbound_bodies(db: Session, conversation_id: str, limit: int = 6) -> list[str]:
    rows = (
        db.query(Chat)
        .filter(Chat.conversation_id == conversation_id, Chat.direction == "outbound")
        .order_by(Chat.id.desc())
        .limit(limit)
        .all()
    )
    return [(row.body or "").strip() for row in rows if (row.body or "").strip()]


def _card_facts(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        if re.match(r"^(Vehicle|Category|Year|Rate|Location|Running|Phone|Name|Photos)\s*:", line, re.I):
            lines.append(re.sub(r"\s+", " ", line.strip().lower()))
    return "\n".join(lines)


def too_similar(a: str, b: str) -> bool:
    left = re.sub(r"\s+", " ", (a or "").strip().lower())
    right = re.sub(r"\s+", " ", (b or "").strip().lower())
    if not left or not right:
        return False
    if left == right:
        return True
    facts_a, facts_b = _card_facts(a), _card_facts(b)
    if facts_a and facts_b:
        return facts_a == facts_b
    shorter = min(len(left), len(right))
    if shorter <= 48 and left[:shorter] == right[:shorter]:
        return True
    if len(left) > 24 and left in right:
        return True
    if len(right) > 24 and right in left:
        return True
    compact_a = re.sub(r"[^\w\u0900-\u097f]+", "", left)
    compact_b = re.sub(r"[^\w\u0900-\u097f]+", "", right)
    if compact_a and compact_a == compact_b:
        return True
    return False


_QUESTION_KINDS = [
    ("confirm", r"haan ya yes|details sahi|ye details|type yes|hain\?"),
    ("photos", r"photo|fotos|tasveer|\bpics\b"),
    ("otp", r"\botp\b"),
    ("state", r"kis state|which state|rajya|state me khadi"),
    ("optional", r"optional hai|tyre kitne|kitne owner|finance kitna"),
    ("account", r"account bana|broker hain|password kya"),
    ("year", r"saal|year|manufacturing"),
    ("price", r"\brate\b|kimat|price|budget"),
    ("location", r"kahan|khadi|location|kahaan|kahan khadi|kis state"),
    ("intent", r"bechni|lene wali|selling or buying|bechna chahte|lena\?"),
    ("category", r"truck, dumper|ye kya hai|backhoe|poclain|excavator|grader|crusher"),
    ("brand", r"kis company|brand|model name|kaunsi gadi"),
    ("name", r"naam bata|your name|apna naam"),
    ("intro", r"ai executive"),
    ("lock", r"lock ho gayi|details lock|details are locked"),
    ("choice", r"alag gadi|\bupdate\b"),
]


def question_kind(text: str) -> str:
    low = re.sub(r"\s+", " ", (text or "").lower())
    for kind, pat in _QUESTION_KINDS:
        if re.search(pat, low, re.I):
            return kind
    return ""


def is_repeat_outbound(reply: str, recents: list[str]) -> bool:
    msg = (reply or "").strip()
    if not msg:
        return True
    if any(too_similar(msg, prev) for prev in recents):
        return True
    kind = question_kind(msg)
    if not kind or kind in {"confirm", "lock"}:
        return False
    for prev in recents[:4]:
        if question_kind(prev) == kind:
            return True
    return False


def harvest_with_llm(db: Session, text: str) -> None:
    """Use the saved API key to learn slang/corrections. Never store PII/secrets."""
    msg = (text or "").strip()
    if len(msg) < 10 or msg.startswith("["):
        return
    if re.fullmatch(r"[\d\s+\-]+", msg):
        return
    try:
        from ..services import resolve_ai_config
        import httpx
    except Exception:
        return
    cfg = resolve_ai_config(db)
    if not cfg.get("enabled") or not cfg.get("api_key"):
        return
    url = (cfg.get("api_base") or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    body = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "temperature": 0,
        "max_tokens": 200,
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract spelling/slang mappings from one Indian WhatsApp vehicle line. "
                    'JSON only: {"items":[{"kind":"slang","cue":"madal","meaning":"model"}]}. '
                    "kind is slang or mapping. cue=misspelling or glued token. meaning=correct English word or 'Brand Model'. "
                    "No phones, names, OTP, prices, cities, or secrets. Empty items if nothing to learn."
                ),
            },
            {"role": "user", "content": msg[:400]},
        ],
    }
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.post(url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return
        raw = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        parsed = json.loads(raw)
        items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            return
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            cue = str(item.get("cue") or "")
            meaning = str(item.get("meaning") or "")
            kind = str(item.get("kind") or "slang")
            if re.search(r"\d{10}|sk-|otp|token", cue + meaning, re.I):
                continue
            remember(db, kind, cue, meaning, source="llm")
    except Exception as exc:
        log.info("memory harvest llm skipped: %s", exc)
