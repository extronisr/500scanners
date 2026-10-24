#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static Hebrew (RTL) mobile-friendly dashboard generator.

Sections:
  1. US market health  - reuses market_health_monitor_FINAL_v2 (score 0-100, 8 checks)
  2. Dip scanner       - S&P 500 dip screener (technical) + Google News headlines
  3. Biotech           - watchlist movers + fresh SEC 8-K filings

Headless, stdlib only, free sources only. Writes index.html next to this script.
"""
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import scan_utils

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# BIOTECH_DIR removed: config lives next to this script
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(OUT_DIR, "index.html")

sys.path.insert(0, BASE_DIR)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36"
# SEC asks for a real contact address. Left unchanged; may be overridden via the SEC_USER_AGENT env var.
SEC_UA = os.environ.get("SEC_USER_AGENT", "ScannersSiteDashboard/1.0 (contact: extronisr@gmail.com)")
JER = ZoneInfo("Asia/Jerusalem")

failures = []          # list of (section, detail) for the run report
stats = {"sp500_missing": 0, "biotech_missing": 0, "sec_failed": 0, "news_failed": 0,
         "sec_ok": 0, "bio_total": 0, "sp500_total": 0, "news_stale": 0}
NEWS_MAX_AGE_DAYS = 3    # news older than this (or undated) is not shown as a current alert
dip_meta = {"coverage_ok": True, "vfrac": None}
MIN_COVERAGE = 0.90   # below this share of data fetched, "no results" is not trustworthy


def fail(section, detail):
    failures.append((section, detail))


def http_get(url, timeout=20, ua=UA, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url, timeout=20, ua=UA, retries=2):
    """GET + parse JSON. Bounded retries (max `retries` extra tries) only for
    transient errors: timeouts, network errors, HTTP 429 and 5xx. 4xx such as
    403/404 fail immediately."""
    for attempt in range(retries + 1):
        try:
            return json.loads(http_get(url, timeout=timeout, ua=ua,
                                       accept="application/json").decode("utf-8"))
        except urllib.error.HTTPError as e:
            if attempt >= retries or not (e.code == 429 or e.code >= 500):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt >= retries:
                raise
        time.sleep(1.5 * (attempt + 1))


def esc(s):
    return htmlmod.escape(str(s), quote=True)


# ---------------------------------------------------------------- market health
CHECK_LABELS = {
    "s5fi": ("רוחב שוק · 50 יום", "אחוז מניות S&P 500 מעל ממוצע 50 יום"),
    "s5th": ("רוחב שוק · 200 יום", "אחוז מניות S&P 500 מעל ממוצע 200 יום"),
    "spx50": ("S&P 500 מול 50DMA", "מגמת הטווח הבינוני"),
    "spx200": ("S&P 500 מול 200DMA", "מגמת הטווח הארוך"),
    "vix": ("VIX", "מתחת 20 רגוע · 20–30 אזהרה · 30+ לחץ"),
    "tnx": ("אג״ח ארה״ב · 10 שנים", "גם הרמה וגם קפיצה בשלושה ימים משפיעות"),
    "brent": ("נפט Brent", "מחיר וקפיצה באחוזים בשלושה ימים"),
    "wti": ("נפט WTI", "מחיר וקפיצה באחוזים בשלושה ימים"),
}


def market_health_section():
    import market_lib as m
    try:
        d = m.get_all_data()
        score, status, states = m.calculate_score(d)
    except Exception as e:
        fail("market", f"get_all_data/calculate_score raised: {e}")
        return None

    def val(key):
        if key == "s5fi":
            return "לא זמין" if d["s5fi"] is None else f"{d['s5fi']:.1f}%"
        if key == "s5th":
            return "לא זמין" if d["s5th"] is None else f"{d['s5th']:.1f}%"
        if key == "spx50":
            if d["spx"] is None or d["dma50"] is None:
                return "לא זמין"
            return f"{d['spx']:,.0f} / {d['dma50']:,.0f}"
        if key == "spx200":
            if d["spx"] is None or d["dma200"] is None:
                return "לא זמין"
            return f"{d['spx']:,.0f} / {d['dma200']:,.0f}"
        if key == "vix":
            return "לא זמין" if d["vix"] is None else f"{d['vix']:.2f}"
        if key == "tnx":
            return "לא זמין" if d["tnx"] is None else f"{d['tnx']:.2f}%"
        if key == "brent":
            return "לא זמין" if d["brent"] is None else f"${d['brent']:.2f}"
        if key == "wti":
            return "לא זמין" if d["wti"] is None else f"${d['wti']:.2f}"
        return "לא זמין"

    checks = []
    for key in ["s5fi", "s5th", "spx50", "spx200", "vix", "tnx", "brent", "wti"]:
        state = states.get(key, "fail") if states else "fail"
        checks.append({
            "label": CHECK_LABELS[key][0],
            "desc": CHECK_LABELS[key][1],
            "value": val(key),
            "state": state,
            "explain": m.CHECK_EXPLANATIONS.get(key, ""),
        })
    # S&P 500 daily change for the hero line
    spx_chg = None
    try:
        spx_s = m.yahoo_close_series("^GSPC", "5d")
        if spx_s and len(spx_s) >= 2 and spx_s[-2]:
            spx_chg = (spx_s[-1] / spx_s[-2] - 1.0) * 100.0
    except Exception:
        pass
    n_pressure = sum(1 for c in checks if c["state"] == "fail")
    return {"score": score, "status": status, "checks": checks,
            "spx": d.get("spx"), "spx_chg": spx_chg, "n_pressure": n_pressure}


# ---------------------------------------------------------------- dip scanner
def spark_closes(symbols):
    """Yahoo spark endpoint: up to ~20 symbols per call. Returns
    {symbol: [closes]}. NOTE: spark quote carries no volume series,
    so volumes are fetched separately (v8 chart) for shortlisted tickers."""
    params = urllib.parse.urlencode(
        {"symbols": ",".join(symbols), "range": "1y", "interval": "1d"})
    url = "https://query1.finance.yahoo.com/v7/finance/spark?" + params
    try:
        data = http_get_json(url, timeout=30)
    except Exception as e:
        fail("dip", f"spark batch failed ({len(symbols)} syms): {e}")
        return {}
    out = {}
    for item in data.get("spark", {}).get("result", []):
        try:
            sym = item.get("symbol")
            q = item["response"][0]["indicators"]["quote"][0]
            closes = [float(c) for c in (q.get("close") or []) if c is not None]
            if closes:
                out[sym] = closes
        except Exception:
            continue
    return out


def rsi14(closes):
    if len(closes) < 16:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - 14, len(closes)):
        ch = closes[i] - closes[i - 1]
        if ch > 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = (gains / 14) / (losses / 14)
    return 100.0 - 100.0 / (1.0 + rs)


def load_sp500_tickers():
    cache = os.path.join(BASE_DIR, "sp500_tickers_cache.json")
    try:
        with open(cache, encoding="utf-8") as f:
            tickers = json.load(f)["tickers"]
        if len(tickers) > 400:
            return tickers
    except Exception as e:
        fail("dip", f"ticker cache unreadable: {e}")
    import market_lib as m
    tickers = m.load_sp500_tickers()
    return tickers or []


def news_headlines(query, n=2, ticker=None, name=None):
    q = urllib.parse.quote(query)
    url = (f"https://news.google.com/rss/search?q={q}"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        raw = http_get(url, timeout=15, accept="application/rss+xml")
        root = ET.fromstring(raw)
        heads = []
        for item in root.iter("item"):
            t = item.findtext("title")
            # only keep headlines that are really about this issuer (ticker collisions are common)
            if t and ticker and not scan_utils.headline_matches(t, ticker, name):
                continue
            # current alerts only: dated within the recent window, and event news, not
            # evergreen quote / forecast / historical-data pages
            pub_dt = scan_utils.parse_pub(item.findtext("pubDate"))
            if t and (not scan_utils.is_recent(pub_dt, max_age_days=NEWS_MAX_AGE_DAYS)
                      or scan_utils.is_evergreen(t)):
                stats["news_stale"] += 1
                continue
            if t:
                heads.append({
                    "title": t.strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "pub": (item.findtext("pubDate") or "").strip(),
                    "pub_ts": pub_dt.timestamp() if pub_dt else None,
                })
            if len(heads) >= n:
                break
        return heads
    except Exception as e:
        stats["news_failed"] += 1
        fail("dip", f"news RSS failed for {query!r}: {e}")
        return []


def fmt_pub(pub):
    """'Sat, 11 Oct 2026 02:08:00 GMT' -> '2:08, 11.10.2026' (best effort)."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub).astimezone(JER)    # show Israel time
        return f"{dt.day}.{dt.month}.{dt.year}, {dt.hour}:{dt.minute:02d}"
    except Exception:
        return ""


def _dip_vfrac():
    """Fraction of the daily volume normally traded by now (None when the US market is closed)."""
    try:
        import numpy as np
        import breakout_lib as b
        state, _, tfrac = b.market_state()      # uses the NYSE calendar (holidays, early closes)
        if state != "OPEN":
            return None
        return max(float(np.interp(tfrac, b.VOL_CURVE_T, b.VOL_CURVE_V)), 0.05)
    except Exception:
        return None


def dip_scan_section():
    tickers = load_sp500_tickers()
    if not tickers:
        fail("dip", "no tickers available")
        return []
    closes_map = {}
    for i in range(0, len(tickers), 20):
        closes_map.update(spark_closes(tickers[i:i + 20]))
        time.sleep(0.2)
    stats["sp500_total"] = len(tickers)
    stats["sp500_missing"] = len(tickers) - len(closes_map)
    dip_meta["coverage_ok"] = len(closes_map) >= MIN_COVERAGE * len(tickers)
    if not dip_meta["coverage_ok"]:
        fail("dip", f"only {len(closes_map)}/{len(tickers)} tickers fetched; dip results unreliable")
    # price-based pre-filter on closes only (volume comes later, per candidate)
    prelim = []
    for sym, closes in closes_map.items():
        if len(closes) < 130:
            continue
        last = closes[-1]
        high52 = max(closes[-252:]) if len(closes) >= 252 else max(closes)
        if high52 <= 0:
            continue
        depth = (high52 - last) / high52 * 100.0
        if not (10.0 <= depth <= 30.0):
            continue
        ref_low = min(closes[-126:-3])
        if last < ref_low:
            continue  # breakdown, not a dip
        if not (ref_low <= last <= ref_low * 1.03):
            continue
        r = rsi14(closes)
        if r is None or r >= 30.0:
            continue
        prelim.append((sym, depth, ref_low, r, last))
    cands = []
    vfrac_now = _dip_vfrac()
    dip_meta["vfrac"] = vfrac_now
    for sym, depth, ref_low, r, last in prelim:
        closes, vols = chart_closes_volumes(sym, "3mo")
        if not vols or len(vols) < 22:
            continue
        vc = scan_utils.volume_context(vols, vfrac_now)
        tier, vol_mult = scan_utils.volume_signal(vc, 1.5, strict=True)
        if tier is None:
            continue
        cands.append({
            "vol_tier": tier,
            "ticker": sym,
            "depth": depth,
            "above_low_pct": (last - ref_low) / ref_low * 100.0,
            "rsi": r,
            "vol_mult": vol_mult,
            "last": last,
        })
        time.sleep(0.15)
    cands.sort(key=lambda c: c["depth"], reverse=True)
    cands = cands[:20]
    try:
        import market_lib as _m
        sp_names = _m.load_sp500_names()
    except Exception:
        sp_names = {}
    for c in cands:
        nm = sp_names.get(c["ticker"])
        c["headlines"] = news_headlines(f'"{nm}" {c["ticker"]}' if nm else c["ticker"] + " stock",
                                        ticker=c["ticker"], name=nm)
        time.sleep(0.2)
    return cands


# ---------------------------------------------------------------- biotech
def chart_closes_volumes(symbol, range_value="3mo"):
    encoded = urllib.parse.quote(symbol, safe="")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
           f"?range={range_value}&interval=1d&events=history")
    try:
        data = http_get_json(url)
        result = data["chart"]["result"][0]
        q = result["indicators"]["quote"][0]
        closes = [float(x) for x in (q.get("close") or []) if x is not None]
        vols = [float(x) if x is not None else 0.0 for x in (q.get("volume") or [])]
        # align: volume list may be longer/shorter; pad with zeros on the left
        if len(vols) < len(closes):
            vols = [0.0] * (len(closes) - len(vols)) + vols
        else:
            vols = vols[-len(closes):] if closes else []
        return closes, vols
    except Exception as e:
        stats["biotech_missing"] += 1
        fail("biotech", f"chart failed for {symbol}: {e}")
        return None, None


def edgar_8k_last_days(cik, days=3):
    """8-K filings in the last `days` days via EDGAR submissions JSON (no auth)."""
    cik10 = str(cik).strip().zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    try:
        data = http_get_json(url, timeout=20, ua=SEC_UA)
        stats["sec_ok"] += 1
    except Exception as e:
        stats["sec_failed"] += 1
        fail("biotech", f"EDGAR failed for CIK {cik10}: {e}")
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    out = []
    for i in range(len(forms)):
        if forms[i] != "8-K":
            continue
        fdate = recent["filingDate"][i]
        if fdate < cutoff:
            continue
        try:
            items = recent.get("items", [""])[i] or ""
        except Exception:
            items = ""
        out.append({"date": fdate, "items": items,
                    "doc": recent["primaryDocument"][i],
                    "accession": recent["accessionNumber"][i]})
    return out


def biotech_alerts():
    """Alerts feed like the app: price/volume spikes, 8-K filings, news.
    Also returns a full-watchlist radar (price, change, volume, severity, why)."""
    cfg_path = os.path.join(BASE_DIR, "biotech_config.json")
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        watchlist = cfg["watchlist"]
    except Exception as e:
        fail("biotech", f"config unreadable: {e}")
        return {"alerts": [], "radar": []}
    p_spike = cfg.get("price_spike_pct", 8.0)
    p_crit = cfg.get("price_critical_pct", 15.0)
    v_mult = cfg.get("volume_mult", 3.0)
    v_crit = cfg.get("volume_critical_mult", 5.0)
    alerts = []
    radar = []
    stats["bio_total"] = len(watchlist)
    vfrac_now = _dip_vfrac()
    today = datetime.now(timezone.utc).date().isoformat()
    for w in watchlist:
        ticker, name, cik = w.get("ticker"), w.get("name", ""), w.get("cik", "")
        closes, vols = chart_closes_volumes(ticker)
        if not closes or len(closes) < 22:
            continue
        chg = (closes[-1] / closes[-2] - 1.0) * 100.0 if closes[-2] else 0.0
        vc = scan_utils.volume_context(vols, vfrac_now)
        v_tier, v_val = scan_utils.volume_signal(vc, v_mult, strict=False)
        shown = v_val if v_tier else (vc["confirmed"] if vc and vc["confirmed"] is not None else (vc or {}).get("estimate"))
        vol_avg = 1.0 if shown is not None else 0.0
        vol_val = shown or 0.0
        vol_hit = v_tier is not None
        vol_crit_hit = scan_utils.volume_signal(vc, v_crit, strict=False)[0] is not None
        filings = edgar_8k_last_days(cik, days=3) if cik else []
        rsev = 0
        if abs(chg) >= p_crit or vol_crit_hit:
            rsev = 3
        elif abs(chg) >= p_spike or vol_hit:
            rsev = 2
        elif abs(chg) >= 2.0 and (shown is not None and shown >= 2.5):
            rsev = 1   # early move: +2% on >=2.5x volume (approved trial addition)
        radar.append({"ticker": ticker, "name": name, "chg": chg,
                      "close": closes[-1], "vol_mult": shown, "sev": rsev, "why": ""})
        if abs(chg) >= p_spike or vol_hit:
            sev = "crit" if (abs(chg) >= p_crit or vol_crit_hit) else "warn"
            direction = "עלייה חדה" if chg >= 0 else "ירידה חדה"
            price = closes[-1]
            vol_txt = (scan_utils.volume_text(v_tier or "confirmed", vol_val, vfrac_now)
                       if vol_avg > 0 else "מחזור לא זמין")
            alerts.append({"sev": sev, "kind": "price", "ticker": ticker,
                           "name": name, "date": today, "ts": time.time(),
                           "head": f"{ticker}: {direction} {chg:+.1f}%",
                           "sub": f"מחיר {price:.2f} · {vol_txt}",
                           "link": f"https://finance.yahoo.com/quote/{ticker}",
                           "source": "Yahoo Finance"})
        for f in filings:
            try:
                cik_num = str(int(cik))
            except Exception:
                cik_num = (cik or "").lstrip("0") or "0"
            acc = f["accession"].replace("-", "")
            doc_url = (f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}/{f['doc']}"
                       if f.get("doc") else
                       f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K")
            alerts.append({"sev": "warn", "kind": "sec", "ticker": ticker,
                           "name": name, "date": f["date"],
                           "ts": datetime.fromisoformat(f["date"]).replace(tzinfo=timezone.utc).timestamp(),
                           "head": f"{ticker}: דיווח 8-K חדש" + (f" · סעיפים {f['items']}" if f["items"] else ""),
                           "sub": name,
                           "link": doc_url, "source": "SEC EDGAR"})
        if abs(chg) >= p_spike or vol_hit or filings:
            for h in news_headlines(f'"{name}" {ticker}' if name else ticker + " stock", n=2, ticker=ticker, name=name):
                if radar and radar[-1]["ticker"] == ticker and not radar[-1]["why"]:
                    radar[-1]["why"] = h["title"]
                pub = fmt_pub(h["pub"]) or today
                alerts.append({"sev": "info", "kind": "news", "ticker": ticker,
                               "name": name, "date": pub, "ts": h.get("pub_ts"),
                               "head": h["title"], "sub": name,
                               "link": h["link"], "source": "Google News RSS"})
        time.sleep(0.15)  # be polite to EDGAR
    alerts.sort(key=lambda a: a.get("ts") or 0, reverse=True)   # newest first, by real timestamp
    alerts.sort(key=lambda a: {"crit": 0, "warn": 1, "info": 2}[a["sev"]])
    radar.sort(key=lambda r: (-r["sev"], -abs(r["chg"])))
    return {"alerts": alerts, "radar": radar}


def breakout_section():
    """Run the vendored breakout scanner. Returns groups + recovery learning history."""
    import numpy as np
    import breakout_lib as b

    S = b.S
    try:
        state, expected_d, tfrac = b.market_state()
    except SystemExit:
        fail("breakout", "market_state: no tzdata")
        return None
    except Exception as e:
        fail("breakout", f"market_state: {e}")
        return None
    intraday = state == "OPEN"
    expected = expected_d.isoformat()
    vfrac = max(float(np.interp(tfrac, b.VOL_CURVE_T, b.VOL_CURVE_V)), 0.05) if intraday else 1.0

    try:
        tickers = b.load_universe()
    except Exception as e:
        fail("breakout", f"load_universe: {e}")
        return None
    try:
        data, failed = b.download_all(tickers)
    except Exception as e:
        fail("breakout", f"download_all: {e}")
        return None

    hits, price_map = [], {}
    for t, df in data.items():
        try:
            x = b.analyze(df, vfrac, expected)
        except Exception:
            continue
        if isinstance(x, dict):
            hits.append((t, x))
        try:
            price_map[t] = float(df["Close"].iloc[-1])
        except Exception:
            pass

    groups = {}
    for st in b.GROUP_ORDER:
        rows = sorted([(t, x) for t, x in hits if x["status"] == st],
                      key=lambda r: -r[1]["score"])
        groups[st] = rows[:int(S["max_results_per_group"])]

    # ---- recovery learning history (persists in repo, committed by workflow)
    hist_path = os.path.join(BASE_DIR, "recovery_history.json")
    try:
        hist = json.load(open(hist_path, encoding="utf-8"))
        sigs = hist.get("signals", [])
    except Exception:
        sigs = []
    seen_keys = {(s["ticker"], s["date"]) for s in sigs}
    today_dm = expected_d.strftime("%d/%m")
    for t, x in groups.get("RECOVERY", []):
        key = (t, x["date"])
        if key not in seen_keys:
            sigs.append({"ticker": t, "date": x["date"],
                         "signal_price": round(x["close"], 2),
                         "pivot": round(x["level"], 2),
                         "target": round(x.get("target") or 0, 2),
                         "stop": round(x["stop"], 2)})
            seen_keys.add(key)
    # annotate with current performance
    history = []
    for s in sigs:
        cur = price_map.get(s["ticker"])
        if cur:
            pct = (cur / s["signal_price"] - 1) * 100 if s["signal_price"] else 0
            tgt_hit = bool(s["target"] and cur >= s["target"])
        else:
            pct, tgt_hit = None, False
        history.append({**s, "current": round(cur, 2) if cur else None,
                        "pct": round(pct, 1) if pct is not None else None,
                        "target_hit": tgt_hit})
    history.sort(key=lambda s: (s["pct"] is None, -(s["pct"] or 0)))
    try:
        json.dump({"signals": sigs}, open(hist_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception as e:
        fail("breakout", f"history save: {e}")

    try:
        pullback = pullback_section(data)
    except Exception as e:
        fail("pullback", f"pullback scan: {e}")
        pullback = []

    return {"groups": groups, "history": history, "intraday": intraday,
            "scanned": len(data), "universe": len(tickers), "failed": len(failed),
            "today_dm": today_dm, "pullback": pullback}





# ---------------------------------------------------------------- market overview
INDEX_SYMBOLS = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq Composite"),
                 ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000")]
SECTOR_SYMBOLS = [("XLK", "טכנולוגיה"), ("XLF", "פיננסים"), ("XLV", "בריאות"),
                  ("XLE", "אנרגיה"), ("XLY", "צריכה מחזורית"), ("XLP", "צריכה בסיסית"),
                  ("XLI", "תעשייה"), ("XLU", "תשתיות ציבוריות"), ("XLRE", "נדל״ן"),
                  ("XLB", "חומרי גלם"), ("XLC", "תקשורת")]
CHANGE3_SYMBOLS = [("^VIX", "VIX", "נקודות", False), ("^TNX", "US 10Y", "נקודות אחוז", False),
                   ("BZ=F", "Brent", "דולר", True), ("CL=F", "WTI", "דולר", True)]


def market_overview_section():
    """Indices, sector heatmap and the 3-trading-day card. One spark batch (20 symbols)."""
    syms = ([s for s, _ in INDEX_SYMBOLS] + [s for s, _ in SECTOR_SYMBOLS]
            + ["SMH"] + [s for s, _, _, _ in CHANGE3_SYMBOLS])
    data = spark_closes(syms)
    if not data:
        fail("overview", "spark batch failed for market overview")
        return None
    out = {"indices": [], "sectors": [], "change3": [], "smh": None}
    for sym, name in INDEX_SYMBOLS:
        cl = data.get(sym)
        if not cl or len(cl) < 60:
            continue
        sma50 = sum(cl[-50:]) / 50.0
        sma200 = sum(cl[-200:]) / 200.0 if len(cl) >= 200 else None
        out["indices"].append({
            "sym": sym, "name": name, "value": cl[-1],
            "day_pct": (cl[-1] / cl[-2] - 1.0) * 100.0 if len(cl) >= 2 and cl[-2] else None,
            "above50": cl[-1] >= sma50,
            "above200": (cl[-1] >= sma200) if sma200 else None,
            "spark": cl[-20:]})
    def _chg3(sym):
        cl = data.get(sym)
        if not cl or len(cl) < 4:
            return None, None
        abs3 = cl[-1] - cl[-4]
        pct3 = (cl[-1] / cl[-4] - 1.0) * 100.0 if cl[-4] else None
        return abs3, pct3
    for sym, name in SECTOR_SYMBOLS + [("SMH", "סמיקונדקטורס")]:
        cl = data.get(sym)
        if not cl or len(cl) < 4:
            continue
        day = (cl[-1] / cl[-2] - 1.0) * 100.0 if cl[-2] else None
        _, pct3 = _chg3(sym)
        row = {"sym": sym, "name": name, "day_pct": day, "pct3": pct3}
        if sym == "SMH":
            out["smh"] = row
        else:
            out["sectors"].append(row)
    for sym, name, unit, show_pct in CHANGE3_SYMBOLS:
        abs3, pct3 = _chg3(sym)
        if abs3 is None:
            continue
        out["change3"].append({"sym": sym, "name": name, "unit": unit,
                               "abs3": abs3, "pct3": pct3 if show_pct else None})
    if not out["indices"] and not out["sectors"]:
        return None
    return out


# ---------------------------------------------------------------- CNN Fear & Greed
CNN_URL = "https://production.dataviz.cnn.io/public/fear-and-greed/graph"
CNN_SNAPSHOT = os.path.join(BASE_DIR, "cnn_snapshot.json")
RATING_HE = {"extreme fear": "פחד קיצוני", "fear": "פחד", "neutral": "נייטרלי",
             "greed": "חמדנות", "extreme greed": "חמדנות קיצונית"}


def cnn_section():
    """Try the live CNN feed; fall back to the bundled snapshot with its own
    timestamp. The snapshot is only rewritten after a successful live fetch."""
    data, live = None, False
    try:
        raw = http_get_json(CNN_URL, timeout=8, retries=0)
        fg = raw.get("fear_and_greed", {}) if isinstance(raw, dict) else {}
        if fg.get("score") is not None:
            def _prev(val, label):
                v = val if not isinstance(val, dict) else val.get("score")
                return {"label": label, "value": round(v)} if v is not None else None
            prev = [p for p in [_prev(fg.get("previous_close"), "סגירה קודמת"),
                                _prev(fg.get("previous_1_week"), "לפני שבוע"),
                                _prev(fg.get("previous_1_month"), "לפני חודש"),
                                _prev(fg.get("previous_1_year"), "לפני שנה")] if p]
            data = {"score": round(float(fg["score"])), "rating": fg.get("rating", ""),
                    "previous": prev, "source": "https://www.cnn.com/markets/fear-and-greed",
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            live = True
    except Exception as e:
        fail("cnn", f"CNN fetch failed: {e}")
    if live:
        try:
            json.dump(data, open(CNN_SNAPSHOT, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception as e:
            fail("cnn", f"snapshot save: {e}")
    else:
        try:
            data = json.load(open(CNN_SNAPSHOT, encoding="utf-8"))
        except Exception as e:
            fail("cnn", f"snapshot unreadable: {e}")
            return None
    data["live"] = live
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(data["timestamp"])).total_seconds() / 3600.0
    except Exception:
        age_h = None
    data["age_hours"] = age_h
    return data


# ---------------------------------------------------------------- pullback in uptrend
def pullback_section(data_map):
    """Pullback-in-uptrend category over the breakout universe (observation,
    not a validated bottom signal). Computed from the same 1y data the scanner uses."""
    out = []
    for t, df in (data_map or {}).items():
        try:
            closes = [float(x) for x in df["Close"].tolist()]
        except Exception:
            continue
        r = rsi14(closes)
        pb = scan_utils.is_pullback_uptrend(closes, r)
        if not pb:
            continue
        volr = None
        try:
            vols = [float(x) for x in df["Volume"].tolist()]
            base = sum(vols[-51:-1]) / 50.0
            volr = vols[-1] / base if base > 0 else None
        except Exception:
            pass
        chg = (closes[-1] / closes[-2] - 1.0) * 100.0 if len(closes) >= 2 and closes[-2] else 0.0
        out.append({"ticker": t, "close": closes[-1], "chg": chg,
                    "rsi": r, "volr": volr, **pb})
    out.sort(key=lambda x: -x["dd"])
    return out[:24]


# ---------------------------------------------------------------- catalysts
def _expected_to_date(exp):
    """ISO date stays exact; 'Q4 2026'/'2H 2026' become a mid-window estimate."""
    exp = (exp or "").strip()
    try:
        return datetime.strptime(exp, "%Y-%m-%d").date(), False
    except ValueError:
        pass
    m = re.match(r"^Q([1-4])\s+(\d{4})$", exp)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        return datetime(y, q * 3 - 1, 15).date(), True
    m = re.match(r"^2H\s+(\d{4})$", exp)
    if m:
        return datetime(int(m.group(1)), 11, 1).date(), True
    return None, True


def catalyst_section():
    path = os.path.join(BASE_DIR, "biotech_catalysts.json")
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        fail("catalysts", f"config unreadable: {e}")
        return None
    today = datetime.now(JER).date()
    upcoming, passed = [], []
    for c in cfg.get("events", []):
        d, approx = _expected_to_date(c.get("expected"))
        days = (d - today).days if d else None
        item = dict(c)
        item.update({"days": days, "approx": approx or bool(c.get("approx")),
                     "bucket": scan_utils.catalyst_bucket(days, approx or bool(c.get("approx")))})
        if days is None or days >= 0:
            upcoming.append(item)
        else:
            passed.append(item)
    upcoming.sort(key=lambda x: (x["days"] is None, x["days"] or 9999))
    passed.sort(key=lambda x: -(x["days"] or -9999))
    return {"upcoming": upcoming, "passed": passed, "asof": cfg.get("asof", "")}


# ---------------------------------------------------------------- clinical trials
CT_STATUS_HE = {
    "RECRUITING": "מגייס", "ACTIVE_NOT_RECRUITING": "פעיל, גיוס הושלם",
    "NOT_YET_RECRUITING": "טרם גיוס", "COMPLETED": "הושלם",
    "ENROLLING_BY_INVITATION": "גיוס בהזמנה", "SUSPENDED": "מושהה",
    "TERMINATED": "הופסק", "WITHDRAWN": "בוטל", "UNKNOWN": "לא ידוע",
}
TRIALS_CACHE = os.path.join(BASE_DIR, "clinical_trials_cache.json")


def _ct_one(nct, ticker):
    url = f"https://clinicaltrials.gov/api/v2/studies/{nct}"
    data = http_get_json(url, timeout=15)
    ps = data.get("protocolSection", {})
    st = ps.get("statusModule", {})
    de = ps.get("designModule", {})
    return {"ticker": ticker, "status": st.get("overallStatus", ""),
            "enrollment": (de.get("enrollmentInfo") or {}).get("count"),
            "primary_completion": (st.get("primaryCompletionDateStruct") or {}).get("date", ""),
            "last_update": (st.get("lastUpdatePostDateStruct") or {}).get("date", "")}


def trials_section():
    """Live ClinicalTrials.gov baseline for the NCT ids in biotech_trials.json.
    On fetch trouble the bundled cache is shown with a staleness note."""
    try:
        cfg = json.load(open(os.path.join(BASE_DIR, "biotech_trials.json"), encoding="utf-8"))
        nct_map = cfg["nct_by_ticker"]
    except Exception as e:
        fail("trials", f"config unreadable: {e}")
        return None
    try:
        cache = json.load(open(TRIALS_CACHE, encoding="utf-8"))
    except Exception:
        cache = {"fetched_at": "", "studies": {}}
    pairs = [(nct, t) for t, ncts in nct_map.items() for nct in ncts]
    studies, errors = {}, []
    for nct, t in pairs:
        try:
            studies[nct] = _ct_one(nct, t)
        except Exception as e:
            errors.append(nct)
            fail("trials", f"{nct}: {e}")
        time.sleep(0.35)   # polite rate
    changed = []
    if studies:
        old = cache.get("studies", {})
        for nct, s in studies.items():
            o = old.get(nct)
            if o and (o.get("status") != s["status"]
                      or o.get("primary_completion") != s["primary_completion"]
                      or str(o.get("enrollment")) != str(s["enrollment"])):
                changed.append(nct)
        cache = {"fetched_at": datetime.now(JER).date().isoformat(), "studies": studies}
        try:
            json.dump(cache, open(TRIALS_CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception as e:
            fail("trials", f"cache save: {e}")
    shown = studies or cache.get("studies", {})
    rows = []
    for nct, t in pairs:
        s = shown.get(nct)
        if not s:
            continue
        rows.append({"nct": nct, "ticker": s.get("ticker", t),
                     "status": s.get("status", ""),
                     "status_he": CT_STATUS_HE.get(s.get("status", ""), s.get("status", "")),
                     "enrollment": s.get("enrollment"),
                     "primary_completion": s.get("primary_completion") or "—",
                     "last_update": s.get("last_update") or "—",
                     "changed": nct in changed,
                     "bad": s.get("status") in ("TERMINATED", "WITHDRAWN", "SUSPENDED")})
    return {"rows": rows, "live": bool(studies), "fetched_at": cache.get("fetched_at", ""),
            "errors": errors, "total": len(pairs)}


# ---------------------------------------------------------------- SEC watcher (broad)
def edgar_recent(cik, days=5):
    """All notable filings in the last `days` days via EDGAR submissions JSON,
    with severity per form type (material 8-K items, dilution, insider, periodic)."""
    cik10 = str(cik).strip().zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    data = http_get_json(url, timeout=20, ua=SEC_UA)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    out = []
    for i in range(len(forms)):
        fdate = (recent.get("filingDate") or [""])[i]
        if not fdate or fdate < cutoff:
            continue
        form = forms[i]
        if form not in scan_utils.SEC_FORM_SEVERITY:
            continue
        items = ""
        try:
            items = recent.get("items", [""])[i] or ""
        except Exception:
            pass
        sev, label = scan_utils.sec_form_severity(form, items)
        out.append({"date": fdate, "form": form, "items": items, "sev": sev, "label": label,
                    "doc": (recent.get("primaryDocument") or [""])[i],
                    "accession": (recent.get("accessionNumber") or [""])[i]})
    return out


def sec_watch_section(watchlist):
    """One submissions call per company; returns broadened watcher events and errors."""
    events, errors = [], []
    for w in watchlist:
        ticker, cik = w.get("ticker"), w.get("cik", "")
        if not cik:
            continue
        try:
            for f in edgar_recent(cik, days=5):
                try:
                    cik_num = str(int(cik))
                except Exception:
                    cik_num = (cik or "").lstrip("0") or "0"
                acc = f["accession"].replace("-", "")
                url = (f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}/{f['doc']}"
                       if f.get("doc") else
                       f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}")
                events.append({**f, "ticker": ticker, "url": url})
        except Exception as e:
            errors.append(ticker)
            fail("secwatch", f"{ticker}: {e}")
        time.sleep(0.35)   # declared UA, well under 2 req/s
    events.sort(key=lambda e: (e["date"], -e["sev"]), reverse=True)
    return {"events": events, "errors": errors, "lookback_days": 5}


# ---------------------------------------------------------------- HTML
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f1f2f4;color:#1f2937;font-family:-apple-system,'Segoe UI',Arial,sans-serif;
direction:rtl;padding:12px 12px 96px;max-width:720px;margin:0 auto}
.hdr{display:flex;align-items:flex-start;justify-content:space-between;padding:6px 2px 12px;gap:10px;position:relative}
.hdr h1{font-size:1.65rem;font-weight:800}
.hdr .ts{color:#6b7280;font-size:.78rem;margin-top:4px}
.brand{position:absolute;left:50%;top:11px;transform:translateX(-50%);
font-weight:800;font-size:1.05rem;color:#16202e;white-space:nowrap}
.refresh-btn{background:#16202e;color:#fff;border:none;border-radius:10px;padding:10px 18px;
font-weight:700;font-size:.9rem;cursor:pointer;font-family:inherit;white-space:nowrap}
.hero{background:#16202e;color:#fff;border-radius:20px;padding:22px 20px;display:flex;
align-items:center;gap:20px;margin-bottom:12px}
.hero .hinfo{flex:1;display:flex;flex-direction:column;gap:10px;align-items:flex-start}
.status-badge{display:inline-block;font-weight:800;font-size:.8rem;border-radius:8px;padding:4px 14px}
.hero .pressure{font-size:1.12rem;font-weight:700}
.hero .note{color:#9ca3af;font-size:.78rem}
.hero .spx{font-size:.95rem;font-weight:700}
.hero .spx .up{color:#4ade80}.hero .spx .down{color:#f87171}
.grid2{display:flex;flex-direction:column;gap:10px}
.chk{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:13px 12px;
display:flex;align-items:center;gap:10px}
.chk .dot{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.dot.pass{background:#22c55e}.dot.warn{background:#f59e0b}.dot.fail{background:#ef4444}
.chk .ct{flex:1;min-width:0}
.chk .ct b{display:block;font-size:.92rem}
.chk .ct span{display:block;color:#6b7280;font-size:.75rem;margin-top:2px}
.chk .v{font-weight:800;font-size:1.05rem;white-space:nowrap}
.chk.spxrow{background:#16202e;color:#fff;border-color:#16202e}
.chk.spxrow .ct b{color:#fff}
.chk.spxrow .ct span{color:#9ca3af}
.chk.spxrow .v{color:#fff}
details.how{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px;margin-top:10px}
details.how summary{font-weight:700;cursor:pointer;font-size:.92rem}
details.how .ex{padding:8px 2px;border-top:1px solid #f3f4f6;font-size:.82rem;color:#4b5563}
details.how .ex b{color:#1f2937}
.stats{display:flex;gap:10px;margin-bottom:12px}
.stat{flex:1;background:#fff;border:1px solid #e5e7eb;border-radius:12px;text-align:center;padding:12px 6px}
.stat b{font-size:1.35rem;display:block}
.stat span{color:#6b7280;font-size:.75rem}
.biorow{display:flex;align-items:center;justify-content:space-between;margin:2px 2px 10px}
.linklike{color:#0e7c8c;font-weight:700;font-size:.88rem;text-decoration:none;cursor:pointer;background:none;border:none;font-family:inherit}
.biorow select{border:1px solid #d1d5db;border-radius:8px;padding:6px 10px;font-family:inherit;background:#fff}
.alert{background:#fff;border:1px solid #e5e7eb;border-right:4px solid #f59e0b;border-radius:12px;
padding:12px;margin:10px 0}
.alert.crit{border-right-color:#ef4444}
.alert.warn{border-right-color:#f59e0b}
.alert.info{border-right-color:#f59e0b}
.a-top{display:flex;align-items:center;gap:6px;margin-bottom:6px}
.newbadge{background:#0e7c8c;color:#fff;font-size:.72rem;font-weight:700;border-radius:6px;padding:3px 10px}
.kbadge{background:#fef3c7;color:#b45309;font-size:.72rem;font-weight:700;border-radius:6px;padding:3px 10px}
.a-top .tick{font-weight:800;font-size:.95rem}
.a-top .sp{flex:1}
.a-head{font-weight:700;font-size:.95rem;margin-bottom:4px}
.a-comp{color:#6b7280;font-size:.8rem}
.a-bot{display:flex;align-items:center;justify-content:space-between;margin-top:8px}
.src-link{color:#0e7c8c;font-weight:700;font-size:.85rem;text-decoration:none}
.a-src{color:#9ca3af;font-size:.75rem}
.warnbox{background:#fff7ed!important;border-color:#fdba74!important;color:#9a3412!important}
.empty{background:#fff;border:1px solid #e5e7eb;color:#6b7280;text-align:center;
padding:22px;border-radius:12px;font-size:.9rem}
.tabbar{position:fixed;bottom:12px;left:50%;transform:translateX(-50%);
width:min(680px,calc(100% - 24px));background:#16202e;border-radius:18px;
display:flex;padding:8px;gap:6px;z-index:50;box-shadow:0 4px 20px rgba(0,0,0,.25)}
.tabbar button{flex:1;background:transparent;color:#cbd5e1;border:none;border-radius:12px;
padding:12px 4px;font-size:.92rem;font-weight:700;cursor:pointer;position:relative;font-family:inherit}
.tabbar button.active{background:#fff;color:#16202e}
.tabbar a.tablink{flex:1;display:flex;align-items:center;justify-content:center;background:transparent;
color:#cbd5e1;border-radius:12px;padding:12px 4px;font-size:.92rem;font-weight:700;
text-decoration:none;font-family:inherit;position:relative}
.count-badge{position:absolute;top:4px;inset-inline-end:14px;background:#dc2626;color:#fff;
font-size:.68rem;font-weight:800;min-width:20px;height:20px;border-radius:10px;
display:flex;align-items:center;justify-content:center;padding:0 5px}
.pane{display:none}
.pane.active{display:block}
h3.sec{font-size:1.05rem;font-weight:800;margin:16px 2px 4px}
.tbl-wrap{overflow-x:auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px}
table.htable{border-collapse:collapse;width:100%;font-size:.82rem;min-width:560px}
table.htable th,table.htable td{border-bottom:1px solid #f3f4f6;padding:9px 8px;text-align:right;white-space:nowrap}
table.htable th{background:#f9fafb;color:#6b7280;font-size:.75rem}
table.htable td.num{font-weight:700}
.up{color:#16a34a;font-weight:700}.down{color:#dc2626;font-weight:700}
.ftr{color:#9ca3af;font-size:.72rem;text-align:center;padding:14px 4px}
.idxgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:8px 0}
.idxcard{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px;display:flex;
flex-direction:column;gap:4px;color:#1f2937}
.idxcard .idxname{font-size:.8rem;color:#6b7280}
.idxcard b{font-size:1.25rem}
.idxcard small{color:#9ca3af;font-size:.72rem}
.hmap{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;background:#161c23;
border-radius:12px;padding:9px;margin:8px 0}
.tile{min-height:92px;text-align:center;padding:10px 6px;border-radius:6px;display:flex;
flex-direction:column;justify-content:center;gap:4px;border:1px solid}
.tile span{font-size:.72rem}
.tile b{font-size:1.15rem}
.tile small{font-size:.68rem;opacity:.85}
.tile.pos{background:color-mix(in srgb,#204f40 calc(30% + var(--i)*60%),#17251e);color:#b9f1d4;border-color:#426b59}
.tile.neg{background:color-mix(in srgb,#733039 calc(30% + var(--i)*60%),#2c1e24);color:#ffc6cd;border-color:#71424c}
.smhrow{display:flex;justify-content:space-between;align-items:center;gap:14px;background:#fff;
border:1px solid #e5e7eb;border-radius:12px;padding:12px;margin:4px 0}
.smhrow small{display:block;color:#9ca3af;font-size:.72rem}
.chg3{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;background:#fff;
border:1px solid #e5e7eb;border-radius:12px;padding:14px;text-align:center;margin:8px 0}
.chg3>div{display:flex;flex-direction:column;gap:3px}
.chg3 span{font-size:.78rem;color:#6b7280}
.chg3 b{font-size:1.2rem}
.chg3 small{font-size:.7rem;color:#9ca3af}
.cnnwrap{display:flex;gap:18px;align-items:center;background:#fff;border:1px solid #e5e7eb;
border-radius:12px;padding:16px;margin:8px 0;flex-wrap:wrap}
.gaugebox{text-align:center}
.cnnrating{font-weight:800;font-size:.9rem;margin-top:2px}
.cnnhist{flex:1;min-width:140px}
.cnnrow{display:flex;justify-content:space-between;border-bottom:1px solid #f3f4f6;
padding:7px 0;font-size:.82rem}
.cnnrow b{border:1px solid #e5e7eb;border-radius:50%;width:30px;height:30px;display:grid;
place-items:center;font-size:.78rem}
.finding{background:#f4f8f6;border-right:3px solid #0e7c8c;border-radius:8px;
padding:12px 14px;margin:10px 0;font-size:.85rem}
.finding.warn{background:#fdf7ee;border-right-color:#c08a3e}
.finding p{margin-top:4px;line-height:1.75;color:#374151}
.pbgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:8px 0}
.pbcard{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px}
.pbcard header{display:flex;justify-content:space-between;align-items:baseline}
.pbcard header b{font-size:1rem}
.pbcard header strong{font-size:.95rem;text-align:left}
.pbcard header strong small{display:block;font-size:.65rem;font-weight:400;color:#9ca3af}
.pbbody{display:flex;flex-direction:column;gap:2px;margin-top:6px;font-size:.76rem;color:#6b7280}
.accgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:8px 0}
.acccard{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:12px}
.acccard header b{font-size:.9rem}
.accrow{display:flex;justify-content:space-between;gap:8px;padding:6px 0;
border-bottom:1px solid #f3f4f6;font-size:.78rem}
.accrow span{color:#6b7280}
.accrow.good b{color:#16a34a}.accrow.bad b{color:#dc2626}
.catitem{display:grid;grid-template-columns:84px 1fr;gap:10px;background:#fff;
border:1px solid #e5e7eb;border-radius:12px;padding:10px;margin:8px 0;align-items:start}
.catitem.past{opacity:.65}
.catdays{font-size:.74rem;font-weight:700;padding:6px 4px;border-radius:8px;
background:#f3f4f6;text-align:center;line-height:1.4}
.catdays.soon{background:#fee2e2;color:#b91c1c}
.catdays.approach{background:#fef3c7;color:#92400e}
.catdays .approx{display:block;font-size:.62rem;font-weight:400;color:#9ca3af;margin-top:2px}
.catbody header{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.catbody header b{font-size:.92rem}
.catbody header span{font-size:.82rem}
.catbody small{display:block;color:#9ca3af;font-size:.72rem;margin:2px 0}
.catbody details{font-size:.76rem;color:#6b7280}
.catbody details p{margin:4px 0;line-height:1.7}
.trialgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:8px 0}
.trialcard{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px}
.trialcard header{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.trialcard header b{font-size:.92rem}
.tstat{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:10px;
background:#e0f2f1;color:#0e7c8c}
.tstat.bad{background:#fee2e2;color:#b91c1c}
.trialbody{display:flex;flex-direction:column;gap:2px;margin:6px 0;font-size:.72rem;color:#6b7280}
.secrow{display:grid;grid-template-columns:80px 52px 1fr auto;gap:8px;align-items:center;
padding:8px 4px;border-bottom:1px solid #f3f4f6;font-size:.78rem}
.secrow.hot{background:#fdf6ee}
.secdate{color:#9ca3af;font-size:.72rem}
.a-comp.why{color:#1f2937;border-top:1px dashed #e5e7eb;margin-top:6px;padding-top:6px}
@media(max-width:480px){.idxgrid,.pbgrid,.accgrid,.trialgrid{grid-template-columns:1fr}
.hmap{grid-template-columns:repeat(2,1fr)}.chg3{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.hdr h1{font-size:1.35rem}.brand{font-size:.9rem;top:13px}
.tabbar button,.tabbar a.tablink{font-size:.78rem;padding:10px 2px}}
@media(max-width:380px){.brand{position:static;transform:none;align-self:flex-start;margin-top:4px;font-size:.85rem}
.hdr h1{font-size:1.15rem;white-space:nowrap}}
"""
STATUS_COLORS = {
    "מתייצב": "#3fb950",
    "שיפור": "#d29922",
    "לא מתייצב": "#f85149",
    "תנאי סיכון פעילים": "#f85149",
    "אין מספיק נתונים": "#8b949e",
}


def fresh_badge(a):
    """'New' only for items from the last 24 hours (by real timestamp)."""
    ts = a.get("ts")
    if ts and time.time() - ts <= 24 * 3600:
        return '<span class="newbadge">חדש</span>'
    return ""


def build_html(mh, dips, alerts, bo, updated, extra=None):
    extra = extra or {}

    def pct2(v, d=2):
        cls = "up" if v >= 0 else "down"
        return f'<span class="{cls}" dir="ltr">{v:+.{d}f}%</span>'

    def ltr(v):
        return f'<span dir="ltr">{v}</span>'

    def sparkline(vals, w=90, h=26):
        if not vals or len(vals) < 2:
            return ""
        mn, mx = min(vals), max(vals)
        rng = (mx - mn) or 1.0
        pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 2 - (v - mn) / rng * (h - 4):.1f}"
                       for i, v in enumerate(vals))
        return (f'<svg dir="ltr" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                f'aria-hidden="true"><polyline points="{pts}" fill="none" '
                f'stroke="currentColor" stroke-width="1.6"/></svg>')
    ts = esc(updated.strftime("%-d.%-m.%Y, %-H:%M"))
    n_alerts = len(alerts)
    n_news = sum(1 for a in alerts if a["kind"] == "news")
    parts = ["<!DOCTYPE html>",
             '<html lang="he" dir="rtl">',
             "<head>",
             '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>סורקי הבורסה</title>",
             "<style>" + CSS + "</style>",
             "</head>",
             "<body>"]

    # ================= market health pane =================
    parts.append('<div class="pane active" id="pane-mh">')
    parts.append(
        '<div class="hdr"><div><h1>בריאות השוק</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<div class="brand">Erez-Stocks</div><button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
    if mh and mh["score"] is not None:
        score = mh["score"]
        col = STATUS_COLORS.get(mh["status"], "#e6edf3")
        ring_col = {"#3fb950": "#22c55e", "#d29922": "#f59e0b",
                    "#f85149": "#ef4444"}.get(col, "#f59e0b")
        circ = 2 * 3.14159 * 48
        dash = score / 100.0 * circ
        n_p = mh.get("n_pressure", 0)
        ptxt = "נקודת לחץ אחת דורשת תשומת לב" if n_p == 1 else f"{n_p} נקודות לחץ דורשות תשומת לב"
        parts.append(
            '<div class="hero">'
            f'<svg width="112" height="112" viewBox="0 0 112 112">'
            f'<circle cx="56" cy="56" r="48" fill="none" stroke="#374151" stroke-width="10"/>'
            f'<circle cx="56" cy="56" r="48" fill="none" stroke="{ring_col}" stroke-width="10" '
            f'stroke-dasharray="{dash:.1f} {circ:.1f}" stroke-linecap="round" transform="rotate(-90 56 56)"/>'
            f'<text x="56" y="60" text-anchor="middle" fill="#fff" font-size="30" font-weight="800">{score}</text>'
            f'<text x="56" y="78" text-anchor="middle" fill="#9ca3af" font-size="11">/100</text>'
            "</svg>"
            '<div class="hinfo">'
            f'<span class="status-badge" style="background:{ring_col}">{esc(mh["status"])}</span>'
            f'<div class="pressure">{ptxt}</div>'
            '<div class="note">הציון הוא צילום מצב של שמונה בדיקות, לא תחזית ולא איתות מסחר.</div>'
            "</div></div>")
        parts.append('<div class="grid2">')
        spx = mh.get("spx")
        spx_chg = mh.get("spx_chg")
        if spx:
            if spx_chg is not None:
                cls = "up" if spx_chg >= 0 else "down"
                chg_html = f' <span class="{cls}" dir="ltr">{spx_chg:+.2f}%</span>'
            else:
                chg_html = ""
            parts.append(
                f'<div class="chk spxrow"><span class="dot pass"></span>'
                f'<div class="ct"><b>S&P 500</b><span>ערך המדד כעת</span></div>'
                f'<div class="v"><span dir="ltr">{spx:,.0f}</span>{chg_html}</div></div>')
        for c in mh["checks"]:
            parts.append(
                f'<div class="chk"><span class="dot {esc(c["state"])}"></span>'
                f'<div class="ct"><b>{esc(c["label"])}</b><span>{esc(c["desc"])}</span></div>'
                f'<div class="v" dir="ltr">{esc(c["value"])}</div></div>')
        parts.append("</div>")
        parts.append('<details class="how"><summary>איך נקבע הציון? &#9664;</summary>')
        for c in mh["checks"]:
            if c.get("explain"):
                parts.append(f'<div class="ex"><b>{esc(c["label"])}:</b> {esc(c["explain"])}</div>')
        parts.append("</details>")
    else:
        parts.append('<div class="empty">אין מספיק נתונים כרגע</div>')

    ov = extra.get("overview")
    if ov:
        parts.append('<h3 class="sec">המבט הרחב · מדדים</h3>')
        parts.append('<div class="idxgrid">')
        for ix in ov["indices"]:
            day = ix["day_pct"]
            day_html = pct2(day) if day is not None else ""
            a50 = "מעל" if ix["above50"] else "מתחת ל־"
            trend = f"{a50} ממוצע 50"
            if ix["above200"] is not None:
                trend += " · " + ("מעל" if ix["above200"] else "מתחת ל־") + " 200"
            val_s = f"{ix['value']:,.2f}"
            parts.append(
                '<div class="idxcard">'
                f'<span class="idxname">{esc(ix["name"])}</span>'
                f'<b>{ltr(val_s)}</b>'
                f'{day_html}<small>מול סגירה קודמת</small>'
                f'{sparkline(ix["spark"])}'
                f'<small>{esc(trend)}</small>'
                "</div>")
        parts.append("</div>")
        if ov["sectors"]:
            parts.append('<h3 class="sec">מפת המגזרים · יום אחרון (ושינוי ב-3 ימי מסחר)</h3>')
            parts.append('<div class="hmap">')
            for s in ov["sectors"]:
                d1 = s["day_pct"] or 0.0
                inten = min(abs(d1) / 3.0, 1.0)
                cls = "pos" if d1 >= 0 else "neg"
                p3 = pct2(s["pct3"]) if s["pct3"] is not None else ""
                parts.append(
                    f'<div class="tile {cls}" style="--i:{inten:.2f}">'
                    f'<span><b>{esc(s["sym"])}</b> · {esc(s["name"])}</span>'
                    f'<b dir="ltr">{d1:+.2f}%</b>'
                    f"<small>3 ימים: {p3}</small></div>")
            parts.append("</div>")
            smh = ov.get("smh")
            if smh and smh["day_pct"] is not None:
                parts.append(
                    '<div class="smhrow"><span><b>סמיקונדקטורס · SMH</b> '
                    "<small>פירוט נוסף, לא מגזר במפה</small></span>"
                    f"{pct2(smh['day_pct'])}</div>")
            parts.append('<div class="ts">11 מגזרי S&P 500 באמצעות קרנות סל ענפיות · Yahoo Finance</div>')
        if ov["change3"]:
            parts.append('<h3 class="sec">שינוי ב-3 ימי מסחר</h3>')
            parts.append('<div class="chg3">')
            for c in ov["change3"]:
                dec = 3 if c["sym"] == "^TNX" else 2
                cls = "up" if c["abs3"] >= 0 else "down"
                p3 = (" · " + pct2(c["pct3"])) if c.get("pct3") is not None else ""
                parts.append(
                    f"<div><span>{esc(c['name'])}</span>"
                    f'<b class="{cls}" dir="ltr">{c["abs3"]:+.{dec}f}</b>'
                    f"<small>{esc(c['unit'])}{p3}</small></div>")
            parts.append("</div>")
            parts.append('<div class="ts">שלושה מרווחי מסחר אחרונים, לא שלושה ימים קלנדריים · Yahoo Finance</div>')

    cn = extra.get("cnn")
    if cn:
        score = cn["score"]
        rating_he = RATING_HE.get((cn.get("rating") or "").lower(), cn.get("rating") or "")
        ang = 3.14159 * (1 - max(0, min(100, score)) / 100.0)
        import math as _m
        nx, ny = 100 - 62 * _m.cos(ang), 92 - 62 * _m.sin(ang)
        arcs = ' '.join(
            f'<path d="M{100 - 80 * _m.cos(3.14159 * a / 100):.1f},{92 - 80 * _m.sin(3.14159 * a / 100):.1f} '
            f'A80,80 0 0 1 {100 - 80 * _m.cos(3.14159 * b / 100):.1f},{92 - 80 * _m.sin(3.14159 * b / 100):.1f}" '
            f'stroke="{c}" stroke-width="20" fill="none"/>'
            for a, b, c in [(0.5, 19.5, "#f8b4b4"), (20.5, 39.5, "#f4d3a8"), (40.5, 59.5, "#e5e7eb"),
                            (60.5, 79.5, "#d2e5dc"), (80.5, 99.5, "#90c7ad")])
        hist = "".join(
            f'<div class="cnnrow"><span>{esc(p["label"])}</span><b dir="ltr">{p["value"]}</b></div>'
            for p in cn.get("previous", []))
        try:
            ts_dt = datetime.fromisoformat(cn["timestamp"]).astimezone(JER)
            ts_txt = f"{ts_dt.day}.{ts_dt.month}.{ts_dt.year}, {ts_dt.hour}:{ts_dt.minute:02d}"
        except Exception:
            ts_txt = ""
        if cn.get("live"):
            src_txt = f"עודכן חי מ-CNN בריצה זו · {ts_txt}"
        else:
            src_txt = f"צילום שמור מ-{ts_txt} · המקור לא היה זמין בריצה זו, והמספר אינו של היום"
        age = cn.get("age_hours")
        stale = ('<div class="empty warnbox">⚠️ צילום ה-CNN ישן (מעל 72 שעות) — להתייחס בזהירות.</div>'
                 if age is not None and age > 72 else "")
        parts.append('<h3 class="sec">סנטימנט · CNN Fear & Greed</h3>')
        parts.append(
            '<div class="cnnwrap"><div class="gaugebox">'
            f'<svg dir="ltr" viewBox="0 0 200 120" width="200" height="120">{arcs}'
            f'<line x1="100" y1="92" x2="{nx:.1f}" y2="{ny:.1f}" stroke="#242424" stroke-width="3.5"/>'
            f'<circle cx="100" cy="92" r="18" fill="#fff"/>'
            f'<text x="100" y="97" text-anchor="middle" font-size="20" font-weight="800" fill="#222">{score}</text>'
            f"</svg>"
            f'<div class="cnnrating">{esc(rating_he)}</div></div>'
            f'<div class="cnnhist">{hist}</div></div>'
            f'<div class="ts">{esc(src_txt)} · <a class="src-link" href="{esc(cn.get("source", ""))}" target="_blank" rel="noopener">מקור</a></div>'
            + stale)
    parts.append("</div>")

    # ================= breakout pane =================
    parts.append('<div class="pane" id="pane-bo">')
    parts.append(
        '<div class="hdr"><div><h1>סורק פריצות</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<div class="brand">Erez-Stocks</div><button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
    if bo and bo.get("groups") is not None:
        groups = bo["groups"]
        total = sum(len(v) for v in groups.values())
        parts.append(
            '<div class="stats">'
            f'<div class="stat"><b>{bo.get("scanned", 0)}</b><span>מניות נסרקו מתוך {bo.get("universe", 0)}</span></div>'
            f'<div class="stat"><b>{total}</b><span>איתותים פעילים</span></div>'
            f'<div class="stat"><b>{len(bo.get("history", []))}</b><span>התאוששויות במעקב</span></div>'
            "</div>")
        if bo.get("universe") and bo.get("failed", 0) > 0.1 * bo["universe"]:
            parts.append('<div class="empty warnbox">⚠️ חסרים נתונים עבור '
                         f'{bo["failed"]} מניות מתוך {bo["universe"]} - התוצאות חלקיות.</div>')
        if bo.get("intraday"):
            parts.append('<div class="alert info"><div class="a-text">'
                         "⏳ השוק פתוח — הנר עוד לא נסגר, המחזור הוא הערכה.</div></div>")
        ginfo = [("BREAKOUT", "🚀 תחילת פריצה", "פריצה"),
                 ("RECOVERY", "↗️ התאוששות", "התאוששות"),
                 ("EXTENDED", "🔥 פריצה חזקה — לא לרדוף", "רחוקה"),
                 ("SETUP", "🎯 על סף פריצה", "על סף")]
        for gkey, gtitle, gkind in ginfo:
            rows = groups.get(gkey, [])
            parts.append(f'<h3 class="sec">{gtitle} ({len(rows)})</h3>')
            if not rows:
                parts.append('<div class="empty">אין מניות כרגע.</div>')
            for t, x in rows:
                is_new = x.get("cross_date") and x["cross_date"] == bo.get("today_dm")
                new_b = '<span class="newbadge">חדש</span>' if is_new else ""
                flags = "<br>".join(esc(fl) for fl in (x.get("flags") or []))
                if gkey == "RECOVERY":
                    head = (f'{t}: {x["from_high_pct"]:.0f}% מתחת לשיא · '
                            f'חצתה {x["level"]:.2f} ב-{x["cross_date"]} (+{x["dist_pct"]:.1f}%)')
                    comp = (f'מחיר {x["close"]:.2f} · מחזור ×{x["vol_ratio"]:.1f} · '
                            f'RSI {x["rsi"]:.0f} · סטופ {x["stop"]:.2f} ({x["risk_pct"]:.1f}%) · '
                            f'יעד {x["target"]:.2f} (+{x["upside_pct"]:.0f}%)')
                elif gkey == "SETUP":
                    head = (f'{t}: {abs(x["dist_pct"]):.1f}% מתחת להתנגדות {x["level"]:.2f}')
                    comp = (f'מחיר {x["close"]:.2f} · {x["touches"]} נגיעות · בסיס {x["base_days"]} ימים · '
                            f'RSI {x["rsi"]:.0f} · סטופ {x["stop"]:.2f}')
                elif gkey == "EXTENDED":
                    head = (f'{t}: חצתה {x["level"]:.2f} ב-{x["cross_date"]} '
                            f'וכבר +{x["dist_pct"]:.1f}% מעליו')
                    comp = (f'מחיר {x["close"]:.2f} · כניסה בחזרה: {x["level"]:.2f}–{x["entry_top"]:.2f} · '
                            f'סטופ {x["stop"]:.2f}')
                else:
                    head = (f'{t} {x["close"]:.2f} | חצתה {x["level"]:.2f} ב-{x["cross_date"]} '
                            f'(+{x["dist_pct"]:.1f}%)')
                    comp = (f'מחזור ×{x["vol_ratio"]:.1f} · RSI {x["rsi"]:.0f} · '
                            f'סטופ {x["stop"]:.2f} ({x["risk_pct"]:.1f}%)')
                parts.append(
                    f'<div class="alert warn" data-kind="breakout">'
                    f'<div class="a-top">{new_b}<span class="sp"></span>'
                    f'<span class="kbadge">פריצות</span><span class="kbadge">{gkind}</span>'
                    f'<span class="tick">{esc(t)}</span></div>'
                    f'<div class="a-head">{esc(head)}</div>'
                    f'<div class="a-comp">{esc(comp)}</div>'
                    + (f'<div class="a-comp" style="color:#b45309">{flags}</div>' if flags else "")
                    + f'<div class="a-bot"><a class="src-link" '
                    f'href="https://finance.yahoo.com/quote/{esc(t)}" target="_blank" '
                    f'rel="noopener">פתח גרף</a>'
                    f'<span class="a-src">{esc(x.get("cross_date") or "")}</span></div>'
                    "</div>")
        # ---- recovery learning history ----
        hist = bo.get("history", [])
        parts.append('<h3 class="sec">📚 מעקב התאוששויות — מי טסה בלעדיך</h3>')
        if hist:
            rows_h = []
            for s in hist[:30]:
                if s["pct"] is None:
                    pct_txt, cls = "—", ""
                else:
                    cls = "up" if s["pct"] >= 0 else "down"
                    pct_txt = f'{s["pct"]:+.1f}%'
                status = "🎯 הגיע ליעד" if s["target_hit"] else "⏳ במעקב"
                cur_cell = (f'<td class="num">{s["current"]:.2f}</td>' if s["current"]
                            else "<td>—</td>")
                rows_h.append(
                    f'<tr><td><b>{esc(s["ticker"])}</b></td><td>{esc(s["date"])}</td>'
                    f'<td class="num">{s["signal_price"]:.2f}</td>'
                    f'{cur_cell}'
                    f'<td class="{cls}">{pct_txt}</td>'
                    f'<td class="num">{s["target"]:.2f}</td><td>{status}</td></tr>')
            parts.append('<div class="tbl-wrap"><table class="htable">'
                         "<tr><th>מניה</th><th>תאריך איתות</th><th>מחיר איתות</th>"
                         "<th>מחיר נוכחי</th><th>שינוי מאז</th><th>יעד</th><th>סטטוס</th></tr>"
                         + "".join(rows_h) + "</table></div>")
        else:
            parts.append('<div class="empty">עדיין אין היסטוריית התאוששויות — היא תצטבר מהריצות הבאות.</div>')
    else:
        parts.append('<div class="empty">סורק הפריצות לא הצליח לרוץ כרגע.</div>')
    acc = extra.get("acc")
    if acc:
        def acc_card(title, rows):
            body = "".join(
                f'<div class="accrow {cls}"><span>{esc(k)}</span><b>{ltr(v)}</b></div>'
                for k, v, cls in rows)
            return f'<div class="acccard"><header><b>{esc(title)}</b></header>{body}</div>'
        tot_s = f"{acc['total_signals']:,}"
        b, x, s, r = acc["breakout"], acc["extended"], acc["setup"], acc["recovery"]
        parts.append('<h3 class="sec">דיוק הזיהוי · מדידה היסטורית</h3>')
        parts.append(
            f'<p class="ts">כמה האיתותים צדקו בעבר: שחזור הסורק כמו שהוא על {ltr(acc["tickers"])} מניות לאורך '
            f'{esc(acc["window"])} ({ltr(tot_s)} איתותים), ובדיקה מה קרה 5, 10 ו-20 ימי מסחר אחרי. '
            'מדד לאיכות הזיהוי ולקריאת המגמה, לא הבטחה ולא מערכת מסחר.</p>')
        parts.append('<div class="accgrid">')
        parts.append(acc_card("🚀 תחילת פריצה", [
            (f'איתותים: {b["n"]:,}', "", ""),
            ("חזרה מתחת לקו תוך 5 ימים (פריצת שווא)", f'{b["false5"]}%', "bad"),
            ("עדיין מעל הקו ביום ה-10", f'{b["above10"]}%', "good"),
            ("פריצת שווא בלי סינון (קו בסיס)", f'{b["base_false5"]}%', "")]))
        parts.append(acc_card("🔥 פריצה חזקה", [
            (f'איתותים: {x["n"]:,}', "", ""),
            ("חזרה מתחת לקו תוך 5 ימים", f'{x["false5"]}%', "good"),
            ("עדיין מעל הקו ביום ה-10", f'{x["above10"]}%', "good")]))
        parts.append(acc_card("🎯 על סף פריצה", [
            (f'איתותים: {s["n"]:,}', "", ""),
            ("פרצה את הקו תוך 10 ימים", f'{s["breaks10"]}%', ""),
            ("קו בסיס: כל מניה 2% מתחת לשיא", f'{s["base_breaks10"]}%', "bad")]))
        parts.append(acc_card("↗️ התאוששות", [
            (f'איתותים: {r["n"]:,}', "", ""),
            ("פגיעה בסטופ תוך 10 ימים", f'{r["stop10"]}%', "bad"),
            ("הגעה ליעד תוך 20 יום", f'{r["target20"]}%', "good")]))
        parts.append('</div>')
        vf = acc.get("volfinding")
        if vf:
            parts.append(
                '<div class="finding"><b>ממצא מרכזי · מחזור הוא המסנן החזק</b>'
                f'<p>בפריצות עם מחזור ×2.5 ומעלה, שיעור פריצת השווא ירד מ-{ltr("47.5%")} ל-{ltr(str(vf["false5"])+"%")} '
                f'וההחזקה מעל הקו עלתה ל-{ltr(str(vf["above10"])+"%")}. בחלון האישור הנפרד: {ltr(str(vf["confirm_false5"])+"%")} '
                f'מול {ltr(str(vf["confirm_all_false5"])+"%")} — היתרון נשמר מחוץ למדגם. '
                'מוצג כמידע בלבד; שינוי כללים רק באישור.</p></div>')
        parts.append(f'<div class="ts">מדידה מ-{esc(acc.get("computed_at", ""))} · שחזור אומת מול קוד הסורק החי · '
                     'המדדים מתייחסים לזיהוי ולהמשכיות, ואינם תחזית תשואה</div>')
    parts.append("</div>")

    # ================= dips pane =================
    parts.append('<div class="pane" id="pane-dip">')
    parts.append(
        '<div class="hdr"><div><h1>סורק דיפים</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<div class="brand">Erez-Stocks</div><button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
    if dips:
        for c in dips:
            links = "".join(
                f'<div style="margin-top:6px"><a class="src-link" href="{esc(h["link"])}" '
                f'target="_blank" rel="noopener">פתח מקור</a> '
                f'<span class="a-src">{esc(h["title"][:90])}</span></div>'
                for h in c["headlines"][:2] if h.get("link"))
            parts.append(
                f'<div class="alert warn" data-kind="dip">'
                f'<div class="a-top"><span class="newbadge">חדש</span><span class="sp"></span>'
                f'<span class="kbadge">דיפ</span><span class="tick">{esc(c["ticker"])}</span></div>'
                f'<div class="a-head">עומק הדיפ: {c["depth"]:.1f}% מהשיא · מעל השפל: {c["above_low_pct"]:.1f}%</div>'
                f'<div class="a-comp">RSI(14): {c["rsi"]:.0f} · {esc(scan_utils.volume_text(c["vol_tier"], c["vol_mult"], dip_meta["vfrac"]))}</div>'
                f"{links}</div>")
    elif not dip_meta["coverage_ok"]:
        parts.append('<div class="empty warnbox">לא ניתן לסמוך על התוצאה: חסרים נתונים '
                     f'({stats["sp500_total"] - stats["sp500_missing"]}/{stats["sp500_total"]} מניות נטענו)</div>')
    else:
        parts.append('<div class="empty">אין מניות שעומדות כרגע בכל תנאי הדיפ '
                     f'(נסרקו {stats["sp500_total"] - stats["sp500_missing"]}/{stats["sp500_total"]})</div>')
    if dips and stats["sp500_missing"]:
        parts.append(f'<div class="ts">כיסוי נתונים: {stats["sp500_total"] - stats["sp500_missing"]}/{stats["sp500_total"]} מניות</div>')

    acc = extra.get("acc")
    dfp = (acc or {}).get("dipfindings")
    if dfp:
        n_s = f"{dfp['n']:,}"
        parts.append('<h3 class="sec">למה הסורק שקט · ומה המדידה מצאה</h3>')
        parts.append(
            '<div class="finding"><b>התנאים דורשים בו-זמנית תיקון רדוד וקריסה טרייה</b>'
            f'<p>במדידה מ-{esc((acc or {}).get("computed_at", ""))}: רק {ltr(dfp["fire_today"])} מתוך {ltr(dfp["universe"])} '
            f'המניות עמדו בכל התנאים בסריקה האחרונה, ורק {ltr(dfp["rsi_only"])} היו עם RSI מתחת ל־30. '
            f'וכשהוא כן מזהה — הזיהוי טוב: על {ltr(n_s)} איתותים בארבע שנים, '
            f'{ltr(str(dfp["pos20"])+"%")} היו חיוביים 20 ימים אחרי מול {ltr(str(dfp["base_pos20"])+"%")} בקו הבסיס, '
            f'ובחלון האישור {ltr(str(dfp["confirm_pos20"])+"%")} מול {ltr(str(dfp["confirm_base"])+"%")}. '
            'המדדים מתייחסים לזיהוי התייצבות, לא תחזית תשואה.</p></div>')

    pb = (bo or {}).get("pullback") if bo else None
    if pb:
        pbs = (acc or {}).get("pullback_stats") or {}
        parts.append('<h3 class="sec">תיקון במגמת עלייה · קטגוריית זיהוי, לא איתות תחתית</h3>')
        parts.append(
            '<div class="finding warn"><b>זו קטגוריית זיהוי נוספת, לא איתות קנייה</b>'
            f'<p>מניה שירדה 10%–30% מתחת לשיא 52 השבועות, עדיין מעל ממוצע 200, עם RSI מתחת ל־40. '
            f'במדידה על {esc(pbs.get("window", ""))}: התייצבות אצל תיקונים כאלה '
            f'{ltr(str(pbs.get("pos20", ""))+"%")} מול {ltr(str(pbs.get("base_pos20", ""))+"%")} ביום אקראי — הפרש זעים. '
            'הקטגוריה מסמנת מצב תיקון במגמה, לא נקודת קנייה.</p></div>')
        parts.append('<div class="pbgrid">')
        for r in pb:
            vol = f' · מחזור {ltr("x" + format(r["volr"], ".1f"))}' if r.get("volr") else ""
            parts.append(
                '<div class="pbcard">'
                f'<header><b>{esc(r["ticker"])}</b><strong class="down">{ltr("-" + format(r["dd"], ".1f") + "%")}'
                '<small>מתחת לשיא</small></strong></header>'
                f'<div class="pbbody"><span>סגירה {ltr(format(r["close"], ".2f"))} · {pct2(r["chg"])} ביום האחרון</span>'
                f'<span>RSI {ltr(format(r["rsi"], ".0f"))}{vol}</span>'
                f'<span>{ltr("+" + format(r["above200"], ".1f") + "%")} מעל ממוצע 200 · '
                f'{ltr("+" + format(r["above_low"], ".1f") + "%")} מעל שפל 126 יום</span></div></div>')
        parts.append('</div>')
        parts.append(f'<div class="ts">{ltr(len(pb))} מניות זוהו · הסריקה רצה על כל היקף הסורק, בלי שינוי כללים בסורק הדיפים עצמו</div>')
    parts.append("</div>")

    # ================= biotech pane =================
    kind_he = {"price": "מחיר", "sec": "דיווח", "news": "חדשות"}
    parts.append('<div class="pane" id="pane-bio">')
    parts.append(
        '<div class="hdr"><div><h1>התראות ביוטק</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<div class="brand">Erez-Stocks</div><button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
    parts.append(
        '<div class="stats">'
        f'<div class="stat"><b>{stats["bio_total"]}</b><span>חברות במעקב</span></div>'
        f'<div class="stat"><b>{n_alerts}</b><span>פריטים חדשים</span></div>'
        f'<div class="stat"><b>{n_news}</b><span>חדשות בסריקה</span></div>'
        "</div>")
    parts.append(
        '<div class="biorow">'
        '<button class="linklike" id="markread">סמן הכל כנקרא</button>'
        '<label style="font-size:.85rem;color:#4b5563">הצג: '
        '<select id="kindfilter">'
        '<option value="">הכל</option><option value="news">חדשות</option>'
        '<option value="price">מחיר</option><option value="sec">דיווח</option>'
        "</select></label></div>")
    sec_bad = stats["sec_failed"]
    if sec_bad or stats["biotech_missing"]:
        parts.append(
            '<div class="empty warnbox">⚠️ תקלת נתונים: '
            f'SEC EDGAR נבדק עבור {stats["sec_ok"]}/{stats["bio_total"]} חברות · '
            f'נתוני מחיר חסרים: {stats["biotech_missing"]}. '
            'היעדר דיווחי 8-K בעמוד אינו אומר שלא היו כאלה.</div>')
    else:
        parts.append(f'<div class="ts">SEC EDGAR נבדק עבור {stats["sec_ok"]}/{stats["bio_total"]} חברות</div>')
    if alerts:
        for a in alerts:
            link_html = (f'<a class="src-link" href="{esc(a["link"])}" target="_blank" '
                         f'rel="noopener">פתח מקור</a>' if a.get("link") else "")
            parts.append(
                f'<div class="alert {esc(a["sev"])}" data-kind="{esc(a["kind"])}">'
                f'<div class="a-top">{fresh_badge(a)}<span class="sp"></span>'
                f'<span class="kbadge">ביוטק</span>'
                f'<span class="kbadge">{kind_he.get(a["kind"], "")}</span>'
                f'<span class="tick">{esc(a["ticker"])}</span></div>'
                f'<div class="a-head">{esc(a["head"])}</div>'
                f'<div class="a-comp">{esc(a.get("sub", ""))}</div>'
                f'<div class="a-bot">{link_html}'
                f'<span class="a-src">{esc(a["date"])} · {esc(a.get("source", ""))}</span></div>'
                "</div>")
    else:
        parts.append('<div class="empty">אין התראות חדשות כרגע</div>')

    radar = extra.get("radar") or []
    if radar:
        flagged = [r for r in radar if r["sev"] >= 1]
        parts.append('<h3 class="sec">רדאר תזוזות · כל הרשימה</h3>')
        if flagged:
            for r in flagged:
                sev_badge = {3: "קריטי", 2: "תזוזה חדה", 1: "תחילת תזוזה"}[r["sev"]]
                vol = f' · מחזור {ltr("x" + format(r["vol_mult"], ".1f"))}' if r.get("vol_mult") else ""
                why = f'<div class="a-comp why"><b>למה: </b>{esc(r["why"])}</div>' if r.get("why") else ""
                parts.append(
                    f'<div class="alert {"crit" if r["sev"] == 3 else "warn" if r["sev"] == 2 else "info"}">'
                    f'<div class="a-top"><span class="sp"></span><span class="kbadge">{sev_badge}</span>'
                    f'<span class="tick">{esc(r["ticker"])}</span></div>'
                    f'<div class="a-head">{esc(r["ticker"])}: {pct2(r["chg"], 1)} · מחיר {ltr(format(r["close"], ".2f"))}{vol}</div>'
                    f'{why}</div>')
        else:
            parts.append('<div class="empty">אין תזוזות חריגות כרגע.</div>')
        rows = "".join(
            f'<tr><td><b>{esc(r["ticker"])}</b></td><td class="num" dir="ltr">{r["close"]:.2f}</td>'
            f'<td class="{"up" if r["chg"] >= 0 else "down"}" dir="ltr">{r["chg"]:+.1f}%</td>'
            f'<td class="num" dir="ltr">{"x" + format(r["vol_mult"], ".1f") if r.get("vol_mult") else "—"}</td></tr>'
            for r in radar)
        parts.append(
            '<details class="how"><summary>כל הרשימה · מחיר, שינוי ומחזור &#9664;</summary>'
            '<div class="tbl-wrap"><table class="htable" style="min-width:0">'
            '<tr><th>מניה</th><th>מחיר</th><th>שינוי</th><th>מחזור</th></tr>'
            f'{rows}</table></div></details>')
        parts.append('<div class="ts">ספי גרסה 2: תזוזה חדה 8% · קריטי 15% · מחזור חריג ×3 · חריג מאוד ×5 · '
                     'תחילת תזוזה: 2% במחזור ×2.5 · שורת "למה": חדשה מ-48 השעות או דיווח SEC אחרון</div>')

    cats = extra.get("cats")
    if cats and cats.get("upcoming"):
        parts.append('<h3 class="sec">קטליזטורים · ספירה לאחור</h3>')
        for c in cats["upcoming"]:
            if c["days"] is None:
                days_txt, cls = "ללא תאריך", ""
            elif c["days"] == 0:
                days_txt, cls = "היום", "soon"
            elif c["days"] == 1:
                days_txt, cls = "מחר", "soon"
            else:
                days_txt = f'בעוד {c["days"]} ימים'
                cls = "soon" if c["bucket"] == "imminent" else "approach" if c["bucket"] == "approaching" else ""
            approx = '<small class="approx">הערכת טווח, לא תאריך מדויק</small>' if c.get("approx") else ""
            parts.append(
                f'<div class="catitem"><span class="catdays {cls}">{esc(days_txt)}{approx}</span>'
                f'<div class="catbody"><header><b>{esc(c["ticker"])}</b><span>{esc(c["event"])}</span></header>'
                f'<small>{esc(c.get("trial", ""))} · צפי: {esc(c.get("expected", ""))}</small>'
                + (f'<details><summary>הערת רקע</summary><p>{esc(c.get("note", ""))}</p></details>' if c.get("note") else "")
                + "</div></div>")
        if cats.get("passed"):
            rows = "".join(
                f'<div class="catitem past"><span class="catdays">לפני {-c["days"]} ימים</span>'
                f'<div class="catbody"><header><b>{esc(c["ticker"])}</b><span>{esc(c["event"])}</span></header></div></div>'
                for c in cats["passed"][:10])
            parts.append(f'<details class="how"><summary>אירועים שכבר עברו ({len(cats["passed"])}) &#9664;</summary>{rows}</details>')
        parts.append(f'<div class="ts">מקור הרשימה: קובץ הביוטק המקומי, עודכן {esc(cats.get("asof", ""))} · '
                     'הדגשה מ-21 יום, הדגשה חזקה בשבוע האחרון · ציפיות רבעון/חציון מסומנות כהערכה</div>')

    tr = extra.get("trials")
    if tr and tr.get("rows"):
        if not tr.get("live"):
            state_txt = f'⚠️ הרשומה לא הייתה זמינה בריצה זו — מוצג צילום שמור מ-{esc(tr.get("fetched_at", ""))}'
            parts.append(f'<div class="empty warnbox">{state_txt}</div>')
        parts.append('<h3 class="sec">ניסויים קליניים · מצב הרשומה</h3>')
        parts.append('<div class="trialgrid">')
        for t in tr["rows"]:
            chg_b = '<span class="newbadge">עודכן</span>' if t.get("changed") else ""
            parts.append(
                f'<div class="trialcard"><header><b>{esc(t["ticker"])}</b>'
                f'<span class="tstat {"bad" if t["bad"] else ""}">{esc(t["status_he"])}</span>{chg_b}</header>'
                f'<div class="trialbody"><span>משתתפים: {ltr(t["enrollment"] or "—")}</span>'
                f'<span>סיום ראשי: {ltr(t["primary_completion"])}</span>'
                f'<span>עודכן ברשומה: {ltr(t["last_update"])}</span></div>'
                f'<a class="src-link" href="https://clinicaltrials.gov/study/{esc(t["nct"])}" target="_blank" rel="noopener">{ltr(t["nct"])} ↗</a>'
                "</div>")
        parts.append("</div>")
        note = (f'ClinicalTrials.gov · {ltr(len(tr["rows"]))} ניסויים · נשלף חי בריצה זו'
                if tr.get("live") else "ClinicalTrials.gov · צילום שמור")
        if tr.get("errors"):
            note += f' · ⚠️ {ltr(len(tr["errors"]))} ניסויים נכשלו בקריאה'
        parts.append(f'<div class="ts">{note} · סימון "עודכן": שינוי מול הצילום השמור (סטטוס, סיום ראשי או משתתפים)</div>')

    sw = extra.get("secw")
    if sw:
        parts.append('<h3 class="sec">צופה דיווחי SEC · 5 ימים אחורה</h3>')
        if sw.get("errors"):
            parts.append('<div class="empty warnbox">⚠️ קריאת EDGAR נכשלה עבור: '
                         f'{esc(", ".join(sw["errors"]))} — ייתכן שחסרים דיווחים שלהם.</div>')
        if sw["events"]:
            for e in sw["events"][:40]:
                items = f' · סעיפים {ltr(e["items"])}' if e.get("items") and e["form"] == "8-K" else ""
                parts.append(
                    f'<div class="secrow {"hot" if e["sev"] == 3 else ""}">'
                    f'<span class="secdate" dir="ltr">{esc(e["date"])}</span>'
                    f'<b>{esc(e["ticker"])}</b>'
                    f'<span>{esc(e["label"])}{items}</span>'
                    f'<a class="src-link" href="{esc(e["url"])}" target="_blank" rel="noopener">לדיווח ↗</a></div>')
        else:
            parts.append('<div class="empty">אין דיווחים בחלון.</div>')
        parts.append('<div class="ts">8-K מסומן "מהותי" רק עם סעיפים <span dir="ltr">1.01 / 1.02 / 7.01 / 8.01</span> '
                     '· הנפקות (<span dir="ltr">424B5 / S-3 / S-1</span>) = אזהרת דילול · '
                     'זיהוי מוצהר ל-SEC · קצב מתחת ל-2 בקשות לשנייה</div>')
    parts.append("</div>")

    # ================= bottom tab bar =================
    badge = (f'<span class="count-badge">{n_alerts}</span>' if n_alerts else "")
    parts.append(
        '<div class="tabbar">'
        '<button class="active" data-t="mh">תמונת שוק</button>'
        '<button data-t="bo">פריצות ומגמה</button>'
        '<button data-t="dip">דיפים ותיקונים</button>'
        f'<button data-t="bio">ביוטק{badge}</button>'
        '<a class="tablink" href="concepts.html">מושגים</a>'
        "</div>")

    # ================= JS =================
    parts.append("""<script>
document.querySelectorAll('.tabbar button').forEach(function(b){
b.addEventListener('click',function(){
document.querySelectorAll('.tabbar button').forEach(function(x){x.classList.remove('active')});
document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('active')});
b.classList.add('active');
document.getElementById('pane-'+b.dataset.t).classList.add('active');
window.scrollTo(0,0);});});
var kf=document.getElementById('kindfilter');
if(kf){kf.addEventListener('change',function(){
var v=this.value;
document.querySelectorAll('#pane-bio .alert').forEach(function(c){
c.style.display=(!v||c.dataset.kind===v)?'':'none';});});}
var mr=document.getElementById('markread');
if(mr){mr.addEventListener('click',function(){
document.querySelectorAll('.newbadge').forEach(function(b){b.style.display='none';});});}
</script>""")

    parts.append(
        '<div class="ftr">מחזור מסומן "הערכה תוך-יומית" הוא הקרנה של מחזור חלקי ליום מלא - לא מחזור שנצפה, והמחזור הרגיל אינו אחיד לאורך היום. · נתונים: Yahoo Finance · SEC EDGAR · Google News · '
        "מקורות חינמיים בלבד · לא ייעוץ השקעות</div></body></html>")
    return "\n".join(parts)


def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)

    mh = market_health_section()
    dips = dip_scan_section()
    bio = biotech_alerts()
    bo = breakout_section()
    overview = market_overview_section()
    cnn = cnn_section()
    cats = catalyst_section()
    trials = trials_section()
    try:
        watchlist = json.load(open(os.path.join(BASE_DIR, "biotech_config.json"),
                                   encoding="utf-8")).get("watchlist", [])
    except Exception:
        watchlist = []
    secw = sec_watch_section(watchlist) if watchlist else None
    # SEC-based "why" for flagged radar rows that have no news headline
    if secw and bio.get("radar"):
        recent_by_ticker = {}
        for ev in secw["events"]:
            recent_by_ticker.setdefault(ev["ticker"], ev)
        for r in bio["radar"]:
            if r["sev"] >= 1 and not r["why"]:
                ev = recent_by_ticker.get(r["ticker"])
                if ev:
                    r["why"] = f"דיווח SEC · {ev['label']} · {ev['date']}"
    try:
        acc = json.load(open(os.path.join(BASE_DIR, "accuracy_stats.json"), encoding="utf-8"))
    except Exception as e:
        fail("accuracy", f"stats unreadable: {e}")
        acc = None
    extra = {"overview": overview, "cnn": cnn, "cats": cats, "trials": trials,
             "secw": secw, "radar": bio.get("radar", []), "acc": acc}
    alerts = bio.get("alerts", [])

    updated = datetime.now(JER)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(mh, dips, alerts, bo, updated, extra))
    dt = time.time() - t0

    print(f"WROTE {OUT_FILE} ({os.path.getsize(OUT_FILE)} bytes) in {dt:.0f}s")
    if mh:
        print(f"MARKET score={mh['score']} status={mh['status']}")
    print(f"DIPS candidates={len(dips)}" +
          (f" top={[c['ticker'] for c in dips[:5]]}" if dips else ""))
    print(f"BIOTECH alerts={len(alerts)}")
    if bo:
        for st, rows in bo["groups"].items():
            print(f"BREAKOUT {st}={len(rows)}" +
                  (f" tickers={[t for t, _ in rows[:5]]}" if rows else ""))
        print(f"RECOVERY history signals={len(bo['history'])}")
    print(f"SP500 missing series: {stats['sp500_missing']}, "
          f"biotech missing: {stats['biotech_missing']}, "
          f"SEC ok/failed: {stats['sec_ok']}/{stats['sec_failed']}, news failures: {stats['news_failed']}")
    if failures:
        print("FAILURES:")
        for sec, det in failures[:20]:
            print(f"  [{sec}] {det}")


if __name__ == "__main__":
    main()
