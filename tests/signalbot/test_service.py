"""End-to-end product behaviour with a fake market and an in-memory database."""

from signalbot.db import DAY, Database
from signalbot.service import Service
from signalbot.settings import Settings

MIN = 60


def go_pro(service, user_id, days=30):
    service.db.extend_pro(user_id, days=days, now=service.clock())


def run_rounds(service, clock, n, step=60):
    """Run n check rounds, step seconds apart; return every message sent."""
    out = []
    for _ in range(n):
        clock.advance(step)
        out += service.check_round()
    return out


# --- onboarding ------------------------------------------------------------

def test_start_registers_user_and_shows_menu(service):
    reply = service.start(1, "ann", "Ann")
    assert "Hi Ann" in reply.text and reply.buttons
    assert service.db.get_user(1).username == "ann"


def test_new_users_get_a_trial_once(market, clock):
    s = Service(Database(":memory:"), market, Settings(bot_token="x", trial_days=3), clock=clock)
    assert "3 days of Pro free" in s.start(1).text
    assert s.db.get_user(1).is_pro(clock())
    assert "Pro free" not in s.start(1).text  # second /start: no new trial
    clock.advance(3 * DAY + 1)
    assert not s.db.get_user(1).is_pro(clock())


def test_start_with_referral_link(service):
    service.start(1)
    service.start(2, arg="ref_1")
    assert service.db.get_user(2).referred_by == 1
    service.start(3, arg="ref_3")  # self-referral ignored
    assert service.db.get_user(3).referred_by is None
    service.start(4, arg="ref_12345")  # unknown referrer ignored
    assert service.db.get_user(4).referred_by is None


# --- prices ----------------------------------------------------------------

def test_price_by_symbol_and_name(service):
    assert "$85,000" in service.price("btc").text
    assert "Ethereum" in service.price("Ethereum").text
    assert "couldn't find" in service.price("nosuchcoin").text
    assert "Which coin" in service.price("").text


def test_price_has_quick_alert_buttons(service):
    datas = [b.data for row in service.price("sol").buttons for b in row]
    assert "qa:solana:up:5" in datas and "qa:solana:move:3" in datas


def test_movers(service):
    gainers, losers = service.movers().text.split("losers")
    assert gainers.index("SOL") < gainers.index("DOGE")  # best first
    assert losers.index("DOGE") < losers.index("SOL")    # worst first


# --- creating alerts -------------------------------------------------------

def test_alert_direction_is_picked_from_current_price(service):
    service.start(1)
    assert "BTC ≥ $90,000" in service.add_alert(1, ["btc", "90000"]).text
    assert "BTC ≤ $80,000" in service.add_alert(1, ["btc", "80k"]).text
    kinds = [a.kind for a in service.db.user_alerts(1)]
    assert kinds == ["above", "below"]


def test_alert_already_past_level_is_refused(service):
    service.start(1)
    assert "already above" in service.add_alert(1, ["btc", ">", "80000"]).text
    assert service.db.count_alerts(1) == 0


def test_free_limit_and_pro_limit(service, settings):
    service.start(1)
    for level in ("90000", "91000", "92000"):
        assert "Alert set" in service.add_alert(1, ["btc", level]).text
    blocked = service.add_alert(1, ["btc", "93000"])
    assert "Free plan" in blocked.text and blocked.buttons
    go_pro(service, 1)
    assert "Alert set" in service.add_alert(1, ["btc", "93000"]).text


def test_move_alerts_are_pro_only(service):
    service.start(1)
    assert "Pro" in service.add_alert(1, ["sol", "5%"]).text
    go_pro(service, 1)
    assert "SOL moves ±5% in 1h" in service.add_alert(1, ["sol", "5%"]).text


def test_bad_input_shows_help(service):
    service.start(1)
    assert "How to set an alert" in service.add_alert(1, ["btc"]).text
    assert "couldn't find" in service.add_alert(1, ["zzz", "5"]).text


def test_quick_alert_buttons(service):
    service.start(1)
    assert "BTC ≥ $89,200" in service.quick_alert(1, "qa:bitcoin:up:5").text   # 85,000 * 1.05 rounded
    assert "BTC ≤ $80,800" in service.quick_alert(1, "qa:bitcoin:down:5").text
    assert "Pro" in service.quick_alert(1, "qa:bitcoin:move:3").text
    assert "expired" in service.quick_alert(1, "qa:broken").text


def test_list_and_delete_alerts(service):
    service.start(1)
    service.start(2)
    service.add_alert(1, ["btc", "90000"])
    service.add_alert(1, ["eth", "3000"])
    reply = service.list_alerts(1)
    assert "(2/3)" in reply.text
    first_id = service.db.user_alerts(1)[0].id
    assert service.delete_alert(2, first_id) and service.db.count_alerts(1) == 2  # can't delete others' alerts
    service.delete_alert(1, first_id)
    assert service.db.count_alerts(1) == 1
    assert "Deleted 1" in service.delete_all(1).text


# --- checking alerts -------------------------------------------------------

def test_pro_alert_fires_once_on_crossing_then_rearms(service, market, clock):
    service.start(1)
    go_pro(service, 1)
    service.add_alert(1, ["btc", "90000"])
    assert run_rounds(service, clock, 1) == []
    market.table["bitcoin"] = 90500
    msgs = run_rounds(service, clock, 1)
    assert msgs == [(1, msgs[0][1])] and "broke $90,000" in msgs[0][1]
    assert run_rounds(service, clock, 5) == []  # stays above: no spam
    market.table["bitcoin"] = 89000  # back below by > 0.5%: re-arms
    assert run_rounds(service, clock, 1) == []
    market.table["bitcoin"] = 90100
    assert len(run_rounds(service, clock, 1)) == 1


def test_free_alerts_are_checked_every_five_minutes(service, market, clock):
    service.start(1)
    service.add_alert(1, ["btc", "90000"])
    run_rounds(service, clock, 1)  # first round counts as a free round
    market.table["bitcoin"] = 91000
    assert run_rounds(service, clock, 3) == []      # minutes 2-4: Pro-only rounds
    assert len(run_rounds(service, clock, 2)) == 1  # by minute 6 the free round ran


def test_move_alert_fires_on_big_move(service, market, clock):
    service.start(1)
    go_pro(service, 1)
    service.add_alert(1, ["sol", "5%"])
    run_rounds(service, clock, 60)  # an hour of flat prices
    market.table["solana"] = 127.0  # +5.8%
    msgs = run_rounds(service, clock, 1)
    assert len(msgs) == 1 and "+5.83% in 1h" in msgs[0][1]
    assert run_rounds(service, clock, 10) == []  # not again within the window


def test_expired_pro_pauses_extra_alerts(service, market, clock):
    service.start(1)
    go_pro(service, 1, days=1)
    for level in ("90000", "91000", "92000", "93000", "94000"):
        service.add_alert(1, ["btc", level])
    clock.advance(DAY + 1)
    assert "⏸" in service.list_alerts(1).text
    market.table["bitcoin"] = 95000
    msgs = run_rounds(service, clock, 6)
    assert len(msgs) == 3  # only the 3 oldest are checked on the free plan


def test_blocked_users_are_skipped(service, market, clock):
    service.start(1)
    go_pro(service, 1)
    service.add_alert(1, ["btc", "90000"])
    service.db.mark_blocked(1)
    market.table["bitcoin"] = 95000
    assert run_rounds(service, clock, 1) == []
    assert market.price_calls == 0  # nothing to watch, no API call


def test_one_price_request_for_all_users(service, market, clock):
    for uid in range(1, 51):
        service.start(uid)
        service.add_alert(uid, ["btc", "90000"])
        service.add_alert(uid, ["eth", "3000"])
    run_rounds(service, clock, 1)
    assert market.price_calls == 1


# --- payments --------------------------------------------------------------

def test_precheckout_validation(service, settings):
    assert service.check_precheckout(1, "pro30:1", "XTR", settings.pro_price_stars) is None
    assert service.check_precheckout(1, "pro30:2", "XTR", settings.pro_price_stars)  # someone else's invoice
    assert service.check_precheckout(1, "pro30:1", "XTR", 1)
    assert service.check_precheckout(1, "pro30:1", "USD", settings.pro_price_stars)


def test_invoice_is_a_30_day_stars_subscription(service, settings):
    inv = service.invoice(7)
    assert inv["currency"] == "XTR" and inv["subscription_period"] == 2592000
    assert inv["payload"] == "pro30:7" and inv["prices"] == [("Pro", settings.pro_price_stars)]


def test_payment_activates_pro_and_rewards_referrer(service, clock):
    service.start(1)
    service.start(2, arg="ref_1")
    expires = clock() + 30 * DAY
    msgs = service.on_payment(2, "charge-1", 250, "pro30:2", expires_at=expires)
    assert service.db.get_user(2).is_pro(clock())
    assert [m[0] for m in msgs] == [2, 1]  # buyer thanked, referrer rewarded
    assert service.db.get_user(1).is_pro(clock())
    # Duplicate delivery of the same payment does nothing.
    assert service.on_payment(2, "charge-1", 250, "pro30:2", expires_at=expires) == []
    # Renewal next month: no second referral bonus.
    msgs = service.on_payment(2, "charge-2", 250, "pro30:2", is_recurring=True, expires_at=expires + 30 * DAY)
    assert [m[0] for m in msgs] == [2]
    assert service.db.get_user(2).pro_until == expires + 30 * DAY


def test_pro_screen(service, clock):
    service.start(1)
    reply, upgrade = service.pro(1)
    assert upgrade and "250 Stars" in reply.text
    service.on_payment(1, "c", 250, "pro30:1", expires_at=clock() + 30 * DAY)
    reply, upgrade = service.pro(1)
    assert not upgrade and "You're Pro until" in reply.text


def test_refund_removes_pro(service, clock):
    service.start(1)
    service.on_payment(1, "c1", 250, "pro30:1", expires_at=clock() + 30 * DAY)
    assert service.db.mark_refunded("c1") == 1
    assert not service.db.get_user(1).is_pro()
    assert service.db.mark_refunded("unknown") is None


# --- digest, admin ---------------------------------------------------------

def test_digest_goes_to_pro_users_and_expiry_notice(service, clock):
    service.start(1)
    service.start(2)
    service.start(3)
    go_pro(service, 1)
    go_pro(service, 3, days=0.5)
    service.set_digest(1, "on")
    clock.advance(DAY * 0.6)  # user 3's Pro just ended
    msgs = dict(service.digest_messages())
    assert "Daily crypto digest" in msgs[1]
    assert 2 not in msgs
    assert "Pro has ended" in msgs[3]


def test_digest_toggle(service):
    service.start(1)
    assert "<b>off</b>" in service.set_digest(1, "off").text
    assert "<b>on</b>" in service.set_digest(1, "on").text


def test_admin_stats_and_grant(service, clock):
    service.start(1)
    service.on_payment(1, "c", 250, "pro30:1", expires_at=clock() + 30 * DAY)
    text = service.stats().text
    assert "Pro now: 1" in text and "Stars 30d: 250" in text
    assert "Pro until" in service.grant(["1", "7"]).text
    assert "Usage" in service.grant([]).text
    assert service.is_admin(999) and not service.is_admin(1)


def test_terms_and_support(service):
    assert "not financial" in service.terms().text
    assert "@owner" in service.paysupport().text


def test_backup_is_a_working_copy(service, tmp_path):
    service.start(1)
    service.add_alert(1, ["btc", "90000"])
    copy = Database(service.db.backup_to(str(tmp_path / "b.db")))
    assert copy.count_alerts(1) == 1


def test_database_is_safe_across_threads(tmp_path):
    import threading
    db = Database(str(tmp_path / "t.db"))
    errors = []

    def worker(uid):
        try:
            db.upsert_user(uid)
            for i in range(30):
                db.add_alert(uid, "bitcoin", "btc", "above", 90000 + i)
                db.add_prices({"bitcoin": 85000.0 + i})
                db.extend_pro(uid, days=1)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(uid,)) for uid in range(1, 11)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert db.stats()["alerts"] == 300
