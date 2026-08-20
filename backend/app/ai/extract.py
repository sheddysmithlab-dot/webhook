import difflib
import re
import unicodedata

from ..parser import CITIES

BRANDS = [
    ("ashok leyland", "Ashok Leyland"),
    ("bharat benz", "BharatBenz"),
    ("bharatbenz", "BharatBenz"),
    ("john deere", "John Deere"),
    ("new holland", "New Holland"),
    ("caterpillar", "Caterpillar"),
    ("leyland", "Ashok Leyland"),
    ("mahindra", "Mahindra"),
    ("sonalika", "Sonalika"),
    ("escorts", "Escorts"),
    ("hitachi", "Hitachi"),
    ("komatsu", "Komatsu"),
    ("kobelco", "Kobelco"),
    ("liebherr", "Liebherr"),
    ("hyundai", "Hyundai"),
    ("volvo", "Volvo"),
    ("scania", "Scania"),
    ("eicher", "Eicher"),
    ("isuzu", "Isuzu"),
    ("force", "Force"),
    ("piaggio", "Piaggio"),
    ("tata", "Tata"),
    ("jcb", "JCB"),
    ("case", "Case"),
    ("ace", "ACE"),
    ("man", "MAN"),
    ("amw", "AMW"),
    ("sml", "SML"),
]

BRAND_NATIVE = [
    ("टाटा", "Tata"),
    ("ताता", "Tata"),
    ("महिंद्रा", "Mahindra"),
    ("महिन्द्रा", "Mahindra"),
    ("अशोक", "Ashok Leyland"),
    ("लेलैंड", "Ashok Leyland"),
    ("एशर", "Eicher"),
    ("ऐशर", "Eicher"),
    ("जेसीबी", "JCB"),
    ("जॉन डियर", "John Deere"),
]

MODELS = [
    (r"\b(signa)\b", "Signa"),
    (r"\b(prima)\b", "Prima"),
    (r"\b(ultra)\b", "Ultra"),
    (r"\b(lpt)\b", "LPT"),
    (r"\b(407|709|909|1109|1518|1613|1618|2518|3118|3518|3718|4018|4923)\b", None),
]

TYPE_MAP = [
    (r"\b(back\s*hoe|backhoe|bokeh\s*loader|bokehloader|bokhoe|backhoeloader)\b", ("Backhoe Loader", "Backhoe Loader")),
    (r"\b(poclain|poclin|pockland|pocklain|pokland|pockalnd|pocland|poklain)\b", ("Poclain", "Poclain")),
    (r"\b(excavator|excavatoer|excavater)\b", ("Excavator", "Excavator")),
    (r"\b(crusher|crucher|crushar)\b", ("Crusher", "Crusher")),
    (r"\b(grader|grder|motor\s*grader)\b", ("Grader", "Grader")),
    (r"\b(crane|crain|krane)\b", ("Crane", "Crane")),
    (r"\b(loader|loder)\b", ("Loader", "Loader")),
    (r"\b(tipper|tiper|tipar)\b", ("Tipper", "Tipper")),
    (r"\b(dumper|dump)\b", ("Dumper", "Dumper")),
    (r"\b(jcb)\b", ("JCB", "JCB")),
    (r"(truck|truk|lorry|lory|6\s*tyre|10\s*tyre|12\s*tyre|6\s*wheeler|10\s*wheeler|गाड़ी|ट्रक)", ("Truck", "Truck")),
    (r"\b(other|anya)\b", ("Other", "Other")),
]

SKIP_MODEL = {
    "model", "madal", "modal", "ka", "ki", "ke", "hai", "hain", "year", "saal", "km", "lac", "lakh",
    "me", "se", "ko", "bhai", "ji", "and", "the", "chahiye", "dena", "lena",
    "bechna", "tak", "he", "ho", "wala", "wali", "gadi", "kimat", "keemat",
    "tex", "tax", "bima", "beema", "fitness", "ranig", "running", "quvtar",
    "location", "photo", "photos", "side", "front", "cabin",
    "truck", "dumper", "tipper", "crane", "poclain", "loader", "backhoe", "jcb",
    "excavator", "grader", "crusher", "other",
}

SKIP_LOC = SKIP_MODEL | {
    "gadi", "truck", "machine", "phone", "number", "mobile",
    "hor", "hai", "nahi", "nahin", "tata", "jcb", "model", "mfg",
}

INDIAN_STATES = [
    ("andhra pradesh", "Andhra Pradesh", ("ap", "andhra", "आंध्र प्रदेश", "आन्ध्र प्रदेश")),
    ("arunachal pradesh", "Arunachal Pradesh", ("arunachal", "अरुणाचल")),
    ("assam", "Assam", ("असम", "आसाम")),
    ("bihar", "Bihar", ("बिहार",)),
    ("chhattisgarh", "Chhattisgarh", ("cg", "chhatisgarh", "chattisgarh", "छत्तीसगढ़", "छत्तीसगढ")),
    ("goa", "Goa", ("गोआ", "गोवा")),
    ("gujarat", "Gujarat", ("gj", "gujrat", "guj", "गुजरात")),
    ("haryana", "Haryana", ("hr", "hariyana", "हरियाणा")),
    ("himachal pradesh", "Himachal Pradesh", ("hp", "himachal", "हिमाचल")),
    ("jharkhand", "Jharkhand", ("jarkhand", "झारखंड", "झारखण्ड")),
    ("karnataka", "Karnataka", ("ka", "karnatak", "bangalore state", "कर्नाटक")),
    ("kerala", "Kerala", ("kl", "केरल")),
    ("madhya pradesh", "Madhya Pradesh", ("mp", "m.p", "m p", "madhya", "मध्य प्रदेश", "मध्यप्रदेश")),
    ("maharashtra", "Maharashtra", ("mh", "maha", "maharastra", "महाराष्ट्र")),
    ("manipur", "Manipur", ("मणिपुर",)),
    ("meghalaya", "Meghalaya", ("मेघालय",)),
    ("mizoram", "Mizoram", ("मिजोरम",)),
    ("nagaland", "Nagaland", ("नागालैंड", "नागालैण्ड")),
    ("odisha", "Odisha", ("orissa", "odisa", "ओडिशा", "उड़ीसा")),
    ("punjab", "Punjab", ("pb", "panjab", "पंजाब")),
    ("rajasthan", "Rajasthan", ("rj", "rajsthan", "rajastan", "राजस्थान")),
    ("sikkim", "Sikkim", ("सिक्किम",)),
    ("tamil nadu", "Tamil Nadu", ("tn", "tamilnadu", "तमिलनाडु", "तमिल नाडु")),
    ("telangana", "Telangana", ("ts", "telengana", "तेलंगाना")),
    ("tripura", "Tripura", ("त्रिपुरा",)),
    ("uttar pradesh", "Uttar Pradesh", ("up", "u.p", "u p", "उत्तर प्रदेश", "उत्तरप्रदेश")),
    ("uttarakhand", "Uttarakhand", ("uk", "uttaranchal", "उत्तराखंड", "उत्तराखण्ड")),
    ("west bengal", "West Bengal", ("wb", "bengal", "पश्चिम बंगाल", "वेस्ट बंगाल")),
    ("delhi", "Delhi", ("ncr", "new delhi", "dilli", "दिल्ली", "नई दिल्ली")),
    ("jammu and kashmir", "Jammu and Kashmir", ("jk", "j&k", "kashmir", "jammu", "जम्मू", "कश्मीर")),
    ("ladakh", "Ladakh", ("लद्दाख",)),
    ("puducherry", "Puducherry", ("pondicherry", "पुडुचेरी")),
    ("chandigarh", "Chandigarh", ("चंडीगढ़", "चण्डीगढ़")),
]

CITY_STATE = {
    "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh", "jabalpur": "Madhya Pradesh",
    "gwalior": "Madhya Pradesh", "ujjain": "Madhya Pradesh", "sagar": "Madhya Pradesh",
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "nashik": "Maharashtra", "aurangabad": "Maharashtra", "solapur": "Maharashtra",
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat", "rajkot": "Gujarat",
    "jaipur": "Rajasthan", "udaipur": "Rajasthan", "jodhpur": "Rajasthan", "kota": "Rajasthan",
    "lucknow": "Uttar Pradesh", "kanpur": "Uttar Pradesh", "varanasi": "Uttar Pradesh",
    "agra": "Uttar Pradesh", "noida": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh",
    "delhi": "Delhi", "new delhi": "Delhi", "gurugram": "Haryana", "gurgaon": "Haryana",
    "faridabad": "Haryana", "panipat": "Haryana", "hisar": "Haryana",
    "chandigarh": "Chandigarh", "ludhiana": "Punjab", "amritsar": "Punjab", "jalandhar": "Punjab",
    "patna": "Bihar", "ranchi": "Jharkhand", "jamshedpur": "Jharkhand",
    "raipur": "Chhattisgarh", "bhilai": "Chhattisgarh",
    "kolkata": "West Bengal", "howrah": "West Bengal",
    "bhubaneswar": "Odisha", "cuttack": "Odisha",
    "hyderabad": "Telangana", "warangal": "Telangana",
    "vijayawada": "Andhra Pradesh", "visakhapatnam": "Andhra Pradesh",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysore": "Karnataka", "hubli": "Karnataka",
    "kochi": "Kerala", "thiruvananthapuram": "Kerala",
    "guwahati": "Assam", "dehradun": "Uttarakhand", "shimla": "Himachal Pradesh",
}
_PHONE = re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)")
_GLUED = re.compile(
    r"\b(tata|eicher|mahindra|jcb|leyland|ashok|volvo|bharatbenz|hyundai|isuzu|force)(\d{3,4})\b",
    re.I,
)


def _fold_indic(text: str) -> str:
    raw = unicodedata.normalize("NFC", text or "")
    raw = raw.replace("ड़", "ड़").replace("ढ़", "ढ़")
    raw = raw.replace("गाडी", "गाड़ी").replace("गाड़ी", "गाड़ी").replace("गादी", "गाड़ी")
    raw = raw.replace("करना हे", "करना है")
    raw = raw.replace("चाहिए हे", "चाहिए है").replace("चाहिये", "चाहिए")
    raw = re.sub(r"(?:^|(?<=\s))हे(?:(?=\s)|$)", "है", raw)
    raw = raw.replace("सेल ", "sell ").replace(" सेल", " sell")
    raw = raw.replace("बाय ", "buy ").replace(" बाय", " buy")
    return raw


def _norm(text: str, extra_reps: list[tuple[str, str]] | None = None) -> str:
    low = _fold_indic(text).lower()
    reps = (
        ("cahiye", "chahiye"),
        ("chahye", "chahiye"),
        ("chahie", "chahiye"),
        ("khrid", "kharid"),
        ("kharidna", "kharidna"),
        ("becna", "bechna"),
        ("bechnaa", "bechna"),
        ("bhech", "bech"),
        ("bikani", "bechna"),
        ("bikau", "bechna"),
        ("gaadi", "gadi"),
        ("gaddi", "gadi"),
        ("kilomeeter", "km"),
        ("kilometer", "km"),
        ("kilometre", "km"),
        ("klm", "km"),
        ("tyer", "tyre"),
        ("tiper", "tipper"),
        ("trolly", "trolley"),
        ("madal", "model"),
        ("modal", "model"),
        ("kimat", "price"),
        ("keemat", "price"),
        ("qimat", "price"),
        ("ranig", "running"),
        ("runing", "running"),
        ("bima", "insurance"),
        ("beema", "insurance"),
        ("quvtar", "tax"),
        ("tex", "tax"),
        ("fotu", "photo"),
        ("foto", "photo"),
        ("pictur", "photo"),
        ("prize", "price"),
        ("prise", "price"),
        ("prate", "rate"),
        ("indor", "indore"),
        ("indoer", "indore"),
        ("banglore", "bangalore"),
        ("bengaluru", "bangalore"),
        ("hydrabad", "hyderabad"),
        ("delhhi", "delhi"),
        ("gurgaon", "gurugram"),
        ("acident", "accident"),
        ("accedent", "accident"),
        ("engin", "engine"),
        ("gante", "hours"),
        ("ghnte", "hours"),
        ("modle", "model"),
        ("menufecturing", "manufacturing"),
        ("manufecturing", "manufacturing"),
        ("bechne", "bechna"),
        ("dena he", "dena hai"),
        ("bechne he", "bechna hai"),
    )
    for a, b in reps:
        low = low.replace(a, b)
    for a, b in extra_reps or []:
        cue = (a or "").strip().lower()
        meaning = (b or "").strip().lower()
        if cue and meaning and cue != meaning and len(cue) >= 3:
            low = re.sub(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", meaning, low)
    low = _GLUED.sub(lambda m: f"{m.group(1)} {m.group(2)}", low)
    low = re.sub(r"[,/|]+", " ", low)
    low = re.sub(r"(\d+(?:\.\d+)?)\s*[lL]\b", r"\1 lakh", low)
    low = re.sub(r"\s+", " ", low)
    return low.strip()


def _fuzzy_city(token: str) -> str | None:
    tok = (token or "").strip().lower()
    if len(tok) < 4 or tok in SKIP_LOC:
        return None
    if tok in CITIES:
        return tok
    hits = difflib.get_close_matches(tok, CITIES, n=1, cutoff=0.78)
    if hits:
        return hits[0]
    for city in CITIES:
        if tok[:4] == city[:4] and abs(len(tok) - len(city)) <= 2:
            return city
    return None


def detect_intent(text: str) -> str | None:
    low = _norm(text)
    sell = bool(re.search(
        r"bech|sell|sale|bikri|bikana|bikau|vech|vikay|dena hai|deni hai|de raha|de rha|"
        r"bechne ko|available|विक्रय|बिक्री|बेच|सेल|"
        r"ਵਿਕ|વેચ|विकाय|விற்|விக்க|అమ్మ|ಮಾರು|বিক্রি|بیچ",
        low,
    ))
    buy = bool(re.search(
        r"kharid|buy|purchase|chahiye|lena hai|lene |dekhna|khoj|milni|milna|चाहिए|"
        r"rate bhejo|koi .+ hai kya|खरीद|ਖਰੀਦ|ખરીદ|खरेदी|வாங்|కొన|ಖರೀದಿ|কিনতে|خرید",
        low,
    ))
    if sell and not buy:
        return "SELL"
    if buy and not sell:
        return "BUY"
    if sell and buy:
        sell_i = max((low.rfind(w) for w in ("bech", "dena", "sell", "bikau")), default=-1)
        buy_i = max((low.rfind(w) for w in ("kharid", "chahiye", "lena", "buy")), default=-1)
        return "BUY" if buy_i > sell_i else "SELL"
    if re.search(r"\b(complaint|problem|help|support|otp nahi)\b", low):
        return "SUPPORT"
    return None


def _amount(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


def _year_from(token: str) -> str | None:
    if re.fullmatch(r"(?:19|20)\d{2}", token):
        return token
    if re.fullmatch(r"\d{2}", token):
        n = int(token)
        if 0 <= n <= 30:
            return f"20{token}"
        if 90 <= n <= 99:
            return f"19{token}"
    return None


def extract_state(low: str) -> str | None:
    blob = low or ""
    ranked = sorted(INDIAN_STATES, key=lambda row: max(len(row[0]), max((len(a) for a in row[2]), default=0)), reverse=True)
    for full, title, aliases in ranked:
        needles = (full,) + tuple(aliases)
        for needle in needles:
            if not needle:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", blob):
                return title
    return None


def infer_state_from_city(city: str) -> str | None:
    tok = (city or "").strip().lower()
    return CITY_STATE.get(tok)


def extract_role(text: str) -> str | None:
    low = _norm(text)
    if re.search(r"\b(broker|dalal|agent|dealer|middleman|brokr)\b", low):
        return "broker"
    if re.search(r"\b(user|owner|khud|end\s*user|customer|kharidar|bechne wala|seller)\b", low):
        return "user"
    return None


def extract_optional(text: str, extra_reps: list[tuple[str, str]] | None = None) -> dict:
    low = _norm(text, extra_reps)
    out: dict = {}
    owners = (
        re.search(r"\b(first|1st|ek|one|1)\s*owner\b", low)
        or re.search(r"\b(second|2nd|do|two|2)\s*owner\b", low)
        or re.search(r"\b(\d{1,2})\s*owner", low)
        or re.search(r"owner\s*(\d{1,2})\b", low)
    )
    if owners:
        raw = owners.group(0)
        if re.search(r"first|1st|ek|one", raw) and not re.search(r"\d{2}", raw):
            out["owners"] = "1"
        elif re.search(r"second|2nd|do|two", raw):
            out["owners"] = "2"
        else:
            digits = re.search(r"\d+", raw)
            if digits:
                out["owners"] = digits.group(0)
    fin = re.search(
        r"(?:finance|loan|udhar)\s*(?:baki|baaki|pending|kitna)?\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?\s*(?:lakh|lac|lacs|hazar|k)?)",
        low,
    ) or re.search(r"(\d+(?:\.\d+)?\s*(?:lakh|lac)?)\s*(?:finance|loan)", low)
    if fin:
        out["finance_amount"] = _amount(fin.group(0))
    if re.search(r"finance.{0,20}(clear|theek|nil|no|nahi|zero)|no finance|finance nahi", low):
        out["finance_condition"] = "clear"
        out.setdefault("finance_amount", "0")
    elif re.search(r"finance.{0,20}(pending|baki|baaki|chal raha)", low):
        out["finance_condition"] = "pending"
    tyre = re.search(r"tyre.{0,12}(\d{1,3})\s*%|(\d{1,3})\s*%.{0,12}tyre", low)
    if tyre:
        pct = tyre.group(1) or tyre.group(2)
        out["tyre_percent"] = f"{pct}%"
    if re.search(r"koi kaam nahi|koi galti nahi|no work|no issue|accident nahi|mistake nahi", low):
        out["work_issues"] = "none"
    elif re.search(r"\b(kaam|galti|mistake|dent|paint|engine issue|gear box|accident)\b", low):
        out["work_issues"] = _amount(text)[:120]
    return out


def extract_from_text(text: str, extra_reps: list[tuple[str, str]] | None = None) -> dict:
    """Customer-confirmed fields only. Never invent. Messy WhatsApp is OK."""
    msg = text or ""
    low = _norm(msg, extra_reps)
    work = _PHONE.sub(" ", _GLUED.sub(lambda m: f"{m.group(1)} {m.group(2)}", msg))
    out: dict = {}

    intent = detect_intent(msg)
    if intent:
        out["intent"] = intent

    for needle, brand in BRANDS:
        if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low):
            out["brand"] = brand
            break
    if not out.get("brand"):
        folded = _fold_indic(msg)
        for needle, brand in BRAND_NATIVE:
            if needle in folded:
                out["brand"] = brand
                break

    if out.get("brand"):
        m = re.search(rf"{re.escape(out['brand'])}\s+([a-z0-9][a-z0-9\-]{{1,12}})", work, re.I)
        if m:
            token = m.group(1).strip(".,")
            if token.lower() not in SKIP_MODEL and not re.fullmatch(r"\d{10}", token):
                out["model"] = token.upper() if token.replace("-", "").isalnum() and any(ch.isdigit() for ch in token) else token.title()

    for pat, label in MODELS:
        m = re.search(pat, low)
        if m:
            out["model"] = label or m.group(1).upper()
            if not out.get("brand") and re.search(r"\b(signa|prima|ultra|lpt|407|709|1618|2518)\b", low):
                out["brand"] = "Tata"
            break

    if "model" not in out and out.get("brand"):
        m = re.search(r"\b(\d{3,4})\b", work)
        if m and not _year_from(m.group(1)) and not re.fullmatch(r"\d{10}", m.group(1)):
            out["model"] = m.group(1)

    for pat, (cat, typ) in TYPE_MAP:
        if re.search(pat, low):
            from .schema import normalize_vehicle_category
            out["category"] = normalize_vehicle_category(cat) or cat
            out["type"] = typ
            break

    y = re.search(r"(?:19|20)\d{2}\s*ke\s*baad", low)
    if y:
        ym = re.search(r"((?:19|20)\d{2})", y.group(0))
        if ym:
            out["year_min"] = ym.group(1)
    else:
        years = re.findall(r"\b((?:19|20)\d{2})\b", work)
        if years:
            out["year"] = years[0]
        else:
            ym = re.search(r"\b(0[0-9]|1[0-9]|2[0-6])\s*(?:model|saal|ki|ka)\b", low) or re.search(
                r"\b(?:model|saal)\s*(0[0-9]|1[0-9]|2[0-6])\b", low
            )
            if ym:
                parsed = _year_from(ym.group(1))
                if parsed:
                    out["year"] = parsed

    km = (
        re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|lacs|\bl\b)\s*(?:\d+\s*hazar\s*)?(?:km|chali|chala)", low)
        or re.search(r"(\d+(?:\.\d+)?)\s*(?:hazar|thousand|k)\s*(?:km|chali)", low)
        or re.search(r"(\d{4,7})\s*km\b", low)
    )
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|ghante)", low)
    if hours:
        out["operating_hours"] = _amount(hours.group(0))
        out["running"] = out["operating_hours"]
    elif km:
        out["running_km"] = _amount(km.group(1) if km.lastindex else km.group(0))
        out["running"] = _amount(km.group(0))
    else:
        bare = re.search(r"\b(\d{5,7})\b", low)
        if bare:
            n = int(bare.group(1))
            if 20000 <= n <= 450000:
                out["running_km"] = bare.group(1)
                out["running"] = bare.group(1) + " km"
            elif n >= 500000 and not out.get("expected_price"):
                out["expected_price"] = bare.group(1)

    price_pat = re.finditer(
        r"(\d+(?:\.\d+)?)\s*(lakh|lac|lacs|\bl\b)|(?:rs\.?|₹|rate|price|dam|daam)?\s*(\d+(?:\.\d+)?)\s*(lakh|lac|lacs|\bl\b)",
        low,
    )
    for m in price_pat:
        tail = low[m.end(): m.end() + 12]
        if re.search(r"^\s*(km|chali|chala|hours?|hrs?)\b", tail):
            continue
        val = _amount(m.group(0))
        if out.get("intent") == "BUY" or re.search(r"\b(budget|andar|kharid|chahiye|lena)\b", low):
            out["budget"] = val
            out["budget_max"] = val
        else:
            out["expected_price"] = val

    if re.search(r"\b(negotiable|baat karke|thoda negotiable|offer|baat cheet)\b", low):
        out["negotiable"] = "true"

    if re.search(r"\b(accident|accidented)\b", low):
        out["accident_history"] = "YES"
    if re.search(r"engine.{0,24}(kaam|issue|problem)|needs?\s*repair|major work", low):
        out["condition"] = "NEEDS_REPAIR"
    elif re.search(r"\b(original|excellent|very good|good|achhi|acchi|badhiya|mast|theek chal)\b", low):
        out["condition"] = "GOOD"
    elif re.search(r"\b(average|fair|ok-ok|theek thaak)\b", low):
        out["condition"] = "AVERAGE"

    found_city = None
    pos = -1
    for city in sorted(CITIES, key=len, reverse=True):
        idx = low.find(city)
        if idx > -1 and (pos == -1 or idx < pos):
            pos, found_city = idx, city
    if found_city:
        out["location"] = found_city.title() if found_city != "new delhi" else "New Delhi"
        out["city"] = out["location"]
    else:
        loc = re.search(r"\b([a-z]{3,18})\s*(?:me|mein|se|wali|wale|khadi|khada)\b", low)
        if loc and loc.group(1) not in SKIP_LOC:
            fuzzy = _fuzzy_city(loc.group(1))
            out["location"] = (fuzzy or loc.group(1)).title()
            if (out["location"] or "").lower() == "new delhi":
                out["location"] = "New Delhi"
            out["city"] = out["location"]
        else:
            for token in re.findall(r"\b[a-z]{4,18}\b", low):
                fuzzy = _fuzzy_city(token)
                if fuzzy:
                    out["location"] = "New Delhi" if fuzzy == "new delhi" else fuzzy.title()
                    out["city"] = out["location"]
                    break

    st = extract_state(low)
    if st:
        out["state"] = st
    elif out.get("city"):
        inferred = infer_state_from_city(out["city"])
        if inferred:
            out["state"] = inferred

    out.update({k: v for k, v in extract_optional(msg, extra_reps).items() if v})

    from ..identity import extract_contact_mobile

    phone = extract_contact_mobile(msg)
    if phone:
        out["contact_phone"] = phone

    return out
