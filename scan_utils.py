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
