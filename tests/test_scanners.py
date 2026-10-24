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
