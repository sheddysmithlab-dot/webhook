"""InfraDealer WhatsApp AI agent layer.

Does NOT publish live listings — all submissions go through the Admin Panel.

Agent flow (four-agent orchestrator):

    runner.py            — webhook entry point (dedup, media download, lock)
      -> orchestrator.py — master workflow router + correlation IDs
          -> session_memory.py  — idle reset + last-listing resume (pre-turn)
          -> account_filter.py  — Agent 1: WHO?  (identity, eligibility, blocked check)
          -> chat_memory.py     — Agent 2: WHAT?  (intent, data collection, confirmation)
              -> data_filteration.py — validate/normalize/extract fields
              -> data_push.py       — Agent 3+4: SUBMIT + admin status sync
          -> free_chat.py     — scoped LLM fallback for non-business messages
          -> engine.py        — legacy fallback (last-listing edit mode only)

Shared modules:
    schema.py         — field definitions, listing title, payload helpers
    tools.py           — execute_tool dispatch + draft helpers
    i18n.py            — Hinglish/Hindi/English reply templates
    confirm.py         — yes/no/modification detection + confirmation helpers
    extract.py         — role/state/city extraction helpers
    cards.py            — multi-card (multi-listing) session isolation
    account.py         — OTP / onboarding UX (uses account_filter for identity)
"""
