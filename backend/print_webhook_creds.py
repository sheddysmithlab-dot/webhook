"""Print live Meta webhook values for developers.facebook.com Configuration."""
from app.database import SessionLocal
from app.services import get_or_create_settings
from app.config import settings

db = SessionLocal()
try:
    m = get_or_create_settings(db)
    db.commit()
    print("=" * 60)
    print("INFRADEALER <-> META WHATSAPP CONNECT")
    print("=" * 60)
    print(f"Callback URL : {m.callback_url}")
    print(f"Verify Token : {m.verify_token}")
    print(f"Public base  : {settings.public_base_url}")
    print()
    print("Meta Console steps:")
    print("1) App → WhatsApp → Configuration (or App → Webhooks)")
    print("2) Paste Callback URL + Verify Token → Verify and save")
    print("3) Subscribe to field: messages")
    print("4) Copy App ID, App Secret, Phone number ID, WABA ID")
    print("5) Create System User token (whatsapp_business_messaging, whatsapp_business_management)")
    print("6) Paste into Infradealer /meta → Save Settings")
    print("=" * 60)
finally:
    db.close()
