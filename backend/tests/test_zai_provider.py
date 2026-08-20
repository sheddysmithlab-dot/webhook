"""Z.AI-only provider: no OpenAI fallback."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import (
    ZAI_API_BASE,
    ZAI_MODEL,
    is_openai_api_base,
    is_zai_api_base,
    normalize_ai_api_base,
    normalize_ai_model,
)


def test_normalize_forces_zai():
    assert normalize_ai_api_base("") == ZAI_API_BASE
    assert normalize_ai_api_base("https://api.openai.com/v1") == ZAI_API_BASE
    assert normalize_ai_api_base("https://api.groq.com/openai/v1") == ZAI_API_BASE
    assert normalize_ai_api_base("https://openrouter.ai/api/v1") == ZAI_API_BASE
    assert normalize_ai_api_base("https://api.z.ai/api/paas/v4") == ZAI_API_BASE
    assert normalize_ai_api_base("https://z.ai") == ZAI_API_BASE
    assert is_zai_api_base(ZAI_API_BASE)
    assert is_openai_api_base("https://api.openai.com/v1")
    assert not is_openai_api_base(ZAI_API_BASE)
    print("OK normalize forces Z.AI")


def test_normalize_model_glm_only():
    assert normalize_ai_model("gpt-4o-mini", ZAI_API_BASE) == ZAI_MODEL
    assert normalize_ai_model("glm-4.5-flash", ZAI_API_BASE) == "glm-4.5-flash"
    assert normalize_ai_model("glm-4.5", ZAI_API_BASE) == "glm-4.5"
    assert normalize_ai_model("", ZAI_API_BASE) == ZAI_MODEL
    print("OK model maps to GLM")


def test_prompt_has_criteria_priority():
    from app.ai.prompt import SYSTEM_PROMPT

    assert "TRAINED CRITERIA PRIORITY" in SYSTEM_PROMPT
    assert "Z.AI" in SYSTEM_PROMPT
    assert "NEVER invent" in SYSTEM_PROMPT
    assert "common sense" not in SYSTEM_PROMPT.lower()
    print("OK prompt criteria")


if __name__ == "__main__":
    test_normalize_forces_zai()
    test_normalize_model_glm_only()
    test_prompt_has_criteria_priority()
    print("ALL OK")
