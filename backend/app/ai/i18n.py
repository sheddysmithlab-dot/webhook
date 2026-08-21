"""WhatsApp AI reply languages. Detect from customer text; persist per chat."""

from __future__ import annotations

import re

REPLY_LANGS = ("hinglish", "hi", "en", "pa", "gu", "mr", "ta", "te", "kn", "ml", "bn", "ur")
POLICY_LANGS = ("auto",) + REPLY_LANGS

LANG_LABELS = {
    "auto": "Auto-detect (customer ki language)",
    "hinglish": "Hinglish",
    "hi": "Hindi",
    "en": "English",
    "pa": "Punjabi",
    "gu": "Gujarati",
    "mr": "Marathi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "bn": "Bengali",
    "ur": "Urdu",
}

NOT_UNDERSTOOD = {
    "hinglish": "Maaf kijiye Sir, mujhe yeh samajh nahi aaya.",
    "hi": "माफ़ कीजिए सर, मुझे यह समझ नहीं आया।",
    "en": "Sorry Sir/Ma’am, I didn’t quite get that.",
    "pa": "ਮਾਫ਼ ਕਰਿਓ, ਮੈਨੂੰ ਸਮਝ ਨਹੀਂ ਆਈ।",
    "gu": "માફ કરશો, મને સમજાયું નહીં.",
    "mr": "माफ करा, मला समजले नाही.",
    "ta": "மன்னிக்கவும், எனக்கு புரியவில்லை.",
    "te": "క్షమించండి, నాకు అర్థం కాలేదు.",
    "kn": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ.",
    "ml": "ക്ഷമിക്കണം, എനിക്ക് മനസ്സിലായില്ല.",
    "bn": "মাফ করবেন, আমি বুঝতে পারিনি।",
    "ur": "معاف کیجیے، مجھے سمجھ نہیں آیا۔",
}

_ROMAN_HI = re.compile(
    r"\b(hai|hain|ho|ji|kya|nahi|nahin|bechna|bechne|kharidna|gadi|gaadi|chahiye|"
    r"kitna|aap|mera|meri|yeh|acha|achha|theek|bhai|lakh|lac|baat|wale|wali|"
    r"namaste|dhanyavaad|kripya)\b",
    re.I,
)
_EN_HINT = re.compile(
    r"\b(want|looking|please|thanks|hello|hi|buy|sell|truck|machine|price|budget|need)\b",
    re.I,
)
_GREET = re.compile(
    r"^(hi+|hii+|hello|hey+|namaste|namaskar|ram\s*ram|thanks|thanx|ok+|haan+|ji+|"
    r"नमस्ते|नमस्कार|राम राम|शुक्रिया|धन्यवाद)\W*$",
    re.I,
)
_WEAK = re.compile(
    r"^(ok+|okay|haan+|han+|yes|no|ji+|h+m+|photos?|otp|\d{1,8}|\[(?:photo|video|media|document|voice).*\])$",
    re.I,
)
_MARATHI = ("आहे", "नाही", "मला", "तुम्ही", "पाहिजे", "विकाय", "घ्याय", "आहेत", "कृपया")
_HINDI = ("है", "नहीं", "मुझे", "आप", "चाहिए", "बेचना", "खरीदना", "जी", "कृपया")


def normalize_policy(value: str | None) -> str:
    raw = (value or "auto").strip().lower()
    return raw if raw in POLICY_LANGS else "auto"


def normalize_reply(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in REPLY_LANGS:
        return raw
    return "hinglish"


def _script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        key = ""
        if 0x0900 <= o <= 0x097F:
            key = "deva"
        elif 0x0A00 <= o <= 0x0A7F:
            key = "pa"
        elif 0x0A80 <= o <= 0x0AFF:
            key = "gu"
        elif 0x0B00 <= o <= 0x0B7F:
            key = "or"
        elif 0x0B80 <= o <= 0x0BFF:
            key = "ta"
        elif 0x0C00 <= o <= 0x0C7F:
            key = "te"
        elif 0x0C80 <= o <= 0x0CFF:
            key = "kn"
        elif 0x0D00 <= o <= 0x0D7F:
            key = "ml"
        elif 0x0980 <= o <= 0x09FF:
            key = "bn"
        elif 0x0600 <= o <= 0x06FF:
            key = "ur"
        elif ch.isascii() and ch.isalpha():
            key = "latn"
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def detect_language(text: str) -> str | None:
    msg = (text or "").strip()
    if not msg or _WEAK.match(msg):
        return None
    counts = _script_counts(msg)
    if not counts:
        return None
    script, n = max(counts.items(), key=lambda kv: kv[1])
    letters = sum(counts.values())
    if n < 3 and n < max(4, int(letters * 0.25)):
        if script != "latn":
            return None
    if script == "deva":
        mr = sum(msg.count(w) for w in _MARATHI)
        hi = sum(msg.count(w) for w in _HINDI)
        return "mr" if mr > hi else "hi"
    if script == "or":
        return "hi"
    if script != "latn":
        return script if script in REPLY_LANGS else None
    hi_hits = len(_ROMAN_HI.findall(msg))
    en_hits = len(_EN_HINT.findall(msg))
    if hi_hits >= 2 or (hi_hits and hi_hits >= en_hits):
        return "hinglish"
    if en_hits >= 2 and hi_hits == 0:
        return "en"
    return None


def is_strong_script(text: str) -> bool:
    counts = _script_counts(text or "")
    native = sum(v for k, v in counts.items() if k != "latn")
    return native >= 8


def pick_language(text: str, previous: str | None, policy: str) -> str:
    policy = normalize_policy(policy)
    if policy != "auto":
        return policy
    detected = detect_language(text)
    prev = normalize_reply(previous) if previous else ""
    if detected:
        if not prev or detected == prev or is_strong_script(text):
            return detected
        return prev
    return prev or "hinglish"


def t(lang: str, key: str, **kwargs) -> str:
    lang = normalize_reply(lang)
    if key == "not_understood":
        return NOT_UNDERSTOOD.get(lang) or NOT_UNDERSTOOD["hinglish"]
    pack = STRINGS.get(lang) or STRINGS["hinglish"]
    raw = pack.get(key) or STRINGS["hinglish"].get(key) or STRINGS["en"].get(key) or key
    if kwargs:
        try:
            return raw.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return raw
    return raw


def language_instruction(lang: str) -> str:
    lang = normalize_reply(lang)
    label = LANG_LABELS.get(lang, lang)
    return (
        f"REPLY LANGUAGE (mandatory): {label} ({lang}). "
        "You are InfraDealer’s AI executive. Tamiz: Sir/Ma’am, aap, ji. Short WhatsApp, not a call-centre speech. "
        f"First show you understood (repeat brand/model/year/state/price they just gave), then ask ONE missing MANDATORY thing. "
        "After all six mandatory fields, optional extras in ONE combined message once. "
        "No forms, no 5 questions, no ‘bhai’, no slang. "
        "MIXED LANGUAGE is normal: Hindi+English in one line like “मुझे tata की गाड़ी sell करना हे” means they want to SELL a Tata vehicle. "
        "Read Devanagari AND English AND Hinglish together. Typos: हे=है, गाड़ी=गाड़ी. "
        f"If you truly cannot understand the message, say exactly: {NOT_UNDERSTOOD.get(lang) or NOT_UNDERSTOOD['hinglish']} "
        "then ask ONE simple question. Do not bluff. "
        "Never claim listing is live. Never re-introduce yourself if CURRENT_STATE.data.ai_introduced is true."
    )


_HI = {
    "intro": "नमस्ते सर/मैम,\n\nमैं InfraDealer का AI executive हूँ. आपसे गाड़ी/मशीन की लिस्टिंग या खरीद-फरोख्त के बारे में बात होगी — used truck, tipper, JCB, ट्रैक्टर और heavy machine.\n\nपूरी बातचीत मैं ही करूँगा. लिस्टिंग तब live होगी जब हमारी टीम review करके post करे.\n\nकृपया बताइए — आप बेचना चाहते हैं या लेना?",
    "ack": "जी सर, {facts}. ",
    "intent": "सर, कृपया बताइए — गाड़ी/मशीन बेचनी है या लेनी है?",
    "brand": "सर, गाड़ी किस कंपनी की है — Tata, JCB, Eicher?",
    "model": "सर, इसका model name क्या है?",
    "year": "सर, manufacturing year कौन सा है?",
    "running": "सर, कितने किलोमीटर चल चुकी है? मशीन हो तो घंटे बता दीजिए.",
    "condition": "सर, कंडीशन कैसी है — ठीक चल रही है, या एक्सीडेंट/इंजन में काम?",
    "location": "सर, गाड़ी किस राज्य में खड़ी है?",
    "state": "सर, गाड़ी किस राज्य में खड़ी है — मध्य प्रदेश, महाराष्ट्र, राजस्थान?",
    "expected_price": "सर, इसकी कीमत / रेट क्या है?",
    "optional_bundle": "सर, ये optional है — जो पता हो एक साथ लिख दीजिए, नहीं पता तो skip लिख दीजिए:\n• कितने km / hours चली\n• कितने owner\n• finance कितना है\n• किस city में है\n• tyre कितने % हैं\n• finance की condition\n• कोई काम या गलती तो नहीं?",
    "account_ask": "सर, InfraDealer पर आपका अकाउंट बना हुआ है? हाँ या नहीं लिख दीजिए।\nनहीं है तो इसी चैट से OTP देकर अकाउंट बना देंगे।",
    "account_otp": "जी सर. OTP इस WhatsApp पर भेज रहा हूँ. 6 अंकों का कोड यहाँ लिख दीजिए — यहीं से अकाउंट बन जाएगा.",
    "account_already": "जी सर, आपका InfraDealer अकाउंट पहले से बना हुआ है — OTP की जरूरत नहीं है.",
    "account_password": "सर, अकाउंट के लिए password क्या रखना है? यहाँ लिख दीजिए (किसी को न बताएँ).",
    "account_role": "सर, आप broker हैं या user? कृपया लिखिए: Broker  या  User",
    "account_created": "जी सर, आपका अकाउंट बन गया है।\nआप हमारी वेबसाइट {site} पर जाकर अपना अकाउंट या लिस्टिंग देख सकते हैं, या वहीं से हमारा ऐप डाउनलोड कर सकते हैं।",
    "budget": "सर, कितने तक का बजट है?",
    "category": "सर, ये क्या है — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, या Other?",
    "customer_name": "सर, इस नंबर पर प्रोफाइल नहीं मिला. कृपया अपना नाम बता दीजिए, OTP आ जाएगा.",
    "photos": "जी सर. कृपया साफ फोटो भेज दीजिए — कम से कम 2, ज्यादा से ज्यादा 5 (आगे और दोनों साइड).",
    "more_detail": "सर, थोड़ा और बता दीजिए.",
    "photo_fail": "सर, फोटो नहीं आई. कृपया एक बार और भेज दीजिए.",
    "photo_more": "जी, आ गई. पीछे और केबिन की भी हो तो भेज दीजिए (कुल 2–5).",
    "photo_need_min": "जी सर, {count} फोटो मिल गई. लिस्टिंग के लिए कम से कम 2 साफ फोटो भेज दीजिए.",
    "photo_note": "फोटो मिल गई सर. और हों तो भेज दीजिए (max 5), नहीं तो टीम को दे देता हूँ.",
    "photos_enough": "जी सर. इतनी फोटो काफी हैं (2–5).",
    "photo_at_max": "जी सर, इस कार्ड पर पहले से 5 फोटो हैं. और फोटो स्वीकार नहीं हो सकती.",
    "card_switched": "जी सर — अब {card} पर काम कर रहा हूँ. इसी कार्ड की डिटेल बताइए.",
    "card_clarify": "सर, कई कार्ड खुली हैं ({cards}). किस कार्ड की बात है? जैसे CARD-001 लिख दीजिए.",
    "card_cleanup_notice": "नोट: {card} की यह चैट डिटेल 10 मिनट बाद क्लियर हो जाएगी. दूसरी कार्ड सुरक्षित रहेंगी.",
    "card_conversation_deleted": "सर, {card} की ऐप conversation delete कर दी गई है. अब नई listing के लिए फिर से डिटेल भेज सकते हैं.",
    "posted_card": "सर, {card} अप्रूव हो गई / InfraDealer पर लाइव है:\n\n{url}",
    "posted_card_nolink": "सर, {card} अप्रूव हो गई / InfraDealer पर लग गई है.",
    "rejected_card": "सर, {card} रिजेक्ट हो गई.",
    "rejected_card_reason": "सर, {card} रिजेक्ट हो गई.\n\n{reason}",
    "account_missing": "सर, पहले इसी WhatsApp से InfraDealer अकाउंट बना लीजिए (OTP). फिर कार्ड पोस्ट होगा.",
    "account_need_tokens": "सर, Token Based अकाउंट पर Verified Tokens चाहिए. खरीदें: {link}\nलॉगिन इसी WhatsApp OTP से होगा.",
    "account_broker_credit": "सर, Broker सब्सक्रिप्शन/क्रेडिट चाहिए. देखें: {link}\nलॉगिन इसी WhatsApp OTP से होगा.",
    "account_blocked": "सर, अभी इस अकाउंट से पोस्ट नहीं हो पा रहा. टोकन/ब्रोकर्स प्लान चेक कर लीजिए.",
    "sorry_repeat": "माफ़ी सर, वही सवाल दोबारा नहीं पूछूँगा.",
    "confirm_ask": "सर, ये डिटेल सही हैं?\nकृपया Haan या Yes लिख दीजिए. गलत हो तो बता दीजिए क्या बदलना है.",
    "confirm_heard": "जी सर, समझ गया. कार्ड में जो गलत है वो लिख दीजिए — जैसे location Madhya Pradesh — फिर Haan बोलिए.",
    "confirm_ready": "जी सर, ये details card में set हैं. सही हों तो Haan लिख दीजिए. गलत हो तो बता दीजिए क्या बदलना है.",
    "chat_cleared": "जी सर, previous conversation delete कर दी गई है. अब बताइए — गाड़ी बेचनी है या लेनी है?",
    "chat_cleared_followup": "जी सर, chat clear हो चुकी है. आप क्या चाहिए — listing बेचनी/लेनी, या कुछ और?",
    "casual_hi": "जी सर, मैं ठीक हूँ. आप बताइए — गाड़ी/मशीन बेचनी है या लेनी है?",
    "casual_hi_after_listing": "जी सर, मैं ठीक हूँ. आपकी listing पहले push हो चुकी है. Link चाहिए तो 'link do' लिखिए. नई listing के लिए बेचना/लेना बताइए.",
    "casual_while_confirm": "जी सर, मैं यहाँ हूँ. {card} confirm करना है तो Haan लिखिए. Chat delete चाहिए तो 'delete conversation' लिखिए, या बताइए क्या change करना है.",
    "listing_link_live": "सर, आपकी listing का direct link:\n\n{url}\n\nWhatsApp से open करें — InfraDealer पर खुल जाएगा.",
    "listing_link_pending": "सर, listing admin approval पर है. Preview/link:\n\n{url}\n\nApprove होते ही live हो जाएगी.",
    "listing_link_missing": "सर, अभी live link ready नहीं है. Listing admin check पर हो सकती है — approve होते ही link भेज दूँगा. 'last post' लिखकर status भी पूछ सकते हैं.",
    "listing_awaiting_approve": "सर, आपकी listing InfraDealer admin approval पर है. Approve होते ही direct live link WhatsApp पर आ जाएगी.",
    "listing_last_live": "सर, आपकी last listing: {label}\n\nDirect link:\n{url}",
    "listing_last_pending": "सर, आपकी last listing ({label}) admin approval / push पर है. Approve होते ही live link मिल जाएगी.",
    "listing_last_rejected": "सर, आपकी last listing ({label}) reject हो गई थी.\n\n{reason}",
    "listing_last_known": "सर, chat में last details: {label}. Live link अभी उपलब्ध नहीं है — admin approve के बाद मिलेगी.",
    "listing_none": "सर, अभी कोई recent listing नहीं मिली. नई listing डालनी है तो बेचना/लेना बता दीजिए.",
    "listing_delete_help": "सर, website listing WhatsApp से auto-delete नहीं होती. Listing: {label}\n\nLink:\n{url}\n\nDelete/remove के लिए InfraDealer admin/support से contact करें, या website पर login करके manage करें.",
    "listing_delete_no_link": "सर, website listing WhatsApp से auto-delete नहीं होती. InfraDealer admin/support से delete request करें. Chat clear के लिए 'delete conversation' लिखें.",
    "confirm_ok": "धन्यवाद सर. डिटेल लॉक हो गई. लिस्टिंग InfraDealer approval पर चली गई है — अप्रूव होने पर आपको लिंक मिल जाएगा.",
    "confirm_pushed": "धन्यवाद सर. WhatsApp से इकट्ठा की गई details InfraDealer card में भेज दी गई हैं — listing direct push हो रही है.",
    "confirm_pushed_live": "धन्यवाद सर. आपकी listing InfraDealer पर live हो गई है:\n\n{url}",
    "confirm_fix": "सर, क्या गलत है — category, vehicle, year, rate या state?",
    "vehicle_choice": "सर, अभी लिस्टिंग: {current}.\nअब जो भेजा: {incoming}.\nयह अलग गाड़ी है या इसी लिस्टिंग में अपडेट?\nकृपया लिखिए: Alag gadi  या  Update",
    "vehicle_new_ok": "जी सर — नई गाड़ी / नया Card. नई लिस्टिंग के लिए डिटेल ले रहा हूँ.",
    "vehicle_update_ok": "जी सर — इसी लिस्टिंग में अपडेट कर दिया.",
    "continue_chat": "जी सर, बोलिए. दूसरी गाड़ी है या इसी में कुछ बदलना है?",
    "continue_chat_2": "जी सर, मैं यहीं हूँ. कौनसी गाड़ी बेचनी है — ब्रांड और मॉडल बता दीजिए.",
    "posted": "सर, लिस्टिंग InfraDealer पर लग गई है. कुछ और हो तो बता दीजिए.",
    "posted_with_link": "सर, आपकी लिस्टिंग InfraDealer पर लाइव हो गई है:\n\n{url}",
    "rejected": "सर, लिस्टिंग अप्रूव नहीं हुई. टीम ने रिजेक्ट कर दी.",
    "rejected_with_reason": "सर, लिस्टिंग अप्रूव नहीं हुई.\n\n{reason}",
    "otp_mismatch": "सर, OTP मैच नहीं हुआ. कृपया 6 अंक वाला कोड फिर भेजिए.",
    "otp_ok_review": "नाम वेरिफाई हो गया सर. डिटेल टीम को दे दी — वो चेक करके लगाएंगे.",
    "otp_ok_pending": "वेरिफाई हो गया सर. दो-एक बात अभी बाकी है.",
    "otp_ask": "सर, WhatsApp पर जो 6 अंक का OTP आया है, वो यहाँ लिख दीजिए.",
    "extra_media": "एक्स्ट्रा फोटो नोट हो गई सर. टीम देख रही है — लाइव तब होगा जब एडमिन लगाए.",
    "received_review": "डिटेल मिल गई सर. टीम चेक करेगी, उसके बाद लिस्टिंग लगेगी.",
    "ignore_inject": "सर, मैं डिटेल लेकर टीम को देता हूँ. गाड़ी की बात बताते रहिए.",
    "intent_confirm": "सर, {label} — बेचनी है या लेनी है?",
    "tech_verify": "सर, थोड़ी टेक्निकल दिक्कत है. डिटेल सेव कर रहा हूँ, एक पल.",
    "otp_send_fail": "सर, OTP नहीं गया. डिटेल रखी हुई है — थोड़ी देर बाद ट्राई कीजिए.",
    "otp_sent": "धन्यवाद सर. OTP WhatsApp पर भेज रहा हूँ. 6 अंक का कोड यहाँ लिख दीजिए.",
    "profile_found_review": "प्रोफाइल मिल गया सर. गाड़ी की डिटेल टीम को भेज रहा हूँ.",
    "saving": "नोट कर लिया सर. जो बाकी हो वो बता दीजिए.",
    "save_pending": "सेव हो रहा है सर. जो छूट गया वो भेज दीजिए.",
    "listing_not_live": "डिटेल टीम को चेक के लिए चली गई हैं",
}

_EN = {
    "intro": "Namaste Sir/Ma’am,\n\nI am InfraDealer’s AI executive. I will speak with you about listing or buying a used vehicle/machine — truck, tipper, JCB, tractor and heavy equipment.\n\nI will handle this entire chat. The listing goes live only after our team reviews and posts it.\n\nMay I ask — would you like to sell or buy?",
    "ack": "Yes Sir, {facts}. ",
    "intent": "Sir, may I ask — selling a vehicle/machine, or buying?",
    "brand": "Sir, which company is the vehicle — Tata, JCB, Eicher?",
    "model": "Sir, what is the model name?",
    "year": "Sir, what is the manufacturing year?",
    "running": "Sir, how many kilometres has it done? Hours, if it is a machine.",
    "condition": "Sir, how is the condition — running well, or accident/engine work?",
    "location": "Sir, in which state is the vehicle standing?",
    "state": "Sir, in which state is the vehicle standing — Madhya Pradesh, Maharashtra, Rajasthan?",
    "expected_price": "Sir, what is the price / rate?",
    "optional_bundle": "Sir, these are optional — send together whatever you know, or type skip:\n• km / hours\n• owners\n• finance amount\n• city\n• tyre %\n• finance condition\n• any work or mistake?",
    "account_ask": "Sir, do you already have an InfraDealer account? Type Yes or No.\nIf not, we will send an OTP here and create it from this chat.",
    "account_otp": "Yes Sir. Sending OTP on this WhatsApp. Type the 6-digit code here to create the account.",
    "account_already": "Yes Sir, your InfraDealer account already exists — OTP is not needed.",
    "account_password": "Sir, what password should we set for your account? Type it here (do not share it).",
    "account_role": "Sir, are you a broker or a user? Kindly type: Broker  or  User",
    "account_created": "Sir, your account is created.\nYou can open {site} to see your account or listing, or download our app from there.",
    "budget": "Sir, what is the budget?",
    "category": "Sir, what is it — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, or Other?",
    "customer_name": "Sir, no profile on this number. Kindly share your name; I will send an OTP.",
    "photos": "Thank you Sir. Kindly send clear photos — min 2, max 5 (front and both sides).",
    "more_detail": "Sir, a little more detail please.",
    "photo_fail": "Sir, the photo did not come through. Please send it once more.",
    "photo_more": "Received, Sir. Rear and cabin too, if you have them (total 2–5).",
    "photo_need_min": "Sir, {count} photo(s) received. Please send at least 2 clear photos for the listing.",
    "photo_note": "Photos noted, Sir. More if you have them (max 5); else I will pass this to the team.",
    "photos_enough": "Thank you Sir. These photos are enough (2–5).",
    "photo_at_max": "Sir, this card already has 5 photos. More photos cannot be accepted.",
    "card_switched": "Yes Sir — working on {card} now. Please share details for this card only.",
    "card_clarify": "Sir, you have multiple open cards ({cards}). Which card? Reply like CARD-001.",
    "card_cleanup_notice": "Note: {card} chat details will clear in 10 minutes. Your other cards stay safe.",
    "card_conversation_deleted": "Sir, the app conversation for {card} has been deleted. You can send details again for a new listing.",
    "posted_card": "Sir, {card} is approved / live on InfraDealer:\n\n{url}",
    "posted_card_nolink": "Sir, {card} is approved / posted on InfraDealer.",
    "rejected_card": "Sir, {card} was rejected.",
    "rejected_card_reason": "Sir, {card} was rejected.\n\n{reason}",
    "account_missing": "Sir, please create an InfraDealer account on this WhatsApp (OTP) before posting.",
    "account_need_tokens": "Sir, Token account needs Verified Tokens. Buy here: {link}\nLogin with this WhatsApp OTP.",
    "account_broker_credit": "Sir, Broker subscription/credits are required. See: {link}\nLogin with this WhatsApp OTP.",
    "account_blocked": "Sir, this account cannot post right now. Please check tokens/broker plan.",
    "sorry_repeat": "Apologies Sir, I will not ask that again.",
    "confirm_ask": "Sir, are these details correct?\nKindly type Yes. If something is wrong, please tell me what to change.",
    "confirm_heard": "Got it Sir. Tell me what to correct on the card — for example location Madhya Pradesh — then write Yes.",
    "confirm_ready": "Yes Sir, these details are set on the card. If correct, please write Yes. If anything is wrong, tell me what to change.",
    "chat_cleared": "Yes Sir, previous conversation has been deleted. What do you need — sell or buy a vehicle?",
    "chat_cleared_followup": "Yes Sir, chat is clear. What do you need — sell/buy listing, or something else?",
    "casual_hi": "I am fine, Sir. Please tell me — selling a vehicle/machine, or buying?",
    "casual_hi_after_listing": "I am fine, Sir. Your listing was already pushed. Write 'link do' for the live link. For a new listing, say buy or sell.",
    "casual_while_confirm": "I am here, Sir. To confirm {card}, write Yes. To delete chat, write 'delete conversation', or tell me what to change.",
    "listing_link_live": "Sir, here is your listing direct link:\n\n{url}\n\nOpen from WhatsApp — it opens on InfraDealer.",
    "listing_link_pending": "Sir, listing is pending admin approval. Preview/link:\n\n{url}\n\nIt goes live after approve.",
    "listing_link_missing": "Sir, live link is not ready yet. Listing may be under admin review — I will send the link once approved. You can also write 'last post' for status.",
    "listing_awaiting_approve": "Sir, your listing is with InfraDealer admin for approval. You will get the direct live link on WhatsApp once approved.",
    "listing_last_live": "Sir, your last listing: {label}\n\nDirect link:\n{url}",
    "listing_last_pending": "Sir, your last listing ({label}) is pending admin approval / push. Live link comes after approve.",
    "listing_last_rejected": "Sir, your last listing ({label}) was rejected.\n\n{reason}",
    "listing_last_known": "Sir, last details in chat: {label}. Live link is not available yet — it comes after admin approve.",
    "listing_none": "Sir, no recent listing found. To start a new one, tell me buy or sell.",
    "listing_delete_help": "Sir, website listings cannot be auto-deleted from WhatsApp. Listing: {label}\n\nLink:\n{url}\n\nPlease contact InfraDealer admin/support to remove it, or manage it after login on the website.",
    "listing_delete_no_link": "Sir, website listings cannot be auto-deleted from WhatsApp. Please ask InfraDealer admin/support to delete. To clear this chat only, write 'delete conversation'.",
    "confirm_ok": "Thank you Sir. Details are locked. The listing has gone to InfraDealer approval — you will get the live link once it is approved.",
    "confirm_pushed": "Thank you Sir. WhatsApp details were sent to InfraDealer as a listing card — direct push is in progress.",
    "confirm_pushed_live": "Thank you Sir. Your listing is live on InfraDealer:\n\n{url}",
    "confirm_fix": "Sir, what should we correct — category, vehicle, year, rate or state?",
    "vehicle_choice": "Sir, current listing: {current}.\nWhat you just sent: {incoming}.\nIs this a different vehicle, or an update to the same listing?\nKindly type: Alag gadi  OR  Update",
    "vehicle_new_ok": "Yes Sir — a new vehicle. I will take details for a fresh listing.",
    "vehicle_update_ok": "Yes Sir — I have updated the same listing.",
    "continue_chat": "Yes Sir, please go ahead. Another vehicle, or a change to this one?",
    "continue_chat_2": "I am here, Sir. Which vehicle would you like to sell — brand and model, please?",
    "posted": "Sir, the listing is up on InfraDealer. Please tell me if anything else is needed.",
    "posted_with_link": "Sir, your listing is live on InfraDealer:\n\n{url}",
    "rejected": "Sir, the listing was not approved. The team has rejected it.",
    "rejected_with_reason": "Sir, the listing was not approved.\n\n{reason}",
    "otp_mismatch": "Sir, the OTP did not match. Please send the 6-digit code again.",
    "otp_ok_review": "Verified, Sir. Details are with the team for review.",
    "otp_ok_pending": "Verified, Sir. A couple of details are still needed.",
    "otp_ask": "Sir, kindly type the 6-digit OTP you received on WhatsApp.",
    "extra_media": "Extra photos noted, Sir. The team is reviewing — live only after admin posts.",
    "received_review": "Details received, Sir. The team will check, then it goes up.",
    "ignore_inject": "Sir, I take the details and send them to the team. Please continue about the vehicle.",
    "intent_confirm": "Sir, {label} — buying or selling?",
    "tech_verify": "Sir, a small technical delay. I have saved your details; one moment please.",
    "otp_send_fail": "Sir, the OTP could not be sent. Details are saved — please try again shortly.",
    "otp_sent": "Thank you Sir. Sending OTP on WhatsApp. Kindly type the 6-digit code here.",
    "profile_found_review": "Profile found, Sir. Sending the vehicle details to the team.",
    "saving": "Noted, Sir. Please send whatever is still missing.",
    "save_pending": "Saving, Sir. Please send the remaining detail.",
    "listing_not_live": "details have gone to the team for a check",
}

_HINGLISH = {
    "intro": "Namaste Sir/Ma'am,\n\nMain InfraDealer ka AI executive hoon. Aapse listing ya kharid-farokht ki baat hogi — used truck, tipper, JCB, tractor aur heavy machine.\n\nPoori baatcheet main hi karunga. Listing tab live hogi jab hamari team review karke post kare.\n\nKripya bataiye — aap gadi/machine bechna chahte hain ya lena?",
    "ack": "Ji Sir, {facts}. ",
    "intent": "Sir, kripya bataiye — gadi/machine bechni hai ya lene wali?",
    "brand": "Sir, gadi kis company ki hai — Tata, JCB, Eicher?",
    "model": "Sir, iska model name kya hai?",
    "year": "Sir, manufacturing year kaunsa hai?",
    "running": "Sir, kitne km chal chuki hai? Machine ho to hours bata dijiye.",
    "condition": "Sir, condition kaisi hai — theek chal rahi hai, ya accident/engine kaam?",
    "location": "Sir, gadi kis state me khadi hai?",
    "state": "Sir, gadi kis state me khadi hai — Madhya Pradesh, Maharashtra, Rajasthan?",
    "expected_price": "Sir, iski kimat / rate kya hai?",
    "optional_bundle": "Sir, ye optional hai — jo pata ho ek saath likh dijiye, nahi pata to skip likh dijiye:\n• kitne km / hours chali\n• kitne owner\n• finance kitna hai\n• kis city me hai\n• tyre kitne % hain\n• finance ki condition\n• koi kaam ya mistake to nahi?",
    "account_ask": "Sir, InfraDealer pe aapka account bana hua hai? Haan ya Nahi likh dijiye.\nNahi hai to isi chat se OTP dekar account bana denge.",
    "account_otp": "Ji Sir. OTP is WhatsApp pe bhej raha hoon. 6 digit code yahan likh dijiye — yahi se account ban jayega.",
    "account_already": "Ji Sir, aapka InfraDealer account pehle se bana hua hai — OTP ki zaroorat nahi hai.",
    "account_password": "Sir, account ke liye password kya rakhna hai? Yahan likh dijiye (kisi ko mat bataiye).",
    "account_role": "Sir, aap broker hain ya user? Kripya likhiye: Broker  ya  User",
    "account_created": "Ji Sir, aapka account create ho gaya hai.\nAap hamari website {site} par jaakar apna account ya listing dekh sakte hain, ya wahi se hamara app download kar sakte hain.",
    "budget": "Sir, kitne tak ka budget hai?",
    "category": "Sir, ye kya hai — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, ya Other?",
    "customer_name": "Sir, is number pe profile nahi mila. Kripya apna naam bata dijiye, OTP aa jayega.",
    "photos": "Ji Sir. Kripya saaf photos bhej dijiye — kam se kam 2, zyada se zyada 5 (aage aur dono side).",
    "more_detail": "Sir, thoda aur bata dijiye.",
    "photo_fail": "Sir, photo nahi aayi. Kripya ek baar aur bhej dijiye.",
    "photo_more": "Ji, mil gayi. Piche aur cabin ki bhi ho to bhej dijiye (kul 2–5).",
    "photo_need_min": "Ji Sir, {count} photo mil gayi. Listing ke liye kam se kam 2 saaf photos bhej dijiye.",
    "photo_note": "Photos mil gayi Sir. Aur hon to bhej dijiye (max 5), nahi to team ko de deta hoon.",
    "photos_enough": "Ji Sir. Itni photos kaafi hain (2–5).",
    "photo_at_max": "Ji Sir, is card pe pehle se 5 photos hain. Aur photos accept nahi ho sakti.",
    "card_switched": "Ji Sir — ab {card} par kaam kar raha hoon. Isi card ki detail bataiye.",
    "card_clarify": "Sir, kai cards khuli hain ({cards}). Kaunsi card? Jaise CARD-001 likh dijiye.",
    "card_cleanup_notice": "Note: {card} ki ye chat detail 10 minute baad clear ho jayegi. Doosri cards safe rahengi.",
    "card_conversation_deleted": "Sir, {card} ki app conversation delete kar di gayi hai. Ab nayi listing ke liye phir se detail bhej sakte hain.",
    "posted_card": "Sir, {card} approve / InfraDealer par live ho gayi hai:\n\n{url}",
    "posted_card_nolink": "Sir, {card} approve / InfraDealer par lag gayi hai.",
    "rejected_card": "Sir, {card} reject ho gayi.",
    "rejected_card_reason": "Sir, {card} reject ho gayi.\n\n{reason}",
    "account_missing": "Sir, pehle isi WhatsApp se InfraDealer account bana lijiye (OTP). Phir card post hoga.",
    "account_need_tokens": "Sir, Token Based account par Verified Tokens chahiye. Khariden: {link}\nLogin isi WhatsApp OTP se hoga.",
    "account_broker_credit": "Sir, Broker subscription/credit chahiye. Dekhen: {link}\nLogin isi WhatsApp OTP se hoga.",
    "account_blocked": "Sir, abhi is account se post nahi ho pa raha. Token/broker plan check kar lijiye.",
    "sorry_repeat": "Maafi Sir, wahi sawal dubara nahi poochunga.",
    "confirm_ask": "Sir, ye details sahi hain?\nKripya Haan ya Yes likh dijiye. Galat ho to bata dijiye kya change karna hai.",
    "confirm_heard": "Ji Sir, samajh gaya. Card me jo galat hai wo likh dijiye — jaise location Madhya Pradesh — phir Haan boliye.",
    "confirm_ready": "Ji Sir, ye details card me set hain. Sahi hon to Haan likh dijiye. Galat ho to bata dijiye kya badalna hai.",
    "chat_cleared": "Ji Sir, previous conversation delete kar di gayi hai. Ab bataiye — gadi bechni hai ya leni hai?",
    "chat_cleared_followup": "Ji Sir, chat clear ho chuki hai. Aap kya chahiye — listing bechni/leni, ya kuch aur?",
    "casual_hi": "Ji Sir, main theek hoon. Aap bataiye — gadi/machine bechni hai ya leni hai?",
    "casual_hi_after_listing": "Ji Sir, main theek hoon. Aapki listing pehle push ho chuki hai. Link chahiye to 'link do' likhiye. Nayi listing ke liye bechna/lena bataiye.",
    "casual_while_confirm": "Ji Sir, main yahin hoon. {card} confirm karna hai to Haan likhiye. Chat delete chahiye to 'delete conversation' likhiye, ya bataiye kya change karna hai.",
    "listing_link_live": "Sir, aapki listing ka direct link:\n\n{url}\n\nWhatsApp se open karein — InfraDealer par khul jayega.",
    "listing_link_pending": "Sir, listing admin approval par hai. Preview/link:\n\n{url}\n\nApprove hote hi live ho jayegi.",
    "listing_link_missing": "Sir, abhi live link ready nahi hai. Listing admin check par ho sakti hai — approve hote hi link bhej dunga. 'last post' likhkar status bhi pooch sakte hain.",
    "listing_awaiting_approve": "Sir, aapki listing InfraDealer admin approval par hai. Approve hote hi direct live link WhatsApp par aa jayegi.",
    "listing_last_live": "Sir, aapki last listing: {label}\n\nDirect link:\n{url}",
    "listing_last_pending": "Sir, aapki last listing ({label}) admin approval / push par hai. Approve hote hi live link mil jayegi.",
    "listing_last_rejected": "Sir, aapki last listing ({label}) reject ho gayi thi.\n\n{reason}",
    "listing_last_known": "Sir, chat me last details: {label}. Live link abhi available nahi — admin approve ke baad milegi.",
    "listing_none": "Sir, abhi koi recent listing nahi mili. Nayi listing dalni hai to bechna/lena bata dijiye.",
    "listing_delete_help": "Sir, website listing WhatsApp se auto-delete nahi hoti. Listing: {label}\n\nLink:\n{url}\n\nDelete/remove ke liye InfraDealer admin/support se contact karein, ya website par login karke manage karein.",
    "listing_delete_no_link": "Sir, website listing WhatsApp se auto-delete nahi hoti. InfraDealer admin/support se delete request karein. Sirf chat clear ke liye 'delete conversation' likhein.",
    "confirm_ok": "Dhanyavaad Sir. Details lock ho gayi. Listing InfraDealer approval pe chali gayi hai — approve hone par aapko live link mil jayega.",
    "confirm_pushed": "Dhanyavaad Sir. WhatsApp se collect details InfraDealer card me bhej di — listing direct push ho rahi hai.",
    "confirm_pushed_live": "Dhanyavaad Sir. Aapki listing InfraDealer par live ho gayi hai:\n\n{url}",
    "confirm_fix": "Sir, kya galat hai — category, vehicle, year, rate ya state?",
    "vehicle_choice": "Sir, abhi listing: {current}.\nAb jo bheja: {incoming}.\nKya ye alag gadi hai ya isi listing me update?\nKripya likhiye: Alag gadi  ya  Update",
    "vehicle_new_ok": "Ji Sir — nayi gadi. Nayi listing ke liye details le raha hoon.",
    "vehicle_update_ok": "Ji Sir — isi listing me update kar diya.",
    "continue_chat": "Ji Sir, boliye. Dusri gadi hai ya isi me kuch change karna hai?",
    "continue_chat_2": "Ji Sir, main yahin hoon. Kaunsi gadi bechni hai — brand aur model bata dijiye.",
    "posted": "Sir, listing InfraDealer pe lag gayi hai. Kuch aur ho to bata dijiye.",
    "posted_with_link": "Sir, aapki listing InfraDealer par live ho gayi hai:\n\n{url}",
    "rejected": "Sir, listing approve nahi hui. Team ne reject kar di.",
    "rejected_with_reason": "Sir, listing approve nahi hui.\n\n{reason}",
    "otp_mismatch": "Sir, OTP match nahi hua. Kripya 6 digit code dubara bhejiye.",
    "otp_ok_review": "Verify ho gaya Sir. Details team ko de di — wo check karke lagayenge.",
    "otp_ok_pending": "Verify ho gaya Sir. Do-ek baat abhi baaki hai.",
    "otp_ask": "Sir, WhatsApp pe jo 6 digit OTP aaya hai, yahan likh dijiye.",
    "extra_media": "Extra photos note ho gayi Sir. Team dekh rahi hai — live tab hoga jab admin lagaye.",
    "received_review": "Details mil gayi Sir. Team check karegi, uske baad listing lagegi.",
    "ignore_inject": "Sir, main details leke team ko deta hoon. Kripya gadi ki baat batate rahiye.",
    "intent_confirm": "Sir, {label} — bechni hai ya lene?",
    "tech_verify": "Sir, thodi technical dikkat hai. Details save kar raha hoon, ek pal.",
    "otp_send_fail": "Sir, OTP nahi gaya. Details rakhi hui hain — thodi der baad try kijiye.",
    "otp_sent": "Dhanyavaad Sir. OTP WhatsApp pe bhej raha hoon. 6 digit code yahan likh dijiye.",
    "profile_found_review": "Profile mil gaya Sir. Gadi ki details team ko bhej raha hoon.",
    "saving": "Note kar liya Sir. Jo baaki ho wo bata dijiye.",
    "save_pending": "Save ho raha hai Sir. Jo chhoot gaya wo bhej dijiye.",
    "listing_not_live": "details team ko check ke liye chali gayi hain",
}

_PA = {
    "intent": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਜੀ। InfraDealer ਤੇ ਤੁਸੀਂ ਗੱਡੀ/ਮਸ਼ੀਨ ਵੇਚਣੀ ਹੈ ਜਾਂ ਖਰੀਦਣੀ?",
    "brand": "ਜੀ, ਬ੍ਰਾਂਡ ਤੇ ਮਾਡਲ ਦੱਸ ਦਿਓ।",
    "model": "ਜੀ, ਮਾਡਲ ਨਾਂ / ਵੇਰੀਐਂਟ ਕੀ ਹੈ?",
    "year": "ਜੀ, ਇਹ ਕਿਹੜੇ ਸਾਲ ਦੀ ਹੈ?",
    "running": "ਗੱਡੀ ਲਗਭਗ ਕਿੰਨੇ ਕਿਲੋਮੀਟਰ ਚੱਲੀ ਹੈ? ਮਸ਼ੀਨ ਹੋਵੇ ਤਾਂ ਘੰਟੇ ਦੱਸੋ।",
    "condition": "ਹਾਲਤ ਕਿਵੇਂ ਹੈ — ਚੰਗੀ ਹੈ, ਜਾਂ ਕੋਈ ਵੱਡਾ ਐਕਸੀਡੈਂਟ / ਮਸ਼ੀਨੀ ਮਸਲਾ?",
    "location": "ਹੁਣ ਕਿਹੜੀ ਥਾਂ ਤੇ ਹੈ?",
    "expected_price": "ਕਿੰਨੀ ਕੀਮਤ ਰੱਖਣੀ ਹੈ?",
    "budget": "ਅੰਦਾਜ਼ਨ ਬਜਟ ਕਿੰਨਾ ਹੈ?",
    "category": "ਜੀ, ਇਹ ਕੀ ਹੈ — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, ਜਾਂ Other?",
    "customer_name": "InfraDealer ਤੇ ਤੁਹਾਡਾ ਪ੍ਰੋਫਾਈਲ ਨਹੀਂ ਮਿਲਿਆ। ਬਣਾਉਣ ਲਈ ਆਪਣਾ ਨਾਂ ਦੱਸੋ ਜੀ।",
    "photos": "ਬਹੁਤ ਵਧੀਆ। ਲਿਸਟਿੰਗ ਲਈ ਸਾਫ਼ ਫੋਟੋ ਭੇਜੋ। ਅੱਗੇ ਤੇ ਦੋਵੇਂ ਪਾਸਿਆਂ ਤੋਂ ਸ਼ੁਰੂ ਕਰ ਸਕਦੇ ਹੋ।",
    "more_detail": "ਜੀ, ਥੋੜਾ ਹੋਰ ਵੇਰਵਾ ਭੇਜੋ।",
    "photo_fail": "ਫੋਟੋ ਡਾਊਨਲੋਡ ਨਹੀਂ ਹੋਈ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਭੇਜੋ।",
    "photo_more": "ਫੋਟੋ ਮਿਲ ਗਈਆਂ। ਹੋ ਸਕੇ ਤਾਂ ਪਿੱਛੇ ਤੇ ਕੈਬਿਨ ਦੀ ਇੱਕ-ਇੱਕ ਸਾਫ਼ ਫੋਟੋ ਵੀ ਭੇਜੋ।",
    "photo_note": "ਫੋਟੋ ਨੋਟ ਹੋ ਗਈਆਂ। ਹੋਰ ਹੋਣ ਤਾਂ ਭੇਜੋ, ਨਹੀਂ ਤਾਂ ਟੀਮ ਨੂੰ ਰਿਵਿਊ ਲਈ ਭੇਜ ਦਿੰਦਾ ਹਾਂ।",
    "posted": "ਤੁਹਾਡੀ ਲਿਸਟਿੰਗ InfraDealer ਤੇ ਪੋਸਟ ਹੋ ਗਈ ਹੈ। ਅਪਡੇਟ ਲਈ ਟੀਮ ਨਾਲ ਸੰਪਰਕ ਕਰੋ।",
    "otp_mismatch": "OTP ਮੇਲ ਨਹੀਂ ਖਾਧਾ। 6 ਅੰਕ ਦਾ ਕੋਡ ਦੁਬਾਰਾ ਭੇਜੋ।",
    "otp_ok_review": "ਪ੍ਰੋਫਾਈਲ ਵੈਰੀਫਾਈ ਹੋ ਗਿਆ। ਡਿਟੇਲ ਟੀਮ ਨੂੰ ਰਿਵਿਊ ਲਈ ਭੇਜ ਦਿੱਤੀਆਂ ਗਈਆਂ ਹਨ।",
    "otp_ok_pending": "ਪ੍ਰੋਫਾਈਲ ਵੈਰੀਫਾਈ ਹੋ ਗਿਆ। ਕੁਝ ਡਿਟੇਲ ਅਜੇ ਬਾਕੀ ਹਨ।",
    "otp_ask": "WhatsApp ਤੇ ਆਇਆ 6 ਅੰਕ ਦਾ OTP ਇੱਥੇ ਟਾਈਪ ਕਰਕੇ ਭੇਜੋ।",
    "extra_media": "ਵਾਧੂ ਫੋਟੋ/ਡਿਟੇਲ ਨੋਟ ਹੋ ਗਏ। ਲਿਸਟਿੰਗ ਐਡਮਿਨ ਪੋਸਟ ਕਰੇ ਤਾਂ ਲਾਈਵ ਹੋਵੇਗੀ।",
    "received_review": "ਤੁਹਾਡੀਆਂ ਡਿਟੇਲ ਮਿਲ ਗਈਆਂ ਹਨ। ਟੀਮ ਨੂੰ ਵੈਰੀਫਿਕੇਸ਼ਨ ਲਈ ਭੇਜ ਦਿੱਤੀਆਂ ਹਨ।",
    "ignore_inject": "ਮੈਂ ਲਿਸਟਿੰਗ ਕਲੈਕਟ ਕਰਕੇ ਟੀਮ ਨੂੰ ਰਿਵਿਊ ਲਈ ਭੇਜਦਾ ਹਾਂ। ਗੱਡੀ ਦੀਆਂ ਡਿਟੇਲ ਭੇਜਦੇ ਰਹੋ।",
    "intent_confirm": "ਜੀ, {label} ਬਾਰੇ ਤੁਸੀਂ ਖਰੀਦਣਾ ਚਾਹੁੰਦੇ ਹੋ ਜਾਂ ਵੇਚਣਾ?",
    "tech_verify": "ਵੈਰੀਫਿਕੇਸ਼ਨ ਵਿੱਚ ਤਕਨੀਕੀ ਸਮੱਸਿਆ ਹੈ। ਡਿਟੇਲ ਸੇਵ ਕਰ ਰਿਹਾ ਹਾਂ, ਥੋੜ੍ਹੀ ਦੇਰ ਬਾਅਦ ਜਾਰੀ ਕਰਦੇ ਹਾਂ।",
    "otp_send_fail": "OTP ਭੇਜਣ ਵਿੱਚ ਸਮੱਸਿਆ ਹੈ। ਡਿਟੇਲ ਸੇਵ ਰੱਖਾਂਗਾ, ਥੋੜ੍ਹੀ ਦੇਰ ਬਾਅਦ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
    "otp_sent": "ਧੰਨਵਾਦ ਜੀ। ਤੁਹਾਡੇ WhatsApp ਤੇ OTP ਭੇਜ ਰਿਹਾ ਹਾਂ। 6 ਅੰਕ ਦਾ ਕੋਡ ਇੱਥੇ ਭੇਜੋ।",
    "profile_found_review": "ਤੁਹਾਡਾ InfraDealer ਪ੍ਰੋਫਾਈਲ ਮਿਲ ਗਿਆ। ਗੱਡੀ ਦੀਆਂ ਡਿਟੇਲ ਟੀਮ ਨੂੰ ਭੇਜੀਆਂ ਜਾ ਰਹੀਆਂ ਹਨ।",
    "saving": "ਡਿਟੇਲ ਸੇਵ ਕਰ ਰਿਹਾ ਹਾਂ। ਜੋ ਬਾਕੀ ਹੋਵੇ ਉਹ ਭੇਜ ਦਿਓ।",
    "save_pending": "ਡਿਟੇਲ ਸੇਵ ਹੋ ਰਹੀਆਂ ਹਨ। ਪੈਂਡਿੰਗ ਫੀਲਡ ਭੇਜ ਦਿਓ।",
    "listing_not_live": "ਡਿਟੇਲ ਟੀਮ ਨੂੰ ਵੈਰੀਫਿਕੇਸ਼ਨ ਲਈ ਭੇਜ ਦਿੱਤੀਆਂ ਗਈਆਂ ਹਨ",
}

_GU = {
    "intent": "નમસ્તે. InfraDealer પર તમે વાહન/મશીન વેચવા માંગો છો કે ખરીદવા?",
    "brand": "જી, બ્રાન્ડ અને મોડેલ જણાવો.",
    "model": "મોડેલ નામ / વેરિયન્ટ શું છે?",
    "year": "આ કયા વર્ષની છે?",
    "running": "ગાડી લગભગ કેટલા કિલોમીટર ચાલી છે? મશીન હોય તો કલાક કહો.",
    "condition": "કન્ડિશન કેવી છે — સારી, કે કોઈ મોટો એક્સિડન્ટ / મશીનની સમસ્યા?",
    "location": "હાલ કઈ જગ્યાએ છે?",
    "expected_price": "કેટલી કિંમત રાખવી છે?",
    "budget": "અંદાજિત બજેટ કેટલું છે?",
    "category": "સર, આ શું છે — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, કે Other?",
    "customer_name": "InfraDealer પર પ્રોફાઇલ મળ્યું નથી. બનાવવા માટે તમારું નામ કહો.",
    "photos": "સરસ. લિસ્ટિંગ માટે સ્પષ્ટ ફોટા મોકલો. આગળ અને બંને બાજુથી શરૂ કરી શકો.",
    "more_detail": "થોડી વધુ વિગત મોકલો.",
    "photo_fail": "ફોટો ડાઉનલોડ ન થયો. કૃપા કરીને ફરી મોકલો.",
    "photo_more": "ફોટા મળ્યા. શક્ય હોય તો પાછળ અને કેબિનની એક-એક સ્પષ્ટ ફોટો મોકલો.",
    "photo_note": "ફોટા નોંધાયા. વધુ હોય તો મોકલો, નહીં તો ટીમને રિવ્યૂ માટે મોકલું.",
    "posted": "તમારી લિસ્ટિંગ InfraDealer પર પોસ્ટ થઈ ગઈ છે.",
    "otp_mismatch": "OTP મેળ ખાતો નથી. 6 અંકનો કોડ ફરી મોકલો.",
    "otp_ok_review": "પ્રોફાઇલ વેરિફાય થયું. વિગતો ટીમને રિવ્યૂ માટે મોકલી છે.",
    "otp_ok_pending": "પ્રોફાઇલ વેરિફાય થયું. થોડી વિગતો હજુ બાકી છે.",
    "otp_ask": "WhatsApp પર આવેલો 6 અંકનો OTP અહીં લખીને મોકલો.",
    "extra_media": "વધારાના ફોટા નોંધ્યા. લિસ્ટિંગ એડમિન પોસ્ટ કરે ત્યારે લાઇવ થશે.",
    "received_review": "વિગતો મળી ગઈ છે. ટીમને વેરિફિકેશન માટે મોકલી છે.",
    "ignore_inject": "હું લિસ્ટિંગ એકત્ર કરીને ટીમને રિવ્યૂ માટે મોકલું છું. વાહનની વિગતો મોકલતા રહો.",
    "intent_confirm": "{label} વિશે તમે ખરીદવા માંગો છો કે વેચવા?",
    "tech_verify": "વેરિફિકેશનમાં ટેકનિકલ સમસ્યા છે. વિગતો સેવ કરું છું, થોડી વાર પછી ચાલુ કરીએ.",
    "otp_send_fail": "OTP મોકલવામાં સમસ્યા છે. વિગતો સેવ રાખીશ, થોડી વાર પછી પ્રયાસ કરો.",
    "otp_sent": "આભાર. તમારા WhatsApp પર OTP મોકલું છું. 6 અંકનો કોડ અહીં મોકલો.",
    "profile_found_review": "તમારું InfraDealer પ્રોફાઇલ મળ્યું. વાહનની વિગતો ટીમને મોકલાઈ રહી છે.",
    "saving": "વિગતો સેવ કરું છું. જે બાકી હોય તે મોકલો.",
    "save_pending": "વિગતો સેવ થઈ રહી છે. પેન્ડિંગ ફીલ્ડ મોકલો.",
    "listing_not_live": "વિગતો ટીમને વેરિફિકેશન માટે મોકલી છે",
}

_MR = {
    "intent": "नमस्कार. InfraDealer वर तुम्ही वाहन/मशीन विकायची आहे की खरेदी करायची?",
    "brand": "जी, ब्रँड आणि मॉडेल सांगा.",
    "model": "मॉडेल नाव / व्हेरिएंट काय आहे?",
    "year": "ही कोणत्या वर्षाची आहे?",
    "running": "गाडी सुमारे किती किलोमीटर चालली आहे? मशीन असेल तर तास सांगा.",
    "condition": "कंडीशन कशी आहे — चांगली, की मोठा अपघात / मशीनची अडचण?",
    "location": "आता कुठल्या ठिकाणी आहे?",
    "expected_price": "किती किंमत ठेवायची आहे?",
    "budget": "अंदाजे बजेट किती आहे?",
    "category": "सर, हे काय आहे — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, किंवा Other?",
    "customer_name": "InfraDealer वर प्रोफाइल सापडले नाही. तयार करण्यासाठी नाव सांगा.",
    "photos": "छान. लिस्टिंगसाठी स्पष्ट फोटो पाठवा. पुढून आणि दोन्ही बाजूंनी सुरू करू शकता.",
    "more_detail": "थोडी अधिक माहिती पाठवा.",
    "photo_fail": "फोटो डाउनलोड झाला नाही. कृपया पुन्हा पाठवा.",
    "photo_more": "फोटो मिळाले. शक्य असल्यास मागचा आणि कॅबिनचा एक-एक स्पष्ट फोटो पाठवा.",
    "photo_note": "फोटो नोंदले. आणखी असतील तर पाठवा, नाहीतर टीमला रिव्ह्यूसाठी पाठवतो.",
    "posted": "तुमची लिस्टिंग InfraDealer वर पोस्ट झाली आहे.",
    "otp_mismatch": "OTP जुळला नाही. 6 अंकी कोड पुन्हा पाठवा.",
    "otp_ok_review": "प्रोफाइल व्हेरिफाय झाले. डिटेल्स टीमला रिव्ह्यूसाठी पाठवल्या आहेत.",
    "otp_ok_pending": "प्रोफाइल व्हेरिफाय झाले. काही डिटेल्स अजून बाकी आहेत.",
    "otp_ask": "WhatsApp वर आलेला 6 अंकी OTP इथे टाइप करून पाठवा.",
    "extra_media": "अतिरिक्त फोटो नोंदले. लिस्टिंग अ‍ॅडमिन पोस्ट करेल तेव्हा लाइव्ह होईल.",
    "received_review": "डिटेल्स मिळाल्या आहेत. टीमला व्हेरिफिकेशनसाठी पाठवल्या आहेत.",
    "ignore_inject": "मी लिस्टिंग गोळा करून टीमला रिव्ह्यूसाठी पाठवतो. वाहनाची माहिती पाठवत रहा.",
    "intent_confirm": "{label} बद्दल तुम्ही खरेदी करायची आहे की विकायची?",
    "tech_verify": "व्हेरिफिकेशनमध्ये तांत्रिक अडचण आहे. डिटेल्स सेव्ह करतो, थोड्या वेळाने पुढे जाऊ.",
    "otp_send_fail": "OTP पाठवण्यात अडचण आहे. डिटेल्स सेव्ह ठेवेन, थोड्या वेळाने प्रयत्न करा.",
    "otp_sent": "धन्यवाद. तुमच्या WhatsApp वर OTP पाठवत आहे. 6 अंकी कोड इथे पाठवा.",
    "profile_found_review": "तुमचे InfraDealer प्रोफाइल सापडले. वाहनाची माहिती टीमला पाठवली जात आहे.",
    "saving": "डिटेल्स सेव्ह करत आहे. जे बाकी असेल ते पाठवा.",
    "save_pending": "डिटेल्स सेव्ह होत आहेत. पेंडिंग फील्ड पाठवा.",
    "listing_not_live": "डिटेल्स टीमला व्हेरिफिकेशनसाठी पाठवल्या आहेत",
}

_TA = {
    "intent": "வணக்கம். InfraDealer-ல் வாகனம்/மெஷீனை விற்க வேண்டுமா வாங்க வேண்டுமா?",
    "brand": "பிராண்டு மற்றும் மாடலை சொல்லுங்கள்.",
    "model": "மாடல் பெயர் / வேரியன்ட் என்ன?",
    "year": "இது எந்த வருஷம்?",
    "running": "வாகனம் ஏறக்குறைய எத்தனை கிலோமீட்டர் ஓடியது? மெஷீன் என்றால் மணிநேரம் சொல்லுங்கள்.",
    "condition": "கண்டிஷன் எப்படி — நல்லதா, பெரிய ஆக்சிடென்ட் / மெக்கானிக்கல் பிரச்சனை உள்ளதா?",
    "location": "இப்போது எந்த இடத்தில் உள்ளது?",
    "expected_price": "எந்த விலை வைக்க நினைக்கிறீர்கள்?",
    "budget": "தோராய பட்ஜெட் எவ்வளவு?",
    "category": "சர், இது என்ன — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, அல்லது Other?",
    "customer_name": "InfraDealer ப்ரொஃபைல் கிடைக்கவில்லை. உருவாக்க உங்கள் பெயரை சொல்லுங்கள்.",
    "photos": "சரி. லிஸ்டிங்கிற்கு தெளிவான புகைப்படம் அனுப்புங்கள். முன் மற்றும் இரு பக்கங்களிலிருந்து தொடங்கலாம்.",
    "more_detail": "இன்னும் கொஞ்சம் விவரம் அனுப்புங்கள்.",
    "photo_fail": "புகைப்படம் டவுன்லோட் ஆகவில்லை. மீண்டும் அனுப்புங்கள்.",
    "photo_more": "புகைப்படம் கிடைத்தது. முடிந்தால் பின்புறம் மற்றும் கேபின் புகைப்படமும் அனுப்புங்கள்.",
    "photo_note": "புகைப்படம் பதிவானது. மேலும் இருந்தால் அனுப்புங்கள், இல்லையெனில் டீமுக்கு ரிவியூக்கு அனுப்புகிறேன்.",
    "posted": "உங்கள் லிஸ்டிங் InfraDealer-ல் போஸ்ட் ஆகிவிட்டது.",
    "otp_mismatch": "OTP பொருந்தவில்லை. 6 இலக்க கோடை மீண்டும் அனுப்புங்கள்.",
    "otp_ok_review": "ப்ரொஃபைல் வெரிஃபை ஆனது. விவரங்கள் டீமுக்கு அனுப்பப்பட்டன.",
    "otp_ok_pending": "ப்ரொஃபைல் வெரிஃபை ஆனது. சில விவரங்கள் இன்னும் நிலுவையில்.",
    "otp_ask": "WhatsApp-ல் வந்த 6 இலக்க OTP-ஐ இங்கே டைப் செய்து அனுப்புங்கள்.",
    "extra_media": "கூடுதல் புகைப்படம் பதிவானது. அட்மின் போஸ்ட் செய்த பிறகுதான் லிஸ்டிங் லைவ் ஆகும்.",
    "received_review": "விவரங்கள் கிடைத்தன. வெரிஃபிகேஷனுக்காக டீமுக்கு அனுப்பப்பட்டன.",
    "ignore_inject": "நான் லிஸ்டிங் விவரங்களை சேகரித்து டீமுக்கு அனுப்புவேன். வாகன விவரங்களை அனுப்பிக்கொண்டே இருங்கள்.",
    "intent_confirm": "{label} குறித்து வாங்க வேண்டுமா விற்க வேண்டுமா?",
    "tech_verify": "வெரிஃபிகேஷனில் தொழில்நுட்ப சிக்கல். விவரங்களை சேமிக்கிறேன், சற்று நேரம் கழித்து தொடரலாம்.",
    "otp_send_fail": "OTP அனுப்புவதில் சிக்கல். விவரங்கள் சேமிக்கப்படும், சற்று நேரம் கழித்து முயலுங்கள்.",
    "otp_sent": "நன்றி. உங்கள் WhatsApp-க்கு OTP அனுப்புகிறேன். 6 இலக்க கோடை இங்கே அனுப்புங்கள்.",
    "profile_found_review": "உங்கள் InfraDealer ப்ரொஃபைல் கிடைத்தது. வாகன விவரங்கள் டீமுக்கு அனுப்பப்படுகின்றன.",
    "saving": "விவரங்களை சேமிக்கிறேன். நிலுவையில் உள்ளதை அனுப்புங்கள்.",
    "save_pending": "விவரங்கள் சேமிக்கப்படுகின்றன. நிலுவை புலத்தை அனுப்புங்கள்.",
    "listing_not_live": "விவரங்கள் வெரிஃபிகேஷனுக்காக டீமுக்கு அனுப்பப்பட்டன",
}

_TE = {
    "intent": "నమస్కారం. InfraDealerలో వాహనం/మెషీన్ అమ్మాలా కొనాలా?",
    "brand": "బ్రాండ్ మరియు మోడల్ చెప్పండి.",
    "model": "మోడల్ పేరు / వేరియంట్ ఏమిటి?",
    "year": "ఇది ఏ సంవత్సరం?",
    "running": "వాహనం సుమారు ఎన్ని కిలోమీటర్లు నడిచింది? మెషీన్ అయితే గంటలు చెప్పండి.",
    "condition": "కండిషన్ ఎలా ఉంది — బాగుందా, పెద్ద యాక్సిడెంట్ / మెకానికల్ సమస్య ఉందా?",
    "location": "ఇప్పుడు ఏ లొకేషన్‌లో ఉంది?",
    "expected_price": "ఎంత ధర పెట్టాలనుకుంటున్నారు?",
    "budget": "సుమారు బడ్జెట్ ఎంత?",
    "category": "సర్, ఇది ఏమిటి — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, లేదా Other?",
    "customer_name": "InfraDealer ప్రొఫైల్ కనబడలేదు. సృష్టించడానికి పేరు చెప్పండి.",
    "photos": "బాగుంది. లిస్టింగ్ కోసం స్పష్టమైన ఫోటోలు పంపండి. ముందు మరియు రెండు వైపుల నుంచి మొదలుపెట్టవచ్చు.",
    "more_detail": "కాస్త మరింత వివరం పంపండి.",
    "photo_fail": "ఫోటో డౌన్‌లోడ్ కాలేదు. దయచేసి మళ్లీ పంపండి.",
    "photo_more": "ఫోటోలు వచ్చాయి. సాధ్యమైతే వెనుక మరియు క్యాబిన్ ఫోటో కూడా పంపండి.",
    "photo_note": "ఫోటోలు నోట్ అయ్యాయి. ఇంకా ఉంటే పంపండి, లేదా టీమ్‌కి రివ్యూకి పంపుతాను.",
    "posted": "మీ లిస్టింగ్ InfraDealerలో పోస్ట్ అయింది.",
    "otp_mismatch": "OTP సరిపోలలేదు. 6 అంకెల కోడ్ మళ్లీ పంపండి.",
    "otp_ok_review": "ప్రొఫైల్ వెరిఫై అయింది. వివరాలు టీమ్‌కి పంపబడ్డాయి.",
    "otp_ok_pending": "ప్రొఫైల్ వెరిఫై అయింది. కొన్ని వివరాలు ఇంకా పెండింగ్.",
    "otp_ask": "WhatsAppలో వచ్చిన 6 అంకెల OTPని ఇక్కడ టైప్ చేసి పంపండి.",
    "extra_media": "అదనపు ఫోటోలు నోట్ అయ్యాయి. అడ్మిన్ పోస్ట్ చేసిన తర్వాతే లిస్టింగ్ లైవ్ అవుతుంది.",
    "received_review": "వివరాలు అందాయి. వెరిఫికేషన్ కోసం టీమ్‌కి పంపాం.",
    "ignore_inject": "నేను లిస్టింగ్ వివరాలు సేకరించి టీమ్‌కి పంపుతాను. వాహన వివరాలు పంపుతూ ఉండండి.",
    "intent_confirm": "{label} గురించి కొనాలా అమ్మాలా?",
    "tech_verify": "వెరిఫికేషన్‌లో టెక్నికల్ సమస్య. వివరాలు సేవ్ చేస్తున్నాను, కాస్త సేపు తర్వాత కొనసాగిద్దాం.",
    "otp_send_fail": "OTP పంపడంలో సమస్య. వివరాలు సేవ్ ఉంచుతాను, కాస్త సేపు తర్వాత ప్రయత్నించండి.",
    "otp_sent": "ధన్యవాదాలు. మీ WhatsAppకి OTP పంపుతున్నాను. 6 అంకెల కోడ్ ఇక్కడ పంపండి.",
    "profile_found_review": "మీ InfraDealer ప్రొఫైల్ దొరికింది. వాహన వివరాలు టీమ్‌కి పంపబడుతున్నాయి.",
    "saving": "వివరాలు సేవ్ చేస్తున్నాను. పెండింగ్ ఉన్నది పంపండి.",
    "save_pending": "వివరాలు సేవ్ అవుతున్నాయి. పెండింగ్ ఫీల్డ్ పంపండి.",
    "listing_not_live": "వివరాలు వెరిఫికేషన్ కోసం టీమ్‌కి పంపబడ్డాయి",
}

_KN = {
    "intent": "ನಮಸ್ಕಾರ. InfraDealerನಲ್ಲಿ ವಾಹನ/ಮೆಷೀನ್ ಮಾರಬೇಕಾ ಖರೀದಿಸಬೇಕಾ?",
    "brand": "ಬ್ರಾಂಡ್ ಮತ್ತು ಮಾದರಿ ಹೇಳಿ.",
    "model": "ಮಾದರಿ ಹೆಸರು / ವೇರಿಯಂಟ್ ಏನು?",
    "year": "ಇದು ಯಾವ ವರ್ಷದ್ದು?",
    "running": "ವಾಹನ ಸುಮಾರು ಎಷ್ಟು ಕಿಲೋಮೀಟರ್ ಓಡಿದೆ? ಮೆಷೀನ್ ಆದರೆ ಗಂಟೆಗಳನ್ನು ಹೇಳಿ.",
    "condition": "ಕಂಡಿಷನ್ ಹೇಗಿದೆ — ಒಳ್ಳೆಯದೆ, ದೊಡ್ಡ ಆಕ್ಸಿಡೆಂಟ್ / ಮೆಕ್ಯಾನಿಕಲ್ ಸಮಸ್ಯೆ ಇದೆಯೆ?",
    "location": "ಈಗ ಯಾವ ಸ್ಥಳದಲ್ಲಿದೆ?",
    "expected_price": "ಎಷ್ಟು ಬೆಲೆ ಇಡಬೇಕು?",
    "budget": "ಅಂದಾಜು ಬಜೆಟ್ ಎಷ್ಟು?",
    "category": "ಸರ್, ಇದು ಏನು — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, ಅಥವಾ Other?",
    "customer_name": "InfraDealer ಪ್ರೊಫೈಲ್ ಸಿಗಲಿಲ್ಲ. ರಚಿಸಲು ನಿಮ್ಮ ಹೆಸರು ಹೇಳಿ.",
    "photos": "ಚೆನ್ನಾಗಿದೆ. ಲಿಸ್ಟಿಂಗ್‌ಗೆ ಸ್ಪಷ್ಟ ಫೋಟೋ ಕಳುಹಿಸಿ. ಮುಂದಿನಿಂದ ಮತ್ತು ಎರಡು ಬದಿಗಳಿಂದ ಪ್ರಾರಂಭಿಸಬಹುದು.",
    "more_detail": "ಸ್ವಲ್ಪ ಹೆಚ್ಚು ವಿವರ ಕಳುಹಿಸಿ.",
    "photo_fail": "ಫೋಟೋ ಡೌನ್‌ಲೋಡ್ ಆಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಕಳುಹಿಸಿ.",
    "photo_more": "ಫೋಟೋ ಸಿಕ್ಕಿವೆ. ಸಾಧ್ಯವಾದರೆ ಹಿಂದಿನ ಮತ್ತು ಕ್ಯಾಬಿನ್ ಫೋಟೋ ಕೂಡ ಕಳುಹಿಸಿ.",
    "photo_note": "ಫೋಟೋ ನೋಂದಾಯಿತು. ಇನ್ನೂ ಇದ್ದರೆ ಕಳುಹಿಸಿ, ಇಲ್ಲದಿದ್ದರೆ ತಂಡಕ್ಕೆ ರಿವ್ಯೂಗೆ ಕಳುಹಿಸುತ್ತೇನೆ.",
    "posted": "ನಿಮ್ಮ ಲಿಸ್ಟಿಂಗ್ InfraDealerನಲ್ಲಿ ಪೋಸ್ಟ್ ಆಗಿದೆ.",
    "otp_mismatch": "OTP ಹೊಂದಿಕೆಯಾಗಲಿಲ್ಲ. 6 ಅಂಕಿಯ ಕೋಡ್ ಮತ್ತೆ ಕಳುಹಿಸಿ.",
    "otp_ok_review": "ಪ್ರೊಫೈಲ್ ವೆರಿಫೈ ಆಯಿತು. ವಿವರಗಳನ್ನು ತಂಡಕ್ಕೆ ಕಳುಹಿಸಲಾಗಿದೆ.",
    "otp_ok_pending": "ಪ್ರೊಫೈಲ್ ವೆರಿಫೈ ಆಯಿತು. ಕೆಲವು ವಿವರಗಳು ಇನ್ನೂ ಬಾಕಿ.",
    "otp_ask": "WhatsAppಗೆ ಬಂದ 6 ಅಂಕಿಯ OTP ಇಲ್ಲಿ ಟೈಪ್ ಮಾಡಿ ಕಳುಹಿಸಿ.",
    "extra_media": "ಹೆಚ್ಚುವರಿ ಫೋಟೋ ನೋಂದಾಯಿತು. ಅಡ್ಮಿನ್ ಪೋಸ್ಟ್ ಮಾಡಿದ ನಂತರವೇ ಲಿಸ್ಟಿಂಗ್ ಲೈವ್.",
    "received_review": "ವಿವರಗಳು ಸಿಕ್ಕಿವೆ. ವೆರಿಫಿಕೇಶನ್‌ಗೆ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಲಾಗಿದೆ.",
    "ignore_inject": "ನಾನು ಲಿಸ್ಟಿಂಗ್ ವಿವರ ಸಂಗ್ರಹಿಸಿ ತಂಡಕ್ಕೆ ಕಳುಹಿಸುತ್ತೇನೆ. ವಾಹನ ವಿವರ ಕಳುಹಿಸುತ್ತಿರಿ.",
    "intent_confirm": "{label} ಬಗ್ಗೆ ಖರೀದಿಸಬೇಕಾ ಮಾರಬೇಕಾ?",
    "tech_verify": "ವೆರಿಫಿಕೇಶನ್‌ನಲ್ಲಿ ತಾಂತ್ರಿಕ ತೊಂದರೆ. ವಿವರ ಸೇವ್ ಮಾಡುತ್ತಿದ್ದೇನೆ, ಸ್ವಲ್ಪ ನಂತರ ಮುಂದುವರಿಸೋಣ.",
    "otp_send_fail": "OTP ಕಳುಹಿಸುವಲ್ಲಿ ತೊಂದರೆ. ವಿವರ ಸೇವ್ ಇಡುತ್ತೇನೆ, ಸ್ವಲ್ಪ ನಂತರ ಪ್ರಯತ್ನಿಸಿ.",
    "otp_sent": "ಧನ್ಯವಾದ. ನಿಮ್ಮ WhatsAppಗೆ OTP ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ. 6 ಅಂಕಿಯ ಕೋಡ್ ಇಲ್ಲಿ ಕಳುಹಿಸಿ.",
    "profile_found_review": "ನಿಮ್ಮ InfraDealer ಪ್ರೊಫೈಲ್ ಸಿಕ್ಕಿತು. ವಾಹನ ವಿವರ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಲಾಗುತ್ತಿದೆ.",
    "saving": "ವಿವರ ಸೇವ್ ಮಾಡುತ್ತಿದ್ದೇನೆ. ಬಾಕಿ ಇರುವುದನ್ನು ಕಳುಹಿಸಿ.",
    "save_pending": "ವಿವರ ಸೇವ್ ಆಗುತ್ತಿದೆ. ಪೆಂಡಿಂಗ್ ಫೀಲ್ಡ್ ಕಳುಹಿಸಿ.",
    "listing_not_live": "ವಿವರಗಳನ್ನು ವೆರಿಫಿಕೇಶನ್‌ಗೆ ತಂಡಕ್ಕೆ ಕಳುಹಿಸಲಾಗಿದೆ",
}

_ML = {
    "intent": "നമസ്കാരം. InfraDealer-ൽ വാഹനം/മെഷീൻ വിൽക്കണോ വാങ്ങണോ?",
    "brand": "ബ്രാൻഡും മോഡലും പറയൂ.",
    "model": "മോഡൽ പേര് / വേരിയന്റ് എന്താണ്?",
    "year": "ഇത് ഏത് വർഷത്തേതാണ്?",
    "running": "വാഹനം ഏകദേശം എത്ര കിലോമീറ്റർ ഓടി? മെഷീൻ ആണെങ്കിൽ മണിക്കൂർ പറയൂ.",
    "condition": "കണ്ടീഷൻ എങ്ങനെ — നല്ലതാണോ, വലിയ ആക്സിഡന്റ് / മെക്കാനിക്കൽ പ്രശ്നം ഉണ്ടോ?",
    "location": "ഇപ്പോൾ ഏത് ലൊക്കേഷനിലാണ്?",
    "expected_price": "എത്ര വില വയ്ക്കാൻ ആഗ്രഹിക്കുന്നു?",
    "budget": "ഏകദേശ ബജറ്റ് എത്ര?",
    "category": "സർ, ഇതെന്താണ് — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, അല്ലെങ്കിൽ Other?",
    "customer_name": "InfraDealer പ്രൊഫൈൽ കണ്ടെത്താനായില്ല. ഉണ്ടാക്കാൻ പേര് പറയൂ.",
    "photos": "നല്ലത്. ലിസ്റ്റിങ്ങിന് വ്യക്തമായ ഫോട്ടോ അയയ്ക്കൂ. മുന്നിൽ നിന്നും രണ്ട് വശങ്ങളിൽ നിന്നും തുടങ്ങാം.",
    "more_detail": "കുറച്ചുകൂടി വിവരം അയയ്ക്കൂ.",
    "photo_fail": "ഫോട്ടോ ഡൗൺലോഡ് ആയില്ല. ദയവായി വീണ്ടും അയയ്ക്കൂ.",
    "photo_more": "ഫോട്ടോ കിട്ടി. കഴിയുമെങ്കിൽ പിന്നിലും ക്യാബിനിലും ഒരു ഫോട്ടോ വീതം അയയ്ക്കൂ.",
    "photo_note": "ഫോട്ടോ നോട്ട് ചെയ്തു. കൂടുതൽ ഉണ്ടെങ്കിൽ അയയ്ക്കൂ, ഇല്ലെങ്കിൽ ടീമിന് റിവ്യൂവിന് അയയ്ക്കാം.",
    "posted": "നിങ്ങളുടെ ലിസ്റ്റിങ് InfraDealer-ൽ പോസ്റ്റ് ആയി.",
    "otp_mismatch": "OTP പൊരുത്തപ്പെട്ടില്ല. 6 അക്ക കോഡ് വീണ്ടും അയയ്ക്കൂ.",
    "otp_ok_review": "പ്രൊഫൈൽ വെരിഫൈ ആയി. വിവരങ്ങൾ ടീമിന് അയച്ചു.",
    "otp_ok_pending": "പ്രൊഫൈൽ വെരിഫൈ ആയി. ചില വിവരങ്ങൾ ഇനിയും ബാക്കി.",
    "otp_ask": "WhatsApp-ൽ വന്ന 6 അക്ക OTP ഇവിടെ ടൈപ്പ് ചെയ്ത് അയയ്ക്കൂ.",
    "extra_media": "അധിക ഫോട്ടോ നോട്ട് ചെയ്തു. അഡ്മിൻ പോസ്റ്റ് ചെയ്ത ശേഷമേ ലിസ്റ്റിങ് ലൈവ് ആകൂ.",
    "received_review": "വിവരങ്ങൾ ലഭിച്ചു. വെരിഫിക്കേഷനായി ടീമിന് അയച്ചു.",
    "ignore_inject": "ഞാൻ ലിസ്റ്റിങ് വിവരം ശേഖരിച്ച് ടീമിന് അയയ്ക്കും. വാഹന വിവരം അയച്ചുകൊണ്ടിരിക്കൂ.",
    "intent_confirm": "{label} സംബന്ധിച്ച് വാങ്ങണോ വിൽക്കണോ?",
    "tech_verify": "വെരിഫിക്കേഷനിൽ ടെക്നിക്കൽ പ്രശ്നം. വിവരം സേവ് ചെയ്യുന്നു, അൽപ്പം കഴിഞ്ഞ് തുടരാം.",
    "otp_send_fail": "OTP അയയ്ക്കുന്നതിൽ പ്രശ്നം. വിവരം സേവ് വയ്ക്കും, അൽപ്പം കഴിഞ്ഞ് ശ്രമിക്കൂ.",
    "otp_sent": "നന്ദി. നിങ്ങളുടെ WhatsApp-ലേക്ക് OTP അയയ്ക്കുന്നു. 6 അക്ക കോഡ് ഇവിടെ അയയ്ക്കൂ.",
    "profile_found_review": "നിങ്ങളുടെ InfraDealer പ്രൊഫൈൽ കണ്ടെത്തി. വാഹന വിവരം ടീമിന് അയയ്ക്കുന്നു.",
    "saving": "വിവരം സേവ് ചെയ്യുന്നു. ബാക്കിയുള്ളത് അയയ്ക്കൂ.",
    "save_pending": "വിവരം സേവ് ആകുന്നു. പെൻഡിങ് ഫീൽഡ് അയയ്ക്കൂ.",
    "listing_not_live": "വിവരങ്ങൾ വെരിഫിക്കേഷനായി ടീമിന് അയച്ചു",
}

_BN = {
    "intent": "নমস্কার। InfraDealer-এ আপনি গাড়ি/মেশিন বিক্রি করতে চান নাকি কিনতে?",
    "brand": "ব্র্যান্ড ও মডেল বলুন।",
    "model": "মডেল নাম / ভ্যারিয়েন্ট কী?",
    "year": "এটি কোন সালের?",
    "running": "গাড়ি প্রায় কত কিলোমিটার চলেছে? মেশিন হলে ঘণ্টা বলুন।",
    "condition": "কন্ডিশন কেমন — ভালো, নাকি বড় অ্যাকসিডেন্ট / মেকানিক্যাল সমস্যা?",
    "location": "এখন কোন লোকেশনে আছে?",
    "expected_price": "কত দাম রাখতে চান?",
    "budget": "আনুমানিক বাজেট কত?",
    "category": "স্যার, এটা কী — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, নাকি Other?",
    "customer_name": "InfraDealer প্রোফাইল পাওয়া যায়নি। তৈরি করতে আপনার নাম বলুন।",
    "photos": "দারুণ। লিস্টিংয়ের জন্য স্পষ্ট ছবি পাঠান। সামনে ও দুই পাশ থেকে শুরু করতে পারেন।",
    "more_detail": "আর একটু বিস্তারিত পাঠান।",
    "photo_fail": "ছবি ডাউনলোড হয়নি। অনুগ্রহ করে আবার পাঠান।",
    "photo_more": "ছবি পেয়েছি। সম্ভব হলে পেছনের ও ক্যাবিনের একটা করে স্পষ্ট ছবিও পাঠান।",
    "photo_note": "ছবি নোট হয়েছে। আর থাকলে পাঠান, নাহলে টিমকে রিভিউয়ের জন্য পাঠাব।",
    "posted": "আপনার লিস্টিং InfraDealer-এ পোস্ট হয়ে গেছে।",
    "otp_mismatch": "OTP মিলছে না। ৬ অঙ্কের কোড আবার পাঠান।",
    "otp_ok_review": "প্রোফাইল ভেরিফাই হয়েছে। বিবরণ টিমকে পাঠানো হয়েছে।",
    "otp_ok_pending": "প্রোফাইল ভেরিফাই হয়েছে। কিছু বিবরণ এখনও বাকি।",
    "otp_ask": "WhatsApp-এ আসা ৬ অঙ্কের OTP এখানে টাইপ করে পাঠান।",
    "extra_media": "অতিরিক্ত ছবি নোট হয়েছে। অ্যাডমিন পোস্ট করলে তবেই লিস্টিং লাইভ হবে।",
    "received_review": "বিবরণ পেয়েছি। ভেরিফিকেশনের জন্য টিমকে পাঠানো হয়েছে।",
    "ignore_inject": "আমি লিস্টিং সংগ্রহ করে টিমকে রিভিউয়ের জন্য পাঠাই। গাড়ির বিবরণ পাঠাতে থাকুন।",
    "intent_confirm": "{label} নিয়ে আপনি কিনতে চান নাকি বিক্রি করতে?",
    "tech_verify": "ভেরিফিকেশনে টেকনিক্যাল সমস্যা। বিবরণ সেভ করছি, কিছুক্ষণ পর চালিয়ে যাব।",
    "otp_send_fail": "OTP পাঠাতে সমস্যা। বিবরণ সেভ রাখব, কিছুক্ষণ পর চেষ্টা করুন।",
    "otp_sent": "ধন্যবাদ। আপনার WhatsApp-এ OTP পাঠাচ্ছি। ৬ অঙ্কের কোড এখানে পাঠান।",
    "profile_found_review": "আপনার InfraDealer প্রোফাইল পাওয়া গেছে। গাড়ির বিবরণ টিমকে পাঠানো হচ্ছে।",
    "saving": "বিবরণ সেভ করছি। যা বাকি আছে তা পাঠান।",
    "save_pending": "বিবরণ সেভ হচ্ছে। পেন্ডিং ফিল্ড পাঠান।",
    "listing_not_live": "বিবরণ ভেরিফিকেশনের জন্য টিমকে পাঠানো হয়েছে",
}

_UR = {
    "intent": "السلام علیکم۔ InfraDealer پر آپ گاڑی/مشین بیچنا چاہتے ہیں یا خریدنا؟",
    "brand": "براہ کرم برانڈ اور ماڈل بتائیں۔",
    "model": "ماڈل نام / ویرینٹ کیا ہے؟",
    "year": "یہ کس سال کی ہے؟",
    "running": "گاڑی تقریباً کتنے کلومیٹر چلی ہے؟ مشین ہو تو گھنٹے بتائیں۔",
    "condition": "کنڈیشن کیسی ہے — اچھی ہے، یا کوئی بڑا ایکسیڈنٹ / مکینیکل مسئلہ؟",
    "location": "اس وقت کس لوکیشن پر ہے؟",
    "expected_price": "کتنی قیمت رکھنی ہے؟",
    "budget": "تقریباً بجٹ کتنا ہے؟",
    "category": "سر، یہ کیا ہے — Truck, Dumper, Tipper, Crane, Poclain, Loader, Backhoe Loader, JCB, Excavator, Grader, Crusher, یا Other?",
    "customer_name": "InfraDealer پر پروفائل نہیں ملا۔ بنانے کے لیے اپنا نام بتائیں۔",
    "photos": "بہت اچھا۔ لسٹنگ کے لیے صاف فوٹو بھیجیں۔ سامنے اور دونوں سائیڈ سے شروع کر سکتے ہیں۔",
    "more_detail": "ذرا اور تفصیل بھیجیں۔",
    "photo_fail": "فوٹو ڈاؤن لوڈ نہیں ہوئی۔ براہ کرم دوبارہ بھیجیں۔",
    "photo_more": "فوٹو مل گئی ہیں۔ ممکن ہو تو پیچھے اور کیبن کی ایک ایک صاف فوٹو بھی بھیجیں۔",
    "photo_note": "فوٹو نوٹ ہو گئیں۔ مزید ہوں تو بھیجیں، ورنہ ٹیم کو ریویو کے لیے بھیج دیتا ہوں۔",
    "posted": "آپ کی لسٹنگ InfraDealer پر پوسٹ ہو گئی ہے۔",
    "otp_mismatch": "OTP میچ نہیں ہوا۔ 6 ہندسوں کا کوڈ دوبارہ بھیجیں۔",
    "otp_ok_review": "پروفائل ویریفائی ہو گیا۔ تفصیل ٹیم کو ریویو کے لیے بھیج دی گئی ہے۔",
    "otp_ok_pending": "پروفائل ویریفائی ہو گیا۔ کچھ تفصیل ابھی باقی ہے۔",
    "otp_ask": "WhatsApp پر آیا 6 ہندسوں کا OTP یہاں ٹائپ کر کے بھیجیں۔",
    "extra_media": "اضافی فوٹو نوٹ ہو گئے۔ لسٹنگ تب لائیو ہوگی جب ایڈمن پوسٹ کرے۔",
    "received_review": "تفصیل مل گئی ہے۔ تصدیق کے لیے ٹیم کو بھیج دی گئی ہے۔",
    "ignore_inject": "میں لسٹنگ جمع کر کے ٹیم کو ریویو کے لیے بھیجتا ہوں۔ گاڑی کی تفصیل بھیجتے رہیں۔",
    "intent_confirm": "{label} کے بارے میں آپ خریدنا چاہتے ہیں یا بیچنا؟",
    "tech_verify": "تصدیق میں تکنیکی مسئلہ ہے۔ تفصیل سیو کر رہا ہوں، تھोड़ी دیر بعد جاری رکھتے ہیں۔",
    "otp_send_fail": "OTP بھیجنے میں مسئلہ ہے۔ تفصیل سیو رکھوں گا، تھोड़ी دیر بعد کوشش کریں۔",
    "otp_sent": "شکریہ۔ آپ کے WhatsApp پر OTP بھیج رہا ہوں۔ 6 ہندسوں کا کوڈ یہاں بھیجیں۔",
    "profile_found_review": "آپ کا InfraDealer پروفائل مل گیا۔ گاڑی کی تفصیل ٹیم کو بھیجی جا رہی ہے۔",
    "saving": "تفصیل سیو کر رہا ہوں۔ جو باقی ہو وہ بھیج دیں۔",
    "save_pending": "تفصیل سیو ہو رہی ہے۔ پینڈنگ فیلڈ بھیج دیں۔",
    "listing_not_live": "تفصیل تصدیق کے لیے ٹیم کو بھیج دی گئی ہے",
}

STRINGS = {
    "hinglish": _HINGLISH,
    "hi": _HI,
    "en": _EN,
    "pa": _PA,
    "gu": _GU,
    "mr": _MR,
    "ta": _TA,
    "te": _TE,
    "kn": _KN,
    "ml": _ML,
    "bn": _BN,
    "ur": _UR,
}
