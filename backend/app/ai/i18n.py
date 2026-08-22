"""Lightweight i18n helpers for AI Relationship Manager replies."""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_GREET = re.compile(r"^\s*(hi|hello|hey|namaste|namaskar|hola)\b", re.I)
_WEAK = re.compile(r"^\s*(ok+|okay|hmm+|han|haan|ji)\s*$", re.I)


def language_instruction(lang: str = "hinglish") -> str:
    if lang == "hindi":
        return "Reply in simple Hindi (Devanagari)."
    if lang == "english":
        return "Reply in clear simple English."
    return "Reply in Hinglish (Hindi + English, Latin script)."

TEMPLATES = {
    "hinglish": {
        "greet": "Namaste! Main InfraDealer Relationship Manager hoon. Aap gaadi/machine bechna chahte ho ya kharidna?",
        "intent": "Aap sell karna chahte ho ya buy?",
        "category": "Kaunsi category hai? (Truck / Tipper / JCB / Excavator / Crane…)",
        "brand": "Brand bata dijiye.",
        "model": "Model bata dijiye.",
        "year": "Model year kya hai?",
        "expected_price": "Expected price bata dijiye.",
        "budget": "Budget kitna hai?",
        "state": "Kaunse state/city se listing hai?",
        "location": "Location / city bata dijiye.",
        "km": "Kitne KM chali hai?",
        "hours": "Kitne operating hours hain?",
        "photos": "Ab clear photos bhej dijiye (kam se kam 2).",
        "photo_need_min": "Abhi {count} photo mili. Kam se kam 2 clear photos bhejiye.",
        "ack": "Theek hai — {facts}. ",
        "confirm_prompt": "Kya ye details sahi hain? Haan likhiye confirm karne ke liye.",
        "confirm_ok": "Confirm ho gaya.",
        "submitted": "Aapki listing submit ho gayi hai. Ab InfraDealer admin team review karegi.",
        "not_live": "Listing abhi live nahi hui — admin review pending hai.",
        "approved": "Aapki listing approve hokar live ho gayi hai.\nListing link: {link}",
        "rejected": "Aapki listing abhi approve nahi hui.\nReason: {reason}\nZaroori correction bhejiye, phir dobara submit karenge.",
        "conflict_year": "Aapne year {user} bataya, document mein {document} dikh raha hai. Listing mein kaunsa year rakhein?",
        "duplicate": "Isi vehicle ki similar listing pehle se mil sakti hai. Purani update karni hai ya nayi listing?",
        "paused": "Theek hai, abhi listing pause kar di. Jab ready ho bol dena — wahi se continue karenge.",
        "resume": "Bilkul. Aapki {vehicle} listing mein abhi {missing} baaki hai.",
        "handoff": "Main aapko human support se connect karata hoon. Thodi der mein team reply karegi.",
        "unclear": "Samajh nahi paya. Thoda clear bata dijiye.",
        "otp_ask": "OTP bhej diya hai. 6-digit OTP type kijiye.",
        "otp_ok": "OTP verify ho gaya.",
        "otp_fail": "OTP match nahi hua. Dobara try kijiye.",
        "no_invent": "Jo detail aapne di hai wahi use karunga — guess nahi karunga.",
        "status_ask": "Listing status: {status}",
        "link_missing": "Abhi listing link backend se confirm nahi hua.",
        "injection_refuse": "Main sirf InfraDealer listing/account help kar sakta hoon.",
        "saving": "Save kar raha hoon…",
        "card_clarify": "Kaunse card pe baat karein?\n{cards}\nJaise: CARD-001",
        "card_switched": "Theek hai — ab {card} active hai.",
        "intro": "Namaste! Main InfraDealer ka AI Relationship Manager hoon.",
        "photo_at_max": "Is card pe max photos lag chuki hain.",
        "photo_need_min": "Abhi {count} photo mili. Kam se kam 2 clear photos bhejiye.",
        "memory_reset_new_chat": "Aapke last message ke 10 minute baad naya chat start kar rahe hain — pehle wali baat clear ho gayi. Ab kya listing karna chahte ho?",
        "last_listing_loaded": "Aapki last listing ({card}) DB se load kar li. Bataiye kya change/update karna hai?",
        "last_listing_missing": "Is number pe koi purani listing nahi mili. Nayi listing start karein?",
        "card_cleanup_warn": "1 minute baad {card} wali chat clear ho jayegi. Agar continue karna hai to kuch bhi message bhej dijiye.",
        "card_conversation_deleted": "{card} wali chat clear ho gayi. Listing details save hain — update ke liye 'last listing change' likhiye.",
        "chat_cleared": "Chat clear ho gayi. Nayi listing shuru karein ya last listing update karein.",
        "chat_cleared_followup": "Ji — naya chat ready hai. Sell/buy bataiye, ya last listing update.",
    },
    "hindi": {
        "greet": "नमस्ते! मैं InfraDealer Relationship Manager हूँ। आप गाड़ी/मशीन बेचना चाहते हैं या खरीदना?",
        "intent": "आप बेचना चाहते हैं या खरीदना?",
        "category": "कौन सी कैटेगरी है? (ट्रक / टिपर / जेसीबी / एक्स्केवेटर…)",
        "brand": "ब्रांड बता दीजिए।",
        "model": "मॉडल बता दीजिए।",
        "year": "मॉडल ईयर क्या है?",
        "expected_price": "अपेक्षित कीमत बता दीजिए।",
        "budget": "बजट कितना है?",
        "state": "किस राज्य/शहर से लिस्टिंग है?",
        "location": "लोकेशन / शहर बता दीजिए।",
        "km": "कितने किलोमीटर चली है?",
        "hours": "कितने ऑपरेटिंग ऑवर्स हैं?",
        "photos": "अब साफ फोटो भेज दीजिए (कम से कम 2)।",
        "photo_need_min": "अभी {count} फोटो मिली। कम से कम 2 साफ फोटो भेजिए।",
        "ack": "ठीक है — {facts}। ",
        "confirm_prompt": "क्या ये डिटेल्स सही हैं? हाँ लिखकर कन्फर्म करें।",
        "confirm_ok": "कन्फर्म हो गया।",
        "submitted": "आपकी लिस्टिंग सबमिट हो गई है। अब InfraDealer एडमिन टीम रिव्यू करेगी।",
        "not_live": "लिस्टिंग अभी लाइव नहीं हुई — एडमिन रिव्यू पेंडिंग है।",
        "approved": "आपकी लिस्टिंग अप्रूव होकर लाइव हो गई है।\nलिस्टिंग लिंक: {link}",
        "rejected": "आपकी लिस्टिंग अभी अप्रूव नहीं हुई।\nकारण: {reason}\nजरूरी सुधार भेजें, फिर दोबारा सबमिट करेंगे।",
        "conflict_year": "आपने ईयर {user} बताया, दस्तावेज़ में {document} दिख रहा है। लिस्टिंग में कौन सा ईयर रखें?",
        "duplicate": "इसी वाहन की मिलती-जुलती लिस्टिंग पहले से हो सकती है। पुरानी अपडेट करनी है या नई?",
        "paused": "ठीक है, लिस्टिंग अभी पॉज़ कर दी। जब तैयार हों बता दें — वहीं से जारी रखेंगे।",
        "resume": "बिल्कुल। आपकी {vehicle} लिस्टिंग में अभी {missing} बाकी है।",
        "handoff": "मैं आपको मानव सपोर्ट से जोड़ रहा हूँ। थोड़ी देर में टीम जवाब देगी।",
        "unclear": "समझ नहीं पाया। थोड़ा साफ़ बताइए।",
        "otp_ask": "OTP भेज दिया है। 6 अंकों का OTP टाइप कीजिए।",
        "otp_ok": "OTP वेरिफाई हो गया।",
        "otp_fail": "OTP मैच नहीं हुआ। दोबारा कोशिश कीजिए।",
        "injection_refuse": "मैं केवल InfraDealer लिस्टिंग/अकाउंट में मदद कर सकता हूँ।",
        "saving": "सेव कर रहा हूँ…",
        "status_ask": "लिस्टिंग स्टेटस: {status}",
        "link_missing": "अभी लिस्टिंग लिंक बैकएंड से कन्फर्म नहीं हुआ।",
        "confirm_ok": "कन्फर्म हो गया।",
        "card_clarify": "किस कार्ड पर बात करें?\n{cards}\nजैसे: CARD-001",
        "card_switched": "ठीक है — अब {card} एक्टिव है।",
        "memory_reset_new_chat": "आपके पिछले मैसेज के 10 मिनट बाद नया चैट शुरू कर रहे हैं — पहले वाली बात क्लियर हो गई। अब क्या लिस्टिंग करना चाहते हैं?",
        "last_listing_loaded": "आपकी पिछली लिस्टिंग ({card}) लोड हो गई। क्या बदलना/अपडेट करना है?",
        "last_listing_missing": "इस नंबर पर कोई पुरानी लिस्टिंग नहीं मिली। नई लिस्टिंग शुरू करें?",
        "card_cleanup_warn": "1 मिनट बाद {card} वाली चैट क्लियर हो जाएगी। जारी रखना हो तो कोई भी मैसेज भेजें।",
        "card_conversation_deleted": "{card} वाली चैट क्लियर हो गई। अपडेट के लिए 'last listing change' लिखें।",
        "chat_cleared": "चैट क्लियर हो गई।",
        "chat_cleared_followup": "नया चैट तैयार है।",
    },
    "english": {
        "greet": "Hello! I'm the InfraDealer Relationship Manager. Do you want to sell or buy a vehicle/machine?",
        "intent": "Would you like to sell or buy?",
        "category": "Which category? (Truck / Tipper / JCB / Excavator / Crane…)",
        "brand": "Please share the brand.",
        "model": "Please share the model.",
        "year": "What is the model year?",
        "expected_price": "What is the expected price?",
        "budget": "What is your budget?",
        "state": "Which state/city is this listing from?",
        "location": "Please share the location/city.",
        "km": "How many KM has it run?",
        "hours": "How many operating hours?",
        "photos": "Please send clear photos (at least 2).",
        "photo_need_min": "Got {count} photo(s). Please send at least 2 clear photos.",
        "ack": "Got it — {facts}. ",
        "confirm_prompt": "Are these details correct? Reply Yes to confirm.",
        "confirm_ok": "Confirmed.",
        "submitted": "Your listing has been submitted. InfraDealer admin will review it.",
        "not_live": "Listing is not live yet — admin review is pending.",
        "approved": "Your listing is approved and live.\nListing link: {link}",
        "rejected": "Your listing was not approved.\nReason: {reason}\nSend the required correction and we will resubmit.",
        "conflict_year": "You said year {user}, but the document shows {document}. Which year should we keep?",
        "duplicate": "A similar listing for this vehicle may already exist. Update the old one or create new?",
        "paused": "Okay, pausing this listing for now. Tell me when to continue.",
        "resume": "Sure. Your {vehicle} listing still needs: {missing}.",
        "handoff": "Connecting you to human support. The team will reply shortly.",
        "unclear": "I didn't catch that. Please clarify.",
        "otp_ask": "OTP sent. Please type the 6-digit OTP.",
        "otp_ok": "OTP verified.",
        "otp_fail": "OTP did not match. Please try again.",
        "injection_refuse": "I can only help with InfraDealer listing/account support.",
        "saving": "Saving…",
        "status_ask": "Listing status: {status}",
        "link_missing": "Listing link is not confirmed by backend yet.",
        "card_clarify": "Which card should we continue on?\n{cards}\nExample: CARD-001",
        "card_switched": "Okay — {card} is now active.",
        "memory_reset_new_chat": "Starting a fresh chat — 10 minutes passed since your last message, so the previous topic was cleared. What would you like to list?",
        "last_listing_loaded": "Loaded your last listing ({card}). What should we change/update?",
        "last_listing_missing": "No previous listing found for this number. Start a new listing?",
        "card_cleanup_warn": "{card} chat will clear in 1 minute. Send any message to keep it.",
        "card_conversation_deleted": "{card} chat cleared. Listing data is saved — say 'update last listing' to edit.",
        "chat_cleared": "Chat cleared.",
        "chat_cleared_followup": "Fresh chat ready.",
    },
}


def normalize_policy(value: str | None) -> str:
    raw = (value or "auto").strip().lower()
    if raw in {"auto", "hinglish", "hindi", "english", "en", "hi"}:
        if raw == "en":
            return "english"
        if raw == "hi":
            return "hindi"
        return raw
    return "auto"


def pick_language(text: str = "", previous: str = "", policy: str = "auto") -> str:
    pol = normalize_policy(policy)
    if pol in {"hinglish", "hindi", "english"}:
        return pol
    if previous in {"hinglish", "hindi", "english"}:
        # Switch if user clearly changed script
        if text and _DEVANAGARI.search(text) and previous != "hindi":
            return "hindi"
        if text and re.search(r"\b(the|please|what|how|yes|no)\b", text, re.I) and not _DEVANAGARI.search(text):
            if previous == "hindi" and re.search(r"\b(the|please|what)\b", text, re.I):
                return "english"
        return previous
    if text and _DEVANAGARI.search(text):
        return "hindi"
    if text and re.search(r"\b(the|please|what|how|yes|want to)\b", text or "", re.I):
        return "english"
    return "hinglish"


def t(lang: str, key: str, **kwargs) -> str:
    bucket = TEMPLATES.get(lang) or TEMPLATES["hinglish"]
    raw = bucket.get(key) or TEMPLATES["hinglish"].get(key) or TEMPLATES["english"].get(key) or key
    try:
        return raw.format(**kwargs)
    except Exception:
        return raw
