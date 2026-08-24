"""InfraDealer WhatsApp AI agent layer.

Does NOT publish live listings — submissions go through Admin / InfraDealer review.

Hot path (Phase 3–4 unified prompt chat):

    runner.py         — webhook entry (lock, media, corrector, route)
      → orchestrator.py — account_filter → prompt_chat_turn | free_chat | chat_memory
          → prompt.py       — SYSTEM_PROMPT + CURRENT_STATE builder
          → engine.py       — llm_reply + tools + hard gates; respond() for last-listing edit
          → chat_memory.py  — soft rules fallback
          → free_chat.py    — secondary LLM fallback (no tools) when no listing context
          → account_filter.py / account.py / confirm.py / data_filteration.py / data_push.py
          → session_memory.py — idle reset / last-listing resume
          → cards.py / extract.py / schema.py / tools.py / corrector.py / i18n*

Phase-4: every turn logs reply_path + ai_ms (AiEvent + orchestrator event).
Rollback: AI_PROMPT_CHAT=false (legacy free_chat / static options).
"""
