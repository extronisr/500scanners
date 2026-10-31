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



def test_sw_push_target_store_and_click_fallbacks():
    sw = open(os.path.join(BASE, "sw.js"), encoding="utf-8").read()
    # tap target persisted at push time (iOS may drop the URL at click time)
    assert "last-target" in sw and "caches.open" in sw
    # declarative payloads parsed via data.notification fallback
    assert "data.notification" in sw
    # click resolves an absolute URL and never depends on notification.data
    assert "self.registration.scope" in sw and "openWindow" in sw


def test_push_js_routes_to_push_target_pane():
    js = open(os.path.join(BASE, "push.js"), encoding="utf-8").read()
    assert "bio-push-v1" in js and "last-target" in js
    assert "tabbar button" in js  # switches to the pushed pane on load
