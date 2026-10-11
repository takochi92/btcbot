from btcbot import desk


def acct(cash=1000.0):
    a = desk.fresh_account()
    a["cash"] = cash
    return a


def test_p_up_is_half_at_the_line_and_moves_with_distance():
    s = 0.0001
    assert abs(desk.p_up(100_000, 100_000, s, 300) - 0.5) < 0.01
    assert desk.p_up(100_100, 100_000, s, 60) > 0.9
    assert desk.p_up(99_900, 100_000, s, 60) < 0.1


def test_decide_buys_only_real_sized_edges():
    a = acct()
    t0 = 1_000_000_200.0
    w = t0 - 100
    tk, why = desk.decide(a, w, t0, 0.62, 0.55, 0.47, 0)  # edge 6c up
    assert why == "fill" and tk["side"] == "UP" and tk["stake"] <= desk.CFG["TICKET_CAP"]
    assert desk.decide(a, w, t0, 0.57, 0.55, 0.47, 0)[1] == "edge too small"
    assert desk.decide(a, w, t0, 0.90, 0.55, 0.47, 0)[1] == "edge too good to be true"
    assert desk.decide(a, w, t0, 0.10, 0.05, 0.97, 0)[1] == "price out of range"
    assert desk.decide(a, w, t0, 0.62, None, 0.47, 0)[1] == "no book"


def test_window_cap_and_cooldown():
    a = acct()
    w, t = 1_000_000_000, 1_000_000_100.0
    for i in range(10):
        tk, why = desk.decide(a, w, t + i * 20, 0.62, 0.55, 0.47, t + i * 20 - 20)
        if tk:
            desk.buy(a, tk)
    assert sum(x["stake"] for x in a["tickets"]) <= desk.CFG["WINDOW_CAP"] + 1e-9
    assert desk.decide(a, w, t + 1, 0.62, 0.55, 0.47, t)[1] in ("cooling down", "window cap")


def test_settle_pays_one_dollar_per_winning_share():
    a = acct()
    w = {"start": 1_000_000_000, "ptb": 100.0}
    tk, _ = desk.decide(a, w["start"], w["start"] + 100.0, 0.62, 0.55, 0.47, 0)
    desk.buy(a, tk)
    before = a["cash"]
    desk.settle(a, w, True, 101.0, "polymarket")
    assert a["cash"] == before + tk["shares"]
    assert a["tickets"][0]["won"] and a["tickets"][0]["pnl"] > 0


def test_runs_until_zero_then_stops():
    a = acct(cash=20.0)
    w = {"start": 1_000_000_000, "ptb": 100.0}
    for _ in range(10_000):
        if a["broke"]:
            break
        tk, why = desk.decide(a, w["start"], w["start"] + 100.0, 0.62, 0.55, 0.47, 0)
        if tk:
            desk.buy(a, tk)
        desk.settle(a, w, False, 99.0, "polymarket")  # always lose
        w = {"start": w["start"] + 300, "ptb": 100.0}
    assert a["broke"] and a["cash"] < 0.01
    assert desk.decide(a, w["start"], w["start"] + 100.0, 0.62, 0.55, 0.47, 0)[1] == "broke"
