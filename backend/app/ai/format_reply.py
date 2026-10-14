"""WhatsApp-friendly ChatGPT-style reply formatting.

WhatsApp supports *bold*, _italic_, ~strike~, ```monospace```, and • bullets.
Keep replies scannable: short heading, bullet points, optional next step.
"""

from __future__ import annotations

import re

# Prompt/state labels the model sometimes echoes as a heading ("*Intent*\n• ...").
_INTERNAL_HEADING = re.compile(
    r"^[ \t]*[*_]*[ \t]*(?:intent|next[ _]?ask|next[ _]?step|rm[ _]?state|state|action|"
    r"reply|response|answer|output|missing[ _]?fields?|field|question|step)[ \t]*:?[ \t]*[*_]*[ \t]*$",
    re.I | re.M,
)


def fmt_section(title: str, bullets: list[str], *, footer: str = "") -> str:
    """Build *Heading* + • points + optional footer."""
    lines: list[str] = []
    title = (title or "").strip()
    if title:
        # Ensure WhatsApp bold heading
        if not (title.startswith("*") and title.endswith("*")):
            title = f"*{title.strip('*')}*"
        lines.append(title)
    for b in bullets:
        b = (b or "").strip()
        if not b:
            continue
        if not b.startswith(("•", "-", "*", "✓", "✅", "⚠️")):
            b = f"• {b}"
        lines.append(b)
    foot = (footer or "").strip()
    if foot:
        if lines:
            lines.append("")
        lines.append(foot)
    return "\n".join(lines).strip()


def strip_internal_headings(text: str) -> str:
    """Drop echoed state-label headings; a lone leftover bullet becomes plain text."""
    raw = text or ""
    cleaned = _INTERNAL_HEADING.sub("", raw)
    if cleaned == raw:
        return raw
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) == 1:
        lines[0] = re.sub(r"^\s*[•\-]\s*", "", lines[0])
    return "\n".join(lines).strip()


def bold(value) -> str:
    return f"*{value}*"
