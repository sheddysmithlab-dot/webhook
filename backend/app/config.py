from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./infradealer.db"
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_origin: str = "http://127.0.0.1:5173"
    admin_token: str = ""
    otp_ttl_seconds: int = 300
    ref_ttl_hours: int = 48
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_waba_id: str = ""
    meta_phone_number_id: str = ""
    meta_system_user_token: str = ""
    meta_verify_token: str = ""
    meta_test_recipient: str = ""

    # ── InfraDealer backend connection ─────────────────────────────────────
    # When set, every approved WhatsApp listing is pushed to InfraDealer for
    # admin review via the authenticated webhook API.
    infradealer_api_url: str = ""       # e.g. https://api.infradealer.com
    infradealer_api_key: str = ""       # X-InfraDealer-Key  (idk_…)
    infradealer_api_secret: str = ""    # HMAC signing secret (ids_…)


settings = Settings()
