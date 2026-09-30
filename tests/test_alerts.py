import pytest

from alerts import Alert, AlertEngine, ConfigError, parse_config

MIN = 60  # seconds


def engine_for(*alerts):
    return AlertEngine(list(alerts), "usd")


# --- above / below ---------------------------------------------------------

def test_above_fires_only_when_price_is_above_threshold():
    engine = engine_for(Alert("bitcoin", "above", 100_000, cooldown_minutes=0))
    assert engine.check({"bitcoin": 99_999}, now=0) == []
    assert engine.check({"bitcoin": 100_000}, now=60) == []  # equal is not above
    msgs = engine.check({"bitcoin": 100_001}, now=120)
    assert len(msgs) == 1
    assert "bitcoin" in msgs[0] and "above" in msgs[0]


def test_below_fires_only_when_price_is_below_threshold():
    engine = engine_for(Alert("ethereum", "below", 2_000, cooldown_minutes=0))
    assert engine.check({"ethereum": 2_500}, now=0) == []
    assert len(engine.check({"ethereum": 1_999.5}, now=60)) == 1


def test_missing_price_is_ignored():
    engine = engine_for(Alert("bitcoin", "above", 1))
    assert engine.check({"ethereum": 5}, now=0) == []


def test_multiple_alerts_fire_independently():
    engine = engine_for(
        Alert("bitcoin", "above", 100),
        Alert("ethereum", "below", 10),
        Alert("solana", "above", 1_000),
    )
    msgs = engine.check({"bitcoin": 200, "ethereum": 5, "solana": 50}, now=0)
    assert len(msgs) == 2


# --- cooldown ----------------------------------------------------------------

def test_cooldown_prevents_repeat_alerts():
    engine = engine_for(Alert("bitcoin", "above", 100, cooldown_minutes=30))
    assert len(engine.check({"bitcoin": 150}, now=0)) == 1
    # Still above on every poll, but inside the cooldown: no repeats.
    for t in range(1, 30):
        assert engine.check({"bitcoin": 150}, now=t * MIN - 1) == []
    # Cooldown over: it may fire again.
    assert len(engine.check({"bitcoin": 150}, now=30 * MIN)) == 1


def test_cooldown_is_per_alert():
    engine = engine_for(
        Alert("bitcoin", "above", 100, cooldown_minutes=60),
        Alert("bitcoin", "above", 120, cooldown_minutes=60),
    )
    assert len(engine.check({"bitcoin": 110}, now=0)) == 1  # only the 100 alert
    msgs = engine.check({"bitcoin": 130}, now=MIN)  # 100 alert is cooling down
    assert len(msgs) == 1 and "120" in msgs[0]


def test_zero_cooldown_fires_every_poll():
    engine = engine_for(Alert("bitcoin", "above", 100, cooldown_minutes=0))
    assert len(engine.check({"bitcoin": 150}, now=0)) == 1
    assert len(engine.check({"bitcoin": 150}, now=1)) == 1


# --- percent change ----------------------------------------------------------

def test_change_waits_until_it_has_a_full_window_of_history():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10))
    assert engine.check({"bitcoin": 100}, now=0) == []
    # +50% but we don't yet have a price from 10 minutes ago.
    assert engine.check({"bitcoin": 150}, now=5 * MIN) == []


def test_change_up_fires():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, direction="up"))
    engine.check({"bitcoin": 100}, now=0)
    engine.check({"bitcoin": 102}, now=5 * MIN)
    msgs = engine.check({"bitcoin": 106}, now=10 * MIN)
    assert len(msgs) == 1
    assert "+6.00%" in msgs[0]


def test_change_down_fires():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, direction="down"))
    engine.check({"bitcoin": 100}, now=0)
    msgs = engine.check({"bitcoin": 94}, now=10 * MIN)
    assert len(msgs) == 1
    assert "-6.00%" in msgs[0]


def test_change_direction_is_respected():
    up_only = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, direction="up"))
    up_only.check({"bitcoin": 100}, now=0)
    assert up_only.check({"bitcoin": 90}, now=10 * MIN) == []

    down_only = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, direction="down"))
    down_only.check({"bitcoin": 100}, now=0)
    assert down_only.check({"bitcoin": 110}, now=10 * MIN) == []


def test_change_any_direction_fires_both_ways():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, cooldown_minutes=0))
    engine.check({"bitcoin": 100}, now=0)
    assert len(engine.check({"bitcoin": 105}, now=10 * MIN)) == 1
    engine2 = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, cooldown_minutes=0))
    engine2.check({"bitcoin": 100}, now=0)
    assert len(engine2.check({"bitcoin": 95}, now=10 * MIN)) == 1


def test_small_change_does_not_fire():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10))
    engine.check({"bitcoin": 100}, now=0)
    assert engine.check({"bitcoin": 104.9}, now=10 * MIN) == []


def test_change_compares_against_price_from_window_ago_not_first_price():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, cooldown_minutes=0))
    # Price rose slowly long ago, then stayed flat for the last 10 minutes.
    for minute, price in [(0, 100), (10, 110), (20, 120), (30, 120)]:
        msgs = engine.check({"bitcoin": price}, now=minute * MIN)
    assert msgs == []  # 120 vs 120 ten minutes ago = 0%
    assert engine.percent_change("bitcoin", 10, 30 * MIN) == pytest.approx(0)


def test_history_is_pruned_but_keeps_reference_point():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10))
    for minute in range(0, 120):
        engine.check({"bitcoin": 100 + minute}, now=minute * MIN)
    history = engine._history["bitcoin"]
    assert len(history) <= 12  # not every sample from 2 hours
    assert engine.percent_change("bitcoin", 10, 119 * MIN) == pytest.approx((219 - 209) / 209 * 100)


def test_change_with_cooldown():
    engine = engine_for(Alert("bitcoin", "change", 5, window_minutes=10, cooldown_minutes=60))
    engine.check({"bitcoin": 100}, now=0)
    assert len(engine.check({"bitcoin": 110}, now=10 * MIN)) == 1
    assert engine.check({"bitcoin": 130}, now=20 * MIN) == []  # cooling down


# --- config ------------------------------------------------------------------

def test_parse_config_defaults_and_values():
    cfg = parse_config({
        "poll_seconds": 30,
        "cooldown_minutes": 15,
        "alerts": [
            {"coin": "Bitcoin", "condition": "ABOVE", "threshold": 100},
            {"coin": "ethereum", "condition": "change", "threshold": 3, "window_minutes": 5,
             "direction": "down", "cooldown_minutes": 1, "name": "eth dump"},
        ],
    })
    assert cfg.poll_seconds == 30
    assert cfg.vs_currency == "usd"
    btc, eth = cfg.alerts
    assert (btc.coin, btc.condition, btc.threshold, btc.cooldown_minutes) == ("bitcoin", "above", 100, 15)
    assert (eth.window_minutes, eth.direction, eth.cooldown_minutes, eth.name) == (5, "down", 1, "eth dump")


def test_shipped_config_yaml_is_valid():
    from alerts import load_config
    import os
    cfg = load_config(os.path.join(os.path.dirname(__file__), "..", "config.yaml"))
    assert cfg.alerts


@pytest.mark.parametrize("data, error", [
    (None, "empty"),
    ({"alerts": []}, "at least one"),
    ({"poll_seconds": 1, "alerts": [{"coin": "b", "condition": "above", "threshold": 1}]}, "at least 10"),
    ({"alerts": [{"condition": "above", "threshold": 1}]}, "missing 'coin'"),
    ({"alerts": [{"coin": "b", "condition": "sideways", "threshold": 1}]}, "condition must be"),
    ({"alerts": [{"coin": "b", "condition": "above", "threshold": "lots"}]}, "must be numbers"),
    ({"alerts": [{"coin": "b", "condition": "change", "threshold": -5}]}, "positive percent"),
    ({"alerts": [{"coin": "b", "condition": "change", "threshold": 5, "direction": "left"}]}, "direction"),
])
def test_parse_config_rejects_bad_input(data, error):
    with pytest.raises(ConfigError, match=error):
        parse_config(data)


# --- saved state -------------------------------------------------------------

def test_state_round_trip_keeps_cooldown_and_history():
    import json
    alerts = [Alert("bitcoin", "above", 100, cooldown_minutes=30),
              Alert("bitcoin", "change", 5, window_minutes=10)]
    first = AlertEngine(alerts)
    first.check({"bitcoin": 100.5}, now=0)
    saved = json.loads(json.dumps(first.to_dict()))  # must survive JSON

    second = AlertEngine(alerts)
    second.load_dict(saved)
    # Cooldown from the earlier run still applies...
    assert all("above" not in m for m in second.check({"bitcoin": 101}, now=5 * MIN))
    # ...and the price history lets the change alert compare with the old run.
    msgs = second.check({"bitcoin": 110}, now=10 * MIN)
    assert len(msgs) == 1 and "+9.45%" in msgs[0]


def test_bad_state_is_ignored():
    engine = engine_for(Alert("bitcoin", "above", 100))
    engine.load_dict({"history": {"bitcoin": "garbage"}, "last_fired": 5})
    assert len(engine.check({"bitcoin": 150}, now=0)) == 1
