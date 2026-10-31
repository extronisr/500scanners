#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily market review ("סקירת שוק") generator for the scanner site.

Runs inside the existing refresh workflow. Regenerates pulse_latest.json only
at the 10:00 / 18:00 Asia/Jerusalem slots (a manual workflow_dispatch run
always scans fresh); otherwise keeps the committed review, so ordinary
15-minute refreshes cost nothing. A failed regeneration never overwrites the
last full review - its honest as-of stamp stays on the site.
button-triggered runs cost nothing extra.

Design rules (from the user's spec):
  * Every number shown comes from a measured source, never from the model.
  * The model (Groq) only selects stories and writes Hebrew prose grounded in
    a numbered fact pack; a deterministic validator rejects invented figures,
    unknown tickers or dangling source references. One retry, then a
    template-only fallback that contains measured data and headlines only.
  * Missing or unreliable data is shown as "אין נתונים אמינים", never filled.
  * Nothing about the user's portfolio is read or used.

Secrets come from env: GROQ_API_KEY, TAVILY_API_KEY. When either is missing
the script logs and exits 0 without touching the saved review.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LATEST_FILE = os.path.join(BASE_DIR, "pulse_latest.json")
HISTORY_FILE = os.path.join(BASE_DIR, "pulse_history.json")

JER = ZoneInfo("Asia/Jerusalem")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36"

SLOT_HOURS = [10, 18]            # Asia/Jerusalem
STALE_HOURS = 6                  # older review regenerates on any run (incl. button)
WINDOW_HOURS = 48                # news freshness window
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_WORDS = 650                  # visible Hebrew budget guard (spec: ~500)
NEWS_QUERIES = [
    "US stock market news today Nasdaq S&P 500",
    "stock market this week recap Wall Street",
    "Wall Street stocks Friday recap Nasdaq Intel Reddit",
    "stock market week ahead earnings Fed November 2026",
]
INDEX_SPECS = [
    ("^IXIC", "NASDAQ Composite"), ("^NDX", "NASDAQ 100"), ("^GSPC", "S&P 500"),
    ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000"), ("^VIX", "VIX"),
]
# tickers that are indices/ETFs - never eligible for "most discussed stocks"
NON_STOCK = {"SPY", "QQQ", "IWM", "DIA", "VIX", "SPX", "NDX", "IXIC", "DJI",
             "RUT", "SMH", "SOXX", "IGV", "SOX", "DRAM", "UVIX", "SQQQ", "TQQQ",
             "TLT", "GLD", "SLV", "USO", "ARKK", "XLF", "XLE", "XLK", "BTC", "ETH"}
TICKER_WHITELIST = {"VIX", "FDA", "SEC", "FED", "ETF", "AI", "EV", "IPO", "US",
                    "GDP", "CPI", "PCE", "FOMC", "CEO", "ANDA", "WSB", "IRA",
                    "USD", "GDP", "API", "USA", "NYSE", "DRAM", "NAND", "TLV",
                    "DOJ", "FTC", "CFO", "EPS", "PE", "TTM", "NASDAQ", "CNN", "CAPEX",
                    "PC", "TV", "WSJ", "AP", "NYT", "UK", "EU", "IMF", "ECB",
                    "BOJ", "CNBC", "SaaS", "SOX", "USD", "ILS", "GBP", "EUR"}
STRUCTURAL_NUMBERS = {str(i) for i in range(0, 13)} | {"24", "48", "100"}


# ------------------------------------------------------------------ http
def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url, timeout=15):
    return json.loads(http_get(url, timeout=timeout))


def http_post_json(url, payload, timeout=30, headers=None):
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ slot gating
def current_slot(now):
    """Id of the active review slot: today 10:00, today 18:00, or yesterday 18:00."""
    today = now.date()
    if now.hour >= SLOT_HOURS[1]:
        return f"{today.isoformat()}T{SLOT_HOURS[1]:02d}:00"
    if now.hour >= SLOT_HOURS[0]:
        return f"{today.isoformat()}T{SLOT_HOURS[0]:02d}:00"
    y = today - timedelta(days=1)
    return f"{y.isoformat()}T{SLOT_HOURS[1]:02d}:00"


def needs_regen(latest, now):
    """Scheduled runs regenerate only when the 10:00/18:00 slot has changed.
    A manual 'עדכן עכשיו' run passes --force and always attempts a fresh scan."""
    if not latest:
        return True
    return latest.get("slot") != current_slot(now)


# ------------------------------------------------------------------ collectors
def get_indices():
    """Last ~10 daily closes per index via Yahoo's public chart endpoint."""
    out = []
    for sym, name in INDEX_SPECS:
        try:
            url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
                   + urllib.parse.quote(sym, safe="") + "?range=1mo&interval=1d")
            d = http_json(url)["chart"]["result"][0]
            closes = [c for c in d["indicators"]["quote"][0]["close"] if c]
            ts = d["timestamp"]
            if len(closes) < 2:
                continue
            last_date = datetime.fromtimestamp(ts[-1], timezone.utc).astimezone(JER).date()
            row = {"sym": sym.lstrip("^"), "name": name, "value": round(closes[-1], 2),
                   "day_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
                   "week_pct": (round((closes[-1] / closes[-6] - 1) * 100, 2)
                                if len(closes) >= 6 else None),
                   "asof": last_date.isoformat()}
            out.append(row)
            time.sleep(0.4)
        except Exception as e:
            print(f"[pulse] index {sym} failed: {e}")
    return out


CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_HEADERS = {"Referer": "https://www.cnn.com/markets/fear-and-greed"}
CNN_SNAPSHOT = os.path.join(BASE_DIR, "cnn_snapshot.json")


def get_fear_greed():
    """Live CNN Fear & Greed (same endpoint + Referer the site's cnn_section uses);
    falls back to the repo's maintained snapshot, which carries its own as-of time
    and CNN's own 1-week-ago value for the weekly comparison."""
    try:
        req = urllib.request.Request(CNN_URL, headers={"User-Agent": UA, **CNN_HEADERS})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        fg = d["fear_and_greed"]
        out = {"score": round(fg["score"]), "rating": fg.get("rating", ""),
               "prev_close": (round(fg["previous_close"])
                              if fg.get("previous_close") is not None else None),
               "prev_week": (round(fg["previous_1_week"])
                             if fg.get("previous_1_week") is not None else None),
               "asof": fg.get("timestamp"), "estimated": False}
        return out
    except Exception as e:
        print(f"[pulse] fear&greed live failed: {e}")
    try:
        snap = json.load(open(CNN_SNAPSHOT, encoding="utf-8"))
        prev = {p["label"]: p["value"] for p in snap.get("previous", [])}
        return {"score": snap["score"], "rating": snap.get("rating", ""),
                "prev_close": prev.get("סגירה קודמת"),
                "prev_week": prev.get("לפני שבוע"),
                "asof": snap.get("timestamp"), "estimated": False,
                "from_snapshot": True}
    except Exception as e:
        print(f"[pulse] fear&greed snapshot failed: {e}")
        return None


def get_apewisdom():
    try:
        d = http_json("https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1")
        rows = []
        for r in d.get("results", [])[:15]:
            rows.append({"ticker": r["ticker"].upper(),
                         "mentions": r.get("mentions"),
                         "mentions_prev": r.get("mentions_24h_ago"),
                         "upvotes": r.get("upvotes")})
        return rows
    except Exception as e:
        print(f"[pulse] apewisdom failed: {e}")
        return []


def get_stocktwits_trending():
    try:
        d = http_json("https://api.stocktwits.com/api/2/trending/symbols.json")
        return [s["symbol"].upper() for s in d.get("symbols", [])[:12]]
    except Exception as e:
        print(f"[pulse] stocktwits trending failed: {e}")
        return []


def get_stocktwits_sentiment(symbol):
    """Bullish/bearish label counts over the symbol's latest messages."""
    try:
        url = ("https://api.stocktwits.com/api/2/streams/symbol/"
               + urllib.parse.quote(symbol) + ".json")
        d = http_json(url)
        bulls = bears = 0
        for m in d.get("messages", [])[:30]:
            s = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
            if s == "Bullish":
                bulls += 1
            elif s == "Bearish":
                bears += 1
        return bulls, bears
    except Exception as e:
        print(f"[pulse] stocktwits stream {symbol} failed: {e}")
        return 0, 0


def get_day_change(ticker):
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               + urllib.parse.quote(ticker, safe="") + "?range=5d&interval=1d")
        d = http_json(url)["chart"]["result"][0]
        closes = [c for c in d["indicators"]["quote"][0]["close"] if c]
        if len(closes) >= 2:
            return round((closes[-1] / closes[-2] - 1) * 100, 2)
    except Exception as e:
        print(f"[pulse] price {ticker} failed: {e}")
    return None


def tavily_search(api_key, query, max_results=6):
    payload = {"api_key": api_key, "query": query, "max_results": max_results,
               "search_depth": "basic", "topic": "news", "days": 3}
    d = http_post_json("https://api.tavily.com/search", payload, timeout=30)
    return d.get("results", [])


def clean_body(text):
    """Strip markdown links/images and boilerplate lines from an extracted
    article body so a capped excerpt carries facts, not navigation junk."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)          # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)        # links -> text
    drop = re.compile(r"^\s*(image\s+\d+|view the|sign up|log ?in|subscribe|"
                      r"advertisement|see more|share\b)", re.I)
    lines = [ln for ln in text.splitlines() if not drop.search(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clip_excerpt(text, cap):
    """Cap an excerpt at a sentence boundary so a truncated tail never
    misreads (e.g. a number cut mid-token reads as a different fact)."""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    cut = max(text.rfind(p, 0, cap) for p in (". ", "! ", "? ", ".\n", "!\n", "?\n"))
    return text[:cut + 1] if cut >= cap // 2 else text[:cap]


def tavily_extract(api_key, urls, timeout=40):
    """Fetch full article bodies for a bounded set of URLs (Tavily extract API,
    same authorized account as search; no Groq tokens). Returns {url: body}.
    Any failure degrades to an empty dict - snippets remain the fallback."""
    out = {}
    if not urls:
        return out
    try:
        d = http_post_json("https://api.tavily.com/extract",
                           {"api_key": api_key, "urls": list(urls)[:6]}, timeout=timeout)
        for r in d.get("results", []):
            body = clean_body((r.get("raw_content") or "").strip())
            # content-free extracts (paywall/nav boilerplate) fall back to the
            # snippet: require real sentences and at least one digit (a market
            # story without a single number is not a usable source body)
            nav_junk = ("Skip to content", "Customer Support", "Remote Login",
                        "Account Settings", "All Rights Reserved")
            if (r.get("url") and body.count(". ") >= 2 and re.search(r"\d", body)
                    and not any(j in body for j in nav_junk)):
                out[r["url"]] = clip_excerpt(body, 1500)
    except Exception as e:
        print(f"[pulse] body extract failed ({type(e).__name__}) - snippets only")
    return out


def get_news(api_key, tickers):
    """Numbered news items N1..Nn with dates where the source gives them."""
    items, seen = [], set()

    def add(results):
        for r in results:
            url = r.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            pub = (r.get("published_date") or "")[:10] or None
            if pub:
                try:
                    date_from_iso(pub)
                except Exception:
                    pub = None  # unparseable API date must not block recovery
            items.append({
                "id": f"N{len(items) + 1}", "title": (r.get("title") or "").strip(),
                "url": url, "source": urllib.parse.urlparse(url).netloc.replace("www.", ""),
                "published": pub,
                "snippet": (r.get("content") or "")[:180]})

    extra_q = [q.strip() for q in os.environ.get("PULSE_EXTRA_QUERIES", "").split("|") if q.strip()]
    for q in NEWS_QUERIES + extra_q:
        try:
            add(tavily_search(api_key, q, 6))
            time.sleep(0.3)
        except Exception as e:
            print(f"[pulse] tavily '{q}' failed: {e}")
    for t in tickers:
        try:
            add(tavily_search(api_key, f"{t} stock news", 3))
            time.sleep(0.3)
        except Exception as e:
            print(f"[pulse] tavily {t} failed: {e}")
    # deterministic date recovery when the API gives no published_date:
    # URL path (/2026/10/30/), then title, then snippet (English + ISO dates)
    today = datetime.now(JER).date()
    for it in items:
        if it["published"]:
            continue
        d, kind = recover_pub_date(it["url"], it["title"], it["snippet"])
        if d and kind in ("url", "title"):
            it["published"] = d.isoformat()
        # unverified-kind dates are intentionally NOT written to published
    # freshness classification
    today = datetime.now(JER).date()
    for it in items:
        it["fresh"] = None
        if it["published"]:
            try:
                d = date_from_iso(it["published"])
                it["fresh"] = (today - d).days * 24 <= WINDOW_HOURS
            except Exception:
                it["published"] = None
    # Body enrichment: full text only for fresh in-window anchor candidates.
    # Root-cause fix: 180-char snippets cannot support results-vs-guidance
    # synthesis; the model hallucinates or goes generic without real facts.
    fresh_urls = [it["url"] for it in items if it["fresh"] is True and it["url"]]
    bodies = tavily_extract(api_key, fresh_urls)
    for it in items:
        if it["url"] in bodies:
            it["body"] = bodies[it["url"]]
    return items


def date_from_iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


# ------------------------------------------------------------------ candidates
def unify_ticker(t):
    return {"GOOGL": "GOOG"}.get(t, t)


def rank_candidates(ape, trending, news):
    """Score discussion leaders. Returns up to 8 stock tickers, best first."""
    scores = {}
    for i, r in enumerate(ape):
        t = unify_ticker(r["ticker"])
        if t in NON_STOCK:
            continue
        m = r.get("mentions") or 0
        mp = r.get("mentions_prev") or 0
        vel = (m / mp) if mp else (2.0 if m >= 50 else 1.0)
        scores.setdefault(t, 0.0)
        scores[t] += m * min(vel, 3.0) + max(0, 15 - i) * 10
    for i, t in enumerate(trending):
        t = unify_ticker(t)
        if t in NON_STOCK:
            continue
        scores.setdefault(t, 0.0)
        scores[t] += max(0, 12 - i) * 15
    titles = " ".join(n["title"].upper() for n in news)
    for t in list(scores):
        hits = len(re.findall(rf"\b{re.escape(t)}\b", titles))
        scores[t] += hits * 60
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [t for t, _ in ranked[:8]]


# ------------------------------------------------------------------ enums (measured)
def retail_label(bulls, bears):
    tot = bulls + bears
    if tot < 10:
        return "אין מספיק נתונים", None
    ratio = bulls / tot
    if ratio >= 0.65:
        return "שורי", ratio
    if ratio <= 0.35:
        return "דובי", ratio
    return "נייטרלי", ratio


def hype_label(ape):
    pairs = [(r["mentions"], r["mentions_prev"]) for r in ape[:10]
             if r.get("mentions") and r.get("mentions_prev")]
    if len(pairs) < 3:
        return "אין נתונים"
    vel = sum(m / p for m, p in pairs) / len(pairs)
    if vel >= 1.5:
        return "גבוה"
    if vel <= 0.75:
        return "נמוך"
    return "בינוני"


def momentum_label(indices):
    weeks = [i["week_pct"] for i in indices
             if i.get("week_pct") is not None and i["sym"] != "VIX"]
    if not weeks:
        return "אין נתונים"
    avg = sum(weeks) / len(weeks)
    if avg >= 1.0:
        return "מתחזק"
    if avg <= -1.0:
        return "נחלש"
    return "יציב"


# ------------------------------------------------------------------ fact pack + model
def build_facts(indices, fg, ape, stocks, news, history_note):
    """Numbered fact lines F1..Fn - the only numbers the model may use."""
    facts = []

    def add(text):
        facts.append(f"F{len(facts) + 1}: {text}")

    for ix in indices:
        wk = f", שבוע {ix['week_pct']:+.2f}%" if ix.get("week_pct") is not None else ""
        add(f"{ix['name']} ({ix['sym']}): {ix['value']:,.2f}, יומי {ix['day_pct']:+.2f}%{wk} (נכון ל-{ix['asof']})")
    if fg:
        prev = f", אתמול {fg['prev_close']}" if fg.get("prev_close") is not None else ""
        add(f"מדד הפחד והחמדה של CNN: {fg['score']} ({fg['rating']}){prev}")
    for s in stocks:
        line = f"{s['ticker']}: אזכורים ב-wallstreetbets "
        if s.get("mentions") is not None:
            line += f"{s['mentions']}"
            if s.get("mentions_prev") is not None:
                line += f" (אתמול {s['mentions_prev']})"
        else:
            line += "לא זמין"
        if s.get("bulls") is not None and (s["bulls"] + s["bears"]) > 0:
            line += f", Stocktwits: {s['bulls']} שוריים מול {s['bears']} דוביים"
        if s.get("day_pct") is not None:
            line += f", שינוי יומי {s['day_pct']:+.2f}%"
        add(line)
    if history_note:
        add(history_note)
    for n in news:
        date_s = n["published"] or "ללא תאריך"
        if n.get("fresh") is True:
            tag = "בחלון 48 השעות - מותר לעגן בו סיפור"
        elif n.get("fresh") is False:
            tag = "ישן - אסור לעגן בו סיפור"
        else:
            tag = "ללא תאריך מאומת - אסור לעגן בו סיפור"
        line = f"חדשות {n['id']} [{n['source']}, {date_s}, {tag}]: {n['title']}. {n['snippet']}"
        if n.get("body"):
            line += f" || גוף הכתבה: {clip_excerpt(n['body'], 1000)}"
        add(line)
    return facts



SCHEMA_SIMPLE = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "stories": {"type": "array", "maxItems": 4, "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "impact": {"type": "string", "enum": ["חיובית", "שלילית", "מעורבת"]},
                "affected": {"type": "string"},
                "news_refs": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "summary", "impact", "affected", "news_refs"],
            "additionalProperties": False}},
        "alerts": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "news_refs": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "news_refs"],
            "additionalProperties": False}},
        "watchlist": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "why": {"type": "string"}},
            "required": ["ticker", "why"],
            "additionalProperties": False}}},
    "required": ["intro", "stories", "alerts", "watchlist"],
    "additionalProperties": False}

PROMPT_SIMPLE = """כתוב סקירת חדשות שוק קצרה בעברית פשוטה, למשקיע שקורא אותה בדקה-שתיים.
כללים מחייבים:
1. הכל בעברית זורמת בלבד (שמות חברות וטיקרים מותר באנגלית). אין מילים באנגלית בטקסט העברי.
2. intro: משפט אחד או שניים - מה קורה בשוק עכשיו. אסור לציין מדדים או אחוזי שינוי של מדדים (נאסד"ק, S&P 500, דאו, נאסד"ק 100) - לא בפתיח ולא באף סיפור; הנתונים האלה כבר מוצגים באתר.
2א. כל חברה, טיקר או אירוע שאתה מזכיר בסיפור חייבים להופיע במפורש בפריטי החדשות שאליהם הסיפור מפנה ב-news_refs. אם העובדה לא שם - אל תכתוב אותה.
3. stories: 2-4 סיפורי החדשות החשובים מהמקורות המצורפים. לכל סיפור: title קצר, summary של 2-3 משפטים שמסבירים מה קרה ולמה זה משנה, impact, affected (חברות/סקטורים), ו-news_refs עם מזהי הפריטים שהסיפור מבוסס עליהם. כל סיפור חייב לעגן לפחות בפריט אחד המתויג "בחלון 48 השעות". אל תספר את אותו אירוע פעמיים מזוויות שונות - אם כל החדשות סובבות אותו נושא, החזר פחות סיפורים. תזכורת קריטית: אין להזכיר מדדים בשום סיפור (כלל 2 חל גם על stories). אם אתה מצטט פריט שבכותרתו 'Live Updates' - אל תכלול ממנו אחוזי שינוי כלל, רק את נושא האירוע.
3ג. אם בפריטים העדכניים יש אירוע גיאופוליטי מהותי (מלחמה, הפסקת אש, סנקציות, בחירות מרכזיות) - כלול אותו כאחד הסיפורים, עם משפט אחד על הערוץ האפשרי לשוק (נפט, שילוח, ריבית). הבחן בין אירוע שקרה בפועל ומתוארך במקור לבין פרשנות - פרשנות תסומן במילה "הערכה". אל תמציא סיפור גיאופוליטי כשאין אירוע כזה בפריטים.
3ח. אסור להוסיף הסבר, סיבה או רקע שלא כתובים במפורש בפריטי המקור המצוטטים. אם הסיבה לירידה או לעלייה לא כתובה במקור - תאר מה קרה בלי לנחש למה. בפרט: אסור לכתוב "משקף/משקפת חשש", "דאגות" או נימוק פסיכולוגי דומה אלא אם בפריט המצוטט מופיעות מילות חשש מפורשות (concern, fear, worry, demand, doubt).
3ט. אם פריט המקור הוא עדכון חי או תוך-יומי (למשל "Live Updates") - כל אחוז שינוי ממנו חייב תיוג זמן מפורש, למשל "בשיא התוך-יומי" או "במהלך המסחר". אסור להציג אותו כשינוי סגירה.
3י. כיסה את כל הנושאים השונים בפריטים העדכניים - אל תסתפק בפריט אחד אם יש כמה סיפורים נפרדים.
4. מספרים רק כפי שמופיעים מילולית בפריטי החדשות שהסיפור מצטט ב-news_refs (לא בפריטים אחרים). אסור בהחלט לשלוף מספרים מהידע הכללי שלך - למשל מספרי תחזית ההכנסות של אינטל (15-17 מיליארד דולר) לא מופיעים בפריטים המצוטטים ולכן אסור לכתוב אותם. אם מספר מתייחס לנקודת זמן מסוימת (מסחר מאוחר, מסחר מוקדם, תוך כדי מסחר) - ציין זאת במפורש, ואל תציג אותו כשינוי הסגירה.
5. alerts: אירועים מתוכננים עם תאריך מפורש DD.MM (למשל פרסום דוחות) שמופיעים במקורות. אם אין - החזר רשימה ריקה.
6. watchlist: עד 3 טיקרים מהסיפורים + משפט קצר למה לעקוב.
7. הסקירה כללית לגמרי - בלי קשר לתיק מניות כלשהו, ובלי המלצות קניה או מכירה."""

SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "sentiment_label": {"type": "string", "enum": ["שורי", "נייטרלי", "דובי"]},
        "sentiment_score": {"type": "integer"},
        "sentiment_driver": {"type": "string"},
        "sentiment_basis": {"type": "string"},
        "weekly_note": {"type": "string"},
        "stories": {"type": "array", "maxItems": 5, "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "impact": {"type": "string", "enum": ["חיובית", "שלילית", "מעורבת"]},
                "affected": {"type": "string"},
                "news_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            "required": ["title", "summary", "impact", "affected", "news_refs"],
            "additionalProperties": False}},
        "stock_notes": {"type": "array", "maxItems": 5, "items": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "reason": {"type": "string"},
                "catalyst": {"type": "string"}},
            "required": ["ticker", "reason", "catalyst"],
            "additionalProperties": False}},
        "alerts": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "news_refs": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
            "required": ["text", "news_refs"],
            "additionalProperties": False}},
        "watchlist": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "why": {"type": "string"}},
            "required": ["ticker", "why"],
            "additionalProperties": False}},
    },
    "required": ["intro", "sentiment_label", "sentiment_score", "sentiment_driver",
                 "sentiment_basis", "weekly_note", "stories", "stock_notes",
                 "alerts", "watchlist"],
    "additionalProperties": False,
}

PROMPT_HE = """אתה אנליסט שוק הון. כתוב סקירת שוק יומית קצרה בעברית (RTL) למשקיע פרטי, על בסיס עובדות בלבד.

חוקים מחייבים:
1. השתמש רק במספרים שמופיעים בעובדות הממוספרות (F1..Fn). אסור להמציא או לאמוד מספרים חדשים (מחירים, אחוזים, כמויות, תאריכים). תאריכים מותרים רק אם מופיעים בכותרת חדשה או בעובדה.
2. טיקרים מותרים רק מתוך רשימת המועמדים שסופקה.
3. לכל סיפור או התראה: ציין ב-news_refs את מזהי החדשות (N1..Nn) שהם מבוססים עליהן. סיפור חייב לעגן לפחות בפריט אחד שמתויג "בחלון 48 השעות" - אסור לעגן סיפור בפריט ישן או חסר תאריך. אם יש פחות מ-3 נושאים בעיגון עדכני, החזר פחות סיפורים. כל מספר בסיפור (אחוזים, דולרים, תוצאות) חייב להופיע בגוף הכתבה/כותרת/תקציר של הפריט שאליו הסיפור עגון.
4. אם אין מספיק מידע לסעיף - כתוב "אין נתונים אמינים" באותו שדה. אל תמלא.
5. stories: 3-4 סיפורים מרכזיים, מדורגים לפי חשיבות (5 רק אם באמת קיימים 5 נושאים חזקים). כל סיפור חייב לעגן באירוע עדכני ספציפית מתוארך מהחדשות - לא רקע מאקרו כללי ולא ניסוח חוזר של הירידה הכללית. העדף זוויות מתוארכות forward-looking (דוחות מתוכננים עם תאריך) על פני תיאור חוזר של המכירה. הבחן בין תוצאות שפורסמו בפועל לבין תחזית/גיידנס עתידי - אל תמזג אותם לאירוע אחד. title קצר, summary משפט אחד, affected חברות/סקטורים מושפעים.
6. stock_notes: בדיוק עבור הטיקרים שצורפו. reason = הסיבה הספציפית של אותה חברה מהחדשות (מה קרה: דוח, הודעה, שינוי מדיניות) - אסור לצטט את האחוז היומי עצמו (הוא מוצג במסך) ואסור נרטיב שוק כללי ("השוק ירד"). catalyst = האירוע הקרוב הספציפי לאותה חברה מהחדשות עם תאריך DD.MM אם יש, אחרת "אין נתונים אמינים".
7. alerts: עד 3 אירועים קרובים בעלי משמעות (דוחות, ריבית, רגולציה) שמופיעים בחדשות.
8. watchlist: עד 3 טיקרים מהמועמדים, או רשימה ריקה. why = על מה לעקוב ומה המשמעות: אירוע/רמה קרובה + ההשלכה אם מתממש (למשל: "דוח ב-3.11: תחזית חזקה תתמוך בהמשך העלייה, אכזבה תלחץ על כל הסקטור"). אסור why כללי כמו "חשש מהוצאות AI".
9. intro: משפט אחד - מה מניע את השוק עכשיו.
10. sentiment_label/sentiment_score: ההערכה שלך לסנטימנט הכללי (0-100), sentiment_driver: משפט אחד. sentiment_basis: הבחנה קצרה בין נתונים מדידים להערכה.
11. weekly_note: {weekly_instruction}
12. סך הכול עד כ-450 מילים. בלי חזרות, בלי מבואות.
13. הסיפורים חייבים להיות נושאים נפרדים: אסור שני סיפורים סביב אותו טיקר או אותה סיבת ירידה. עדיף 3 סיפורים שונים באמת על פני 5 חופפים. כל סיפור חייב נקודת מידע ייחודית מהחדשות שלו (לא "טכנולוגיה ירדה" כללי).
14. אסור להשתמש במילים "היום", "מחר" או "אתמול" בטקסט. תאריכים תמיד במפורש בפורמט DD.MM (למשל 3.11).
15. alerts: רק אירועים עתידיים מתוארכים (דוחות, החלטות ריבית, FDA) שמופיעים בחדשות, וכל התראה חייבת לכלול תאריך DD.MM מפורש.
16. הניואנסים חייבים להיות עקביים עם המספרים: אם מניה ירדה 4%, אל תכתוב 2%; אם מדד הפחד ירד (פחד עולה), אל תכתוב שהמדד עלה.
17. אל תייחס סיבות שלא מופיעות בחדשות (למשל "מחסור בשבבים") אם הכותרות לא אומרות זאת.
"""


def allowed_numbers(facts):
    """Strings of every numeric form the model may legitimately use."""
    allow = set(STRUCTURAL_NUMBERS) | {"2024", "2025", "2026", "2027"}
    for line in facts:
        for tok in re.findall(r"-?\d[\d,]*(?:\.\d+)?", line):
            v = strip_zeros(tok.replace(",", ""))
            try:
                f = float(v)
            except ValueError:
                continue
            for fmt in (f"{f:.2f}", f"{f:.1f}", f"{f:.0f}"):
                allow.add(fmt)
                if fmt.startswith("-"):
                    allow.add(fmt[1:])
                    allow.add("+" + fmt[1:])
            allow.add(v)
            allow.add("+" + v)
    return allow


def strip_zeros(v):
    """03.11 -> 3.11 ; 05 -> 5 ; keep plain 0 and decimals like 0.5 intact."""
    sign = ""
    if v.startswith(("+", "-")):
        sign, v = v[0], v[1:]
    if "." in v:
        a, b = v.split(".", 1)
        a = a.lstrip("0") or "0"
        v = f"{a}.{b}"
    elif v != "0":
        v = v.lstrip("0") or "0"
    return sign + v


def allowed_dates(facts):
    """DD.MM forms of every date the fact pack itself carries (news dates etc.)."""
    out = set()
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    for f in facts:
        for y, m, d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", f):
            out.add(f"{int(d)}.{int(m)}")  # ISO dates from news published fields
        for d, m in re.findall(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b", f):
            out.add(f"{int(d)}.{int(m)}")
        for m in re.finditer(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+(\d{1,2})", f, re.I):
            out.add(f"{int(m.group(2))}.{months.index(m.group(1).lower()[:3]) + 1}")
    return out


DATE_TOK = re.compile(r"^\d{1,2}\.\d{1,2}$")


def find_bad_numbers(text, allow, allow_dates=()):
    bad = []
    for tok in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?%?", text):
        v = strip_zeros(tok.replace(",", "").rstrip("%"))
        core = v.lstrip("+-")
        if DATE_TOK.match(core):
            d, m = core.split(".")
            if f"{int(d)}.{int(m)}" in allow_dates:
                continue  # a date the fact pack itself mentions
            # a DD.MM date not in the pack still counts as an ungrounded number
        if v in allow or core in allow or ("+" + core) in allow or ("-" + core) in allow:
            continue
        bad.append(tok)
    return bad


NOT_TICKERS = {"Q1", "Q2", "Q3", "Q4", "FED", "GDP", "CPI", "PPI", "ETF",
               "AI", "US", "USA", "IPO", "CEO", "SEC", "EPS", "YOY", "QOQ",
               "ATH", "FDA", "USD", "EUR", "ILS", "WSB", "GPU", "EV", "FX"}


HEB = re.compile(r"[\u0590-\u05ff]")
IDX_RE = re.compile(r"Nasdaq|נאסד|S\s*&\s*P|Dow Jones|דאו ג'ונס|Russell|ראסל", re.I)


def find_bad_tickers(text, allowed_tickers):
    bad = []
    for tok in re.findall(r"\b[A-Z][A-Z0-9.]{1,5}\b", text):
        t = tok.rstrip(".")
        if re.fullmatch(r"[NF]\d+", t):
            continue  # internal fact/news reference, not a ticker
        if t in NOT_TICKERS:
            continue  # macro/quarter acronym, not a ticker
        if t not in allowed_tickers and t not in TICKER_WHITELIST:
            bad.append(tok)
    return bad


def word_count(out):
    texts = [out.get("intro", ""), out.get("sentiment_driver", ""),
             out.get("sentiment_basis", ""), out.get("weekly_note", "")]
    for s in out.get("stories", []):
        texts += [s.get("title", ""), s.get("summary", ""), s.get("affected", "")]
    for s in out.get("stock_notes", []):
        texts += [s.get("reason", ""), s.get("catalyst", "")]
    for a in out.get("alerts", []):
        texts.append(a.get("text", ""))
    for w in out.get("watchlist", []):
        texts.append(w.get("why", ""))
    return sum(len(t.split()) for t in texts)


def norm_refs(refs):
    """Normalize model refs: split combined strings ('N1, N2'), uppercase, dedupe."""
    out = []
    for r in refs or []:
        for tok in re.findall(r"[Nn]\d+", str(r)):
            t = tok.upper()
            if t not in out:
                out.append(t)
    return out


def validate_output(out, facts, candidate_tickers, news_ids, fg_score,
                  news_fresh_ids=None):
    """Return list of violations (empty = ok). Deterministic grounding checks."""
    v = []
    allow = allowed_numbers(facts)
    allow_dates = allowed_dates(facts)
    allowed_tickers = set(candidate_tickers)

    def check_text(text, where):
        for b in find_bad_numbers(text or "", allow, allow_dates):
            v.append(f"{where}: מספר לא מבוסס '{b}'")
        for b in find_bad_tickers(text or "", allowed_tickers):
            v.append(f"{where}: טיקר לא מוכר '{b}'")

    check_text(out.get("intro"), "intro")
    check_text(out.get("sentiment_driver"), "driver")
    check_text(out.get("sentiment_basis"), "basis")
    check_text(out.get("weekly_note"), "weekly")
    label = out.get("sentiment_label")
    if label not in ("שורי", "נייטרלי", "דובי"):
        v.append("sentiment_label לא חוקי")
    # measured-data guardrails on the model's estimate
    day_vals = [float(f.split("יומי ")[1].split("%")[0])
                for f in facts if "יומי " in f and "%" in f.split("יומי ")[1]]
    if day_vals:
        avg_day = sum(day_vals[:5]) / min(len(day_vals), 5)
        if avg_day < -1.5 and label == "שורי":
            v.append("סנטימנט שורי סותר מדדים יומיים חד-שליליים")
        if avg_day > 1.5 and label == "דובי":
            v.append("סנטימנט דובי סותר מדדים יומיים חד-חיוביים")
    if fg_score is not None and isinstance(out.get("sentiment_score"), int):
        if abs(out["sentiment_score"] - fg_score) > 20:
            v.append(f"ציון סנטימנט {out['sentiment_score']} רחוק מ-CNN F&G {fg_score}")
    rel = re.compile(r"(היום|מחר|אתמול)")

    def check_no_rel(text, where):
        if text and rel.search(text):
            v.append(f"{where}: תאריך יחסי אסור (היום/מחר/אתמול) - נדרש DD.MM")

    check_no_rel(out.get("intro"), "intro")
    check_no_rel(out.get("sentiment_driver"), "driver")


    def check_heb(text, where):
        if text and not HEB.search(text):
            v.append(f"{where}: טקסט חייב להיות בעברית")
    check_no_rel(out.get("sentiment_basis"), "basis")
    _intro = (out.get("intro") or "").strip()
    if len(_intro.split()) < 6 or not _intro.endswith((".", "!", "?", ":", '"')):
        v.append("intro: חייב להיות משפט מלא (6+ מילים, סימן סיום)")
    fresh_ids = news_fresh_ids if news_fresh_ids is not None else set()
    for i, s in enumerate(out.get("stories", [])):
        check_text(s.get("title"), f"story{i}.title")
        check_text(s.get("summary"), f"story{i}.summary")
        check_heb(s.get("title"), f"story{i}.title")
        check_heb(s.get("summary"), f"story{i}.summary")
        check_text(s.get("affected"), f"story{i}.affected")
        check_no_rel(s.get("title"), f"story{i}.title")
        check_no_rel(s.get("summary"), f"story{i}.summary")
        refs = norm_refs(s.get("news_refs"))
        s["news_refs"] = refs
        if not refs or any(r not in news_ids for r in refs):
            v.append(f"story{i}: הפניות חדשות לא חוקיות")
        elif not any(r in fresh_ids for r in refs):
            v.append(f"story{i}: אין מקור עדכני בחלון - סיפור כללי/ישן")
    for i, s in enumerate(out.get("stock_notes", [])):
        if s.get("ticker") not in allowed_tickers:
            v.append(f"stock{i}: טיקר '{s.get('ticker')}' לא ברשימת המועמדים")
        check_text(s.get("reason"), f"stock{i}.reason")
        check_text(s.get("catalyst"), f"stock{i}.catalyst")
        check_no_rel(s.get("reason"), f"stock{i}.reason")
        check_heb(s.get("reason"), f"stock{i}.reason")
        check_no_rel(s.get("catalyst"), f"stock{i}.catalyst")
    for i, a in enumerate(out.get("alerts", [])):
        # the alert's own event date is required by design - mask it before
        # number-grounding (the source-dating rule below covers it)
        check_text(re.sub(r"\d{1,2}\.\d{1,2}", "תאריך", a.get("text") or "", count=1),
                   f"alert{i}")
        check_no_rel(a.get("text"), f"alert{i}")
        if not re.search(r"\d{1,2}\.\d{1,2}", a.get("text") or ""):
            v.append(f"alert{i}: חסר תאריך DD.MM מפורש")
        refs = norm_refs(a.get("news_refs"))
        a["news_refs"] = refs
        if not refs or any(r not in news_ids for r in refs):
            v.append(f"alert{i}: הפניות חדשות לא חוקיות")
        else:
            # past events need a source dated inside the window; future events
            # (e.g. upcoming earnings) only need a real dated source
            dm = re.search(r"(\d{1,2})\.(\d{1,2})", a.get("text") or "")
            is_future = False
            if dm:
                today = datetime.now(JER).date()
                try:
                    ev = datetime(today.year, int(dm.group(2)), int(dm.group(1)), tzinfo=JER).date()
                    if (ev - today).days > 180:
                        ev = datetime(today.year - 1, ev.month, ev.day, tzinfo=JER).date()
                    is_future = ev >= today
                except ValueError:
                    pass
            if not is_future and not any(r in fresh_ids for r in refs):
                v.append(f"alert{i}: אין מקור חדשות מתוארך בחלון לתיארוך האירוע")
    for i, w in enumerate(out.get("watchlist", [])):
        if w.get("ticker") not in allowed_tickers:
            v.append(f"watch{i}: טיקר '{w.get('ticker')}' לא ברשימת המועמדים")
        check_text(w.get("why"), f"watch{i}.why")
        check_no_rel(w.get("why"), f"watch{i}.why")
    wc = word_count(out)
    if wc > MAX_WORDS:
        v.append(f"ארוך מדי: {wc} מילים (מעל {MAX_WORDS})")
    return v


def call_groq(api_key, prompt, facts, schema=None):
    schema = schema or SCHEMA
    facts_text = "\n".join(facts)
    now = datetime.now(JER)
    date_ctx = (f"התאריך כעת: {now.day}.{now.month}.{now.year}, "
                f"יום {'שני שלישי רביעי חמישי שישי שבת ראשון'.split()[now.weekday() if now.weekday()<6 else 6] if False else ['שני','שלישי','רביעי','חמישי','שישי','שבת','ראשון'][now.weekday()]}. ")
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "user",
             "content": date_ctx + prompt + "\nעובדות ממוספרות:\n" + facts_text}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "market_pulse", "strict": True,
                                            "schema": schema}},
        "max_completion_tokens": 4096,  # prompt ~2.8k + completion must stay under the 8k free-tier TPM
        "temperature": 0.2,
        "reasoning_effort": "low",  # docs-supported on gpt-oss; frees budget for content
    }
    try:
        d = http_post_json("https://api.groq.com/openai/v1/chat/completions", body,
                           timeout=120,
                           headers={"Authorization": f"Bearer {api_key}"})
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        raise RuntimeError(f"HTTP Error {e.code}: {e.reason} {detail}") from None
    content = d["choices"][0]["message"]["content"]
    return json.loads(content), d.get("usage", {})


# ------------------------------------------------------------------ fallback
MONTHS_EN = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def title_is_stale(title, today=None):
    """A headline carrying an explicit old date ('Jan 27', 'Dec 26, 2025') is stale
    even when the feed gave no published date."""
    today = today or datetime.now(JER).date()
    m = re.search(rf"\b({MONTHS_EN})\w*\.?\s+(\d{{1,2}})(?:,?\s*(\d{{4}}))?", title or "")
    if not m:
        return False
    mon = "jan feb mar apr may jun jul aug sep oct nov dec".split().index(m.group(1).lower()[:3]) + 1
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else (today.year if mon <= today.month else today.year - 1)
    try:
        return (today - datetime(year, mon, day, tzinfo=JER).date()).days > 4
    except ValueError:
        return False


def recover_date(text):
    """Best-effort date from free text or a URL path. Returns (date, kind):
    kind 'url' counts as original publication; 'title'/'snippet' are
    UNVERIFIED kind (may be an event date) and never prove freshness."""
    if not text:
        return None
    today = datetime.now(JER).date()
    m = re.search(r"(20\d{2})[-/](\d{2})[-/](\d{2})", text)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=JER).date()
            return d if d <= today else None
        except ValueError:
            return None
    m = re.search(rf"\b({MONTHS_EN})\w*\.?\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?", text)
    if m:
        mon = "jan feb mar apr may jun jul aug sep oct nov dec".split().index(m.group(1).lower()[:3]) + 1
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else (today.year if mon <= today.month else today.year - 1)
        try:
            d = datetime(year, mon, day, tzinfo=JER).date()
            return d if d <= today else None
        except ValueError:
            return None
    return None


def recover_pub_date(url, title, snippet):
    """Publication-date recovery with explicit date-kind separation.
    Only API-provided dates and URL-path dates count as publication;
    title/snippet dates are recorded as unverified-kind and never
    prove freshness (they may be event dates)."""
    d = recover_date(url)
    if d:
        return d, "url"
    # only a PARENTHESIZED title date is a publisher publication label
    # ("Stock Market Today (Oct. 29, 2026)"); a free-floating title date is
    # usually an event date ("Apple to report November 2") and must not
    # prove freshness.
    for seg in re.findall(r"\(([^)]*)\)", title or ""):
        d = recover_date(seg)
        if d:
            return d, "title"
    d = recover_date(snippet)
    if d:
        return d, "unverified"
    return None, None


def build_fallback(indices, fg, stocks, news, weekly_note, slot, now):
    """Template-only review: measured numbers + headlines, zero model prose."""
    fresh = [n for n in news if n["fresh"] is True][:5]
    if len(fresh) < 3:
        fresh += [n for n in news
                  if n["fresh"] is None and not title_is_stale(n["title"])][:5 - len(fresh)]
    stories = [{"title": n["title"], "summary": "", "impact": "מעורבת",
                "affected": "", "source_url": n["url"], "source_name": n["source"],
                "published": n.get("published")} for n in fresh]
    day_avgs = [i["day_pct"] for i in indices if i["sym"] != "VIX"]
    avg = sum(day_avgs) / len(day_avgs) if day_avgs else 0.0
    label = "דובי" if avg <= -1 else ("שורי" if avg >= 1 else "נייטרלי")
    return {
        "version": 1, "slot": slot,
        "generated_at": now.isoformat(), "window_hours": WINDOW_HOURS,
        "mode": "measured_only",
        "intro": "סקירה מבוססת נתונים מדידים בלבד, ללא ניתוח מילולי בריצה זו.",
        "indices": indices, "fear_greed": fg,
        "sentiment": {"label": label,
                      "score": fg["score"] if fg else None,
                      "driver": "", "estimated": True},
        "stories": stories, "stocks": stocks,
        "retail": None, "institutional": "אין מספיק נתונים",
        "hype": None, "momentum": None, "sentiment_basis": "",
        "alerts": [], "watchlist": [],
        "weekly": weekly_note or "אין עדיין בסיס להשוואה שבועית.",
        "sources": [{"name": n["source"], "url": n["url"]} for n in fresh],
    }


# ------------------------------------------------------------------ history
def weekly_note(fg):
    """Measured weekly comparison. CNN's own 1-week-ago value wins; local history
    (once it has a >=6-day-old entry) is the backup; otherwise an honest empty."""
    if fg and fg.get("score") is not None and fg.get("prev_week") is not None:
        delta = fg["score"] - fg["prev_week"]
        trend = "משתפר" if delta > 3 else ("מתדרדר" if delta < -3 else "יציב")
        return (f"מדד הפחד והחמדה {fg['score']} לעומת {fg['prev_week']} לפני שבוע "
                f"(לפי CNN) - הסנטימנט {trend}.")
    return weekly_note_from_history(fg["score"] if fg else None)[0]


def weekly_note_from_history(fg_score):
    try:
        hist = json.load(open(HISTORY_FILE, encoding="utf-8"))
    except Exception:
        return None, None
    today = datetime.now(JER).date()
    old = None
    for h in hist:
        try:
            d = date_from_iso(h["date"])
        except Exception:
            continue
        age = (today - d).days
        if age >= 6 and (old is None or d > date_from_iso(old["date"])):
            old = h
    if not old or old.get("fng") is None or fg_score is None:
        return None, None
    delta = fg_score - old["fng"]
    trend = "משתפר" if delta > 3 else ("מתדרדר" if delta < -3 else "יציב")
    note = (f"מדד הפחד והחמדה {fg_score} לעומת {old['fng']} לפני כשבוע "
            f"({old['date']}) - הסנטימנט {trend}.")
    return note, old


def append_history(fg_score, indices):
    today = datetime.now(JER).date().isoformat()
    try:
        hist = json.load(open(HISTORY_FILE, encoding="utf-8"))
    except Exception:
        hist = []
    hist = [h for h in hist if h.get("date") != today]
    entry = {"date": today, "fng": fg_score}
    spx = next((i for i in indices if i["sym"] == "GSPC"), None)
    if spx:
        entry["spx"] = spx["value"]
    hist.append(entry)
    hist = hist[-45:]
    tmp = HISTORY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    os.replace(tmp, HISTORY_FILE)


def validate_simple(out, facts, news_ids, fresh_ids):
    """Deterministic grounding for the simple review: Hebrew, grounded numbers,
    valid fresh-anchored refs, dated alerts."""
    v = []
    allow = allowed_numbers(facts)
    allow_dates = allowed_dates(facts)

    def bad_nums(text, where):
        for b in find_bad_numbers(text or "", allow, allow_dates):
            v.append(f"{where}: מספר לא מבוסס '{b}'")

    def heb(text, where):
        if text and not HEB.search(text):
            v.append(f"{where}: טקסט חייב להיות בעברית")

    intro = (out.get("intro") or "").strip()
    heb(intro, "intro")
    bad_nums(intro, "intro")
    if len(intro.split()) < 5 or not intro.endswith((".", "!", "?", ":")):
        v.append("intro: חייב להיות משפט מלא")
    if IDX_RE.search(intro):
        v.append("intro: אזכור מדד - נתוני המדדים כבר מוצגים באתר")
    stories = out.get("stories", [])
    if not stories:
        v.append("stories: רשימה ריקה")
    for i, s in enumerate(stories):
        heb(s.get("title"), f"story{i}.title")
        heb(s.get("summary"), f"story{i}.summary")
        bad_nums(s.get("title"), f"story{i}.title")
        bad_nums(s.get("summary"), f"story{i}.summary")
        if IDX_RE.search((s.get("title") or "") + " " + (s.get("summary") or "")):
            v.append(f"story{i}: אזכור מדד - נתוני המדדים כבר מוצגים באתר")
        refs = [r for r in (s.get("news_refs") or []) if r in news_ids]
        if not refs or not any(r in fresh_ids for r in refs):
            v.append(f"story{i}: אין מקור עדכני בחלון")
        else:
            cited_facts = [str(f) for f in facts
                           if any(f"חדשות {r} " in str(f) for r in refs)]
            cited = " ".join(cited_facts)
            cited_allow = allowed_numbers(cited_facts)
            story_txt = (s.get("title") or "") + " " + (s.get("summary") or "")
            for b in find_bad_numbers(story_txt, cited_allow, allow_dates):
                v.append(f"story{i}: מספר שלא במקורות המצוטטים '{b}'")
            if re.search(r"חשש|דאגה|דאגות", story_txt) and not re.search(
                    r"concern|fear|worr|demand|doubt|skeptic", cited, re.I):
                v.append(f"story{i}: נימוק חשש/דאגה ללא בסיס במקורות המצוטטים")
            if re.search(r"Live Updates|premarket|after[- ]hours|intraday", cited, re.I) \
                    and re.search(r"\d", story_txt) and not re.search(
                    r"תוך[- ]יומי|במהלך המסחר|במסחר המוקדם|במסחר המאוחר|בשיא|טרום|לאחר המסחר", story_txt):
                v.append(f"story{i}: מספר ממקור תוך-יומי ללא תיוג זמן")
            for ent in re.findall(r"[A-Z][a-zA-Z&]+(?:\s[A-Z][a-zA-Z&]+)*",
                                  (s.get("title") or "") + " " + (s.get("summary") or "")):
                if len(ent) < 3 or ent in ("IPO", "ET", "Q1", "Q2", "Q3", "Q4"):
                    continue
                if ent not in cited:
                    v.append(f"story{i}: הישות '{ent}' לא מופיעה במקורות המצוטטים")
    for i, a in enumerate(out.get("alerts", [])):
        heb(a.get("text"), f"alert{i}")
        bad_nums(re.sub(r"\d{1,2}\.\d{1,2}", "תאריך", a.get("text") or "", count=1), f"alert{i}")
        if not re.search(r"\d{1,2}\.\d{1,2}", a.get("text") or ""):
            v.append(f"alert{i}: חסר תאריך DD.MM")
        if not any(r in news_ids for r in (a.get("news_refs") or [])):
            v.append(f"alert{i}: הפניה לא חוקית")
    for i, w in enumerate(out.get("watchlist", [])):
        heb(w.get("why"), f"watch{i}.why")
        bad_nums(w.get("why"), f"watch{i}.why")
    return v


# ------------------------------------------------------------------ main run
def load_latest():
    try:
        return json.load(open(LATEST_FILE, encoding="utf-8"))
    except Exception:
        return None


def run(now=None, force=False):
    now = now or datetime.now(JER)
    slot = current_slot(now)
    latest = load_latest()
    if not force and not needs_regen(latest, now):
        print(f"[pulse] review for slot {slot} is fresh - keeping existing")
        return "skip"

    groq_key = os.environ.get("GROQ_API_KEY")
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not groq_key or not tavily_key:
        print("[pulse] API keys missing (GROQ_API_KEY / TAVILY_API_KEY) - keeping existing review")
        return "no-keys"

    print(f"[pulse] generating review for slot {slot}")
    # Simple review (user spec 2026-10-31 18:54): news-only, 1-2 minute read,
    # no market-picture duplication, no portfolio link. Index/social feeds
    # intentionally skipped.
    # major market movers as retrieval seeds (news-only, portfolio-independent)
    news = get_news(tavily_key, ["INTC", "RDDT", "NVDA", "AMD", "AMZN", "GOOG"])
    with open("/tmp/pulse_news_debug.json", "w", encoding="utf-8") as _f:
        json.dump(news, _f, ensure_ascii=False, indent=1)  # post-hoc verification
    news = news[:16]
    facts = build_facts([], None, [], [], news, None)
    news_ids = {n["id"] for n in news}
    fresh_ids = {n["id"] for n in news if n["fresh"] is True}
    try:
        with open("/tmp/pulse_facts_debug.json", "w", encoding="utf-8") as _f:
            json.dump(facts, _f, ensure_ascii=False, indent=1)
    except Exception:
        pass

    out = None
    violations = []
    for attempt in (1, 2):  # one generation + at most one repair pass
        prompt = PROMPT_SIMPLE
        if attempt == 2 and violations:
            prompt += ("\nבניסיון הקודם נמצאו השגיאות הבאות. תקן אותן בלבד:\n- "
                       + "\n- ".join(violations[:12]))
        try:
            out, usage = call_groq(groq_key, prompt, facts, schema=SCHEMA_SIMPLE)
            print(f"[pulse] groq attempt {attempt} ok, usage={usage}")
            try:
                with open("/tmp/pulse_candidate_debug.json", "w", encoding="utf-8") as _f:
                    json.dump({"attempt": attempt, "usage": usage, "output": out},
                              _f, ensure_ascii=False, indent=1)
            except Exception:
                pass
        except Exception as e:
            print(f"[pulse] groq attempt {attempt} failed: {e}")
            out = None
            break
        violations = validate_simple(out, facts, news_ids, fresh_ids)
        if not violations:
            break
        print(f"[pulse] validation: {len(violations)} violations")
        for vv in violations[:15]:
            print(f"[pulse]   - {vv}")
    if violations:
        out = None

    if out is None:
        print("[pulse] keeping existing review (no valid generation)")
        return "kept-full"
    result = {
        "version": 1, "slot": slot,
        "generated_at": now.isoformat(), "window_hours": WINDOW_HOURS,
        "mode": "full",
        "intro": out["intro"],
        "stories": [dict(s, **{
            "source_url": next((n["url"] for n in news if n["id"] == s["news_refs"][0]), None),
            "source_name": next((n["source"] for n in news if n["id"] == s["news_refs"][0]), None),
            "published": next((n.get("published") for n in news if n["id"] == s["news_refs"][0]), None)})
            for s in out["stories"] if s.get("news_refs")],
        "alerts": [dict(a, **{
            "source_url": next((n["url"] for n in news if n["id"] == a["news_refs"][0]), None)})
            for a in out["alerts"] if a.get("news_refs")],
        "watchlist": out["watchlist"],
        "sources": [{"name": n["source"], "url": n["url"]} for n in news[:8]],
    }

    # Retention distinguishes generated/schema-valid from content-approved: a
    # retained review keeps its ORIGINAL generated_at stamp and is never
    # re-labeled verified; content approval happens outside the pipeline.
    if result["mode"] == "measured_only" and latest and latest.get("mode") == "full":
        print("[pulse] keeping last generated full review (schema-valid only; "
              "fallback not written; original as-of stamp untouched)")
        return "kept-full"
    tmp = LATEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LATEST_FILE)
    print(f"[pulse] wrote {LATEST_FILE} mode={result['mode']}")
    return "generated"


if __name__ == "__main__":
    force = "--force" in sys.argv
    # "kept-full" is a soft outcome: generation failed (e.g. Groq quota) and the
    # last-good review was preserved on purpose. It must NOT fail the workflow -
    # generate.py and the publish steps still run so market data and the button
    # keep working with the existing review.
    sys.exit(0 if run(force=force) in ("skip", "no-keys", "generated", "kept-full") else 1)
