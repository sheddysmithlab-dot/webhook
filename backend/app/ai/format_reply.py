"""WhatsApp-friendly ChatGPT-style reply formatting.

WhatsApp supports *bold*, _italic_, ~strike~, ```monospace```, and • bullets.
Keep replies scannable: short heading, bullet points, optional next step.
"""

from __future__ import annotations


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


def bold(value) -> str:
    return f"*{value}*"
