#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
סורק שוק — Market Breakout Scanner  (גרסה 2)
---------------------------------------------
סורק כ-950 מניות נזילות (S&P 500 + S&P 400 + Nasdaq-100) ומחפש שלושה מצבים:

  🎯 על סף פריצה   — עד 2% מתחת להתנגדות אופקית שנבדקה לפחות פעמיים,
                     עם שפלים עולים והתכווצות תנודתיות (לחץ שמצטבר מתחת לקו).
  🚀 תחילת פריצה   — חצתה את ההתנגדות היום או ביום המסחר הקודם, עד 4% מעליה,
                     במחזור גבוה, נר חזק ו-RSI 50–80.
  🔥 פריצה חזקה    — אותה פריצה איכותית, אבל המחיר כבר ברח יותר מ-4% מהקו
                     (או RSI מעל 80). לא לרדוף — לחכות לחזרה לאזור הקו.

אזהרות: ⚠️ יום פקיעה (מחזור מנופח), ⚠️ דחייה מהקו לאחרונה, ⚠️ סיכון גבוה.

מכויל למינימום רעש: מסננים נזילות, מגמה, איכות התנגדות — ורק מניות חדשות נשלחות לטלגרם.
תוכנה עצמאית: לא תלויה בשום קובץ אחר. ההגדרות: market_scanner_settings.json

הרצה:          python market_scanner.py
הרצה + פתיחה:  python market_scanner.py --open
"""
import csv
import io
import json
import sys
import time
import datetime as dt
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent
MS_SETTINGS = BASE / "market_scanner_settings.json"
UNIVERSE = BASE / "market_scanner_universe.csv"
REPORTS = BASE / "market_scanner_reports"
STATE = BASE / "market_scanner_state.json"
LOG = BASE / "market_scanner_log.csv"

DEFAULTS = {
    "settings_version": 4,
    "universe": ["sp500", "sp400", "nasdaq100"],
    "universe_refresh_days": 7,
    "extra_tickers": [],
    "download_batch_size": 100,

    "min_price": 10.0,
    "min_dollar_volume_m": 20.0,
    "require_above_sma200": True,

    "resistance_lookback_days": 60,
    "touch_tolerance_pct": 1.5,
    "touch_separation_days": 5,
    "min_touches": 2,
    "min_base_days": 15,

    "breakout_max_age_days": 2,
    "breakout_max_extension_pct": 4.0,
    "breakout_volume_multiplier": 1.5,
    "breakout_rsi_min": 50,
    "breakout_rsi_max": 80,
    "breakout_close_location_min": 0.6,

    "extended_max_pct": 15.0,
    # ריצה לפני הקו: עלייה מהשפל של 20 הימים עד הקו. מעל הסף זו "ריצה אל הקו" ולא בסיס —
    # ביום החצייה המניה תסווג 🔥 (מתוחה) ולא 🚀. חזרה לאזור הקו למחרת = 🚀 עם הערת כניסה
    # "מתוחה" = רצה יותר מ-max_runup_pct וגם לא נחה ליד הקו (פחות מ-min_handle_days ימים
    # עד 5% מתחת לקו) או שה-RSI לפני החצייה כבר היה גבוה
    "max_runup_pct": 25.0,
    "runup_lookback_days": 20,
    "min_handle_days": 5,
    "handle_rsi_max": 75,
    "extended_entry_zone_pct": 2.0,

    "setup_max_distance_pct": 2.0,
    "setup_rsi_min": 50,
    "setup_rsi_max": 70,
    "setup_require_higher_lows": True,
    "setup_max_atr_ratio": 0.85,
    "setup_require_above_sma50": True,
    "max_risk_pct": 6.0,

    # ↗️ התאוששות: מניה 15%-40% מתחת לשיא 52 שבועות, שבנתה שפל עולה ופורצת את הגבוה של הבסיס
    "recovery_enabled": True,
    "recovery_min_dd_pct": 15.0,
    "recovery_max_dd_pct": 40.0,
    "recovery_base_days": 25,
    "recovery_max_base_depth_pct": 20.0,
    "recovery_rsi_min": 50,
    "recovery_rsi_max": 75,
    "recovery_min_rr": 2.0,

    "max_results_per_group": 10,
    "alert_only_new": True,
    "keep_reports_days": 14,

    "telegram_bot_token": "",
    "telegram_chat_id": "",
}

SOURCES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "nasdaq100": "https://en.wikipedia.org/wiki/Nasdaq-100",
}

# Wikipedia removed the Nasdaq-100 constituents table (Oct 2026). When the live
# fetch fails, fall back to this snapshot (taken 2026-10-24) so Nasdaq-only
# names like ASML/ARM/MELI are not silently dropped from the scan universe.
FALLBACK_NASDAQ100 = ['AAPL', 'ABNB', 'ADBE', 'ADI', 'ADP', 'ADSK', 'AEP', 'ALAB', 'ALNY', 'AMAT', 'AMD', 'AMGN', 'AMZN', 'APP', 'ARM', 'ASML', 'AVGO', 'AXON', 'BKNG', 'BKR', 'CCEP', 'CDNS', 'CEG', 'CMCSA', 'COST', 'CPRT', 'CRWD', 'CRWV', 'CSCO', 'CSX', 'CTAS', 'DASH', 'DDOG', 'DXCM', 'EXC', 'FANG', 'FAST', 'FER', 'FTNT', 'GEHC', 'GILD', 'GOOG', 'GOOGL', 'HON', 'HONA', 'IDXX', 'INTC', 'INTU', 'ISRG', 'KDP', 'KLAC', 'LIN', 'LITE', 'LRCX', 'MAR', 'MCHP', 'MDLZ', 'MELI', 'META', 'MNST', 'MPWR', 'MRNA', 'MRVL', 'MSFT', 'MSTR', 'MU', 'NBIS', 'NFLX', 'NVDA', 'NXPI', 'ODFL', 'ORLY', 'PANW', 'PAYX', 'PCAR', 'PDD', 'PEP', 'PLTR', 'PYPL', 'QCOM', 'REGN', 'RKLB', 'ROP', 'ROST', 'SBUX', 'SHOP', 'SNDK', 'SNPS', 'SPCX', 'STX', 'TER', 'TMUS', 'TRI', 'TSLA', 'TTWO', 'TXN', 'VRTX', 'WDAY', 'WDC', 'WMT', 'XEL']



# חלק מהמחזור היומי שנסחר עד נקודת זמן ביום (צורת U: פתיחה וסגירה ערות במיוחד)
VOL_CURVE_T = [0.0, 0.077, 0.154, 0.308, 0.500, 0.692, 0.846, 0.923, 1.0]
VOL_CURVE_V = [0.0, 0.140, 0.220, 0.340, 0.470, 0.600, 0.730, 0.820, 1.0]

LABELS = {"BREAKOUT": "🚀 תחילת פריצה", "RECOVERY": "↗️ התאוששות — פריצת בסיס מתחת לשיא", "EXTENDED": "🔥 פריצה חזקה — כבר רחוקה מהקו",
          "SETUP": "🎯 על סף פריצה"}
GROUP_ORDER = ("BREAKOUT", "RECOVERY", "EXTENDED", "SETUP")
# BREAKOUT ו-EXTENDED הם אותו אירוע (המחיר רק התרחק מהקו) — לא שולחים עליו התראה פעמיים
FAMILY = {"BREAKOUT": "breakout", "EXTENDED": "breakout", "RECOVERY": "breakout", "SETUP": "setup"}


def load_ms_settings():
    s = dict(DEFAULTS)
    if MS_SETTINGS.exists():
        user = json.loads(MS_SETTINGS.read_text(encoding="utf-8"))
        if user.get("settings_version", 1) < 2:
            # שדרוג מגרסה 1: מעדכנים רק ערכים שהמשתמש לא שינה, ושומרים את כל השאר (כולל טלגרם)
            if user.get("breakout_rsi_max") == 75:
                user["breakout_rsi_max"] = 80
            for k, val in DEFAULTS.items():
                user.setdefault(k, val)
            user["settings_version"] = 2
            try:
                MS_SETTINGS.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
                print("קובץ ההגדרות שודרג לגרסה 2 (הערכים שלך נשמרו).")
            except Exception:
                pass
        if user.get("settings_version", 1) < DEFAULTS["settings_version"]:
            for k, val in DEFAULTS.items():
                user.setdefault(k, val)
            user["settings_version"] = DEFAULTS["settings_version"]
            try:
                MS_SETTINGS.write_text(json.dumps(user, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"קובץ ההגדרות שודרג לגרסה {DEFAULTS['settings_version']} (הערכים שלך נשמרו).")
            except Exception:
                pass
        s.update(user)
    return s


def is_quarterly_expiry(d):
    """יום שישי השלישי של מרץ/יוני/ספטמבר/דצמבר — Triple Witching."""
    return d.month in (3, 6, 9, 12) and d.weekday() == 4 and 15 <= d.day <= 21


S = load_ms_settings()


# ---------- כלים ----------
def market_state():
    """(state, expected_date, time_fraction). Uses the NYSE exchange calendar (holidays and
    early closes) when exchange_calendars is installed; otherwise falls back to weekdays only."""
    try:
        import scan_utils
        r = scan_utils.market_state()
        if r is not None:
            st, d, frac = r
            return st, d, (max(frac, 0.05) if st == "OPEN" else 1.0)
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        ny = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        sys.exit("חסר מסד אזורי זמן. הרץ: python -m pip install tzdata")
    open_t = ny.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = ny.replace(hour=16, minute=0, second=0, microsecond=0)
    wd = ny.weekday()
    if wd < 5 and open_t <= ny < close_t:
        frac = (ny - open_t).total_seconds() / (close_t - open_t).total_seconds()
        return "OPEN", ny.date(), max(frac, 0.05)
    d = ny.date()
    if wd >= 5 or ny < open_t:
        d -= dt.timedelta(days=1)
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
    return "CLOSED", d, 1.0


def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def send_telegram(text):
    tok, chat = S["telegram_bot_token"], S["telegram_chat_id"]
    if not tok or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)
    except Exception as e:
        print(f"שליחת טלגרם נכשלה: {e}")


# ---------- יקום המניות ----------
def _fetch_index(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (BreakoutScanner)"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    for table in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in table.columns]
        for name in ("Symbol", "Ticker"):
            if name in cols:
                syms = (table[name].astype(str).str.strip()
                        .str.replace(".", "-", regex=False).str.upper())
                syms = [x for x in syms if x and x.replace("-", "").isalpha() and len(x) <= 6]
                if len(syms) > 50:
                    return syms
    raise ValueError("לא נמצאה טבלת רכיבי מדד בעמוד")


# chosen safety minimums per index (a default we picked, not a sourced
# standard): below this the source is treated as failed so an entire index
# can never silently drop out of the scan universe
INDEX_MIN_COUNT = {"sp500": 400, "sp400": 300, "nasdaq100": 80}
UNIVERSE_JSON = BASE / "market_scanner_universe.json"


def _idx_fresh(entry):
    try:
        age = (time.time() - dt.datetime.fromisoformat(entry["fetched_at"]).timestamp()) / 86400
        return age <= S["universe_refresh_days"]
    except Exception:
        return False


def load_universe():
    # per-index cache with per-index fetched_at: staleness never comes from
    # file mtime (a fresh git checkout rewrites it), and a failed index keeps
    # its OLD fetched_at so it is retried on the next run instead of looking fresh
    cached, cached_legacy = {}, []
    if UNIVERSE_JSON.exists():
        try:
            meta = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8"))
            cached = meta.get("indexes", {}) or {}
            cached_legacy = meta.get("legacy", []) or []
        except Exception:
            cached, cached_legacy = {}, []
    if not cached and not cached_legacy and UNIVERSE.exists():
        cached_legacy = pd.read_csv(UNIVERSE)["ticker"].dropna().astype(str).tolist()

    indexes = {}
    errors = []
    for idx in S["universe"]:
        entry = cached.get(idx)
        if entry and entry.get("tickers") and _idx_fresh(entry):
            indexes[idx] = entry
            continue
        try:
            syms = _fetch_index(SOURCES[idx])
            if len(syms) < INDEX_MIN_COUNT.get(idx, 0):
                raise ValueError(
                    f"רק {len(syms)} רשומות (מינימום {INDEX_MIN_COUNT[idx]})")
            indexes[idx] = {"tickers": sorted(set(syms)),
                            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        except Exception as e:
            if idx == "nasdaq100" and not (entry and entry.get("tickers")):
                # in-repo static snapshot: full coverage, stamped now
                indexes[idx] = {"tickers": sorted(set(FALLBACK_NASDAQ100)),
                                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
                errors.append(f"{idx}: {type(e).__name__}: {e} (fallback snapshot used)")
            elif entry and entry.get("tickers"):
                indexes[idx] = entry   # keeps its original fetched_at
                errors.append(f"{idx}: {type(e).__name__}: {e} (נשמרה הרשימה השמורה)")
            else:
                errors.append(f"{idx}: {type(e).__name__}: {e} (אין רשימה שמורה!)")

    # the legacy CSV/one-time import covers an index only until that index has
    # its own entry; it is never a substitute once per-index entries exist
    legacy = [] if len(indexes) == len(S["universe"]) else list(cached_legacy)
    for e in errors:
        print(f"⚠️  עדכון רשימה נכשל — {e}")
    if not any(v.get("tickers") for v in indexes.values()) and not legacy:
        print("⚠️  אין רשימת מניות ואין גיבוי. סורק רק את extra_tickers.")

    if indexes != cached or legacy != cached_legacy:
        payload = {"indexes": indexes}
        if legacy:
            payload["legacy"] = legacy
        UNIVERSE_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        merged = sorted({t for v in indexes.values() for t in v["tickers"]} | set(legacy))
        pd.DataFrame({"ticker": merged}).to_csv(UNIVERSE, index=False)
        sizes = ", ".join(f"{k}:{len(v['tickers'])}" for k, v in sorted(indexes.items()))
        print(f"רשימת המניות עודכנה: {len(merged)} מניות ({sizes}"
              + (f", legacy:{len(legacy)}" if legacy else "") + ")")

    tickers = {t for v in indexes.values() for t in v["tickers"]} | set(legacy)
    tickers |= {t.strip().upper() for t in S["extra_tickers"] if t.strip()}
    return sorted(tickers)


# ---------- הורדת נתונים ----------
def download_all(tickers):
    import yfinance as yf
    out, failed = {}, []
    size = int(S["download_batch_size"])
    total = len(tickers)
    for i in range(0, total, size):
        chunk = tickers[i:i + size]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(chunk, period="1y", interval="1d", group_by="ticker",
                                 auto_adjust=False, threads=True, progress=False)
                break
            except Exception:
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            failed += chunk
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 205:
                    out[t] = sub
                else:
                    failed.append(t)
            except KeyError:
                failed.append(t)
        print(f"  הורדו {min(i + size, total)}/{total}")
        time.sleep(1)
    return out, failed


# ---------- ניתוח ----------
def _touches(highs, level, tol, sep):
    """כמה פעמים נפרדות המחיר נגע ברמה, וכמה ימים עברו מהנגיעה הראשונה."""
    idx = np.where(highs >= level * (1 - tol))[0]
    if len(idx) == 0:
        return 0, 0
    clusters, last = 1, idx[0]
    for i in idx[1:]:
        if i - last >= sep:
            clusters += 1
        last = i
    return clusters, len(highs) - idx[0]


def risk_pct(entry, stop):
    """Relative loss from entry to stop, as % of the ENTRY price."""
    return (entry - stop) / entry * 100


def _recovery(df, c, h, l, v, vfrac, base, r):
    """↗️ התאוששות: 15%-40% מתחת לשיא, שפל עולה בתוך בסיס, וחצייה של הגבוה של הבסיס במחזור."""
    close = base["close"]
    if not (S["recovery_min_dd_pct"] <= base["from_high_pct"] <= S["recovery_max_dd_pct"]):
        return None
    # מבנה W: שפל (bottom) → קפיצה לפיבוט → ירידה לשפל גבוה יותר → חצייה של הפיבוט
    B = int(S["recovery_base_days"])
    sma20 = float(c.rolling(20).mean().iloc[-1])
    for k in range(1, int(S["breakout_max_age_days"]) + 1):
        hi = h.iloc[-(B + k):-k].values
        lo = l.iloc[-(B + k):-k].values
        b = int(np.argmin(lo))
        bottom = float(lo[b])
        if bottom > float(l.iloc[-(40 + k):-k].min()) * 1.001:
            continue                                  # זה לא השפל של המהלך
        if b >= B - 3:
            continue
        # הפיבוט: הגבוה האחרון מאז השפל שאחריו היה תיקון, ושמאז לא נסגר מעליו
        cw = c.iloc[-(B + k):-k].values
        P = None
        for p in range(B - 2, b, -1):
            if hi[p] < hi[b + 1:p + 1].max():
                continue                              # לא הגבוה ביותר מאז השפל
            if (cw[p + 1:] > hi[p]).any() or (hi[p + 1:] > hi[p] * 1.01).any():
                continue
            P, recent_low = float(hi[p]), float(lo[p + 1:].min())
            break
        if P is None or not all(c.iloc[-k:] > P):
            continue
        pullback = (P - recent_low) / P * 100
        higher_low = recent_low > bottom * 1.01 and pullback >= 2.0
        depth = (P / bottom - 1) * 100
        ext = (close / P - 1) * 100
        xh, xl, xc = float(h.iloc[-k]), float(l.iloc[-k]), float(c.iloc[-k])
        cross_loc = (xc - xl) / (xh - xl) if xh > xl else 1.0
        cross_vol = float(v.iloc[-k]) / (vfrac if k == 1 else 1.0)
        cross_avg = float(v.iloc[-(50 + k):-k].mean())
        vol_ratio = cross_vol / cross_avg if cross_avg else 0.0
        ok = (higher_low and depth <= S["recovery_max_base_depth_pct"]
              and ext <= S["breakout_max_extension_pct"] and close > sma20
              and vol_ratio >= S["breakout_volume_multiplier"]
              and S["recovery_rsi_min"] <= r <= S["recovery_rsi_max"]
              and cross_loc >= S["breakout_close_location_min"])
        if not ok:
            continue
        bottom_pos = b
        win = hi
        stop = max(recent_low * 0.99, P * 0.97)
        risk = risk_pct(close, stop)
        # יעד ראשון: "מהלך נמדד" של תבנית W — גובה הבסיס מעל הפיבוט, ולא מעבר לשיא 52 השבועות
        target = min(P + (P - bottom), base["high52"])
        upside = (target / close - 1) * 100
        rr = upside / risk if risk > 0 else 0.0
        cross_date = df.index[-k].date()
        flags = []
        if close < base["sma200"]:
            flags.append("מתחת ל-SMA 200 — המגמה הארוכה עדיין יורדת, להקטין פוזיציה")
        if rr < S["recovery_min_rr"]:
            flags.append(f"⚠️ יחס סיכוי/סיכון נמוך (x{rr:.1f})")
        if is_quarterly_expiry(cross_date):
            flags.append("⚠️ יום פקיעה — המחזור עלול להיות מנופח")
        touches, _ = _touches(win, P, S["touch_tolerance_pct"] / 100, 3)
        score = (min(vol_ratio, 4) * 10 + min(rr, 5) * 6 + cross_loc * 10
                 + (recent_low / bottom - 1) * 200 - min(ext, 10) * 2 - (k - 1) * 5)
        return {**base, "status": "RECOVERY", "level": P, "dist_pct": ext, "age": k,
                "cross_date": cross_date.strftime("%d/%m"),
                "touches": touches, "base_days": B - bottom_pos, "vol_ratio": vol_ratio,
                "close_loc": cross_loc, "trigger": P, "stop": stop, "entry_top": None,
                "risk_pct": risk, "score": score, "higher_lows": True, "flags": flags,
                "bottom": bottom, "higher_low": recent_low, "target": target,
                "upside_pct": upside, "rr": rr}
    return None


def analyze(df, vfrac, expected):
    if df.index[-1].date().isoformat() < expected:
        return "STALE"

    c, h, l, v = (df[k].astype(float) for k in ("Close", "High", "Low", "Volume"))
    close, prev = float(c.iloc[-1]), float(c.iloc[-2])

    # מסנני נזילות ומגמה
    avg_vol = float(v.iloc[-51:-1].mean())
    if close < S["min_price"] or avg_vol * float(c.iloc[-51:-1].mean()) < S["min_dollar_volume_m"] * 1e6:
        return "FILTERED"
    sma50 = float(c.rolling(50).mean().iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1])
    trend_ok = close >= sma200 or not S["require_above_sma200"]
    if not trend_ok and not S["recovery_enabled"]:
        return "FILTERED"

    r = float(rsi(c).iloc[-1])
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ratio = float(tr.iloc[-10:].mean() / tr.iloc[-50:].mean())
    day_rng = float(h.iloc[-1] - l.iloc[-1])
    close_loc = (close - float(l.iloc[-1])) / day_rng if day_rng > 0 else 1.0

    N = int(S["resistance_lookback_days"])
    tol, sep = S["touch_tolerance_pct"] / 100, int(S["touch_separation_days"])
    high52 = float(h.max())
    base = {"close": close, "chg_pct": (close / prev - 1) * 100, "rsi": r,
            "sma50": sma50, "sma200": sma200, "atr_ratio": atr_ratio,
            "date": df.index[-1].date().isoformat(),
            "high52": high52, "from_high_pct": (1 - close / high52) * 100}

    def recovery():
        return _recovery(df, c, h, l, v, vfrac, base, r) if S["recovery_enabled"] else None

    if not trend_ok:            # מתחת ל-SMA 200: רק התאוששות רלוונטית
        return recovery()

    # --- 🚀 / 🔥 פריצה: חצתה בנר האחרון (k=1) או בזה שלפניו (k=2) ---
    for k in range(1, int(S["breakout_max_age_days"]) + 1):
        win = h.iloc[-(N + k):-k].values
        R = float(win.max())
        if not (all(c.iloc[-k:] > R) and float(c.iloc[-k - 1]) <= R):
            continue
        touches, base_days = _touches(win, R, tol, sep)
        ext = (close / R - 1) * 100
        cross_vol = float(v.iloc[-k]) / (vfrac if k == 1 else 1.0)
        cross_avg = float(v.iloc[-(50 + k):-k].mean())
        vol_ratio = cross_vol / cross_avg if cross_avg else 0.0
        # עוצמת נר החצייה עצמו (ביום שאחרי, נר של חזרה לקו נסגר נמוך באופן טבעי)
        xh, xl, xc = float(h.iloc[-k]), float(l.iloc[-k]), float(c.iloc[-k])
        cross_loc = (xc - xl) / (xh - xl) if xh > xl else 1.0
        quality = (touches >= S["min_touches"] and base_days >= S["min_base_days"]
                   and vol_ratio >= S["breakout_volume_multiplier"]
                   and r >= S["breakout_rsi_min"]
                   and cross_loc >= S["breakout_close_location_min"])
        if not quality:
            continue   # אולי זו "פריצה" מעל נר של אתמול בלבד — נבדוק את הפריצה של אתמול
        entry_top = R * (1 + S["extended_entry_zone_pct"] / 100)
        # כמה המניה רצה אל הקו לפני החצייה (מהשפל של 20 הימים שלפני נר החצייה)
        L = int(S["runup_lookback_days"])
        runup = (R / float(l.iloc[-(L + k):-k].min()) - 1) * 100
        handle = 0          # כמה ימים רצופים לפני החצייה המחיר נח עד 5% מתחת לקו
        for i in range(k + 1, k + 40):
            if float(h.iloc[-i]) >= R * 0.95:
                handle += 1
            else:
                break
        rsi_before = float(rsi(c).iloc[-k - 1])
        stretched = (runup > S["max_runup_pct"]
                     and (handle < S["min_handle_days"] or rsi_before >= S["handle_rsi_max"]))
        if ext > S["extended_max_pct"]:
            continue
        if ext <= S["breakout_max_extension_pct"] and r <= S["breakout_rsi_max"] and not stretched:
            status = "BREAKOUT"
        elif stretched and k >= 2 and close <= entry_top and r <= S["breakout_rsi_max"]:
            status = "BREAKOUT"     # רצה אל הקו, חצתה, וחזרה לאזור הקו — זו נקודת הכניסה
        else:
            status = "EXTENDED"     # פריצה אמיתית, אבל המחיר כבר ברח או שהמניה מתוחה — לא לרדוף
        cross_date = df.index[-k].date()
        stop = R * 0.98
        flags = []
        if base["from_high_pct"] >= S["recovery_min_dd_pct"]:
            flags.append(f"↗️ {base['from_high_pct']:.0f}% מתחת לשיא 52 שבועות — יש מקום לעלות")
        if stretched:
            flags.append(f"⚠️ עלתה {runup:.0f}% ב-{L} ימים ישר אל הקו, בלי מנוחה ({handle} ימים ליד הקו) — מתוחה")
            if status == "BREAKOUT":
                flags.append("↩️ חזרה לאזור הקו אחרי הריצה — כניסה אפשרית")
        if is_quarterly_expiry(cross_date):
            flags.append("⚠️ יום פקיעה — המחזור עלול להיות מנופח")
        if status == "EXTENDED" and r > S["breakout_rsi_max"]:
            flags.append(f"RSI {r:.0f} — קניית-יתר")
        score = (touches * 10 + min(base_days, 60) / 3 + min(vol_ratio, 4) * 10
                 + close_loc * 10 - min(ext, 10) * 2 - (k - 1) * 5
                 - (15 if stretched else 0))
        return {**base, "status": status, "level": R, "dist_pct": ext, "age": k,
                "cross_date": cross_date.strftime("%d/%m"),
                "touches": touches, "base_days": base_days, "vol_ratio": vol_ratio,
                "close_loc": close_loc, "trigger": R, "stop": stop, "entry_top": entry_top,
                "risk_pct": ((close if status == "BREAKOUT" else entry_top) / stop - 1) * 100,
                "score": score, "higher_lows": None, "flags": flags, "runup_pct": runup}

    # --- 🎯 על סף פריצה ---
    win = h.iloc[-(N + 1):-1].values
    R = float(win.max())
    dist = (R - close) / R * 100
    if not (0 <= dist <= S["setup_max_distance_pct"]):
        return recovery()
    touches, base_days = _touches(win, R, tol, sep)
    higher_lows = float(l.iloc[-10:].min()) > float(l.iloc[-40:-10].min())
    vol_ratio = (float(v.iloc[-1]) / vfrac) / avg_vol if avg_vol else 0.0
    ok = (touches >= S["min_touches"] and base_days >= S["min_base_days"]
          and S["setup_rsi_min"] <= r <= S["setup_rsi_max"]
          and atr_ratio <= S["setup_max_atr_ratio"]
          and (higher_lows or not S["setup_require_higher_lows"])
          and (close > sma50 or not S["setup_require_above_sma50"]))
    if not ok:
        return recovery()

    # סטופ: התמיכה הקרובה ביותר מתחת למחיר — השפל של 5 הימים או SMA 20 (הגבוה מביניהם)
    sma20 = float(c.rolling(20).mean().iloc[-1])
    candidates = [x for x in (float(l.iloc[-5:].min()), sma20) if x < close * 0.99]
    stop = max(candidates) if candidates else float(l.iloc[-10:].min())
    risk = (R / stop - 1) * 100

    L = int(S["runup_lookback_days"])
    runup = (R / float(l.iloc[-L:].min()) - 1) * 100
    flags = []
    handle = 0
    for i in range(1, 40):
        if float(h.iloc[-i]) >= R * 0.95:
            handle += 1
        else:
            break
    if runup > S["max_runup_pct"] and (handle < S["min_handle_days"] or r >= S["handle_rsi_max"]):
        flags.append(f"⚠️ עלתה {runup:.0f}% ב-{L} ימים ישר אל הקו, בלי מנוחה — מתוחה")
    if risk > S["max_risk_pct"]:
        flags.append(f"⚠️ סיכון גבוה ({risk:.1f}%)")
    # דחייה מהקו ב-3 הנרות האחרונים: נגע בקו, ירד לפחות 1% מהשיא ונסגר בחצי התחתון
    for i in (-3, -2, -1):
        hi, lo, cl = float(h.iloc[i]), float(l.iloc[i]), float(c.iloc[i])
        if hi >= R * 0.995 and hi > lo and (cl - lo) / (hi - lo) < 0.5 and (hi - cl) / hi >= 0.01:
            flags.append(f"⚠️ נדחתה מהקו ב-{df.index[i].strftime('%d/%m')}")
            break

    score = (touches * 10 + min(base_days, 60) / 3 + (S["setup_max_distance_pct"] - dist) * 10
             + (1 - atr_ratio) * 30 - (10 if len(flags) else 0))
    return {**base, "status": "SETUP", "level": R, "dist_pct": -dist, "age": 0,
            "cross_date": None,
            "touches": touches, "base_days": base_days, "vol_ratio": vol_ratio,
            "close_loc": close_loc, "trigger": R, "stop": stop, "entry_top": None,
            "risk_pct": risk, "score": score, "higher_lows": higher_lows, "flags": flags,
            "runup_pct": runup}


# ---------- מצב (התראה רק על חדשות) ----------
def load_state(session):
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if st.get("session") == session:
            return st
    return {"session": session, "seen": {}}


def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def append_log(hits, session):
    new = not LOG.exists()
    seen = set()
    if not new:
        with LOG.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            old_rows = list(reader)
            old_fields = reader.fieldnames or []
        seen = {(r["session"], r["ticker"], r["status"]) for r in old_rows}
        if "flags" not in old_fields:   # יומן מגרסה 1 — מוסיפים עמודת הערות ושומרים את כל השורות
            fields = old_fields + ["flags"]
            with LOG.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                for r in old_rows:
                    w.writerow({**r, "flags": ""})
    with LOG.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["session", "ticker", "status", "close", "level", "dist_pct",
                        "touches", "base_days", "vol_ratio", "rsi", "stop", "flags"])
        for t, x in hits:
            if (session, t, x["status"]) not in seen:
                w.writerow([session, t, x["status"], round(x["close"], 2), round(x["level"], 2),
                            round(x["dist_pct"], 2), x["touches"], x["base_days"],
                            round(x["vol_ratio"], 2), round(x["rsi"], 1), round(x["stop"], 2),
                            " / ".join(x.get("flags") or [])])


# ---------- פלט ----------
def line_text(t, x, intraday):
    vol = f"{'מחזור צפוי' if intraday else 'מחזור'} x{x['vol_ratio']:.1f}"
    flags = (" | " + " | ".join(x["flags"])) if x.get("flags") else ""
    if x["status"] == "BREAKOUT":
        return (f"{t} {x['close']:.2f} | חצתה {x['level']:.2f} ב-{x['cross_date']} "
                f"(+{x['dist_pct']:.1f}%) | {vol} | RSI {x['rsi']:.0f} | סטופ {x['stop']:.2f}{flags}")
    if x["status"] == "RECOVERY":
        return (f"{t} {x['close']:.2f} | {x['from_high_pct']:.0f}% מתחת לשיא | חצתה את הבסיס "
                f"{x['level']:.2f} ב-{x['cross_date']} (+{x['dist_pct']:.1f}%) | {vol} | RSI {x['rsi']:.0f} | "
                f"סטופ {x['stop']:.2f} ({x['risk_pct']:.1f}%) | יעד {x['target']:.2f} "
                f"(+{x['upside_pct']:.0f}%, סיכוי/סיכון x{x['rr']:.1f}){flags}")
    if x["status"] == "EXTENDED":
        return (f"{t} {x['close']:.2f} | חצתה {x['level']:.2f} ב-{x['cross_date']} "
                f"וכבר +{x['dist_pct']:.1f}% מעליו | {vol} | לא לרדוף — כניסה רק בחזרה ל-"
                f"{x['level']:.2f}–{x['entry_top']:.2f}, סטופ {x['stop']:.2f}{flags}")
    return (f"{t} {x['close']:.2f} | {abs(x['dist_pct']):.1f}% מתחת ל-{x['level']:.2f} | "
            f"{x['touches']} נגיעות, בסיס {x['base_days']} ימים | RSI {x['rsi']:.0f} | "
            f"סטופ {x['stop']:.2f} ({x['risk_pct']:.1f}%){flags}")


def cleanup_reports():
    """מוחק דוחות ישנים (הסורק רץ כל 30 דקות, כך שהם מצטברים מהר)."""
    limit = time.time() - float(S["keep_reports_days"]) * 86400
    for p in REPORTS.glob("market_*.html"):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except Exception:
            pass


def write_html(groups, new_keys, meta, stamp, intraday, file_tag):
    REPORTS.mkdir(exist_ok=True)
    cleanup_reports()
    vol_head = "מחזור צפוי" if intraday else "מחזור"

    def table(status, rows):
        if not rows:
            return f"<p class='none'>אין מניות כרגע.</p>"
        body = []
        if status == "RECOVERY":
            for t, x in rows:
                badge = " <span class='new'>חדש</span>" if (t, status) in new_keys else ""
                notes = "<br>".join(x.get("flags") or []) or "—"
                body.append(
                    f"<tr><td><b>{t}</b>{badge}</td><td>{x['close']:,.2f}</td>"
                    f"<td dir='ltr'>{x['chg_pct']:+.2f}%</td>"
                    f"<td><span dir='ltr'>-{x['from_high_pct']:.0f}%</span><br><small>שיא {x['high52']:,.2f}</small></td>"
                    f"<td>{x['level']:,.2f}<br><small><span dir='ltr'>+{x['dist_pct']:.1f}%</span> מעל</small></td>"
                    f"<td>{x['bottom']:,.2f} ← {x['higher_low']:,.2f}</td>"
                    f"<td dir='ltr'>x{x['vol_ratio']:.2f}</td><td>{x['rsi']:.0f}</td><td>{x['cross_date']}</td>"
                    f"<td>{x['stop']:,.2f}<br><small>{x['risk_pct']:.1f}% סיכון</small></td>"
                    f"<td>{x['target']:,.2f}<br><small><span dir='ltr'>+{x['upside_pct']:.0f}%</span> · "
                    f"סיכוי/סיכון x{x['rr']:.1f}</small></td><td class='flags'>{notes}</td></tr>")
            return (f"<table><tr><th>מניה</th><th>מחיר</th><th>שינוי</th><th>משיא 52 ש'</th>"
                    f"<th>גבוה הבסיס</th><th>שפל ← שפל עולה</th><th>{vol_head}</th><th>RSI</th>"
                    f"<th>תאריך חצייה</th><th>סטופ</th><th>יעד ראשון</th><th>הערות</th></tr>"
                    f"{''.join(body)}</table>")
        for t, x in rows:
            badge = " <span class='new'>חדש</span>" if (t, status) in new_keys else ""
            if status == "SETUP":
                dist = f"<span dir='ltr'>{abs(x['dist_pct']):.1f}%</span> מתחת"
                when = f"ATR x{x['atr_ratio']:.2f}"
                stop_cell = f"{x['stop']:,.2f}<br><small>{x['risk_pct']:.1f}% סיכון מהקו</small>"
            else:
                dist = f"<span dir='ltr'>+{x['dist_pct']:.1f}%</span> מעל"
                when = x["cross_date"]
                if status == "EXTENDED":
                    stop_cell = (f"כניסה: <span dir='ltr'>{x['level']:,.2f}–{x['entry_top']:,.2f}</span><br>"
                                 f"סטופ {x['stop']:,.2f} <small>({x['risk_pct']:.1f}%)</small>")
                else:
                    stop_cell = f"{x['stop']:,.2f}<br><small>{x['risk_pct']:.1f}% סיכון</small>"
            notes = "<br>".join(x.get("flags") or []) or "—"
            body.append(
                f"<tr><td><b>{t}</b>{badge}</td><td>{x['close']:,.2f}</td>"
                f"<td dir='ltr'>{x['chg_pct']:+.2f}%</td><td dir='ltr'>-{x['from_high_pct']:.0f}%</td>"
                f"<td>{x['level']:,.2f}</td><td>{dist}</td>"
                f"<td>{x['touches']} / {x['base_days']} ימים</td>"
                f"<td dir='ltr'>x{x['vol_ratio']:.2f}</td><td>{x['rsi']:.0f}</td><td>{when}</td>"
                f"<td>{stop_cell}</td><td class='flags'>{notes}</td></tr>")
        when_head = "התכווצות" if status == "SETUP" else "תאריך חצייה"
        stop_head = "כניסה / סטופ" if status == "EXTENDED" else "סטופ מוצע"
        return (f"<table><tr><th>מניה</th><th>מחיר</th><th>שינוי</th><th>משיא 52 ש'</th><th>התנגדות</th><th>מרחק</th>"
                f"<th>נגיעות / בסיס</th><th>{vol_head}</th><th>RSI</th>"
                f"<th>{when_head}</th><th>{stop_head}</th><th>הערות</th></tr>"
                f"{''.join(body)}</table>")

    banner = ""
    if intraday:
        banner = ("<p class='info'>⏳ <b>השוק פתוח — הנר עוד לא נסגר.</b> המחזור הוא הערכה לסוף היום. "
                  "פריצה תוך-יומית יכולה להימחק עד הסגירה.</p>")
    if meta["stale_share"] > 0.5:
        banner += ("<p class='warn'>⚠️ לרוב המניות עוד אין נר עדכני. אם הסגירה הייתה לפני פחות מ-30 "
                   "דקות, הרץ שוב מאוחר יותר.</p>")

    html = f"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>סורק שוק {stamp}</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;background:#1e222d;color:#d1d4dc;margin:20px}}
h3{{margin-top:28px}} table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #363a45;padding:8px;text-align:right;vertical-align:top}}
th{{background:#2a2e39}} small{{color:#868993}} .none{{color:#868993}}
.bo td{{background:#1b3a2a}} .rc td{{background:#2a1b3a}} .ex td{{background:#3a2a1b}} .su td{{background:#1b2a3a}}
td.flags{{color:#f7a600;font-size:12px}}
.new{{background:#f7a600;color:#000;border-radius:3px;padding:1px 5px;font-size:11px;margin-right:4px}}
.warn{{background:#3a331b;border:1px solid #b58900;padding:10px;border-radius:4px}}
.info{{background:#1b2a3a;border:1px solid #2962ff;padding:10px;border-radius:4px}}
.meta{{color:#868993;font-size:13px}}
</style></head><body>
<h2>סורק שוק — <span dir='ltr'>{stamp}</span></h2>
{banner}
<p class='meta'>נסרקו {meta['scanned']} מניות · עברו סינון נזילות ומגמה {meta['eligible']} ·
ללא נתונים {meta['failed']} · זמן ריצה {meta['secs']:.0f} שניות</p>
<h3>🚀 תחילת פריצה ({len(groups['BREAKOUT'])})</h3>
<div class='bo'>{table('BREAKOUT', groups['BREAKOUT'])}</div>
<h3>↗️ התאוששות — פריצת בסיס מתחת לשיא ({len(groups['RECOVERY'])})</h3>
<p class='meta'>מניה 15%–40% מתחת לשיא 52 השבועות שהפסיקה לרדת ובנתה תבנית W: שפל, קפיצה, ירידה לשפל גבוה יותר,
ועכשיו חצתה במחזור את הגבוה שבאמצע (הפיבוט). המחיר עדיין רחוק מהשיא, והגרף כבר מראה ביקוש. יעד ראשון = גובה הבסיס
מעל הפיבוט (מהלך נמדד), ולא מעבר לשיא. סטופ = מתחת לשפל העולה, ולא יותר מ-3% מתחת לפיבוט.</p>
<div class='rc'>{table('RECOVERY', groups['RECOVERY'])}</div>
<h3>🔥 פריצה חזקה — כבר רחוקה מהקו ({len(groups['EXTENDED'])})</h3>
<p class='meta'>פריצה איכותית שהמחיר כבר ברח ממנה יותר מ-4% (או RSI מעל 80). לא לרדוף —
הכניסה הנמוכת-סיכון היא בחזרה לאזור הקו, אם תגיע.</p>
<div class='ex'>{table('EXTENDED', groups['EXTENDED'])}</div>
<h3>🎯 על סף פריצה ({len(groups['SETUP'])})</h3>
<div class='su'>{table('SETUP', groups['SETUP'])}</div>
<p class='meta'>"נגיעות / בסיס" = כמה פעמים המחיר נדחה מההתנגדות, ומשך הבסיס בימים.
"התכווצות" = ATR של 10 ימים חלקי ATR של 50 (מתחת ל-1 = התנודתיות מצטמצמת לפני פריצה).
סטופ מוצע: לפריצה — 2% מתחת לקו שנפרץ; לסף פריצה — התמיכה הקרובה (שפל 5 ימים או SMA 20),
והסיכון מחושב מכניסה בקו. "יום פקיעה" = יום שישי השלישי ברבעון, שבו המחזור מנופח מלאכותית.</p>
</body></html>"""
    path = REPORTS / f"market_{file_tag}.html"
    path.write_text(html, encoding="utf-8")
    (REPORTS / "latest.html").write_text(html, encoding="utf-8")
    return path


# ---------- ראשי ----------
def main():
    t0 = time.time()
    state, expected_d, tfrac = market_state()
    intraday = state == "OPEN"
    expected = expected_d.isoformat()
    vfrac = max(float(np.interp(tfrac, VOL_CURVE_T, VOL_CURVE_V)), 0.25) if intraday else 1.0

    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    file_tag = now.strftime("%Y-%m-%d_%H%M") + ("_intraday" if intraday else "")
    print(f"סורק שוק — {stamp}" + (f"  ⏳ שוק פתוח ({tfrac:.0%} מהיום)" if intraday else ""))

    tickers = load_universe()
    print(f"מוריד נתונים ל-{len(tickers)} מניות...")
    data, failed = download_all(tickers)

    hits, eligible, stale = [], 0, 0
    for t, df in data.items():
        try:
            x = analyze(df, vfrac, expected)
        except Exception:
            failed.append(t)
            continue
        if x == "STALE":
            stale += 1
            continue
        if x == "FILTERED":
            continue
        eligible += 1
        if isinstance(x, dict):
            hits.append((t, x))

    groups = {}
    for st in GROUP_ORDER:
        rows = sorted([(t, x) for t, x in hits if x["status"] == st], key=lambda r: -r[1]["score"])
        groups[st] = rows[: int(S["max_results_per_group"])]

    # "חדש" = אירוע חדש למניה היום. מעבר מ"על סף" לפריצה = חדש;
    # מעבר בין 🚀 ל-🔥 = אותה פריצה, לא שולחים שוב.
    mstate = load_state(expected)
    mstate.setdefault("retest_alerted", [])
    seen = {t: FAMILY.get(st, st) for t, st in mstate["seen"].items()}
    new_keys = {(t, x["status"]) for st in groups for t, x in groups[st]
                if seen.get(t) != FAMILY[x["status"]]}
    # 🔥 שחזרה לאזור הקו = נקודת הכניסה שחיכינו לה. התראה אחת בלבד ביום לכל מניה.
    for t, x in groups["BREAKOUT"]:
        if mstate["seen"].get(t) == "EXTENDED" and t not in mstate["retest_alerted"]:
            x["flags"] = ["↩️ חזרה לאזור הקו אחרי שברחה — זו נקודת הכניסה"] + x["flags"]
            new_keys.add((t, "BREAKOUT"))
            mstate["retest_alerted"].append(t)
    for st in groups:
        for t, x in groups[st]:
            mstate["seen"][t] = x["status"]
    save_state(mstate)

    meta = {"scanned": len(tickers), "eligible": eligible, "failed": len(failed),
            "stale_share": stale / max(len(data), 1), "secs": time.time() - t0}
    report = write_html(groups, new_keys, meta, stamp, intraday, file_tag)
    if not intraday and meta["stale_share"] <= 0.5:
        append_log([r for st in groups for r in groups[st]], expected)

    print()
    for st in GROUP_ORDER:
        print(f"{LABELS[st]} ({len(groups[st])}):")
        for t, x in groups[st]:
            mark = "  ★ חדש" if (t, st) in new_keys else ""
            print("   " + line_text(t, x, intraday) + mark)
    print(f"\nדוח נשמר: {report}")

    to_send = [(t, x) for st in groups for t, x in groups[st]
               if not S["alert_only_new"] or (t, st) in new_keys]
    if to_send:
        head = ("⏳ סורק שוק — בזמן מסחר, לא סופי" if intraday else "🔎 סורק שוק — סגירה") + f"  {stamp}"
        lines = [head]
        for st in GROUP_ORDER:
            items = [(t, x) for t, x in to_send if x["status"] == st]
            if items:
                lines.append(LABELS[st])
                lines += ["• " + line_text(t, x, intraday) for t, x in items]
        send_telegram("\n".join(lines))

    if "--open" in sys.argv:
        webbrowser.open(report.as_uri())


if __name__ == "__main__":
    main()
