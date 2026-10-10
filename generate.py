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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# BIOTECH_DIR removed: config lives next to this script
OUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(OUT_DIR, "index.html")

sys.path.insert(0, BASE_DIR)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142 Safari/537.36"
SEC_UA = "ScannersSiteDashboard/1.0 contact@example.com"
JER = ZoneInfo("Asia/Jerusalem")

failures = []          # list of (section, detail) for the run report
stats = {"sp500_missing": 0, "biotech_missing": 0, "sec_failed": 0, "news_failed": 0}


def fail(section, detail):
    failures.append((section, detail))


def http_get(url, timeout=20, ua=UA, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url, timeout=20, ua=UA):
    return json.loads(http_get(url, timeout=timeout, ua=ua,
                               accept="application/json").decode("utf-8"))


def esc(s):
    return htmlmod.escape(str(s), quote=True)


# ---------------------------------------------------------------- market health
CHECK_LABELS = {
    "s5fi": "רוחב שוק: מניות מעל ממוצע 50 יום",
    "s5th": "רוחב שוק: מניות מעל ממוצע 200 יום",
    "spx50": "מגמה: המדד מעל ממוצע 50 יום",
    "spx200": "מגמה: המדד מעל ממוצע 200 יום",
    "vix": "מדד הפחד (VIX)",
    "tnx": "תשואת אג״ח 10 שנים",
    "brent": "נפט ברנט",
    "wti": "נפט WTI",
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
            return f"{d['spx']:,.0f} מול ממוצע {d['dma50']:,.0f}"
        if key == "spx200":
            if d["spx"] is None or d["dma200"] is None:
                return "לא זמין"
            return f"{d['spx']:,.0f} מול ממוצע {d['dma200']:,.0f}"
        if key == "vix":
            return "לא זמין" if d["vix"] is None else f"{d['vix']:.2f}"
        if key == "tnx":
            if d["tnx"] is None:
                return "לא זמין"
            s = f"{d['tnx']:.2f}%"
            if d["tnx_3d"] is not None:
                bp = d["tnx_3d"] * 100
                s += f" (3 ימים: {'+' if bp >= 0 else ''}{bp:.0f}bp)"
            return s
        if key == "brent":
            if d["brent"] is None:
                return "לא זמין"
            s = f"${d['brent']:.2f}"
            if d["brent_3d_pct"] is not None:
                p = d["brent_3d_pct"]
                s += f" (3 ימים: {'+' if p >= 0 else ''}{p:.1f}%)"
            return s
        if key == "wti":
            if d["wti"] is None:
                return "לא זמין"
            s = f"${d['wti']:.2f}"
            if d["wti_3d_pct"] is not None:
                p = d["wti_3d_pct"]
                s += f" (3 ימים: {'+' if p >= 0 else ''}{p:.1f}%)"
            return s
        return "לא זמין"

    checks = []
    for key in ["s5fi", "s5th", "spx50", "spx200", "vix", "tnx", "brent", "wti"]:
        state = states.get(key, "fail") if states else "fail"
        checks.append({
            "label": CHECK_LABELS[key],
            "value": val(key),
            "state": state,
            "explain": m.CHECK_EXPLANATIONS.get(key, ""),
        })
    return {"score": score, "status": status, "checks": checks}


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


def news_headlines(query, n=2):
    q = urllib.parse.quote(query)
    url = (f"https://news.google.com/rss/search?q={q}"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        raw = http_get(url, timeout=15, accept="application/rss+xml")
        root = ET.fromstring(raw)
        heads = []
        for item in root.iter("item"):
            t = item.findtext("title")
            if t:
                heads.append(t.strip())
            if len(heads) >= n:
                break
        return heads
    except Exception as e:
        stats["news_failed"] += 1
        fail("dip", f"news RSS failed for {query!r}: {e}")
        return []


def dip_scan_section():
    tickers = load_sp500_tickers()
    if not tickers:
        fail("dip", "no tickers available")
        return []
    closes_map = {}
    for i in range(0, len(tickers), 20):
        closes_map.update(spark_closes(tickers[i:i + 20]))
        time.sleep(0.2)
    stats["sp500_missing"] = len(tickers) - len(closes_map)
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
    for sym, depth, ref_low, r, last in prelim:
        closes, vols = chart_closes_volumes(sym, "3mo")
        if not vols or len(vols) < 22:
            continue
        vol_avg = sum(vols[-21:-1]) / 20.0
        if vol_avg <= 0:
            continue
        vol_mult = vols[-1] / vol_avg
        if vol_mult <= 1.5:
            continue
        cands.append({
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
    for c in cands:
        c["headlines"] = news_headlines(c["ticker"] + " stock")
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


def biotech_section():
    cfg_path = os.path.join(BASE_DIR, "biotech_config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            watchlist = json.load(f)["watchlist"]
    except Exception as e:
        fail("biotech", f"config unreadable: {e}")
        return []
    rows = []
    today_iso = datetime.now(timezone.utc).date().isoformat()
    for w in watchlist:
        ticker, name, cik = w.get("ticker"), w.get("name", ""), w.get("cik", "")
        closes, vols = chart_closes_volumes(ticker)
        if not closes or len(closes) < 22:
            continue
        chg = (closes[-1] / closes[-2] - 1.0) * 100.0 if closes[-2] else 0.0
        vol_avg = sum(vols[-21:-1]) / 20.0 if len(vols) >= 22 else 0.0
        vol_mult = (vols[-1] / vol_avg) if vol_avg > 0 else 0.0
        filings = edgar_8k_last_days(cik, days=3) if cik else []
        notable = abs(chg) >= 5.0 or vol_mult >= 2.0 or bool(filings)
        if not notable:
            continue
        newest = filings[0]["date"] if filings else today_iso
        rows.append({
            "ticker": ticker, "name": name, "chg": chg,
            "vol_mult": vol_mult, "filings": filings, "newest": newest,
        })
        time.sleep(0.15)  # be polite to EDGAR
    rows.sort(key=lambda r: r["newest"], reverse=True)
    return rows


# ---------------------------------------------------------------- HTML
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,'Segoe UI',Arial,sans-serif;
direction:rtl;padding:12px;max-width:720px;margin:0 auto}
.hdr{text-align:center;padding:14px 6px 10px}
.hdr h1{font-size:1.35rem;margin-bottom:6px}
.hdr .ts{color:#8b949e;font-size:.85rem}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px;margin:12px 0}
.card h2{font-size:1.05rem;margin-bottom:10px;color:#f0f3f6}
.score-wrap{display:flex;align-items:center;gap:16px}
.score{font-size:3rem;font-weight:800;min-width:96px;text-align:center}
.status{font-size:1.05rem;font-weight:700}
.sub{color:#8b949e;font-size:.8rem;margin-top:4px}
.chk{display:flex;align-items:flex-start;gap:10px;padding:9px 2px;border-top:1px solid #21262d}
.chk:first-of-type{border-top:none}
.dot{width:12px;height:12px;border-radius:50%;margin-top:4px;flex-shrink:0}
.dot.pass{background:#3fb950}.dot.warn{background:#d29922}.dot.fail{background:#f85149}
.chk .t{flex:1}.chk .t b{display:block;font-size:.92rem}.chk .t span{color:#8b949e;font-size:.78rem}
.chk .v{font-size:.85rem;color:#c9d1d9;white-space:nowrap}
.dip{border:1px solid #30363d;border-radius:10px;padding:10px;margin:8px 0;background:#0d1117}
.dip .tick{font-size:1.05rem;font-weight:800;color:#58a6ff}
.dip .meta{font-size:.82rem;color:#c9d1d9;margin:4px 0}
.dip .hl{font-size:.78rem;color:#8b949e;margin-top:6px}
.dip .hl li{margin:3px 0;list-style:none}
.dip .hl li::before{content:"📰 "}
.bio{border:1px solid #30363d;border-radius:10px;padding:10px;margin:8px 0;background:#0d1117}
.bio .tick{font-weight:800;font-size:1rem}
.up{color:#3fb950}.down{color:#f85149}
.badge{display:inline-block;background:#1f6feb;color:#fff;font-size:.72rem;
border-radius:20px;padding:2px 10px;margin:2px 4px 2px 0}
.empty{color:#8b949e;text-align:center;padding:14px;font-size:.88rem}
.ftr{color:#6e7681;font-size:.72rem;text-align:center;padding:16px 4px}
"""

STATUS_COLORS = {
    "מתייצב": "#3fb950",
    "שיפור": "#d29922",
    "לא מתייצב": "#f85149",
    "תנאי סיכון פעילים": "#f85149",
    "אין מספיק נתונים": "#8b949e",
}


def build_html(mh, dips, bios, updated):
    parts = [f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>סורקי הבורסה</title>
<style>{CSS}</style>
</head>
<body>
<div class="hdr">
<h1>📊 סורקי הבורסה</h1>
<div class="ts">עודכן: {esc(updated.strftime('%H:%M'))} (שעון ישראל) · {esc(updated.strftime('%d/%m/%Y'))}</div>
</div>"""]

    # ---- market health
    parts.append('<div class="card"><h2>בריאות שוק ארה״ב</h2>')
    if mh and mh["score"] is not None:
        col = STATUS_COLORS.get(mh["status"], "#e6edf3")
        parts.append(
            f'<div class="score-wrap"><div class="score" style="color:{col}">'
            f'{mh["score"]}</div>'
            f'<div><div class="status" style="color:{col}">{esc(mh["status"])}</div>'
            f'<div class="sub">ציון 0–100 לפי 8 בדיקות</div></div></div>')
        for c in mh["checks"]:
            parts.append(
                f'<div class="chk"><span class="dot {esc(c["state"])}"></span>'
                f'<div class="t"><b>{esc(c["label"])}</b>'
                f'<span>{esc(c["explain"])}</span></div>'
                f'<div class="v">{esc(c["value"])}</div></div>')
    else:
        parts.append('<div class="empty">אין מספיק נתונים כרגע</div>')
    parts.append("</div>")

    # ---- dips
    parts.append('<div class="card"><h2>סורק דיפים (S&amp;P 500)</h2>')
    if dips:
        for c in dips:
            hls = "".join(f"<li>{esc(h)}</li>" for h in c["headlines"])
            parts.append(
                f'<div class="dip"><span class="tick">{esc(c["ticker"])}</span>'
                f'<div class="meta">עומק הדיפ: <b>{c["depth"]:.1f}%</b> מהשיא · '
                f'מעל השפל: {c["above_low_pct"]:.1f}% · '
                f'RSI(14): {c["rsi"]:.0f} · מחזור ×{c["vol_mult"]:.1f}</div>'
                + (f'<ul class="hl">{hls}</ul>' if hls else "") + "</div>")
    else:
        parts.append('<div class="empty">אין מניות שעומדות כרגע בכל תנאי הדיפ</div>')
    parts.append("</div>")

    # ---- biotech
    parts.append('<div class="card"><h2>סורק ביוטק</h2>')
    if bios:
        for b in bios:
            cls = "up" if b["chg"] >= 0 else "down"
            sign = "+" if b["chg"] >= 0 else ""
            badges = "".join(
                f'<span class="badge">8-K · {esc(f["date"])}'
                + (f' · סעיפים {esc(f["items"])}' if f["items"] else "")
                + "</span>" for f in b["filings"])
            parts.append(
                f'<div class="bio"><span class="tick">{esc(b["ticker"])}</span> '
                f'<span class="{cls}">{sign}{b["chg"]:.1f}%</span>'
                f'<div class="sub">{esc(b["name"])}</div>'
                f'<div class="meta" style="font-size:.82rem;color:#c9d1d9">'
                f'מחזור ×{b["vol_mult"]:.1f} מהממוצע</div>'
                f'{badges}</div>')
    else:
        parts.append('<div class="empty">אין תנועות חריגות או דיווחי 8-K חדשים כרגע</div>')
    parts.append("</div>")

    parts.append(
        '<div class="ftr">נתונים: Yahoo Finance · SEC EDGAR · Google News · '
        'מקורות חינמיים בלבד · לא ייעוץ השקעות</div></body></html>')
    return "\n".join(parts)


def main():
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)

    mh = market_health_section()
    dips = dip_scan_section()
    bios = biotech_section()

    updated = datetime.now(JER)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_html(mh, dips, bios, updated))
    dt = time.time() - t0

    print(f"WROTE {OUT_FILE} ({os.path.getsize(OUT_FILE)} bytes) in {dt:.0f}s")
    if mh:
        print(f"MARKET score={mh['score']} status={mh['status']}")
    print(f"DIPS candidates={len(dips)}" +
          (f" top={[c['ticker'] for c in dips[:5]]}" if dips else ""))
    print(f"BIOTECH notable={len(bios)}" +
          (f" tickers={[b['ticker'] for b in bios]}" if bios else ""))
    print(f"SP500 missing series: {stats['sp500_missing']}, "
          f"biotech missing: {stats['biotech_missing']}, "
          f"SEC failures: {stats['sec_failed']}, news failures: {stats['news_failed']}")
    if failures:
        print("FAILURES:")
        for sec, det in failures[:20]:
            print(f"  [{sec}] {det}")


if __name__ == "__main__":
    main()
