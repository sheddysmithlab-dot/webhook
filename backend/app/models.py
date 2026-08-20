from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(40), default="form")
    role: Mapped[str] = mapped_column(String(20), default="user")
    password_hash: Mapped[str] = mapped_column(String(128), default="")
    account_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    price: Mapped[float] = mapped_column(Float)
    condition: Mapped[str] = mapped_column(String(40))
    city: Mapped[str] = mapped_column(String(80))
    seller_name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="published", index=True)
    spam_flags: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    photo_ids: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wamid: Mapped[str] = mapped_column(String(128), default="", index=True)
    from_mobile: Mapped[str] = mapped_column(String(20), index=True)
    text: Mapped[str] = mapped_column(Text)
    ref: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    product_name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(80), default="Other")
    price: Mapped[str] = mapped_column(String(40), default="")
    price_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition: Mapped[str] = mapped_column(String(40), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    parsed_mobile: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(20), default="received")
    direction: Mapped[str] = mapped_column(String(12), default="inbound")
    source: Mapped[str] = mapped_column(String(40), default="webhook")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Otp(Base):
    __tablename__ = "otps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="sent")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80))
    price: Mapped[float] = mapped_column(Float)
    condition: Mapped[str] = mapped_column(String(40))
    city: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_account")
    dup_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    account_mode: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BlockedNumber(Base):
    __tablename__ = "blocked_numbers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mobile: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mobile: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    wa_id: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wamid: Mapped[str] = mapped_column(String(128), default="", index=True)
    conversation_id: Mapped[str] = mapped_column(String(80), index=True)
    from_mobile: Mapped[str] = mapped_column(String(20), index=True)
    from_name: Mapped[str] = mapped_column(String(120), default="")
    to_mobile: Mapped[str] = mapped_column(String(20), default="")
    direction: Mapped[str] = mapped_column(String(12), default="inbound")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="delivered")
    unread: Mapped[bool] = mapped_column(Boolean, default=True)
    sent_to_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    broadcast_id: Mapped[int | None] = mapped_column(ForeignKey("broadcasts.id"), nullable=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    media_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message: Mapped[str] = mapped_column(Text)
    total: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    recipients: Mapped[list["BroadcastRecipient"]] = relationship(back_populates="broadcast")


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id"))
    to_mobile: Mapped[str] = mapped_column(String(10))
    wamid: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(20), default="sent")
    broadcast: Mapped[Broadcast] = relationship(back_populates="recipients")


class MetaSettings(Base):
    __tablename__ = "meta_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    callback_url: Mapped[str] = mapped_column(String(300), default="")
    verify_token: Mapped[str] = mapped_column(String(80), default="")
    app_secret: Mapped[str] = mapped_column(String(200), default="")
    app_id: Mapped[str] = mapped_column(String(80), default="")
    waba_id: Mapped[str] = mapped_column(String(80), default="")
    phone_number_id: Mapped[str] = mapped_column(String(80), default="")
    system_user_token: Mapped[str] = mapped_column(String(512), default="")
    graph_version: Mapped[str] = mapped_column(String(20), default="v23.0")
    test_recipient: Mapped[str] = mapped_column(String(10), default="")
    field_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    field_template_status: Mapped[bool] = mapped_column(Boolean, default=True)
    field_account_alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_delivery: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_log: Mapped[str] = mapped_column(Text, default="[]")
    seq: Mapped[int] = mapped_column(Integer, default=1000)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_api_key: Mapped[str] = mapped_column(String(1024), default="")
    ai_api_base: Mapped[str] = mapped_column(String(300), default="https://api.openai.com/v1")
    ai_model: Mapped[str] = mapped_column(String(80), default="gpt-4o-mini")
    ai_reply_language: Mapped[str] = mapped_column(String(16), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiConversation(Base):
    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mobile: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(32), default="NEW_CHAT", index=True)
    intent: Mapped[str] = mapped_column(String(12), default="")
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    profile_status: Mapped[str] = mapped_column(String(24), default="none")
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    draft_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    last_wamid: Mapped[str] = mapped_column(String(128), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AiListingDraft(Base):
    __tablename__ = "ai_listing_drafts"
    __table_args__ = (UniqueConstraint("mobile", "card_id", name="uq_ai_listing_drafts_mobile_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    card_id: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    intent: Mapped[str] = mapped_column(String(12), default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING_REVIEW", index=True)
    customer_json: Mapped[str] = mapped_column(Text, default="{}")
    inferred_json: Mapped[str] = mapped_column(Text, default="{}")
    confirmed_json: Mapped[str] = mapped_column(Text, default="{}")
    title: Mapped[str] = mapped_column(String(200), default="")
    posted_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    cleanup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AiMedia(Base):
    __tablename__ = "ai_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id"), index=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("ai_listing_drafts.id"), nullable=True)
    wamid: Mapped[str] = mapped_column(String(128), default="", index=True)
    meta_media_id: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(20), default="image")
    mime: Mapped[str] = mapped_column(String(80), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    local_path: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiEvent(Base):
    __tablename__ = "ai_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wamid: Mapped[str] = mapped_column(String(128), default="", index=True)
    mobile: Mapped[str] = mapped_column(String(10), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AiAgentMemory(Base):
    __tablename__ = "ai_agent_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="slang", index=True)
    cue: Mapped[str] = mapped_column(String(120), default="", index=True)
    meaning: Mapped[str] = mapped_column(String(300), default="")
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(24), default="rule")
    hits: Mapped[int] = mapped_column(Integer, default=1)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InfraDealerIntegration(Base):
    __tablename__ = "infradealer_integration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(300), default="")
    api_key_enc: Mapped[str] = mapped_column(String(512), default="")
    api_secret_enc: Mapped[str] = mapped_column(String(512), default="")
    integration_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    callback_url: Mapped[str] = mapped_column(String(300), default="")
    api_version: Mapped[str] = mapped_column(String(10), default="v1")
    mode: Mapped[str] = mapped_column(String(10), default="LIVE")
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED")
    event_flags_json: Mapped[str] = mapped_column(Text, default="{}")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_code: Mapped[str] = mapped_column(String(80), default="")
    avg_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class InfraDealerOutbox(Base):
    __tablename__ = "infradealer_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    parent_request_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("ai_conversations.id"), nullable=True, index=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("ai_listing_drafts.id"), nullable=True, index=True)
    mobile: Mapped[str] = mapped_column(String(20), default="", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    business_status: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InfraDealerRequest(Base):
    __tablename__ = "infradealer_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    mobile: Mapped[str] = mapped_column(String(20), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    http_status: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    response_class: Mapped[str] = mapped_column(String(24), default="")
    business_code: Mapped[str] = mapped_column(String(80), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    safe_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(String(300), default="")
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    draft_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    outbox_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InfraDealerCallback(Base):
    __tablename__ = "infradealer_callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    callback_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(24), default="RECEIVED")
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InfraDealerAccountState(Base):
    __tablename__ = "infradealer_account_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mobile: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    profile_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    account_status: Mapped[str] = mapped_column(String(24), default="NOT_REQUESTED")
    infradealer_user_id: Mapped[str] = mapped_column(String(64), default="")
    registration_id: Mapped[str] = mapped_column(String(64), default="")
    pending_draft_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_request_id: Mapped[str] = mapped_column(String(64), default="")
    last_request_id: Mapped[str] = mapped_column(String(64), default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
