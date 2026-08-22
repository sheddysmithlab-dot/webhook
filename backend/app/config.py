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
    # True = plain Z.AI chat only (no listing agent). False = InfraDealer listing agent.
    ai_simple_chat: bool = False
    # Scoped free-chat for non-business messages (greetings, general queries, small talk).
    # Known listing/account/OTP/confirm flows still use hard rules + tools.
    ai_free_chat: bool = True
    # AI Corrector: fix user message typos/spelling before agent processes it.
    ai_corrector: bool = True
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


settings = Settings()
