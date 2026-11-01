"""Push/toast alert feature: payload shape, dedup stability, static assets."""
import json
import os

import generate as g

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _DT:
    def isoformat(self):
        return "2026-11-01T00:30:00+02:00"

    def timestamp(self):
        return 1762900000.0


def _alert(ticker, head):
    return {"ticker": ticker, "head": head, "date": "01.11.2026, 00:30",
            "link": "https://example.com/x"}


def test_bio_hot_payload_stable_keys_and_shape():
    reps = [_alert("INO", "INOVIO gets FDA accelerated approval"),
            _alert("NRXP", "NRx Receives FDA Approval")]
    p1 = g.bio_hot_payload(reps, _DT())
    p2 = g.bio_hot_payload(reps, _DT())
    assert [i["key"] for i in p1["hot"]] == [i["key"] for i in p2["hot"]]
    assert len({i["key"] for i in p1["hot"]}) == 2
    assert p1["epoch"] == 1762900000
    item = p1["hot"][0]
    assert item["ticker"] == "INO" and item["link"].startswith("https://")


def test_bio_hot_payload_new_headline_new_key():
    a = g.bio_hot_payload([_alert("INO", "story A")], _DT())["hot"][0]["key"]
    b = g.bio_hot_payload([_alert("INO", "story B")], _DT())["hot"][0]["key"]
    assert a != b


def test_service_worker_push_only_no_fetch_interception():
    sw = open(os.path.join(BASE, "sw.js"), encoding="utf-8").read()
    assert "addEventListener('push'" in sw
    assert "showNotification" in sw
    assert "notificationclick" in sw
    assert "addEventListener('fetch'" not in sw  # page loads must stay untouched


def test_manifest_ios_push_capable():
    m = json.load(open(os.path.join(BASE, "manifest.webmanifest"), encoding="utf-8"))
    assert m["display"] == "standalone"  # required for iOS Home Screen web push
    assert m["start_url"] == "./" and m["scope"] == "./"
    assert any(i["sizes"] == "192x192" for i in m["icons"])
    assert any(i["sizes"] == "512x512" for i in m["icons"])


def test_push_js_no_retroactive_blast_and_ios_guidance():
    js = open(os.path.join(BASE, "push.js"), encoding="utf-8").read()
    assert "first visit: no retroactive toast" in js
    assert "הוסף למסך הבית" in js  # iOS capability guidance instead of a dead button
    assert "push/test" in js and "push/unsubscribe" in js


def test_icons_exist():
    for name in ("icon-180.png", "icon-192.png", "icon-512.png"):
        assert os.path.getsize(os.path.join(BASE, name)) > 500



def test_sw_records_tap_at_click_time_not_push_time():
    sw = open(os.path.join(BASE, "sw.js"), encoding="utf-8").read()
    push_body = sw[sw.index("addEventListener('push'"):sw.index("addEventListener('notificationclick'")]
    click_body = sw[sw.index("addEventListener('notificationclick'"):]
    # a push that is never tapped must not persist anything (no hijack of the next plain open)
    assert "caches.open" not in push_body
    # the tap is recorded at click time, before opening (covers WebKit 263687 dropped URLs)
    assert "'/last-click'" in click_body and "caches.open" in click_body
    # declarative payloads parsed via data.notification fallback
    assert "data.notification" in sw
    # click resolves an absolute URL and never depends on notification.data surviving
    assert "self.registration.scope" in sw and "openWindow" in sw


def test_push_js_routes_only_after_click_and_consumes():
    js = open(os.path.join(BASE, "push.js"), encoding="utf-8").read()
    assert "bio-push-v1" in js and "last-click" in js and "last-target" not in js
    assert "tabbar button" in js  # switches to the pushed pane on load
    assert "2*60*1000" in js      # stale tap entries are ignored
    assert "delete('/last-click')" in js  # entry consumed on read


def test_push_js_reconcile_failure_visible():
    js = open(os.path.join(BASE, "push.js"), encoding="utf-8").read()
    # self-heal re-registers an existing browser subscription on load
    assert "reconcile" in js and "/push/subscribe" in js
    # a failed server upsert must be surfaced, never a silent false 'active'
    assert "הרישום בשרת נכשל" in js and "throw new Error('reconcile failed')" in js


def _news_alert(ticker, head, link):
    return {"sev": "info", "kind": "news", "ticker": ticker, "name": ticker,
            "date": "01.11.2026, 00:30", "ts": 1.0, "head": head, "sub": "",
            "link": link, "source": "T"}


def test_bio_hot_payload_flags_unverified_issuer_fda_claim():
    issuer = _news_alert("NRXP", "NRx Receives FDA Approval of Preservative-Free Ketamine",
                         "https://news.google.com/rss/articles/x")
    item = g.bio_hot_payload([issuer], _DT())["hot"][0]
    assert item.get("fda_unverified") is True


def test_bio_hot_payload_no_flag_when_official_backing():
    issuer = _news_alert("NRXP", "NRx Receives FDA Approval of Preservative-Free Ketamine",
                         "https://news.google.com/rss/articles/x")
    verified = {"NRXP": "https://www.fda.gov/drugs/news-events/announcement"}
    item = g.bio_hot_payload([issuer], _DT(), None, verified)["hot"][0]
    assert "fda_unverified" not in item
    assert item["link"] == verified["NRXP"]  # notification links the official source
    fda_gov = _news_alert("INO", "FDA Approves First DNA Immunotherapy",
                          "https://www.fda.gov/news-events/press-announcements/x")
    item2 = g.bio_hot_payload([fda_gov], _DT())["hot"][0]
    assert "fda_unverified" not in item2


def test_bio_hot_payload_no_flag_for_non_fda_news():
    plain = _news_alert("X", "Company reports quarterly results", "https://example.com/x")
    item = g.bio_hot_payload([plain], _DT())["hot"][0]
    assert "fda_unverified" not in item


def test_toast_and_payload_qualifier_strings_present():
    js = open(os.path.join(BASE, "push.js"), encoding="utf-8").read()
    assert "fda_unverified" in js
    assert "דיווח - לא אומת מול FDA" in js  # same wording as the alert card badge
