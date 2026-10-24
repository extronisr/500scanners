# -*- coding: utf-8 -*-
"""Pure helpers shared by the scanners (no network code, so they are unit-tested)."""
import datetime as dt
import re

MIN_SESSION_FRACTION = 0.25   # no intraday estimate before 25% of the session has elapsed


# ----------------------------------------------------------------- volume policy
def volume_context(vols, vfrac):
    """Volume readings for one stock. Two separate readings, never mixed:

    CONFIRMED  = the last COMPLETED session's volume / the average of the 20 completed
                 sessions before it (the reference session is never in its own average).
    ESTIMATE   = only while the market is open: today's partial volume scaled up to a full
                 day by the expected intraday volume curve. This is NOT observed volume.
                 It is shown only after MIN_SESSION_FRACTION of the session has elapsed,
                 because intraday volume is not uniform (heavy at open and close) and the
                 projection is unreliable early in the day.

    vols  : daily volumes, oldest first; the last bar is today's bar.
    vfrac : None if the market is closed (last bar is a complete session), otherwise the
            fraction of a normal day's volume expected so far (0-1].

    Returns {"confirmed": float|None, "estimate": float|None} or None if no data.
    """
    vols = [float(v or 0.0) for v in (vols or [])]
    out = {"confirmed": None, "estimate": None}
    if vfrac is None:                      # closed: last bar is complete
        if len(vols) >= 21:
            avg = sum(vols[-21:-1]) / 20.0
            if avg > 0:
                out["confirmed"] = vols[-1] / avg
    else:                                  # open: last bar is partial
        if len(vols) >= 22:
            avg_prev = sum(vols[-22:-2]) / 20.0          # 20 sessions before yesterday
            if avg_prev > 0:
                out["confirmed"] = vols[-2] / avg_prev
            avg_today = sum(vols[-21:-1]) / 20.0         # 20 completed sessions before today
            if avg_today > 0 and vols[-1] > 0 and vfrac >= MIN_SESSION_FRACTION:
                out["estimate"] = vols[-1] / vfrac / avg_today
    if out["confirmed"] is None and out["estimate"] is None:
        return None
    return out


def volume_signal(vc, threshold, strict=True):
    """Apply a volume threshold to a volume_context result.
    Returns (tier, multiple): tier is "confirmed" (completed session passes), "estimate"
    (only the intraday estimate passes) or None. A confirmed pass always wins; the tiers are
    reported separately so the page never presents an estimate as observed volume."""
    if not vc:
        return None, None
    def ok(x):
        return x is not None and (x > threshold if strict else x >= threshold)
    if ok(vc["confirmed"]):
        return "confirmed", vc["confirmed"]
    if ok(vc["estimate"]):
        return "estimate", vc["estimate"]
    return None, None


def volume_text(tier, mult, vfrac):
    """Hebrew label for a volume reading."""
    if tier == "estimate":
        return f"מחזור ×{mult:.1f} (הערכה תוך-יומית, לא נצפה; הסשן עדיין פתוח)"
    if vfrac is None:
        return f"מחזור ×{mult:.1f} מהממוצע (סשן שהושלם)"
    return f"מחזור ×{mult:.1f} מהממוצע (אתמול, סשן שהושלם; המחיר של היום)"


# ----------------------------------------------------------------- market sessions
def market_state(now=None):
    """(state, session_date, fraction) for the NYSE using a real exchange calendar
    (holidays and early closes). state is "OPEN" or "CLOSED"; for CLOSED, session_date is
    the last completed session. Returns None if the calendar library is unavailable."""
    try:
        import exchange_calendars as xc
        import pandas as pd
    except Exception:
        return None
    cal = xc.get_calendar("XNYS")
    now = pd.Timestamp(now if now is not None else dt.datetime.now(dt.timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    minute = now.floor("min")
    if cal.is_trading_minute(minute):
        sess = cal.minute_to_session(minute)
        o, c = cal.session_open(sess), cal.session_close(sess)
        frac = (now - o).total_seconds() / (c - o).total_seconds()
        return "OPEN", sess.date(), min(max(frac, 0.0), 1.0)
    sess = cal.minute_to_past_session(minute)    # last session that has already closed
    return "CLOSED", sess.date(), 1.0


# ----------------------------------------------------------------- news relevance
def _words(name):
    stop = {"inc", "corp", "corporation", "co", "ltd", "plc", "nv", "n.v.", "the", "holdings",
            "group", "company", "therapeutics", "pharmaceuticals", "pharma", "biosciences",
            "bio", "technologies", "sciences", "medicines", "oncology", "&", "and"}
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", name or "") if t]
    keep = [t for t in toks if t.lower() not in stop and len(t) >= 4]
    return keep or [t for t in toks if len(t) >= 3][:1]


EVERGREEN = re.compile(
    r"historical\s+(quotes?|prices?|data)|price\s+target\s*(&|and)\s*forecast|"
    r"stock\s+price\s*(quote|history|today|forecast|prediction)|equity\s+price\s+and\s+quote|"
    r"stock\s+quote|quotes?\s*(&|and)\s*(history|news)|share\s+price\s+(today|history)",
    re.I)


def is_evergreen(title):
    """Quote / forecast / historical-data pages are reference pages, not event news."""
    return bool(EVERGREEN.search(title or ""))


def parse_pub(pub):
    """RSS pubDate -> aware UTC datetime, or None if missing or unparseable."""
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(pub)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def is_recent(pub_dt, now=None, max_age_days=3):
    """Within the window (default 3 days, same as the SEC 8-K window); undated -> False."""
    if pub_dt is None:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    age = now - pub_dt
    return dt.timedelta(days=-1) <= age <= dt.timedelta(days=max_age_days)


def headline_matches(title, ticker, name=None):
    """True if a news headline is plausibly about this US-listed issuer.
    With a company name: the headline must contain the name's first distinctive word, or carry
    the ticker as '(TICKER)' / 'NASDAQ:TICKER' / 'NYSE:TICKER' WITHOUT naming a different issuer
    right before it (so "PHAROS ENERGY PLC (PHAR)" is rejected for Pharming).
    Without a name: only the exchange-style ticker form is accepted. A bare ticker word never is."""
    t = title or ""
    tick = re.escape(ticker)
    words = _words(name) if name else []
    if words and re.search(rf"\b{re.escape(words[0])}", t, re.I):
        return True
    m = re.search(rf"\(\s*{tick}\s*\)|\b(?:NASDAQ|NYSE|NYSEARCA|AMEX)\s*:\s*{tick}\b", t)
    if not m:
        return False
    if not words:
        return True
    before = re.sub(r"[^\w\s&.\-]", " ", t[:m.start()]).split()[-4:]
    # some other issuer name stands right before the ticker -> reject; nothing before it -> accept
    return not before


# ---------------------------------------------------------------- new sections (live site)
MATERIAL_8K_ITEMS = {"1.01", "1.02", "7.01", "8.01"}

# form -> (severity, Hebrew label). severities: 3 = material, 2 = dilution warning, 1 = info
SEC_FORM_SEVERITY = {
    "8-K": (3, "8-K"),
    "424B5": (2, "הנפקה 424B5"),
    "S-3": (2, "מדף S-3"),
    "S-3ASR": (2, "מדף S-3"),
    "S-1": (2, "רישום S-1"),
    "4": (1, "Form 4 · insiders"),
    "10-Q": (1, "דוח רבעוני 10-Q"),
    "10-K": (1, "דוח שנתי 10-K"),
}


def sec_form_severity(form, items=""):
    """(severity, label) for an EDGAR form. An 8-K is material only when it
    carries one of the material item numbers; otherwise it is informational."""
    form = (form or "").strip()
    if form == "8-K":
        nums = {p.strip().rstrip(",") for p in (items or "").split() if p.strip()}
        if nums & MATERIAL_8K_ITEMS:
            return 3, "8-K מהותי"
        return 1, "8-K"
    return SEC_FORM_SEVERITY.get(form, (1, form or "דיווח"))


def is_pullback_uptrend(closes, rsi_val, dd_min=10.0, dd_max=30.0, rsi_max=40.0):
    """Pullback-in-uptrend category (observation, not a bottom signal):
    10-30% below the 52-week high, still above the 200-day average, RSI below 40."""
    if len(closes) < 210 or rsi_val is None:
        return None
    last = closes[-1]
    high52 = max(closes[-252:])
    sma200 = sum(closes[-200:]) / 200.0
    low126 = min(closes[-126:])
    if high52 <= 0 or low126 <= 0:
        return None
    dd = (1.0 - last / high52) * 100.0
    # small tolerance: binary floats put exact boundaries (e.g. 10%) a hair below the limit
    if not (dd_min - 0.05 <= dd <= dd_max + 0.05):
        return None
    if last <= sma200 or rsi_val >= rsi_max:
        return None
    return {"dd": round(dd, 1), "above200": round((last / sma200 - 1.0) * 100.0, 1),
            "above_low": round((last / low126 - 1.0) * 100.0, 1)}


def catalyst_bucket(days, approx=False):
    """Countdown tier: 'imminent' (<=7d), 'approaching' (<=21d), else 'watch'.
    Approx (quarter-window) events never rank above 'approaching'."""
    if days is None:
        return "watch"
    if days <= 7 and not approx:
        return "imminent"
    if days <= 21:
        return "approaching"
    return "watch"


# ----------------------------------------------------------------- biotech breakthroughs (market-wide)
# Pure logic for the "פריצות דרך ביוטק" layer: classification, severity, ticker
# extraction and event clustering. No network code here, so it is unit-tested.

# Recognition-only lexicon: helps tag well-known companies in headlines.
# It never limits which stories surface (the scanner scans everything).
BT_COMPANY_ALIASES = {
    "moderna": "MRNA", "merck": "MRK", "merck sharp": "MRK", "pfizer": "PFE",
    "eli lilly": "LLY", "lilly": "LLY", "abbvie": "ABBV",
    "bristol-myers": "BMY", "bristol myers": "BMY", "bms": "BMY",
    "amgen": "AMGN", "gilead": "GILD", "vertex": "VRTX", "regeneron": "REGN",
    "biogen": "BIIB", "biomarin": "BMRN", "novartis": "NVS", "roche": "RHHBY",
    "hoffmann-la roche": "RHHBY", "genentech": "RHHBY", "astrazeneca": "AZN",
    "gsk": "GSK", "glaxosmithkline": "GSK", "sanofi": "SNY",
    "johnson & johnson": "JNJ", "j&j": "JNJ", "novo nordisk": "NVO",
    "biontech": "BNTX", "crispr therapeutics": "CRSP", "intellia": "NTLA",
    "editas": "EDIT", "sarepta": "SRPT", "alnylam": "ALNY", "ionis": "IONS",
    "ultragenyx": "RARE", "argenx": "ARGX", "takeda": "TAK", "bayer": "BAYRY",
    "daiichi sankyo": "DSNKY", "incyte": "INCY", "neurocrine": "NBIX",
    "seagen": "PFE", "immunogen": "ABBV", "mirati": "BMY", "karuna": "BMY",
    "cerevel": "ABBV", "prometheus": "MRK", "chiasma": "ENTA",
    "acadia": "ACAD", "disc medicine": "IRON", "veradermics": "",
}

BT_CATEGORY_HE = {
    "fda": "אישור FDA",
    "spec": "ציפייה לאישור FDA",
    "crl": "דחיית FDA",
    "phase3": "תוצאות שלב 3",
    "phase2": "תוצאות שלב 2",
    "btd": "אירוע רגולטורי",
    "hold": "עצירה קלינית",
}

_BT_SKIP = re.compile(
    r"price\s+target|\banalyst|upgrad|downgrad|initiates?\s+coverage|\betf\b|"
    r"stocks?\s+to\s+(buy|watch)|top\s+\d+\s+biotech|earnings?\s+(preview|recap|call)|"
    r"short\s+interest|insider\s+(buying|selling)|\b13f\b|hedge\s+fund|"
    r"historical prices|\b\d+\.\d{2,4}\s*(?:call|put)\b|options?\s+chain|"
    r"interactive stock chart|stock price,\s*quote",
    re.I)

_BT_OPS = re.compile(
    r"\b(?:doses?|dosing)\s+(?:the\s+)?first\s+patient|first\s+patient\s+dosed|"
    r"\b(?:initiates?|launches?|begins?|starts?|opens?)\s+(?:a\s+|its\s+|the\s+)?(?:phase|pivotal|clinical)|"
    r"\b(?:enrol?s?|enrolling|enrollment)\s+(?:of|in|begins)|"
    r"\b(?:expands?|expansion of)\s+(?:its\s+)?(?:phase|trial|study)\b|"
    r"\b(?:to|will|set\s+to)\s+present\b|\bto\s+be\s+presented\b",
    re.I)

_BT_READOUT = re.compile(
    r"results?|data|readout|top[\s-]?line|meets?|\bmet\b|fails?|misses?|wins?\b|"
    r"endpoint|announces?|posts?\b|reports?|cuts?|reduces?|reduction|"
    r"overall survival|progression-free|recurrence-free|\bpfs\b|\borr\b|\bos\b|"
    r"response rate|efficacy",
    re.I)

_BT_APPROVAL = re.compile(
    r"\bfda\s+(?:approves?|oks?|okay?s?|clears?|authorizes?|"
    r"grants?\s+(?:accelerated\s+|full\s+|traditional\s+)?(?:approval|clearance|authorization))\b|"
    r"\b(?:wins?|gets?|receives?|secures?|earns?|lands?|snags?)\s+(?:the\s+)?(?:fda|u\.?s\.?)\s*(?:regulatory\s+)?approval|"
    r"\bfda\s+(?:green\s*light|greenlight)|\bapproved\s+by\s+(?:the\s+)?fda\b",
    re.I)

_BT_CRL = re.compile(
    r"complete\s+response\s+letter|\bcrl\b|"
    r"\bfda\s+(?:rejects?|rejection|turns?\s+down|snubs?|refuses?|denies|declines)\b|"
    r"\brejected\s+by\s+(?:the\s+)?fda\b",
    re.I)

_BT_HOLD = re.compile(
    r"clinical\s+hold|(?:placed|put)\s+on\s+(?:clinical\s+)?hold|"
    r"\bhalts?\s+(?:the\s+|its\s+)?(?:trial|study|phase)\b|\bdiscontinues?\s+(?:the\s+)?(?:trial|study|program)\b",
    re.I)

_BT_PHASE3 = re.compile(r"\bphase\s*(?:3|iii)\b|\bpivotal\s+(?:trial|study|data|results|win)", re.I)
_BT_PHASE2 = re.compile(r"\bphase\s*(?:2|ii)\b(?!i)", re.I)

_BT_BTD = re.compile(
    r"breakthrough\s+therapy\s+designation|priority\s+review|"
    r"fast[\s-]?track\s+(?:designation|status)|\brmat\b|"
    r"regenerative\s+medicine\s+advanced\s+therapy|orphan\s+drug\s+designation|"
    r"\bpdufa\b|advisory\s+committee|\badcom\b",
    re.I)

_BT_POSITIVE = re.compile(
    r"\bapproves?|approved|approval|meets?|met\b|positive|overwhelmingly|"
    r"\bwins?\b|succeeds?|successful|success\b|cuts?\b|reduces?|reduction|"
    r"superior(?:ity)?|statistically\s+significant|significant(?:ly)?\s+(?:improve|benefit)|"
    r"stops?\s+trial\s+early|breakthrough\s+data|\bhits?\b|\bbeats?\b",
    re.I)

_BT_NEGATIVE = re.compile(
    r"\bfails?|failed|flunks?|misses?|missed|rejects?|rejection|"
    r"complete\s+response\s+letter|\bcrl\b|disappoint|setback|\bhalts?\b|\bpauses?\b|"
    r"falls?\s+short|\bdents?\b|safety\s+(?:concern|issue|signal)|\bstumbles?\b",
    re.I)

# Rejection framed as overcome background ("spins past FDA rejection",
# "bounces back from CRL", "despite FDA rejection"): the rejection is history,
# not the current event - it must not count as negative evidence.
_BT_HISTORICAL_REJECTION = re.compile(
    r"(?:spins?\s+past|bounces?\s+back\s+(?:from|after)|overcomes?|despite|"
    r"after|following|moves?\s+past|shrugs?\s+off)"
    r"[^.]{0,45}?(?:rejection|rejects|complete\s+response\s+letter|\bcrl\b)",
    re.I)

# Tickers that are also ordinary English words (from the user's desktop monitor):
# exchange-style mentions stay valid, but a bare "(EDIT)" is treated as prose.
BT_COMMON_WORD_TICKERS = {"IRON", "HELP", "VIR", "CURE", "EDIT", "BEAM", "FATE", "BLUE",
                          "GOLD", "ARROW", "FOLD", "WAVE", "TIDE", "RARE", "AURA"}

# Acronyms that look like parenthesized tickers but are not
_BT_TICKER_STOP = {
    "FDA", "EMA", "MHRA", "CRL", "BTD", "RMAT", "PDUFA", "NDA", "BLA", "ANDA",
    "ESMO", "ASCO", "ASH", "AHA", "EHA", "AACR", "NYSE", "NASDAQ", "AMEX",
    "CEO", "CFO", "COO", "CMO", "USA", "ETF", "IPO", "HIV", "DNA", "RNA",
    "MRNA", "EBITDA", "ADHD", "PTSD", "NASH", "MASH", "NSCLC", "SMA", "RSI",
    "IT", "US", "UK", "EU", "PH", "II", "III", "IV",
}


_BT_SPECULATIVE = re.compile(
    r"\bcould\s+(?:get|win|receive|gain|secure|land)|\bmight\s+(?:get|win|receive)|"
    r"\bmay\s+(?:get|win|receive)|poised\s+to\s+(?:win|get|receive)|"
    r"expected\s+to\s+(?:win|get|receive)|seeks?\s+(?:fda\s+)?(?:approval|clearance)|"
    r"files?\s+for\s+(?:fda\s+)?(?:approval|clearance)",
    re.I)

_BT_FAIL_MEET = re.compile(
    r"(?:fails?|does\s+not|did\s+not|do\s+not)\s+(?:to\s+)?meet|"
    r"miss(?:es|ed)?\s+(?:the\s+|its\s+)?(?:key\s+|primary\s+|main\s+)?endpoint|"
    r"f(?:ails?|ell)\s+(?:its\s+|the\s+)?(?:primary\s+)?endpoint|fell\s+short",
    re.I)


def breakthrough_direction(title):
    """1 = positive readout/approval, -1 = failure/rejection, 0 = unclear."""
    t = title or ""
    # 'HER2-positive' / 'PD-L1-negative' name the disease subtype, not the
    # readout direction; drop hyphenated subtype mentions before scoring words.
    t = re.sub(r"\b[A-Za-z0-9]+-(?:positive|negative)\b", " ", t, flags=re.I)
    t2 = _BT_FAIL_MEET.sub(" ", t)        # 'fails to meet' is not a positive 'meet'
    th = _BT_HISTORICAL_REJECTION.sub(" ", t2)   # drop overcome-history rejections
    pos = bool(_BT_POSITIVE.search(th))
    neg = bool(_BT_NEGATIVE.search(th)) or t2 != t
    if pos and not neg:
        return 1
    if neg and not pos:
        return -1
    return 0


def classify_breakthrough(title):
    """Classify one headline as a biotech breakthrough event.
    Returns {"cat", "sev", "dir"} or None when the headline is not a serious
    event (early phase, trial ops, analyst noise, evergreen).

    Severity: 4 = practice-changing (FDA approval, positive phase 3),
              3 = major (phase 3 readout of any direction, CRL),
              2 = notable (positive phase 2, regulatory designations),
              1 = minor (phase 2 readout without a clear positive result)."""
    t = (title or "").strip()
    if not t or is_evergreen(t) or _BT_SKIP.search(t):
        return None
    if _BT_HOLD.search(t):
        return {"cat": "hold", "sev": 3, "dir": -1}
    if _BT_APPROVAL.search(t):
        if _BT_SPECULATIVE.search(t):
            # expecting a future approval is not an approval event
            return {"cat": "spec", "sev": 2, "dir": 0}
        if re.search(r"\bgeneric\b", t, re.I):
            # a generic approval is a real approval, just lower-impact
            return {"cat": "fda", "sev": 2, "dir": 1}
        return {"cat": "fda", "sev": 4, "dir": 1}
    # A fresh readout with a clear direction outranks a historical CRL mention
    # ("spins past FDA rejection with positive phase 3 data"). A real new
    # rejection headline carries no positive readout, so it still falls to CRL.
    if _BT_PHASE3.search(t) and _BT_READOUT.search(t) and not _BT_OPS.search(t):
        d = breakthrough_direction(t)
        if d:
            return {"cat": "phase3", "sev": 4 if d == 1 else 3, "dir": d}
    if _BT_PHASE2.search(t) and _BT_READOUT.search(t) and not _BT_OPS.search(t):
        d = breakthrough_direction(t)
        if d:
            return {"cat": "phase2", "sev": 2 if d == 1 else 1, "dir": d}
    if _BT_CRL.search(t):
        return {"cat": "crl", "sev": 3, "dir": -1}
    if _BT_PHASE3.search(t) and _BT_READOUT.search(t) and not _BT_OPS.search(t):
        return {"cat": "phase3", "sev": 3, "dir": 0}
    if _BT_PHASE2.search(t) and _BT_READOUT.search(t) and not _BT_OPS.search(t):
        return {"cat": "phase2", "sev": 1, "dir": 0}
    if _BT_BTD.search(t):
        return {"cat": "btd", "sev": 2, "dir": 1}
    return None


def extract_tickers(text):
    """Exchange-style tickers from a headline: '(NASDAQ: MRNA)', '(NYSE: MRK)'
    or a bare parenthesized '(MRNA)'. Acronyms (FDA, ESMO, ...) never match."""
    t = text or ""
    out = []
    # Exchange-prefixed mentions are always tickers, even when the symbol is
    # also an acronym or common word ("NASDAQ: MRNA" is Moderna, not mRNA).
    for m in re.finditer(
            r"(?:\(\s*)?(?i:NASDAQ|NYSE\s*American|NYSEARCA|NYSE|AMEX|ENXT[A-Z]{2}|EPA|ETR|LSE|SWX|TSE|TSX)\s*:\s*([A-Z][A-Z0-9.\-]{0,6})(?:\s*\))?", t):
        if m.group(1) not in out:
            out.append(m.group(1))
    for m in re.finditer(r"\(\s*([A-Z]{2,5})\s*\)", t):
        tk = m.group(1)
        if tk not in _BT_TICKER_STOP and tk not in BT_COMMON_WORD_TICKERS and tk not in out:
            out.append(tk)
    return out


def _bt_norm_name(name):
    n = re.sub(r"[^a-z0-9 &\-]+", " ", (name or "").lower())
    n = re.sub(r"\b(inc|corp|corporation|co|ltd|plc|llc|lp|sa|ag|nv|se|spa|asa|ab|oyj)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _bt_first_word(name):
    toks = [t for t in re.split(r"[^A-Za-z0-9&\-]+", name or "") if t]
    skip = {"the", "inc", "corp", "ltd", "plc", "llc"}
    for t in toks:
        if t.lower() not in skip:
            return t.lower()
    return ""


def build_bt_lexicon(ticker_names=None):
    """alias(lowercase) -> ticker. Curated aliases first; then each {ticker: name}
    map (S&P 500 cache, biotech watchlist) contributes its normalized full name
    and its first distinctive word. Tickers with an empty-string alias are known
    to be private/unlisted and resolve to nothing on purpose."""
    lex = {k: v for k, v in BT_COMPANY_ALIASES.items() if v}
    for ticker, name in (ticker_names or {}).items():
        norm = _bt_norm_name(name)
        if norm and len(norm) >= 4:
            lex.setdefault(norm, ticker.upper())
        first = _bt_first_word(name)
        if len(first) >= 5 and first not in BT_ALIAS_STOP:
            lex.setdefault(first, ticker.upper())
    return lex


def companies_in_text(text, lexicon):
    """Tickers of lexicon companies mentioned in text, longest aliases first."""
    t = " " + re.sub(r"[^A-Za-z0-9&\- ]+", " ", (text or "").lower()) + " "
    found = []
    for alias in sorted(lexicon, key=len, reverse=True):
        if not alias or len(alias) < 3:
            continue
        if re.search(r"(?<![a-z0-9&\-])" + re.escape(alias) + r"(?![a-z0-9\-])", t):
            tk = lexicon[alias]
            if tk and tk not in found:
                found.append(tk)
    return found


_BT_COMMON_NEWS = {"fda", "approval", "approves", "approved", "treatment", "hepatitis",
                   "cancer", "results", "announces", "patients", "therapy",
                   "chronic", "disease", "receives", "positive", "negative",
                   "melanoma", "prostate", "leukemia", "lymphoma", "myeloma",
                   "diabetes", "alzheimers", "oncology", "vaccine", "antibody",
                   "phase", "trial", "study", "data"}

# Generic English words that are also the first word of a company name
# ("First Solar" -> "first"): never a reliable company mention on their own.
BT_ALIAS_STOP = {"first", "global", "general", "united", "american", "national",
                 "advanced", "modern", "central", "western", "eastern", "southern",
                 "northern", "group", "holdings", "capital", "royal", "pacific"}


_BT_TITLE_STOP = {"the", "a", "an", "and", "or", "of", "in", "on", "for", "to",
                  "with", "by", "as", "at", "from", "after", "over", "its", "his",
                  "says", "new", "early", "posts", "post", "amid", "what", "why",
                  "how", "first", "early", "leak"}


def _bt_title_tokens(title):
    t = (title or "").lower()
    t = re.sub(r"^\s*[^:]{0,25}:\s", " ", t)          # drop outlet prefix like "ESMO:"
    # drop outlet suffix like " - Pharmaceutical Executive": Google News appends
    # the source name, and those tokens otherwise dominate the overlap rule and
    # merge different events from the same outlet.
    t = re.sub(r"\s+[-–—|]\s+\S[^-–—|]{0,50}$", " ", t)
    toks = set(re.findall(r"[a-z0-9]{3,}", t))
    return {x for x in toks if x not in _BT_TITLE_STOP}


def cluster_breakthroughs(items):
    """Merge items about the same event. Two items merge when they share the
    category AND (a ticker, a company ticker, or >=60% token overlap of the
    smaller title). The cluster keeps the highest-severity title (ties: newest),
    unions tickers/companies, and collects up to 3 distinct sources.

    items: [{title, link, pub_ts, source, cat, sev, dir, tickers, companies, nct?}]
    Returns clusters sorted by severity then recency."""
    items = sorted(items, key=lambda x: -(x.get("pub_ts") or 0))
    # company-name words are identity, not event evidence: never let the
    # shared company name itself justify a merge
    co_words = set()
    for _a in BT_COMPANY_ALIASES:
        co_words.update(_a.split())
    # distinctive tokens: uncommon across this batch (drug names like 'hibsago')
    # merge same-category items even when outlet wording differs.
    df = {}
    toks_per = []
    for it in items:
        tk = _bt_title_tokens(it.get("title", ""))
        toks_per.append(tk)
        for t in tk:
            df[t] = df.get(t, 0) + 1
    n = max(1, len(items))
    distinctive = {t for t, c in df.items()
                   if len(t) >= 6 and c <= 2 and t not in _BT_COMMON_NEWS}
    clusters = []
    for it in items:
        if not it.get("title") or not it.get("cat"):
            continue
        toks = _bt_title_tokens(it["title"])
        placed = None
        for c in clusters:
            if c["cat"] != it["cat"]:
                continue
            # A conflicting trial id blocks the merge: same company + same
            # category is not proof of the same event.
            if c["ncts"] and it.get("nct") and it["nct"] not in c["ncts"]:
                continue
            # opposite directions (approval vs rejection) are different events:
            # a failure must never disappear into a success cluster
            if c["dir"] and it.get("dir") and c["dir"] != it["dir"]:
                continue
            ct = c["_toks"]
            co_here = {t.lower() for t in c["tickers"] + (it.get("tickers") or [])}
            ft = toks - _BT_COMMON_NEWS - co_words - co_here
            fct = ct - _BT_COMMON_NEWS - co_words - co_here
            inter = (ft & fct) if (ft and fct) else set()
            shared_co = bool(set(c["tickers"]) & set(it.get("tickers") or [])) \
                     or bool(set(c["companies"]) & set(it.get("companies") or []))
            if shared_co:
                # same company + same category still needs token evidence of
                # the same drug/event; the company name alone is not enough
                if inter:
                    placed = c
                    break
            elif (inter and (inter & distinctive)
                    and len(inter) / min(len(ft), len(fct)) >= 0.5):
                # cross-company merge needs BOTH a batch-distinctive shared
                # token AND high overlap - a lone medical term is not an event
                placed = c
                break
        if placed is None:
            placed = {"title": it["title"], "link": it.get("link", ""),
                      "pub_ts": it.get("pub_ts"), "date": it.get("date", ""),
                      "cat": it["cat"], "sev": it["sev"], "dir": it["dir"],
                      "tickers": list(it.get("tickers") or []),
                      "companies": list(it.get("companies") or []),
                      "sources": [], "ncts": [], "_toks": set(toks)}
            clusters.append(placed)
        else:
            placed["_toks"] |= toks
            if it["sev"] > placed["sev"]:
                placed.update({"title": it["title"], "link": it.get("link", ""),
                               "pub_ts": it.get("pub_ts"), "date": it.get("date", ""),
                               "sev": it["sev"], "dir": it["dir"]})
            if placed["dir"] == 0 and it["dir"]:
                placed["dir"] = it["dir"]
        for t in (it.get("tickers") or []):
            if t not in placed["tickers"]:
                placed["tickers"].append(t)
        for t in (it.get("companies") or []):
            if t not in placed["companies"]:
                placed["companies"].append(t)
        src = {"source": it.get("source", ""), "link": it.get("link", "")}
        if src["source"] and all(s["link"] != src["link"] for s in placed["sources"]):
            if len(placed["sources"]) < 3:
                placed["sources"].append(src)
        if it.get("nct") and it["nct"] not in placed["ncts"]:
            placed["ncts"].append(it["nct"])
    for c in clusters:
        if not c["date"] and c.get("pub_ts"):
            c["date"] = dt.datetime.fromtimestamp(c["pub_ts"], dt.timezone.utc).date().isoformat()
        c.pop("_toks", None)
    clusters.sort(key=lambda c: (-c["sev"], -(c["pub_ts"] or 0)))
    return clusters
