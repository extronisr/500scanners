import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_lib as m
import scan_utils as su


def base_data(**over):
    d = dict(s5fi=60, s5th=65, spx=6000, dma50=5900, dma200=5500, vix=15, vix_3d=0,
             tnx=4.0, tnx_3d=0.0, brent=70, brent_3d=0, brent_3d_pct=0,
             wti=65, wti_3d=0, wti_3d_pct=0)
    d.update(over)
    return d


def test_score_with_all_data():
    score, status, states = m.calculate_score(base_data())
    assert score == 100 and status == "מתייצב" and len(states) == 8


def test_score_missing_index_does_not_raise():
    score, status, states = m.calculate_score(base_data(spx=None, dma50=None, dma200=None))
    assert score is None and status == "אין מספיק נתונים" and states == {}


def test_score_missing_one_input_is_unavailable_not_zero():
    score, status, _ = m.calculate_score(base_data(s5fi=None))
    assert score is None and status == "אין מספיק נתונים"


# ---- volume policy
FLAT = [100.0] * 25


def test_closed_confirmed_excludes_reference_session_from_average():
    vc = su.volume_context(FLAT[:-1] + [400.0], None)
    assert abs(vc["confirmed"] - 4.0) < 1e-9 and vc["estimate"] is None


def test_open_confirmed_is_yesterday_vs_20_sessions_before_yesterday():
    v = [100.0] * 22 + [500.0, 10.0]
    vc = su.volume_context(v, 0.5)
    assert abs(vc["confirmed"] - 5.0) < 1e-9


def test_open_estimate_is_separate_and_labelled_estimate():
    v = FLAT[:-1] + [100.0]                  # 100 by 31% of the day -> ~3.2x projected
    vc = su.volume_context(v, 0.31)
    assert vc["estimate"] > 3.0 and abs(vc["confirmed"] - 1.0) < 1e-9
    tier, mult = su.volume_signal(vc, 1.5)
    assert tier == "estimate"                   # large early volume can pass, as an estimate
    assert "הערכה" in su.volume_text(tier, mult, 0.31)


def test_confirmed_pass_wins_over_estimate():
    v = [100.0] * 22 + [200.0, 100.0]           # yesterday 2x, today projected ~3.2x
    tier, mult = su.volume_signal(su.volume_context(v, 0.31), 1.5)
    assert tier == "confirmed" and abs(mult - 2.0) < 1e-9


def test_no_estimate_early_in_session():
    v = FLAT[:-1] + [50.0]
    vc = su.volume_context(v, 0.05)             # 5% of session elapsed: projection unreliable
    assert vc["estimate"] is None


def test_partial_volume_alone_does_not_pass():
    v = FLAT[:-1] + [30.0]
    assert su.volume_signal(su.volume_context(v, 0.31), 1.5) == (None, None)


def test_threshold_boundaries():
    vc = {"confirmed": 1.5, "estimate": None}
    assert su.volume_signal(vc, 1.5, strict=True)[0] is None      # dips use >
    assert su.volume_signal(vc, 1.5, strict=False)[0] == "confirmed"  # biotech uses >=


def test_not_enough_data():
    assert su.volume_context([1.0] * 10, None) is None
    assert su.volume_context([1.0] * 10, 0.5) is None
    assert su.volume_context([0.0] * 30, None) is None


# ---- session boundaries (real NYSE calendar)
import datetime as dt
import pytest

xc = pytest.importorskip("exchange_calendars")
UTC = dt.timezone.utc


def test_regular_day_open_and_closed():
    st, d, frac = su.market_state(dt.datetime(2026, 10, 23, 15, 0, tzinfo=UTC))   # Fri 11:00 ET
    assert st == "OPEN" and d == dt.date(2026, 10, 23) and 0.2 < frac < 0.4
    st, d, _ = su.market_state(dt.datetime(2026, 10, 23, 20, 30, tzinfo=UTC))     # after close
    assert st == "CLOSED" and d == dt.date(2026, 10, 23)
    st, d, _ = su.market_state(dt.datetime(2026, 10, 23, 12, 0, tzinfo=UTC))      # pre-market
    assert st == "CLOSED" and d == dt.date(2026, 10, 22)


def test_weekend_and_holiday_are_closed_with_last_session():
    st, d, _ = su.market_state(dt.datetime(2026, 10, 24, 16, 0, tzinfo=UTC))      # Saturday
    assert st == "CLOSED" and d == dt.date(2026, 10, 23)
    st, d, _ = su.market_state(dt.datetime(2026, 11, 26, 16, 0, tzinfo=UTC))      # Thanksgiving
    assert st == "CLOSED" and d == dt.date(2026, 11, 25)


def test_early_close_day_after_thanksgiving():
    st, _, frac = su.market_state(dt.datetime(2026, 11, 27, 16, 0, tzinfo=UTC))   # 11:00 ET
    assert st == "OPEN" and 0.4 < frac < 0.5      # 1pm close: 1.5h of 3.5h (a normal day would be ~0.23)
    st, d, _ = su.market_state(dt.datetime(2026, 11, 27, 19, 0, tzinfo=UTC))      # 2pm ET
    assert st == "CLOSED" and d == dt.date(2026, 11, 27)


# ---- news disambiguation
def test_news_observed_pharos_headline_rejected():
    t = "PHAROS ENERGY PLC Equity Price and Quote - London Stock Exchange"   # real PHAR result seen in the page
    assert not su.headline_matches(t, "PHAR", "Pharming Group N.V.")


def test_news_pharos_without_ticker_pattern_rejected():
    t = "Pharos Energy plc reports half year results - London Stock Exchange"
    assert not su.headline_matches(t, "PHAR", "Pharming Group N.V.")


def test_news_pharming_accepted_by_name():
    assert su.headline_matches("Pharming Group N.V. announces Q3 results", "PHAR", "Pharming Group N.V.")


def test_news_requires_exchange_style_ticker_without_name():
    assert su.headline_matches("Hubbell Inc (HUBB) Shares Fall 3.2%", "HUBB")
    assert su.headline_matches("Eaton (NYSE:ETN) upgraded", "ETN")
    assert not su.headline_matches("Why hubb is trending", "HUBB")
    assert not su.headline_matches("Etna erupts again", "ETN")


# ---- bounded retries
def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def test_retry_only_transient_and_bounded(monkeypatch):
    import generate as g
    monkeypatch.setattr(g.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always(code):
        def f(*a, **k):
            calls["n"] += 1
            raise _http_error(code)
        return f

    monkeypatch.setattr(g, "http_get", always(403))
    try:
        g.http_get_json("http://x")
    except Exception:
        pass
    assert calls["n"] == 1            # 403 is not retried

    calls["n"] = 0
    monkeypatch.setattr(g, "http_get", always(503))
    try:
        g.http_get_json("http://x", retries=2)
    except Exception:
        pass
    assert calls["n"] == 3            # 1 try + 2 retries, then gives up


def test_retry_recovers(monkeypatch):
    import generate as g
    monkeypatch.setattr(g.time, "sleep", lambda *_: None)
    seq = [_http_error(429), b'{"ok": 1}']

    def f(*a, **k):
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x
    monkeypatch.setattr(g, "http_get", f)
    assert g.http_get_json("http://x") == {"ok": 1}


# ---- news freshness and relevance
NOW = dt.datetime(2026, 10, 23, 15, 0, tzinfo=UTC)


def _pub(days_ago):
    d = NOW - dt.timedelta(days=days_ago)
    return d.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_recent_news_accepted_and_stale_rejected():
    assert su.is_recent(su.parse_pub(_pub(0.1)), now=NOW)
    assert su.is_recent(su.parse_pub(_pub(2.9)), now=NOW)
    assert not su.is_recent(su.parse_pub(_pub(3.5)), now=NOW)


def test_september_item_is_stale():
    # "What Makes Faeth Therapeutics (FTH) a Strong Momentum Stock" was shown with 1.9.2026
    sept = su.parse_pub("Tue, 01 Sep 2026 04:00:00 GMT")
    assert not su.is_recent(sept, now=NOW)


def test_2023_historical_quotes_item_rejected_by_age_and_as_evergreen():
    title = "Genelux Corporation Common Stock (GNLX) Historical Quotes - Nasdaq"
    assert not su.is_recent(su.parse_pub("Thu, 26 Jan 2023 14:40:00 GMT"), now=NOW)
    assert su.is_evergreen(title)


def test_undated_or_garbage_date_rejected():
    assert su.parse_pub("") is None and su.parse_pub(None) is None and su.parse_pub("soon") is None
    assert not su.is_recent(None, now=NOW)


def test_future_dated_item_rejected():
    assert not su.is_recent(su.parse_pub(_pub(-5)), now=NOW)


def test_evergreen_patterns_but_event_news_kept():
    assert su.is_evergreen("Genelux Corp (GNLX) Stock Price Target & Forecast - TradingKey")
    assert su.is_evergreen("PHAROS ENERGY PLC Equity Price and Quote - London Stock Exchange")
    assert not su.is_evergreen("Faeth Therapeutics Announces Inducement Grants Under Nasdaq Listing Rule")
    assert not su.is_evergreen("Genelux (GNLX) shares jump after trial update")


def test_conflicting_issuer_rejected_even_with_ticker_in_brackets():
    assert not su.headline_matches("PHAROS ENERGY PLC (PHAR) shares rise", "PHAR", "Pharming Group N.V.")
    assert not su.headline_matches("Acme Widgets (NASDAQ:GNLX) surges", "GNLX", "Genelux Corp")


def test_ticker_in_brackets_accepted_when_nothing_conflicts():
    assert su.headline_matches("(GNLX) trial update", "GNLX", "Genelux Corp")
    assert su.headline_matches("Genelux Corporation (GNLX) trial update", "GNLX", "Genelux Corp")


# ---- new live-site sections (scan_utils helpers)
def test_sec_form_severity_material_8k():
    sev, _ = su.sec_form_severity("8-K", "1.01 2.02 8.01")
    assert sev == 3


def test_sec_form_severity_routine_8k_is_info():
    sev, _ = su.sec_form_severity("8-K", "5.07")
    assert sev == 1


def test_sec_form_severity_dilution():
    assert su.sec_form_severity("424B5")[0] == 2
    assert su.sec_form_severity("S-3ASR")[0] == 2
    assert su.sec_form_severity("4")[0] == 1


def test_pullback_requires_uptrend_and_rsi():
    closes = [100.0] * 150 + [88.0] * 60     # 12% below high, still above sma200? no: sma200 ~ 96.4
    assert su.is_pullback_uptrend(closes, 35) is None
    closes = [80.0] * 150 + [100.0] * 55 + [90.0] * 5   # dd 10%, sma200 ~ 86.3, last above it
    r = su.is_pullback_uptrend(closes, 35)
    assert r is not None and abs(r["dd"] - 10.0) < 0.2
    assert su.is_pullback_uptrend(closes, 45) is None   # RSI too high


def test_catalyst_bucket_tiers_and_approx():
    assert su.catalyst_bucket(3) == "imminent"
    assert su.catalyst_bucket(3, approx=True) == "approaching"   # quarter estimate never imminent
    assert su.catalyst_bucket(14) == "approaching"
    assert su.catalyst_bucket(60) == "watch"
    assert su.catalyst_bucket(None) == "watch"


# ---- biotech breakthrough layer (whole-market scan)

def test_breakthrough_direction_words():
    assert su.breakthrough_direction("cuts melanoma recurrence risk by 42%") == 1
    assert su.breakthrough_direction("fails to meet primary endpoint") == -1
    assert su.breakthrough_direction("announces phase 3 data readout") == 0


def test_breakthrough_practice_changing_phase3_win_is_top():
    c = su.classify_breakthrough("Merck and Moderna cancer vaccine cuts melanoma recurrence risk by 42% in phase 3")
    assert c == {"cat": "phase3", "sev": 4, "dir": 1}
    c = su.classify_breakthrough("FDA approves first-in-class treatment for rare blood disorder")
    assert c["sev"] == 4 and c["dir"] == 1


def test_breakthrough_major_events_are_sev3():
    c = su.classify_breakthrough("BioNTech phase 3 lung cancer trial fails to meet primary endpoint")
    assert c["cat"] == "phase3" and c["sev"] == 3 and c["dir"] == -1
    c = su.classify_breakthrough("FDA issues complete response letter to Applied Therapeutics")
    assert c["cat"] == "crl" and c["sev"] == 3 and c["dir"] == -1
    c = su.classify_breakthrough("FDA approves GSK's new shingles vaccine")
    assert c["cat"] == "fda" and c["sev"] == 4 and c["dir"] == 1


def test_breakthrough_clinical_hold_is_major_negative():
    c = su.classify_breakthrough("FDA places clinical hold on Beam Therapeutics base editing trial")
    assert c == {"cat": "hold", "sev": 3, "dir": -1}
    c = su.classify_breakthrough("Vertex halts trial of pain drug after safety signal")
    assert c["cat"] == "hold" and c["dir"] == -1


def test_breakthrough_regulatory_designations_are_notable():
    for title in ("Acadia receives breakthrough therapy designation for Rett drug",
                  "FDA grants priority review to Veradermics hair loss treatment",
                  "FDA sets PDUFA date for Madrigal's MASH drug",
                  "Advisory committee to review GSK vaccine next month"):
        c = su.classify_breakthrough(title)
        assert c["cat"] == "btd" and c["sev"] == 2 and c["dir"] == 1, title


def test_breakthrough_filters_trial_ops_and_noise():
    for title in ("Company doses first patient in phase 1 trial",
                  "Biotech announces quarterly earnings call date",
                  "CAPR Oct 2026 13.000 call - Historical Prices",
                  "EDIT stock options chain",
                  "Analyst upgrades five biotech stocks to buy",
                  "Top 10 biotech stocks to watch this month",
                  "Small biotech reports positive phase 1 safety data"):
        assert su.classify_breakthrough(title) is None, title


def test_breakthrough_efficacy_terms_count_as_readout():
    c = su.classify_breakthrough("Drug shows progression-free survival benefit in pivotal phase 3")
    assert c is not None and c["sev"] >= 3
    c = su.classify_breakthrough("Company reports 68% overall response rate in phase 2 study")
    assert c["cat"] == "phase2" and c["sev"] == 1 and c["dir"] == 0   # readout, direction unclear
    c = su.classify_breakthrough("Company reports positive phase 2 results in eczema")
    assert c["cat"] == "phase2" and c["sev"] == 2 and c["dir"] == 1


def test_extract_tickers_styles_and_stoplist():
    assert su.extract_tickers("Nasdaq: MRNA jumps on data") == ["MRNA"]
    assert su.extract_tickers("Shares of Vertex (VRTX) rose") == ["VRTX"]
    # acronyms and common-word tickers are not extracted from bare parens
    assert su.extract_tickers("Drug approved under accelerated pathway (FDA)") == []
    assert su.extract_tickers("a treatment for rare (RARE) disease") == []
    # but they survive an explicit exchange prefix
    assert su.extract_tickers("NASDAQ: EDIT reported results") == ["EDIT"]


def test_build_bt_lexicon_and_alias_matching():
    lex = su.build_bt_lexicon({"MRNA": "Moderna Inc", "VRTX": "Vertex Pharmaceuticals"})
    hits = su.companies_in_text("Moderna and Vertex report data", lex)
    assert "MRNA" in hits and "VRTX" in hits
    # alias table maps pharma names not in the exchange list
    assert "ACAD" in su.companies_in_text("Acadia wins FDA nod", lex)
    # look-alike prose does not match (false-positive guard)
    assert su.companies_in_text("Modern art exhibit opens", lex) == []


def test_cluster_breakthroughs_merges_same_story():
    items = [
        {"id": "a", "title": "Moderna Merck vaccine cuts melanoma recurrence risk by 42% in phase 3",
         "cat": "phase3", "tickers": ["MRNA"], "companies": ["MRNA"], "sev": 4, "dir": 1,
         "date": "2026-10-24", "source": "חדשות גוגל", "link": "https://a", "pub_ts": 200},
        {"id": "b", "title": "Moderna and Merck vaccine cuts melanoma recurrence risk by 42 percent",
         "cat": "phase3", "tickers": ["MRNA", "MRK"], "companies": ["MRNA", "MRK"], "sev": 4, "dir": 1,
         "date": "2026-10-23", "source": "חדשות גוגל", "link": "https://b", "pub_ts": 100},
        {"id": "c", "title": "Unrelated: FDA approves Acadia drug for Rett syndrome",
         "cat": "fda", "tickers": ["ACAD"], "companies": ["ACAD"], "sev": 3, "dir": 1,
         "date": "2026-10-22", "source": "FDA", "link": "https://c", "pub_ts": 50},
    ]
    out = su.cluster_breakthroughs(items)
    assert len(out) == 2
    top = out[0]
    assert top["sev"] == 4 and "MRK" in top["tickers"] and "MRNA" in top["tickers"]
    assert top["link"] == "https://a"          # tied severity: newest headline kept
    assert len(top["sources"]) == 2


def test_breakthrough_speculative_and_generic_approvals_capped():
    # an expectation of approval is not an approval event: own category, no direction
    c = su.classify_breakthrough("MDMA-assisted therapy could get FDA approval as soon as January")
    assert c == {"cat": "spec", "sev": 2, "dir": 0}
    # a generic approval is a real approval, capped severity
    c = su.classify_breakthrough("FDA Approves First Generic Hormone Therapy Patch")
    assert c["cat"] == "fda" and c["sev"] == 2 and c["dir"] == 1


def test_breakthrough_death_as_endpoint_stays_positive():
    c = su.classify_breakthrough("Personalized mRNA melanoma vaccine cut recurrence or death risk 42% in phase 3 trial")
    assert c["cat"] == "phase3" and c["sev"] == 4 and c["dir"] == 1


def test_breakthrough_conference_preview_is_not_a_readout():
    assert su.classify_breakthrough("Brian Slomovitz to present KEYNOTE-C93 phase 3 results at ESMO 2026") is None
    assert su.classify_breakthrough("Company will present phase 2 data at ASCO next week") is None


def test_extract_tickers_extra_exchanges():
    assert su.extract_tickers("argenx (ENXTBR:ARGX) reports data") == ["ARGX"]


def test_breakthrough_does_not_meet_endpoint_is_negative():
    c = su.classify_breakthrough("Vorolanib phase 3 trial does not meet nAMD endpoint")
    assert c["cat"] == "phase3" and c["sev"] == 3 and c["dir"] == -1


def test_cluster_breakthroughs_merges_outlet_rewordings_via_distinctive_token():
    items = [
        {"title": "Hibsago Receives FDA Approval for Chronic HBV Treatment",
         "cat": "fda", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "A", "link": "https://a", "pub_ts": 200},
        {"title": "FDA Approves Hibsago to Treat Certain Chronic HBV Patients",
         "cat": "fda", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "B", "link": "https://b", "pub_ts": 100},
        {"title": "FDA approves GSK's functional cure for hepatitis B",
         "cat": "fda", "tickers": [], "companies": ["GSK"], "sev": 4, "dir": 1,
         "source": "C", "link": "https://c", "pub_ts": 50},
    ]
    out = su.cluster_breakthroughs(items)
    assert len(out) == 2                     # the two Hibsago write-ups merge
    assert len(out[0]["sources"]) == 2


def test_build_bt_lexicon_skips_generic_first_words():
    lex = su.build_bt_lexicon({"FSLR": "First Solar"})
    assert su.companies_in_text("FDA approves first-in-class drug", lex) == []


def test_cluster_breakthroughs_does_not_merge_shared_condition():
    items = [
        {"title": "CompanyA reports positive phase 3 melanoma results",
         "cat": "phase3", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "A", "link": "https://a", "pub_ts": 300},
        {"title": "CompanyB posts phase 3 melanoma trial results",
         "cat": "phase3", "tickers": [], "companies": [], "sev": 3, "dir": 0,
         "source": "B", "link": "https://b", "pub_ts": 200},
        {"title": "CompanyC melanoma vaccine cuts recurrence in phase 3",
         "cat": "phase3", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "C", "link": "https://c", "pub_ts": 100},
    ]
    assert len(su.cluster_breakthroughs(items)) == 3


# ---- breakthrough layer: layout + FDA fallback (post-deploy fixes)

def test_kindfilter_sits_above_watchlist_alerts_not_above_breakthroughs():
    import generate as g
    from datetime import datetime
    extra = {"overview": None, "cnn": None, "cats": None, "trials": None,
             "secw": None, "radar": [], "acc": None, "bt": None}
    html = g.build_html(None, [], [], None, datetime.now(g.JER), extra)
    i_h3 = html.find("התראות רשימת המעקב והסריקה הכלל-ביוטקית")
    i_kf = html.find('id="kindfilter"')
    i_ba = html.find('id="bioalerts"')
    assert -1 < i_h3 < i_kf < i_ba          # filter row moved next to the cards it filters
    assert html.find("scrollIntoView") > 0  # change scrolls the list into view on mobile


def test_openfda_fallback_parses_approvals(monkeypatch):
    import generate as g
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc).date() - timedelta(days=2)).strftime("%Y%m%d")
    recent2 = (datetime.now(timezone.utc).date() - timedelta(days=5)).strftime("%Y%m%d")
    payload = {"results": [
        {"application_number": "NDA021449",
         "sponsor_name": "ASTRAZENECA AB",
         "products": [{"brand_name": "BREZTRI AEROSPHERE"}],
         "submissions": [
             {"submission_status": "AP", "submission_type": "ORIG",
              "submission_status_date": recent},
             {"submission_status": "AP", "submission_type": "SUPPL",
              "submission_status_date": recent2}]},
        {"application_number": "ANDA214455",
         "sponsor_name": "AJANTA PHARMA LTD",
         "products": [{"brand_name": "TOFACITINIB CITRATE"}],
         "submissions": [{"submission_status": "AP", "submission_type": "ORIG",
                          "submission_status_date": recent}]},
        {"application_number": "NDA999999",       # outside the window: dropped
         "sponsor_name": "X", "products": [{"brand_name": "OLDDRUG"}],
         "submissions": [{"submission_status": "AP", "submission_type": "ORIG",
                          "submission_status_date": "20200101"}]},
    ]}
    monkeypatch.setattr(g, "http_get", lambda *a, **k: __import__("json").dumps(payload))
    items = g._bt_openfda_items()
    assert len(items) == 2
    nda = [i for i in items if "Breztri" in i["title"]][0]
    assert nda["title"] == "FDA approves Breztri Aerosphere (Astrazeneca Ab)"
    assert "ApplNo=021449" in nda["link"]
    anda = [i for i in items if "Tofacitinib" in i["title"]][0]
    assert "generic" in anda["title"]               # ANDA flagged for the severity cap
    assert su.classify_breakthrough(anda["title"])["sev"] == 2
    assert su.classify_breakthrough(nda["title"])["sev"] == 4


def test_fda_items_falls_back_to_openfda(monkeypatch):
    import generate as g
    monkeypatch.setattr(g, "_bt_rss", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
    monkeypatch.setattr(g, "_bt_openfda_items", lambda: [{"title": "FDA approves X"}])
    errs = []
    items = g._bt_fda_items(errs)
    assert items == [{"title": "FDA approves X"}] and errs == []
    monkeypatch.setattr(g, "_bt_openfda_items", lambda: (_ for _ in ()).throw(RuntimeError("down too")))
    assert g._bt_fda_items(errs) == [] and errs == ["FDA"]


def test_cluster_does_not_merge_same_outlet_different_events():
    items = [
        {"title": "FDA Approves Subcutaneous Induction for Skyrizi in Crohn's Disease - Pharmaceutical Executive",
         "cat": "fda", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "Google News", "link": "https://sky", "pub_ts": 300},
        {"title": "FDA Approves Hibsago (Bepirovirsen) for Chronic Hepatitis B - Pharmaceutical Executive",
         "cat": "fda", "tickers": [], "companies": [], "sev": 4, "dir": 1,
         "source": "Google News", "link": "https://hib", "pub_ts": 200},
        {"title": "FDA approves Hibsago (Glaxosmithkline Llc)",
         "cat": "fda", "tickers": [], "companies": ["GSK"], "sev": 4, "dir": 1,
         "source": "FDA · openFDA", "link": "https://fda", "pub_ts": 100},
    ]
    out = su.cluster_breakthroughs(items)
    assert len(out) == 2                            # Skyrizi and Hibsago stay separate
    sky = [c for c in out if "Skyrizi" in c["title"]][0]
    assert sky["companies"] == []                   # no GSK chip on AbbVie's drug
    hib = [c for c in out if "Hibsago" in c["title"]][0]
    assert hib["companies"] == ["GSK"]              # openFDA item joins its own event
    assert any(s["source"] == "FDA · openFDA" for s in hib["sources"])


def test_cluster_bare_fda_approvals_of_different_drugs_do_not_merge():
    items = [
        {"title": "FDA approves DrugAlpha", "cat": "fda", "tickers": [], "companies": [],
         "sev": 4, "dir": 1, "source": "A", "link": "https://a", "pub_ts": 200},
        {"title": "FDA approves DrugBeta", "cat": "fda", "tickers": [], "companies": [],
         "sev": 4, "dir": 1, "source": "B", "link": "https://b", "pub_ts": 100},
    ]
    assert len(su.cluster_breakthroughs(items)) == 2


# ------------------------------------------------------- audit 2026-10-24 regressions

def test_disc_medicine_alias_is_iron():
    assert su.BT_COMPANY_ALIASES["disc medicine"] == "IRON"
    assert su.build_bt_lexicon({}).get("disc medicine") == "IRON"


def test_her2_subtype_is_not_a_positive_readout():
    # "HER2-positive" names the cancer subtype, not the trial result
    c = su.classify_breakthrough("BioX reports phase 3 data in HER2-positive gastroesophageal cancer")
    assert c is not None and c["dir"] != 1 and c["sev"] <= 3
    assert su.breakthrough_direction("Phase 3 data in HER2-positive breast cancer") == 0


def test_fresh_positive_readout_outranks_historical_crl():
    c = su.classify_breakthrough("Disc Medicine spins past FDA rejection with overwhelmingly positive phase 3 data")
    assert c["cat"] == "phase3" and c["dir"] == 1 and c["sev"] == 4


def test_actual_crl_headline_still_crl():
    for t in ("FDA rejects Disc Medicine drug, issues complete response letter",
              "Company receives CRL from FDA for its lead candidate"):
        c = su.classify_breakthrough(t)
        assert c == {"cat": "crl", "sev": 3, "dir": -1}, t


def _bt(title, cat="phase3", sev=3, dir=1, tickers=None, companies=None, nct=None, ts=0):
    return {"title": title, "link": title[:10], "pub_ts": ts, "source": "s",
            "cat": cat, "sev": sev, "dir": dir, "date": "2026-10-24",
            "tickers": tickers or [], "companies": companies or [], "nct": nct}


def test_cluster_conflicting_nct_blocks_merge():
    items = [_bt("Pfizer announces positive phase 3 results for drugA in lung cancer",
                 sev=4, tickers=["PFE"], companies=["PFE"], nct="NCT111", ts=1000),
             _bt("Pfizer phase 3 trial of drugB in heart failure fails primary endpoint",
                 sev=3, dir=-1, tickers=["PFE"], companies=["PFE"], nct="NCT222", ts=2000)]
    cl = su.cluster_breakthroughs(items)
    assert len(cl) == 2  # the failure keeps its own representation


def test_cluster_same_company_needs_token_evidence():
    # same company + same category but nothing else shared: no merge
    items = [_bt("Pfizer announces positive phase 3 results for drugA in lung cancer",
                 sev=4, tickers=["PFE"], companies=["PFE"], ts=1000),
             _bt("Pfizer phase 3 win for drugB in heart failure",
                 sev=4, tickers=["PFE"], companies=["PFE"], ts=2000)]
    assert len(su.cluster_breakthroughs(items)) == 2


def test_cluster_same_event_shared_drug_still_merges():
    items = [_bt("FDA approves Pfizer xarelto for atrial fibrillation",
                 sev=4, tickers=["PFE"], companies=["PFE"], ts=1000),
             _bt("Pfizer wins FDA nod as xarelto cleared for atrial fibrillation",
                 sev=4, tickers=["PFE"], companies=["PFE"], ts=2000)]
    assert len(su.cluster_breakthroughs(items)) == 1


def test_cluster_cross_company_medical_term_no_merge():
    items = [_bt("Acme reports phase 3 results in pulmonary hypertension",
                 tickers=["ACME"], companies=["ACME"], ts=1000),
             _bt("Zebra therapeutics phase 3 data in pulmonary fibrosis",
                 dir=-1, tickers=["ZBRA"], companies=["ZBRA"], ts=2000)]
    assert len(su.cluster_breakthroughs(items)) == 2


def test_cluster_cross_outlet_same_event_still_merges():
    items = [_bt("Hibsago wins FDA approval for chronic hepatitis B",
                 cat="fda", sev=4, companies=["GSK"], ts=1000),
             _bt("GSK's Hibsago hepatitis B vaccine approved by FDA",
                 cat="fda", sev=4, tickers=["GSK"], ts=2000)]
    cl = su.cluster_breakthroughs(items)
    assert len(cl) == 1 and len(cl[0]["sources"]) == 2


def test_risk_pct_is_relative_to_entry():
    import breakout_lib as b
    assert b.risk_pct(100, 90) == 10.0
    assert abs(b.risk_pct(110, 99) - 10.0) < 1e-9


def test_chart_closes_volumes_keep_pairs():
    import generate as g
    orig = g.http_get_json
    g.http_get_json = lambda *a, **k: {"chart": {"result": [
        {"indicators": {"quote": [{"close": [10.0, 11.0, None, 12.0],
                                   "volume": [100, 200, 999, 400]}]}}]}}
    try:
        closes, vols, last_d = g.chart_closes_volumes("TEST")
        assert last_d is None or len(last_d) == 10
    finally:
        g.http_get_json = orig
    assert closes == [10.0, 11.0, 12.0]
    assert vols == [100.0, 200.0, 400.0]  # the null bar's volume dropped with it


def test_expected_to_date_window_end():
    import generate as g
    import datetime as _dt
    d, approx, wend = g._expected_to_date("Q4 2026")
    assert d == _dt.date(2026, 10, 1) and approx and wend == _dt.date(2026, 12, 31)
    d, approx, wend = g._expected_to_date("2H 2026")
    assert d == _dt.date(2026, 7, 1) and approx and wend == _dt.date(2026, 12, 31)
    d, approx, wend = g._expected_to_date("1H 2027")
    assert d == _dt.date(2027, 1, 1) and approx and wend == _dt.date(2027, 6, 30)
    d, approx, wend = g._expected_to_date("2026-11-05")
    assert not approx and wend == d


def test_catalyst_today_first_and_approx_window(tmp_path, monkeypatch):
    import generate as g
    import datetime as _dt
    from zoneinfo import ZoneInfo
    today = _dt.datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    q = (today.month - 1) // 3 + 1
    cfg = {"asof": "x", "events": [
        {"ticker": "FAR", "event": "far future", "expected": (today + _dt.timedelta(days=158)).isoformat()},
        {"ticker": "TODAY", "event": "happens today", "expected": today.isoformat()},
        {"ticker": "QNOW", "event": "this quarter", "expected": f"Q{q} {today.year}"},
        {"ticker": "OLD", "event": "long past", "expected": "Q1 2020"},
    ]}
    (tmp_path / "biotech_catalysts.json").write_text(
        __import__("json").dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(g, "BASE_DIR", str(tmp_path))
    res = g.catalyst_section()
    tick_order = [c["ticker"] for c in res["upcoming"]]
    assert tick_order[0] == "TODAY"                     # day 0 is not 9999
    assert "QNOW" in tick_order                         # open window is not "past"
    assert [c["ticker"] for c in res["passed"]] == ["OLD"]


def test_breadth_coverage_floor(monkeypatch):
    import market_lib as m
    tickers = [f"T{i:03d}" for i in range(500)]
    monkeypatch.setattr(m, "spark_closes_batch",
                        lambda batch: {batch[0]: [1.0] * 260} if batch else {})
    out = m.compute_breadth_yahoo(tickers)
    assert out["s5fi"] is None and out["s5th"] is None   # 1/500 is not 100%
    # 100-day series: enough for MA50, never for MA200
    monkeypatch.setattr(m, "spark_closes_batch",
                        lambda batch: {t: [1.0] * 100 for t in batch})
    out = m.compute_breadth_yahoo(tickers)
    assert out["s5fi"] == 100.0 and out["s5th"] is None  # MA200 coverage tracked separately


def test_pullback_rejects_stale_series(monkeypatch):
    import pandas as pd
    import generate as g
    monkeypatch.setattr(su, "is_pullback_uptrend", lambda closes, r: {"dd": 5.0})
    fresh = pd.DataFrame({"Close": [100 + i * 0.1 for i in range(60)],
                          "Volume": [1e6] * 60},
                         index=pd.date_range("2026-08-25", periods=60, freq="B"))
    stale = pd.DataFrame({"Close": [100 + i * 0.1 for i in range(60)],
                          "Volume": [1e6] * 60},
                         index=pd.date_range("2021-01-04", periods=60, freq="B"))
    out = g.pullback_section({"FRESH": fresh, "STALE": stale}, expected="2026-10-23")
    assert [x["ticker"] for x in out] == ["FRESH"]


def test_recovery_path_first_touch():
    import pandas as pd
    import generate as g
    idx = pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"])
    df = pd.DataFrame({"High": [100, 113, 100, 100], "Low": [99, 100, 89, 99],
                       "Close": [100, 110, 95, 105]}, index=idx)
    sig = {"ticker": "X", "date": "20/10", "target": 110.0, "stop": 95.0}
    tgt, stp = g._recovery_path({"X": df}, sig)
    assert tgt == idx[1] and stp == idx[2]          # target hit a day before the stop
    df2 = pd.DataFrame({"High": [100, 100, 115, 100], "Low": [99, 89, 100, 99],
                        "Close": [100, 90, 110, 105]}, index=idx)
    tgt, stp = g._recovery_path({"X": df2}, sig)
    assert stp == idx[1] and tgt == idx[2]          # stop hit first
    df3 = pd.DataFrame({"High": [100, 120, 100, 100], "Low": [99, 80, 99, 99],
                        "Close": [100, 100, 100, 100]}, index=idx)
    tgt, stp = g._recovery_path({"X": df3}, sig)
    assert tgt == idx[1] and stp == idx[1]          # same bar -> order unknown


def test_cluster_opposite_directions_never_merge():
    # same company, same distinctive drug token, no NCTs - but approval vs
    # rejection are different events; a failure must not vanish into a success
    items = [
        {"title": "FDA approves Zynthexa for rare anemia", "cat": "fda", "sev": 4,
         "dir": 1, "tickers": ["ARGX"], "companies": [], "nct": None,
         "link": "l1", "pub_ts": 200.0, "date": "24.10.2026, 10:00", "source": "A"},
        {"title": "FDA rejects Zynthexa for rare anemia", "cat": "fda", "sev": 4,
         "dir": -1, "tickers": ["ARGX"], "companies": [], "nct": None,
         "link": "l2", "pub_ts": 100.0, "date": "23.10.2026, 10:00", "source": "B"},
    ]
    cl = su.cluster_breakthroughs(items)
    assert len(cl) == 2
    dirs = sorted(c["dir"] for c in cl)
    assert dirs == [-1, 1]
    assert any("rejects" in c["title"] for c in cl)


def test_universe_json_persistence_and_per_index_floor(tmp_path, monkeypatch):
    import breakout_lib as bl
    monkeypatch.setattr(bl, "UNIVERSE", tmp_path / "u.csv")
    monkeypatch.setattr(bl, "UNIVERSE_JSON", tmp_path / "u.json")
    monkeypatch.setitem(bl.S, "universe", ["sp500", "sp400", "nasdaq100"])
    monkeypatch.setitem(bl.S, "universe_refresh_days", 7)
    monkeypatch.setitem(bl.S, "extra_tickers", [])
    fake = {"sp500": [f"SP{i}" for i in range(500)],
            "sp400": [f"MD{i}" for i in range(400)],
            "nasdaq100": [f"ND{i}" for i in range(100)]}
    monkeypatch.setattr(bl, "_fetch_index", lambda url: fake[[k for k, v in bl.SOURCES.items() if v == url][0]])
    out = bl.load_universe()
    assert len(out) == 1000
    import json as _j
    meta = _j.loads((tmp_path / "u.json").read_text(encoding="utf-8"))
    assert len(meta["indexes"]["sp500"]["tickers"]) == 500
    assert meta["indexes"]["sp500"]["fetched_at"]
    sp500_stamp = meta["indexes"]["sp500"]["fetched_at"]
    # fresh checkout WITH the seeded file + one index down + one too short:
    # failed index keeps its cached bucket AND its old fetched_at (not advanced)
    def broken(url):
        if "S%26P_500" in url:
            raise RuntimeError("down")
        if "Nasdaq-100" in url:
            return ["ONLY3"]  # below the per-index minimum
        return [f"MD{i}" for i in range(400)]
    monkeypatch.setattr(bl, "_fetch_index", broken)
    monkeypatch.setitem(bl.S, "universe_refresh_days", 0)  # force refresh attempt
    out2 = bl.load_universe()
    got = set(out2)
    assert {f"SP{i}" for i in range(500)} <= got   # cached sp500 kept
    assert {f"MD{i}" for i in range(400)} <= got   # sp400 refetched
    assert {f"ND{i}" for i in range(100)} <= got  # short ndx rejected, cache kept
    meta2 = _j.loads((tmp_path / "u.json").read_text(encoding="utf-8"))
    assert meta2["indexes"]["sp500"]["fetched_at"] == sp500_stamp  # NOT advanced
    meta_ndx = meta2["indexes"]["nasdaq100"]
    assert meta_ndx["tickers"][0] == "ND0"  # cached bucket, not the fallback



def test_universe_fresh_checkout_without_cache_failed_index(tmp_path, monkeypatch):
    # second simulation: fresh runner, no cache at all, non-fallback index down
    import breakout_lib as bl
    monkeypatch.setattr(bl, "UNIVERSE", tmp_path / "none.csv")
    monkeypatch.setattr(bl, "UNIVERSE_JSON", tmp_path / "none.json")
    monkeypatch.setitem(bl.S, "universe", ["sp500", "sp400", "nasdaq100"])
    monkeypatch.setitem(bl.S, "universe_refresh_days", 7)
    monkeypatch.setitem(bl.S, "extra_tickers", [])
    def broken(url):
        if "S%26P_500" in url:
            raise RuntimeError("down")
        if "Nasdaq-100" in url:
            raise RuntimeError("down")
        return [f"MD{i}" for i in range(400)]
    monkeypatch.setattr(bl, "_fetch_index", broken)
    out = bl.load_universe()
    got = set(out)
    assert {f"MD{i}" for i in range(400)} <= got
    assert "ASML" in got and "ARM" in got and "MELI" in got  # ndx fallback covers
    assert not [t for t in got if t.startswith("SP") and t[2:].isdigit()]  # sp500 honestly absent


def test_header_honest_about_cadence():
    import generate as g
    from datetime import datetime as _dt
    html = g.build_html(None, [], [], None, _dt.now(g.JER), {})
    assert "מתעדכן אוטומטית כל חצי שעה" in html
    assert "עלולה להתעכב שעות" not in html
    assert 'onclick="location.reload()"' not in html
    assert html.count('class="refresh-btn"') == 5
    assert "id='rfStatus'" in html


# ---- CNN Fear & Greed source
def _cnn_payload():
    return {"fear_and_greed": {"score": 20.98, "rating": "extreme fear",
            "timestamp": "2026-10-23T23:59:44+00:00", "previous_close": 19.8,
            "previous_1_week": 22.1, "previous_1_month": 32.5, "previous_1_year": 27.6}}


def test_cnn_live_fetch_uses_current_endpoint_with_referer(monkeypatch, tmp_path):
    import generate as g
    calls = {}
    monkeypatch.setattr(g, "http_get_json",
        lambda url, **kw: (calls.update(url=url, headers=kw.get("extra_headers")) or _cnn_payload()))
    monkeypatch.setattr(g, "CNN_SNAPSHOT", str(tmp_path / "snap.json"))
    cn = g.cnn_section()
    assert calls["url"] == "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    assert calls["headers"] == {"Referer": "https://www.cnn.com/markets/fear-and-greed"}
    assert cn["live"] is True and cn["score"] == 21


def test_cnn_live_value_carries_cnn_timestamp_not_fetch_time(monkeypatch, tmp_path):
    import generate as g
    monkeypatch.setattr(g, "http_get_json", lambda *a, **k: _cnn_payload())
    monkeypatch.setattr(g, "CNN_SNAPSHOT", str(tmp_path / "snap.json"))
    cn = g.cnn_section()
    assert cn["timestamp"] == "2026-10-23T23:59:44+00:00"
    assert cn["age_hours"] > 0


def test_cnn_live_display_labels_asof_not_today(monkeypatch, tmp_path):
    import generate as g
    from datetime import datetime as _dt
    monkeypatch.setattr(g, "http_get_json", lambda *a, **k: _cnn_payload())
    monkeypatch.setattr(g, "CNN_SNAPSHOT", str(tmp_path / "snap.json"))
    cn = g.cnn_section()
    html = g.build_html(None, [], [], None, _dt.now(g.JER), {"cnn": cn})
    assert "מ-CNN · נכון ל-" in html
    assert "עודכן חי מ-CNN בריצה זו" not in html


# ---- whole-sector biotech universe + readout classification (SMMT/AGEN misses)
def test_breakthrough_strong_readout_without_phase_word():
    # the HARMONi-GI1 biliary-cancer OS win headline never says "phase 3"
    c = su.classify_breakthrough(
        "Ivonescimab Plus Chemotherapy Demonstrates Overall Survival Superiority "
        "Compared to Durvalumab Plus Chemotherapy in First-Line Biliary Tract Cancer")
    assert c == {"cat": "readout", "sev": 3, "dir": 1}
    c = su.classify_breakthrough("Summit Therapeutics reports biliary-cancer survival win")
    assert c["cat"] == "readout" and c["dir"] == 1 and c["sev"] == 3
    # phase unknown from the headline -> never the proven phase-3/approval tier
    assert su.classify_breakthrough(
        "Ivonescimab Demonstrates Overall Survival Superiority in Biliary Tract Cancer")["sev"] < 4


def test_breakthrough_strong_readout_needs_biotech_context_and_not_ops():
    assert su.classify_breakthrough("Team demonstrates superiority in league finals") is None
    assert su.classify_breakthrough("Company to present overall survival data at ESMO") is None
    assert su.classify_breakthrough("Small biotech reports positive phase 1 safety data") is None


def test_breakthrough_agen_neoasis_style_title():
    c = su.classify_breakthrough(
        "Agenus Reports 72% Pathologic Complete Response Rate with BOT+BAL "
        "in Phase 2 NEOASIS Trial at ESMO 2026")
    assert c is not None and c["cat"] == "phase2" and c["sev"] >= 1


def test_biotech_universe_from_screener_rows():
    rows = [
        {"symbol": "AGEN", "name": "Agenus Inc. Common Stock", "sector": "Health Care",
         "industry": "Biotechnology: Pharmaceutical Preparations", "marketCap": "150000000"},
        {"symbol": "A", "name": "Agilent Technologies Inc. Common Stock",
         "sector": "Industrials",
         "industry": "Biotechnology: Laboratory Analytical Instruments",
         "marketCap": "49024608422"},
        {"symbol": "XXY", "name": "Some Bio Warrant", "sector": "Health Care",
         "industry": "Biotechnology: Pharmaceutical Preparations", "marketCap": "100"},
        {"symbol": "ZZZ", "name": "No Cap Bio", "sector": "Health Care",
         "industry": "Biotechnology: Pharmaceutical Preparations", "marketCap": "0"},
        {"symbol": "BAD^", "name": "Odd Symbol Bio", "sector": "Health Care",
         "industry": "Biotechnology: Pharmaceutical Preparations", "marketCap": "100"},
    ]
    assert su.biotech_universe_from_screener(rows) == {"AGEN": "Agenus Inc."}


def test_universe_aliases_tag_real_names_safely():
    lex = su.build_bt_lexicon({})
    su.add_bt_name_aliases(lex, {"AGEN": "Agenus Inc.",
                                 "SMMT": "Summit Therapeutics Inc.",
                                 "PPBT": "Purple Biotech Ltd."})
    assert su.companies_in_text("Agenus reports phase 2 data", lex) == ["AGEN"]
    assert su.companies_in_text("Summit Therapeutics reports survival win", lex) == ["SMMT"]
    assert su.companies_in_text("Leaders meet at climate summit", lex) == []
    assert su.companies_in_text("The purple dinosaur sings", lex) == []



def test_breakthrough_pcr_headline_is_positive():
    c = su.classify_breakthrough(
        "Agenus Reports 72% Pathologic Complete Response Rate with BOT+BAL "
        "in Phase 2 NEOASIS Trial at ESMO 2026")
    assert c["cat"] == "phase2" and c["sev"] == 2 and c["dir"] == 1



def test_biotech_universe_loader_survives_older_scan_utils(tmp_path, monkeypatch):
    # staggered upload: new generate.py may briefly run with the previous
    # scan_utils.py - it must fall back to the committed snapshot, not crash
    import generate as g
    snap = tmp_path / "biotech_universe.json"
    import json as _json
    from datetime import datetime, timezone
    snap.write_text(_json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickers": {"SMMT": "Summit Therapeutics Inc.", "AGEN": "Agenus Inc."}}))
    monkeypatch.setattr(g, "BIO_UNIVERSE", str(snap))
    monkeypatch.setattr(g, "_BIO_UNI", None)
    monkeypatch.delattr(su, "biotech_universe_from_screener")
    u = g.load_biotech_universe()
    assert u["SMMT"].startswith("Summit") and u["AGEN"].startswith("Agenus")
    monkeypatch.undo()
    monkeypatch.setattr(g, "_BIO_UNI", None)


# ---- pre-market futures section
def _chart_payload(price, closes, ts=1792991133, venue="CME"):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price,
                                           "regularMarketTime": ts,
                                           "fullExchangeName": venue},
                                  "indicators": {"quote": [{"close": closes}]}}]}}


def test_fut_row_change_vs_previous_close():
    import generate as g
    row = g._fut_row_from_chart("ES=F", "S&P 500",
                                _chart_payload(7683.25, [7800.25, 7704.5, 7659.75, 7683.25]))
    # change is vs the previous close in the series (7659.75), not the range start
    assert abs(row["chg"] - (7683.25 / 7659.75 - 1) * 100) < 1e-9
    assert row["price"] == 7683.25 and row["ts"] == 1792991133 and row["venue"] == "CME"


def test_fut_row_falls_back_to_last_close_and_skips_none():
    import generate as g
    row = g._fut_row_from_chart("^VIX", "VIX", _chart_payload(None, [19.03, None, 19.45]))
    assert row["price"] == 19.45 and abs(row["chg"] - (19.45 / 19.03 - 1) * 100) < 1e-9


def test_fut_row_rejects_missing_history():
    import generate as g
    for bad in [_chart_payload(None, []), _chart_payload(10.0, [10.0]),
                _chart_payload(None, [None, None]), {"chart": {"result": []}}]:
        try:
            g._fut_row_from_chart("X", "X", bad)
            assert False, f"should have raised: {bad}"
        except Exception:
            pass


def test_futures_session_context_bands():
    import generate as g
    # Mon 2026-10-26, ET is UTC-4 (DST ends 2026-11-01)
    assert g._futures_session_context(dt.datetime(2026, 10, 26, 8, 0, tzinfo=UTC))[0] == "\u05de\u05e1\u05d7\u05e8 \u05de\u05d5\u05e7\u05d3\u05dd \u00b7 \u05dc\u05e4\u05e0\u05d9 \u05d4\u05e4\u05ea\u05d9\u05d7\u05d4"   # 04:00 ET
    assert g._futures_session_context(dt.datetime(2026, 10, 26, 14, 0, tzinfo=UTC))[0] == "\u05d1\u05de\u05d4\u05dc\u05da \u05d4\u05de\u05e1\u05d7\u05e8 \u05d4\u05e8\u05d2\u05d9\u05dc"  # 10:00 ET
    assert g._futures_session_context(dt.datetime(2026, 10, 26, 21, 0, tzinfo=UTC))[0] == "\u05d0\u05d7\u05e8\u05d9 \u05d4\u05e1\u05d2\u05d9\u05e8\u05d4"   # 17:00 ET
    assert g._futures_session_context(dt.datetime(2026, 10, 26, 5, 0, tzinfo=UTC))[0] == "\u05de\u05e1\u05d7\u05e8 \u05dc\u05d9\u05dc\u05d9"  # 01:00 ET
    assert g._futures_session_context(dt.datetime(2026, 10, 25, 15, 0, tzinfo=UTC))[0] == "\u05e1\u05d5\u05e3 \u05e9\u05d1\u05d5\u05e2"  # Sunday


def test_premarket_read_thresholds_and_minimum():
    import generate as g
    S = "\u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05e4\u05ea\u05d9\u05d7\u05d4"
    assert g._premarket_read([0.29, 0.29, 0.29, 0.29], S) == "\u05ea\u05de\u05d5\u05e0\u05d4 \u05de\u05e2\u05d5\u05e8\u05d1\u05ea \u05d0\u05d5 \u05e9\u05e7\u05d8\u05d4 " + S
    assert g._premarket_read([0.5, 0.4, -0.1, 0.4], S) == "\u05e0\u05d8\u05d9\u05d9\u05d4 \u05d7\u05d9\u05d5\u05d1\u05d9\u05ea " + S
    assert g._premarket_read([-0.4, -0.5, 0.1, -0.4], S) == "\u05e0\u05d8\u05d9\u05d9\u05d4 \u05e9\u05dc\u05d9\u05dc\u05d9\u05ea " + S
    assert g._premarket_read([1.5, -1.2], S) is None      # fewer than 3 index quotes
    assert g._premarket_read([], S) is None


def _pm_rows(latest=1792991133):
    return [{"sym": "ES=F", "name": "S&P 500", "price": 7683.25, "chg": 0.31,
             "ts": latest, "venue": "CME", "stale": False},
            {"sym": "NQ=F", "name": "NASDAQ 100", "price": 25437.0, "chg": -0.5,
             "ts": latest - 13 * 3600, "venue": "CME", "stale": True},
            {"sym": "^VIX", "name": "VIX", "price": 19.45, "chg": 2.21,
             "ts": latest - 13 * 3600, "venue": "Cboe Indices", "stale": True}]


def test_build_html_renders_premarket_section():
    import generate as g
    pm = {"rows": _pm_rows(), "latest": 1792991133, "session": "X",
          "venues": ["CME", "Cboe Indices"], "read": g._premarket_read([0.5, 0.4, 0.4], "\u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05e4\u05ea\u05d9\u05d7\u05d4")}
    page = g.build_html(None, [], [], None, dt.datetime(2026, 10, 26, 7, 24), {"pm": pm})
    assert "\u05d7\u05d5\u05d6\u05d9\u05dd \u05e2\u05ea\u05d9\u05d3\u05d9\u05d9\u05dd \u00b7 X" in page
    assert "7,683.25" in page and "+0.31%" in page
    assert "\u05e0\u05d8\u05d9\u05d9\u05d4 \u05d7\u05d9\u05d5\u05d1\u05d9\u05ea \u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05e4\u05ea\u05d9\u05d7\u05d4" in page
    assert "\u05e6\u05d9\u05d8\u05d5\u05d8" in page                       # per-card quote time present
    assert "finance.yahoo.com/quote/ES%3DF" in page                       # usable source link


def test_build_html_vix_not_in_futures():
    import generate as g
    pm = {"rows": _pm_rows(), "latest": 1792991133, "session": "X", "venues": [], "read": None}
    page = g.build_html(None, [], [], None, dt.datetime(2026, 10, 26, 7, 24), {"pm": pm})
    # VIX card removed from the futures area per user request 2026-10-28 ("זה כבר מופיע")
    assert "+2.21%" not in page
    assert "\u05e2\u05dc\u05d9\u05d9\u05d4 \u05d1-VIX = \u05e2\u05dc\u05d9\u05d9\u05d4 \u05d1\u05d7\u05d5\u05e1\u05e8 \u05d5\u05d3\u05d0\u05d5\u05ea" not in page


def test_build_html_stale_quote_shows_date():
    import generate as g
    pm = {"rows": _pm_rows(), "latest": 1792991133, "session": "X", "venues": [], "read": None}
    page = g.build_html(None, [], [], None, dt.datetime(2026, 10, 26, 7, 24), {"pm": pm})
    # the fresh row shows a clock time, the 13h-old VIX row shows a date
    fresh = g.datetime.fromtimestamp(1792991133, g.timezone.utc).astimezone(g.JER)
    assert f"\u05e6\u05d9\u05d8\u05d5\u05d8 {fresh.hour}:{fresh.minute:02d}" in page
    old = g.datetime.fromtimestamp(1792991133 - 13 * 3600, g.timezone.utc).astimezone(g.JER)
    assert f"\u05e6\u05d9\u05d8\u05d5\u05d8 {old.day}.{old.month}," in page


def test_build_html_premarket_failure_note():
    import generate as g
    page = g.build_html(None, [], [], None, dt.datetime(2026, 10, 26, 7, 0), {"pm": None})
    assert "\u05e6\u05d9\u05d8\u05d5\u05d8\u05d9 \u05d4\u05d7\u05d5\u05d6\u05d9\u05dd \u05dc\u05d0 \u05d4\u05d9\u05d5 \u05d6\u05de\u05d9\u05e0\u05d9\u05dd" in page


def test_premarket_section_marks_stale_and_excludes_from_read(monkeypatch):
    import generate as g
    import urllib.parse
    latest = 1792991133
    by_sym = {"ES=F": _chart_payload(100.5, [100.0, 100.5], ts=latest),
              "NQ=F": _chart_payload(100.5, [100.0, 100.5], ts=latest),
              "YM=F": _chart_payload(100.5, [100.0, 100.5], ts=latest),
              "RTY=F": _chart_payload(50.0, [100.0, 50.0], ts=latest - 20 * 3600),  # stale, -50%
              "^VIX": _chart_payload(19.0, [18.0, 19.0], ts=latest - 20 * 3600)}
    monkeypatch.setattr(g, "http_get_json",
                        lambda url, **k: by_sym[urllib.parse.unquote(url.split("/chart/")[1].split("?")[0])])
    pm = g.premarket_section(now=dt.datetime(2026, 10, 26, 5, 0, tzinfo=UTC))
    by = {r["sym"]: r for r in pm["rows"]}
    assert by["RTY=F"]["stale"] is True and by["ES=F"]["stale"] is False
    # without the stale RTY (-50%) the read is positive; a stale crash must not panic it
    assert pm["read"] == "\u05e0\u05d8\u05d9\u05d9\u05d4 \u05d7\u05d9\u05d5\u05d1\u05d9\u05ea \u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05de\u05e1\u05d7\u05e8 \u05d4\u05d1\u05d0"
    assert pm["venues"] == ["CME"]


# ---- breakthroughs cache retention (SMMT disappeared 40 min after showing, 2026-10-25)
def _bt_cluster(title, pub_ts, companies=None, link="http://a", cat="readout", sev=3, d=1):
    return {"title": title, "link": link, "pub_ts": pub_ts, "date": "25.10.2026",
            "cat": cat, "sev": sev, "dir": d, "tickers": [], "companies": companies or [],
            "sources": [{"source": "Google News", "link": link}], "ncts": []}


def test_bt_cache_items_for_merge_window_and_undated():
    import generate as g
    cutoff = 1000
    items = [_bt_cluster("old", 999), _bt_cluster("kept", 1001), _bt_cluster("undated", None)]
    out = g._bt_cache_items_for_merge(items, cutoff)
    assert [i["title"] for i in out] == ["kept"]
    assert g._bt_cache_items_for_merge(None, cutoff) == []


def test_bt_cache_merge_keeps_story_that_fell_out_of_fresh_results():
    import time
    import generate as g
    now = time.time()
    cached = [_bt_cluster("Summit Therapeutics reports biliary-cancer survival win",
                          now - 3600, companies=["SMMT"], link="http://smmt")]
    fresh_items = [{"title": "Acme Bio wins FDA nod for new antibiotic",
                    "link": "http://acme", "pub_ts": now - 60, "date": "26.10.2026",
                    "cat": "approval", "sev": 3, "dir": 1, "tickers": ["ACME"],
                    "companies": [], "source": "Google News", "nct": None}]
    cutoff = now - g.BT_LOOKBACK_DAYS * 86400
    merged = su.cluster_breakthroughs(fresh_items + g._bt_cache_items_for_merge(cached, cutoff))
    titles = [c["title"] for c in merged]
    assert any("Summit" in t for t in titles), "cached in-window story must survive"
    assert any("Acme" in t for t in titles)


def test_bt_cache_merge_does_not_duplicate_same_event():
    import time
    import generate as g
    now = time.time()
    cached = [_bt_cluster("Summit Therapeutics reports biliary-cancer survival win",
                          now - 3600, companies=["SMMT"], link="http://smmt")]
    fresh_items = [{"title": "Summit biliary-cancer survival win impresses analysts",
                    "link": "http://smmt2", "pub_ts": now - 60, "date": "26.10.2026",
                    "cat": "readout", "sev": 2, "dir": 1, "tickers": [],
                    "companies": ["SMMT"], "source": "Google News", "nct": None}]
    cutoff = now - g.BT_LOOKBACK_DAYS * 86400
    merged = su.cluster_breakthroughs(fresh_items + g._bt_cache_items_for_merge(cached, cutoff))
    smmt = [c for c in merged if "SMMT" in c["companies"]]
    assert len(smmt) == 1, "same event must stay one cluster"
    assert len(smmt[0]["sources"]) == 2


def test_bt_identical_title_merges_regardless_of_batch_size():
    # accumulation bug: a persisted company-less story exploded to per-source
    # items must stay ONE cluster, not split because tokens lost distinctiveness
    import time
    now = time.time()
    items = [{"title": "BioCo wins FDA nod for new antibiotic", "link": f"http://s{i}",
              "pub_ts": now - i * 60, "date": "25.10.2026", "cat": "approval",
              "sev": 3, "dir": 1, "tickers": [], "companies": [], "source": f"S{i}",
              "nct": None} for i in range(6)]
    merged = su.cluster_breakthroughs(items)
    assert len(merged) == 1 and len(merged[0]["sources"]) == 3   # capped at 3


def test_bt_cache_roundtrip_keeps_companyless_cluster_single():
    import time
    import generate as g
    now = time.time()
    cached = [_bt_cluster("BioCo wins FDA nod for new antibiotic", now - 3600,
                          companies=[], link="http://s0", cat="approval")]
    cached[0]["sources"] = [{"source": f"S{i}", "link": f"http://s{i}"} for i in range(3)]
    cutoff = now - g.BT_LOOKBACK_DAYS * 86400
    merged = su.cluster_breakthroughs(g._bt_cache_items_for_merge(cached, cutoff))
    assert len(merged) == 1 and len(merged[0]["sources"]) == 3


def test_premarket_read_qualified_when_freshest_quote_is_old(monkeypatch):
    import generate as g
    import urllib.parse
    now = dt.datetime(2026, 10, 25, 12, 0, tzinfo=UTC)   # Saturday, all quotes from Friday
    old_ts = now.timestamp() - 30 * 3600
    by_sym = {s: _chart_payload(100.5, [100.0, 100.5], ts=old_ts)
              for s in ["ES=F", "NQ=F", "YM=F", "RTY=F", "^VIX"]}
    monkeypatch.setattr(g, "http_get_json",
                        lambda url, **k: by_sym[urllib.parse.unquote(url.split("/chart/")[1].split("?")[0])])
    pm = g.premarket_section(now=now)
    assert pm["read"].startswith("\u05dc\u05e4\u05d9 \u05e6\u05d9\u05d8\u05d5\u05d8\u05d9\u05dd \u05de-"), pm["read"]
    assert "\u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05de\u05e1\u05d7\u05e8 \u05d4\u05d1\u05d0" in pm["read"]


def test_premarket_read_not_qualified_when_quotes_fresh(monkeypatch):
    import generate as g
    import urllib.parse
    now = dt.datetime(2026, 10, 26, 5, 0, tzinfo=UTC)
    fresh_ts = now.timestamp() - 600
    by_sym = {s: _chart_payload(100.5, [100.0, 100.5], ts=fresh_ts)
              for s in ["ES=F", "NQ=F", "YM=F", "RTY=F", "^VIX"]}
    monkeypatch.setattr(g, "http_get_json",
                        lambda url, **k: by_sym[urllib.parse.unquote(url.split("/chart/")[1].split("?")[0])])
    pm = g.premarket_section(now=now)
    assert pm["read"] == "\u05e0\u05d8\u05d9\u05d9\u05d4 \u05d7\u05d9\u05d5\u05d1\u05d9\u05ea \u05dc\u05e7\u05e8\u05d0\u05ea \u05d4\u05de\u05e1\u05d7\u05e8 \u05d4\u05d1\u05d0"
