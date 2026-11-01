import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scan_utils


def mk(n, base=100.0):
    return [base + i * 0.1 for i in range(n)]


def test_no_crash_returns_none():
    closes = mk(20)  # gentle up drift, no -4% day
    assert scan_utils.three_day_marker(closes) is None


def test_crash_yesterday_waits():
    closes = mk(20)
    closes[-2] = closes[-3] * 0.93   # -7% two sessions ago
    closes[-1] = closes[-2] * 1.001
    m = scan_utils.three_day_marker(closes)
    assert m and m["days_since"] == 1 and m["ok"] is False and abs(m["drop_pct"] + 7.0) < 0.01


def test_crash_three_days_ago_satisfied():
    closes = mk(20)
    closes[-4] = closes[-5] * 0.95   # -5% crash, then 3 full sessions
    for j in range(-3, 0):
        closes[j] = closes[j - 1] * 1.002
    m = scan_utils.three_day_marker(closes)
    assert m and m["days_since"] == 3 and m["ok"] is True


def test_latest_crash_anchors():
    closes = mk(20)
    closes[-10] = closes[-11] * 0.90  # old -10% crash (clock expired)
    closes[-2] = closes[-3] * 0.955   # fresh -4.5% crash resets the clock
    closes[-1] = closes[-2] * 1.001
    m = scan_utils.three_day_marker(closes)
    assert m and m["days_since"] == 1 and m["ok"] is False


def test_short_history_returns_none():
    assert scan_utils.three_day_marker([1, 2, 3]) is None
    assert scan_utils.three_day_marker(None) is None


def test_crash_today_zero_days():
    closes = mk(20)
    closes[-1] = closes[-2] * 0.92
    m = scan_utils.three_day_marker(closes)
    assert m and m["days_since"] == 0 and m["ok"] is False


def test_exclude_last_drops_partial_session():
    closes = mk(20)
    closes[-1] = closes[-2] * 0.90   # crash on the still-forming session
    m = scan_utils.three_day_marker(closes, exclude_last=True)
    assert m is None                  # partial bar must not count as a crash day
    m2 = scan_utils.three_day_marker(closes, exclude_last=False)
    assert m2 and m2["days_since"] == 0


def test_exclude_last_keeps_completed_count():
    closes = mk(20)
    closes[-4] = closes[-5] * 0.95    # completed crash, 2 full sessions after...
    for j in range(-3, 0):
        closes[j] = closes[j - 1] * 1.002
    closes.append(closes[-1] * 1.001) # ...plus a partial live session
    m = scan_utils.three_day_marker(closes, exclude_last=True)
    assert m and m["days_since"] == 3 and m["ok"] is True


def test_chip_completion_text_no_bare_checkmark():
    import generate
    chip = generate.tdr_chip({"days_since": 4, "drop_pct": -6.2, "ok": True})
    assert "תקופת ההמתנה" in chip and "✓" not in chip
    wait = generate.tdr_chip({"days_since": 1, "drop_pct": -6.2, "ok": False})
    assert "1/3" in wait and "להמתין" in wait
    assert generate.tdr_chip(None) == ""
