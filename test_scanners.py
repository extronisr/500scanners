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
    c = su.classify_breakthrough("MDMA-assisted therapy could get FDA approval as soon as January")
    assert c["cat"] == "fda" and c["sev"] == 2
    c = su.classify_breakthrough("FDA Approves First Generic Hormone Therapy Patch")
    assert c["cat"] == "fda" and c["sev"] == 2


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
    i_h3 = html.find("התראות רשימת המעקב")
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
