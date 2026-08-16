import re

CITIES = [
    "delhi", "new delhi", "mumbai", "pune", "hyderabad", "secunderabad", "bangalore",
    "bengaluru", "chennai", "kolkata", "ahmedabad", "surat", "jaipur", "lucknow",
    "kanpur", "nagpur", "indore", "bhopal", "patna", "varanasi", "agra", "meerut",
    "noida", "gurgaon", "gurugram", "chandigarh", "ludhiana", "amritsar", "dehradun",
    "kochi", "coimbatore", "visakhapatnam", "vijayawada", "nashik", "vadodara",
    "rajkot", "bhubaneswar", "goa", "hubli", "mysore", "madurai", "thrissur",
    "ghaziabad", "faridabad", "sonipat", "panipat", "mohali", "zirakpur", "ranchi",
    "raipur", "jodhpur", "udaipur", "guwahati", "jammu", "srinagar", "shimla",
]

CAT_MAP = [
    (r"activa|pulsar|splendor|bike|scooty|scooter|yamaha|fz|r15|enfield|ktm|duke|apache", "Bike"),
    (r"creta|swift|dzire|baleno|alto|nexon|thar|xuv|car|sedan|hatchback|suv|innova", "Car"),
    (r"iphone|samsung|galaxy|redmi|poco|realme|oppo|vivo|oneplus|phone|mobile|smartphone", "Mobile Phone"),
    (r"laptop|macbook|thinkpad|pavilion|inspiron|chromebook|notebook|dell|lenovo|hp", "Laptop"),
    (r"fridge|refrigerator|godrej|whirlpool\s?fridge|lg\s?fridge", "Refrigerator"),
    (r"air\s?conditioner|\bac\b|split\s?ac|window\s?ac|cooler", "AC / Cooler"),
    (r"sofa|table|chair|bed|almirah|wardrobe|furniture|mattress", "Furniture"),
    (r"washing\s?machine|\bwm\b|top\s?load|front\s?load|ifb", "Washing Machine"),
    (r"\btv\b|television|led\s?tv|smart\s?tv|oled|projector", "TV / Projector"),
    (r"camera|dslr|mirrorless|canon|nikon|gopro", "Camera"),
    (r"watch|smartwatch|apple\s?watch|boat|garmin", "Watch"),
    (r"geyser|mixer|grinder|oven|microwave|vacuum|inverter|purifier", "Home Appliance"),
]

STOPWORDS = {
    "mai", "mein", "main", "bech", "raha", "rahi", "hu", "hoon", "hai", "ke", "ki",
    "ko", "ka", "se", "me", "liye", "kya", "ye", "yeh", "wo", "woh", "kar", "to",
    "a", "an", "and", "with", "for", "my", "i", "am", "is", "this", "that", "in",
    "on", "at", "of", "or", "sale", "selling", "sell", "want", "contact", "call",
    "whatsapp", "number", "mobile", "phone", "price", "rate", "daam", "kimat",
    "condition", "location", "shahar", "place", "rupees", "rupaye", "rs", "inr",
    "only", "urgent", "bhai", "sir", "good", "fair", "used", "new", "available",
    "mera", "meri", "please", "pls",
}

CATEGORIES = [
    "Bike", "Car", "Mobile Phone", "Laptop", "Refrigerator", "AC / Cooler",
    "Furniture", "Washing Machine", "TV / Projector", "Camera", "Watch",
    "Home Appliance", "Other",
]

CONDITIONS = [
    "Brand New", "Like New", "Excellent", "Very Good", "Good", "Fair",
    "Average", "Used", "Refurbished",
]


def cap(value: str) -> str:
    return re.sub(r"\b[a-z]", lambda m: m.group(0).upper(), (value or "").strip())


def parse_message(raw: str) -> dict:
    text = (raw or "").strip()
    res = {
        "product_name": "",
        "category": "Other",
        "price": "",
        "price_num": None,
        "condition": "",
        "city": "",
        "mobile": "",
        "rest": text,
    }
    work = text

    phone_re = re.compile(r"(?:\+?91[\s-]?|0)?(?:[6-9](?:\d[\s-]?){9})")
    m = phone_re.search(work)
    if m:
        digits = re.sub(r"\D", "", m.group(0))[-10:]
        res["mobile"] = digits
        work = phone_re.sub(" ", work)

    city_hit = re.search(r"(?:city|location|shahar|jagah|place)\s*[:=-]?\s*([a-z\s]{2,25})", work, re.I)
    if city_hit:
        cv = city_hit.group(1).strip().lower()
        if cv in CITIES:
            res["city"] = cap(cv)
            work = work.replace(city_hit.group(0), " ")
    if not res["city"]:
        low = work.lower()
        found, pos = None, -1
        for city in CITIES:
            idx = low.find(city)
            if idx > -1 and (pos == -1 or idx < pos):
                pos, found = idx, city
        if found:
            res["city"] = cap(found)
            work = re.sub(rf"\b{re.escape(found)}\b", " ", work, flags=re.I)

    cond = re.search(
        r"\b(brand\s?new|like\s?new|excellent|very\s?good|good|fair|average|poor|used|refurbished)\b",
        work,
        re.I,
    )
    if cond:
        res["condition"] = cap(cond.group(1))
        work = work.replace(cond.group(0), " ")

    price = (
        re.search(r"(?:₹|rs\.?|inr|rupees|rupaye)\s*([\d,]*\d(?:\.\d+)?)\s*(k|lakh|lac|thousand|hundred)?", work, re.I)
        or re.search(r"([\d,]*\d(?:\.\d+)?)\s*(k|lakh|lac|thousand|hundred|rupees|rupaye|rs\.?)\b", work, re.I)
        or re.search(r"(?:price|pricing|rate|daam|kimat)\s*[:=-]?\s*([\d,]*\d(?:\.\d+)?)", work, re.I)
    )
    if price:
        num = float(price.group(1).replace(",", ""))
        mult = 1
        if price.lastindex and price.lastindex >= 2 and price.group(2):
            suf = price.group(2).lower()
            if suf in {"k", "thousand"}:
                mult = 1000
            elif suf in {"lakh", "lac"}:
                mult = 100000
            elif suf == "hundred":
                mult = 100
        res["price_num"] = round(num * mult)
        res["price"] = f"{res['price_num']:,}".replace(",", "")
        work = work.replace(price.group(0), " ")

    for pattern, cat in CAT_MAP:
        if re.search(pattern, work, re.I):
            res["category"] = cat
            break

    words = re.sub(r"\s+", " ", work).strip().split(" ")
    keep = []
    for wd in words:
        wd = re.sub(r"[,.!?;:]+$", "", wd)
        if len(wd) < 2 or re.fullmatch(r"\d{10}", wd) or wd.lower() in STOPWORDS:
            continue
        keep.append(wd)
        if len(keep) >= 7:
            break
    res["product_name"] = cap(" ".join(keep)) if keep else f"{res['category']} (name chahiye)"
    return res
