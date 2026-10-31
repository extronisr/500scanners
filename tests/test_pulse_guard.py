"""Production guard: a schema-valid but stale / badly-sourced review candidate
must be rejected so the approved live seed is never overwritten by it.
Regression cover for 2026-10-31: a weekend run cited 3-day-old live blogs and
listed YouTube/Facebook as sources, and validate_simple passed it."""
import datetime as dt

import market_pulse as mp


class _FrozenDT(dt.datetime):
    """2026-10-31 20:00 Jerusalem - the moment the bad candidate passed."""
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 31, 20, 0, tzinfo=tz)


def _fixture_results():
    return [
        {"url": "https://www.bloomberg.com/news/articles/2026-10-29/stock-market-today-dow-s-p-live-updates",
         "title": "Stock market today live updates", "published_date": "2026-10-29", "content": "x"},
        {"url": "https://www.reuters.com/markets/us/futures-edge-up-2026-10-30/",
         "title": "Nasdaq futures rise", "published_date": "2026-10-30", "content": "x"},
        {"url": "https://www.youtube.com/watch?v=abc123",
         "title": "Market recap video", "published_date": "2026-10-31", "content": "x"},
        {"url": "https://www.facebook.com/somepage/videos/123",
         "title": "Happy monday recap", "published_date": "2026-10-31", "content": "x"},
        {"url": "https://www.aljazeera.com/news/liveblog/2026/10/31/iran-war-live",
         "title": "Iran war live blog", "published_date": "2026-10-31", "content": "x"},
    ]


def _news(monkeypatch):
    calls = {"n": 0}

    def fake_search(api_key, q, n):
        calls["n"] += 1
        return _fixture_results() if calls["n"] == 1 else []

    monkeypatch.setattr(mp, "tavily_search", fake_search)
    monkeypatch.setattr(mp, "tavily_extract", lambda api_key, urls: {})
    monkeypatch.setattr(mp, "datetime", _FrozenDT)
    monkeypatch.setattr(mp.time, "sleep", lambda s: None)
    return mp.get_news("k", [])


def test_social_video_domains_excluded_at_retrieval(monkeypatch):
    items = _news(monkeypatch)
    domains = {it["source"] for it in items}
    assert "youtube.com" not in domains
    assert "facebook.com" not in domains
    assert all(urllib_d not in mp.BLOCKED_DOMAINS for urllib_d in domains)


def test_freshness_worst_case_hours_for_date_only_sources(monkeypatch):
    items = {it["url"]: it for it in _news(monkeypatch)}
    # at 20:00 on 31.10 a 29.10 item is up to 68h old: outside the 48h window
    assert items["https://www.bloomberg.com/news/articles/2026-10-29/stock-market-today-dow-s-p-live-updates"]["fresh"] is False
    assert items["https://www.reuters.com/markets/us/futures-edge-up-2026-10-30/"]["fresh"] is True
    # a TODAY live blog stays citable: freshness decides, not the URL shape
    assert items["https://www.aljazeera.com/news/liveblog/2026/10/31/iran-war-live"]["fresh"] is True


def test_stale_candidate_rejected_so_seed_kept(monkeypatch):
    items = _news(monkeypatch)
    news_ids = {it["id"] for it in items}
    fresh_ids = {it["id"] for it in items if it["fresh"] is True}
    stale_id = next(it["id"] for it in items if "2026-10-29" in (it["published"] or ""))
    facts = mp.build_facts([], None, [], [], items, None)
    candidate = {
        "intro": "היום נרשמו תנודות חדשותיות משמעותיות במגזר הטכנולוגיה.",
        "stories": [
            {"title": "נפילת מניית אינטל",
             "summary": "המניה ירדה בעקבות התחזית.",
             "impact": "שלילית", "affected": "Intel",
             "news_refs": [stale_id]},
        ],
        "alerts": [],
        "watchlist": [],
    }
    violations = mp.validate_simple(candidate, facts, news_ids, fresh_ids)
    assert any("אין מקור עדכני בחלון" in v for v in violations), violations
