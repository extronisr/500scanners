# -*- coding: utf-8 -*-
"""
US MARKET HEALTH MONITOR - FINAL (fixed)

No third-party Python packages are required.
Uses only Python standard library + Tkinter.

Monitors:
  S5FI  - % of S&P 500 stocks above 50-day MA  (computed from Yahoo Finance constituent data)
  S5TH  - % of S&P 500 stocks above 200-day MA (computed from Yahoo Finance constituent data)
  SPX   - S&P 500
  SPX 50DMA / 200DMA
  VIX
  US 10Y
  Brent

The app refreshes every 60 seconds.
S5FI/S5TH are daily breadth indicators, computed from ~500 constituents via
Yahoo Finance's batch spark endpoint. The result is cached for 6 hours because
breadth only changes once per trading day. The constituent list (Wikipedia)
is cached on disk for 7 days.

IMPORTANT:
The Stability Score is a monitoring formula, not a prediction and not a buy/sell signal.
If a source cannot be read, the value is shown as "לא זמין". The program never invents data.
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

REFRESH_SECONDS = 60
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36"

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKER_CACHE_FILE = os.path.join(CACHE_DIR, "sp500_tickers_cache.json")
NAMES_CACHE_FILE = os.path.join(CACHE_DIR, "sp500_names_cache.json")
TICKER_CACHE_DAYS = 7
BREADTH_TTL_SECONDS = 6 * 3600  # breadth is a daily indicator; recompute at most every 6h
SPARK_BATCH_SIZE = 20           # Yahoo spark endpoint rejects more than ~20 symbols per call

_breadth_cache = {"ts": 0.0, "s5fi": None, "s5th": None, "n": 0}


def fmt(value, digits=2, suffix=""):
    if value is None:
        return "לא זמין"
    return f"{value:.{digits}f}{suffix}"


def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_close_series(symbol, range_value="1y"):
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_value}&interval=1d&events=history"
    try:
        data = http_get_json(url)
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        return [float(x) for x in closes if x is not None]
    except Exception:
        return None


def get_market_data():
    spx = yahoo_close_series("^GSPC", "1y")
    vix = yahoo_close_series("^VIX", "3mo")
    tnx = yahoo_close_series("^TNX", "3mo")
    brent = yahoo_close_series("BZ=F", "3mo")
    wti = yahoo_close_series("CL=F", "3mo")

    d = {
        "spx": None,
        "dma50": None,
        "dma200": None,
        "vix": None,
        "vix_3d": None,
        "tnx": None,
        "tnx_3d": None,
        "brent": None,
        "brent_3d": None,
        "brent_3d_pct": None,
        "wti": None,
        "wti_3d": None,
        "wti_3d_pct": None,
    }

    if spx and len(spx) >= 200:
        d["spx"] = spx[-1]
        d["dma50"] = sum(spx[-50:]) / 50
        d["dma200"] = sum(spx[-200:]) / 200

    if vix:
        d["vix"] = vix[-1]
        if len(vix) >= 4:
            d["vix_3d"] = vix[-1] - vix[-4]

    if tnx:
        # Yahoo ^TNX currently returns the 10Y yield directly in percent, e.g. 5.28 = 5.28%.
        d["tnx"] = tnx[-1]
        if len(tnx) >= 4:
            d["tnx_3d"] = tnx[-1] - tnx[-4]

    if brent:
        d["brent"] = brent[-1]
        if len(brent) >= 4:
            d["brent_3d"] = brent[-1] - brent[-4]
            d["brent_3d_pct"] = d["brent_3d"] / brent[-4] * 100

    if wti:
        d["wti"] = wti[-1]
        if len(wti) >= 4:
            d["wti_3d"] = wti[-1] - wti[-4]
            d["wti_3d_pct"] = d["wti_3d"] / wti[-4] * 100

    return d


def load_sp500_tickers():
    """Return the S&P 500 constituent list (Yahoo-style symbols), cached on disk."""
    try:
        with open(TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if (time.time() - cached.get("ts", 0) < TICKER_CACHE_DAYS * 86400
                and len(cached.get("tickers", [])) > 400):
            return cached["tickers"]
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", errors="ignore")
        start = html.find('class="wikitable')
        table = html[start:html.find("</table>", start)]
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)
        tickers = []
        for row in rows[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            if not cells:
                continue
            # Wikipedia uses dots (BRK.B); Yahoo uses dashes (BRK-B).
            sym = re.sub(r"<[^>]+>", "", cells[0]).strip().replace(".", "-")
            if sym and re.fullmatch(r"[A-Z0-9\-]+", sym):
                tickers.append(sym)
        if len(tickers) > 400:
            try:
                with open(TICKER_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "tickers": tickers}, f)
            except Exception:
                pass
            return tickers
    except Exception:
        pass
    return None


def load_sp500_names():
    """{symbol: company name} from the same Wikipedia table, cached for TICKER_CACHE_DAYS.
    Used to check that news headlines are about the right company. {} if unavailable."""
    try:
        with open(NAMES_CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if time.time() - cached.get("ts", 0) < TICKER_CACHE_DAYS * 86400 and len(cached.get("names", {})) > 400:
            return cached["names"]
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            html = response.read().decode("utf-8", errors="ignore")
        start = html.find('class="wikitable')
        table = html[start:html.find("</table>", start)]
        names = {}
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S)[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
            if len(cells) < 2:
                continue
            sym = re.sub(r"<[^>]+>", "", cells[0]).strip().replace(".", "-")
            nm = re.sub(r"<[^>]+>", "", cells[1]).strip()
            if sym and nm and re.fullmatch(r"[A-Z0-9\-]+", sym):
                names[sym] = nm
        if len(names) > 400:
            try:
                with open(NAMES_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "names": names}, f, ensure_ascii=False)
            except Exception:
                pass
            return names
    except Exception:
        pass
    return {}


def spark_closes_batch(symbols):
    """Fetch 1y daily closes for up to SPARK_BATCH_SIZE symbols in one Yahoo call."""
    params = urllib.parse.urlencode({"symbols": ",".join(symbols), "range": "1y", "interval": "1d"})
    url = "https://query1.finance.yahoo.com/v7/finance/spark?" + params
    try:
        data = http_get_json(url, timeout=30)
    except Exception:
        return {}
    out = {}
    for item in data.get("spark", {}).get("result", []):
        try:
            indicators = item["response"][0]["indicators"]
            adj = indicators.get("adjclose", [{}])[0].get("adjclose")
            raw = indicators["quote"][0]["close"]
            src = adj if adj and any(x is not None for x in adj) else raw
            closes = [float(x) for x in src if x is not None]
            if closes:
                out[item["symbol"]] = closes
        except Exception:
            continue
    return out


def compute_breadth_yahoo(tickers):
    """Compute % of constituents above their 50-day and 200-day moving averages."""
    closes_map = {}
    for i in range(0, len(tickers), SPARK_BATCH_SIZE):
        closes_map.update(spark_closes_batch(tickers[i:i + SPARK_BATCH_SIZE]))
        time.sleep(0.2)  # be polite to the API
    above50 = above200 = n50 = n200 = 0
    for closes in closes_map.values():
        if len(closes) >= 50:
            n50 += 1
            if closes[-1] >= sum(closes[-50:]) / 50:
                above50 += 1
        if len(closes) >= 200:
            n200 += 1
            if closes[-1] >= sum(closes[-200:]) / 200:
                above200 += 1
    # coverage floor: publishing 100% from a handful of loaded tickers is
    # worse than admitting the data is unavailable (60% minimum coverage)
    min_cov = 0.6 * len(tickers) if tickers else 0
    s5fi = round(100.0 * above50 / n50, 1) if (n50 and n50 >= min_cov) else None
    s5th = round(100.0 * above200 / n200, 1) if (n200 and n200 >= min_cov) else None
    return {"s5fi": s5fi, "s5th": s5th, "n": n50}


def get_breadth():
    """Breadth with a 6-hour cache — it is a daily indicator, no need to recompute."""
    global _breadth_cache
    if (time.time() - _breadth_cache["ts"] < BREADTH_TTL_SECONDS
            and _breadth_cache["s5fi"] is not None):
        return _breadth_cache
    tickers = load_sp500_tickers()
    if not tickers:
        return {"s5fi": None, "s5th": None, "n": 0}
    try:
        result = compute_breadth_yahoo(tickers)
    except Exception:
        return {"s5fi": None, "s5th": None, "n": 0}
    if result["s5fi"] is not None:
        _breadth_cache = {"ts": time.time(), **result}
    return _breadth_cache


def get_all_data():
    data = get_market_data()
    breadth = get_breadth()
    data["s5fi"] = breadth["s5fi"]
    data["s5th"] = breadth["s5th"]
    data["breadth_n"] = breadth["n"]
    return data


def zone_high(x, elevated_at, stress_at):
    """Higher value = worse (VIX, yields, oil). 1.0 below elevated_at,
    0.5 below stress_at, else 0.0. None-safe (returns None)."""
    if x is None:
        return None
    if x < elevated_at:
        return 1.0
    if x < stress_at:
        return 0.5
    return 0.0


def zone_low(x, healthy_at, stress_at):
    """Higher value = better (breadth). 1.0 at/above healthy_at,
    0.5 at/above stress_at, else 0.0. None-safe (returns None)."""
    if x is None:
        return None
    if x >= healthy_at:
        return 1.0
    if x >= stress_at:
        return 0.5
    return 0.0


def bin_check(ok, available):
    if not available:
        return None
    return 1.0 if ok else 0.0


def both(a, b):
    """Worst of two sub-scores (e.g. level and spike); None if either missing."""
    if a is None or b is None:
        return None
    return min(a, b)


def calculate_score(d):
    # Research-backed bands (deep research 2026-10-08):
    #  breadth 50d: >=55 healthy, 25-55 elevated, <25 stress (washout <15)
    #  breadth 200d: >=60 healthy, 40-60 elevated, <40 stress
    #  VIX: <20 calm, 20-30 elevated, >=30 stress
    #  10Y: <4.25% healthy, 4.25-5.0% elevated, >=5.0% stress (>=5.5% severe);
    #       3d jump <15bp / 15-30bp / >=30bp
    #  Brent: <$85 healthy, $85-105 elevated, >=$105 stress
    #  WTI: <$78 healthy, $78-95 elevated, >=$95 stress
    #  oil 3d spike: <5% / 5-10% / >=10%
    checks = {
        "s5fi": zone_low(d["s5fi"], 55.0, 25.0),
        "s5th": zone_low(d["s5th"], 60.0, 40.0),
        "spx50": bin_check(d["spx"] is not None and d["dma50"] is not None and d["spx"] >= d["dma50"],
                           d["spx"] is not None and d["dma50"] is not None),
        "spx200": bin_check(d["spx"] is not None and d["dma200"] is not None and d["spx"] >= d["dma200"],
                            d["spx"] is not None and d["dma200"] is not None),
        "vix": zone_high(d["vix"], 20.0, 30.0),
        "tnx": both(zone_high(d["tnx"], 4.25, 5.0),
                    zone_high(d["tnx_3d"], 0.15, 0.30)),
        "brent": both(zone_high(d["brent"], 85.0, 105.0),
                      zone_high(d["brent_3d_pct"], 5.0, 10.0)),
        "wti": both(zone_high(d["wti"], 78.0, 95.0),
                    zone_high(d["wti_3d_pct"], 5.0, 10.0)),
    }

    # Do not produce a misleading score when one of the eight inputs is missing.
    required = [
        d["s5fi"], d["s5th"], d["spx"], d["dma50"], d["dma200"],
        d["vix"], d["tnx"], d["tnx_3d"], d["brent"], d["brent_3d_pct"],
        d["wti"], d["wti_3d_pct"],
    ]
    all_available = all(x is not None for x in required)

    score = round(sum(checks.values()) / 8 * 100) if all_available else None

    hard_risk = (
        (d["s5fi"] is not None and d["s5fi"] < 15)
        or (d["s5th"] is not None and d["s5th"] < 35)
        or (d["spx"] is not None and d["dma50"] is not None and d["spx"] < d["dma50"])
        or (d["vix"] is not None and d["vix"] >= 30)
        or (d["tnx"] is not None and d["tnx"] >= 5.5)
        or (d["tnx_3d"] is not None and d["tnx_3d"] >= 0.30)
        or (d["brent_3d_pct"] is not None and d["brent_3d_pct"] >= 10)
        or (d["wti_3d_pct"] is not None and d["wti_3d_pct"] >= 10)
    )

    if not all_available:
        status = "אין מספיק נתונים"
    elif hard_risk:
        status = "תנאי סיכון פעילים"
    elif score >= 71:
        status = "מתייצב"
    elif score >= 57:
        status = "שיפור"
    else:
        status = "לא מתייצב"

    if all_available:
        check_states = {
            name: ("pass" if v == 1.0 else "warn" if v == 0.5 else "fail")
            for name, v in checks.items()
        }
    else:
        check_states = {}

    return score, status, check_states


# Maps each of the 8 score checks to the UI row(s) carrying its status dot.
CHECK_EXPLANATIONS = {
    "s5fi": "מעט מדי מניות מעל ממוצע 50 יום (רוחב שוק חלש)",
    "s5th": "מעט מדי מניות מעל ממוצע 200 יום",
    "spx50": "המדד מתחת לממוצע 50 הימים שלו",
    "spx200": "המדד מתחת לממוצע 200 הימים שלו",
    "vix": "תנודתיות גבוהה",
    "tnx": "תשואת אגח 10 שנים גבוהה או עולה מהר",
    "brent": "מחיר נפט ברנט גבוה או עולה מהר",
    "wti": "מחיר נפט WTI גבוה או עולה מהר",
}

