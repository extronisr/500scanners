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
SEC_UA = os.environ.get("SEC_USER_AGENT", "ScannersSiteDashboard/1.0 contact@example.com")
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
    """Alerts feed like the app: price/volume spikes, 8-K filings, news."""
    cfg_path = os.path.join(BASE_DIR, "biotech_config.json")
    try:
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        watchlist = cfg["watchlist"]
    except Exception as e:
        fail("biotech", f"config unreadable: {e}")
        return []
    p_spike = cfg.get("price_spike_pct", 8.0)
    p_crit = cfg.get("price_critical_pct", 15.0)
    v_mult = cfg.get("volume_mult", 3.0)
    v_crit = cfg.get("volume_critical_mult", 5.0)
    alerts = []
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
                pub = fmt_pub(h["pub"]) or today
                alerts.append({"sev": "info", "kind": "news", "ticker": ticker,
                               "name": name, "date": pub, "ts": h.get("pub_ts"),
                               "head": h["title"], "sub": name,
                               "link": h["link"], "source": "Google News RSS"})
        time.sleep(0.15)  # be polite to EDGAR
    alerts.sort(key=lambda a: a.get("ts") or 0, reverse=True)   # newest first, by real timestamp
    alerts.sort(key=lambda a: {"crit": 0, "warn": 1, "info": 2}[a["sev"]])
    return alerts


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

    return {"groups": groups, "history": history, "intraday": intraday,
            "scanned": len(data), "universe": len(tickers), "failed": len(failed), "today_dm": today_dm}




# ---------------------------------------------------------------- HTML
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#f1f2f4;color:#1f2937;font-family:-apple-system,'Segoe UI',Arial,sans-serif;
direction:rtl;padding:12px 12px 96px;max-width:720px;margin:0 auto}
.hdr{display:flex;align-items:flex-start;justify-content:space-between;padding:6px 2px 12px;gap:10px}
.hdr h1{font-size:1.65rem;font-weight:800}
.hdr .ts{color:#6b7280;font-size:.78rem;margin-top:4px}
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


def build_html(mh, dips, alerts, bo, updated):
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
        '<button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
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
    parts.append("</div>")

    # ================= dips pane =================
    parts.append('<div class="pane" id="pane-dip">')
    parts.append(
        '<div class="hdr"><div><h1>סורק דיפים</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
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
    parts.append("</div>")

    # ================= biotech pane =================
    kind_he = {"price": "מחיר", "sec": "דיווח", "news": "חדשות"}
    parts.append('<div class="pane" id="pane-bio">')
    parts.append(
        '<div class="hdr"><div><h1>התראות ביוטק</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
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
    parts.append("</div>")

    # ================= breakout pane =================
    parts.append('<div class="pane" id="pane-bo">')
    parts.append(
        '<div class="hdr"><div><h1>סורק פריצות</h1>'
        f'<div class="ts">עדכון {ts} · רענון אוטומטי כל 15 דקות</div></div>'
        '<button class="refresh-btn" onclick="location.reload()">עדכן עכשיו</button></div>')
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
                flags = "<br>".join(x.get("flags") or [])
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
                    + (f'<div class="a-comp" style="color:#b45309">{esc(flags)}</div>' if flags else "")
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
    parts.append("</div>")

    # ================= bottom tab bar =================
    badge = (f'<span class="count-badge">{n_alerts}</span>' if n_alerts else "")
    parts.append(
        '<div class="tabbar">'
        '<button class="active" data-t="mh">בריאות השוק</button>'
        f'<button data-t="bio">ביוטק{badge}</button>'
        '<button data-t="dip">דיפים</button>'
        '<button data-t="bo">פריצות</button>'
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
    alerts = biotech_alerts()
    bo = breakout_section()

    updated = datetime.now(JER)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(mh, dips, alerts, bo, updated))
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
