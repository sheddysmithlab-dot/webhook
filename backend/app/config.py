from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./infradealer.db"
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_origin: str = "http://127.0.0.1:5173"
    admin_token: str = ""
    auth_email: str = ""
    auth_password_hash: str = ""
    auth_session_secret: str = ""
    otp_ttl_seconds: int = 300
    ref_ttl_hours: int = 48
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_waba_id: str = ""
    meta_phone_number_id: str = ""
    meta_system_user_token: str = ""
    meta_verify_token: str = ""
    meta_test_recipient: str = ""
    ai_enabled: bool = True
    ai_api_key: str = ""
    ai_api_base: str = "https://api.z.ai/api/paas/v4"
    ai_model: str = "glm-4.5-flash"
    ai_reply_language: str = "auto"
    # True = plain Z.AI chat only (removed; kept False for env compat, ignored by runner).
    ai_simple_chat: bool = False
    # Scoped free-chat secondary fallback when prompt_chat LLM fails (no listing context).
    # With ai_prompt_chat=True, primary path is unified prompt chat (Phase 3).
    ai_free_chat: bool = True
    # AI Corrector: fix user message typos/spelling before agent processes it.
    ai_corrector: bool = True
    # Phase-1/3/4 prompt chat: LLM-first (tools + SYSTEM_PROMPT) with chat_memory fallback.
    # Phase-3: free_chat + static options merge into orchestrator when True.
    # Phase-4: reply_path + ai_ms logged on every turn. Rollback: AI_PROMPT_CHAT=false.
    ai_prompt_chat: bool = True
    ai_media_dir: str = "./data/media"
    infradealer_base_url: str = ""
    infradealer_api_key: str = ""
    infradealer_api_secret: str = ""
    infradealer_api_version: str = "v1"
    infradealer_timeout: float = 30.0
    infradealer_max_retries: int = 3
    infradealer_mode: str = "LIVE"
    infradealer_auto_publish: bool = False
    # When True (default), unsigned admin events are accepted if no API secret is configured.
    # Set to False in production with a configured secret for strict HMAC verification.
    allow_unsigned_admin_events: bool = True
    redis_url: str = ""

    # --- OTP via India DLT SMS (never WhatsApp) ---
    # SMS_PROVIDER: log | msg91 | http | textguru
    sms_provider: str = "log"
    sms_api_key: str = ""
    sms_api_url: str = ""
    sms_sender_id: str = ""
    sms_dlt_template_id: str = ""
    sms_dlt_entity_id: str = ""
    # Must match DLT-registered template; use {otp} placeholder.
    sms_otp_template: str = (
        "Your OTP for InfraDealer is {otp}. The OTP is valid for 10 minutes. "
        "Please do not share this OTP with anyone. Regards, AREANS"
    )
    # TextGuru (same as api.infradealer.com OTP)
    textguru_api_url: str = "https://www.textguru.in/api/v22.0/"
    textguru_username: str = ""
    textguru_password: str = ""
    textguru_sender_id: str = "AREANS"
    textguru_dlt_template_id: str = "1777178540657949209"
    textguru_service: str = "OTP"
    textguru_message_template: str = (
        "Your OTP for InfraDealer is {OTP}. The OTP is valid for 10 minutes. "
        "Please do not share this OTP with anyone. Regards, AREANS"
    )
    sms_enabled: bool = True


settings = Settings()
