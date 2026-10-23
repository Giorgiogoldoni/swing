"""Test su dati sintetici. Si lanciano con:  python -m pytest tests -q

Verificano la logica (indicatori, segnali senza look-ahead, regole di portafoglio), non la redditivita'.
"""
import dataclasses
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import swing_lib as sl  # noqa: E402
import backtest as bt  # noqa: E402


def make_df(closes, rng_pct=0.005, volume=1e6, start="2020-01-01"):
    c = np.asarray(closes, float)
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) * (1 + rng_pct)
    l = np.minimum(o, c) * (1 - rng_pct)
    idx = pd.bdate_range(start, periods=len(c))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": volume}, index=idx)


def drift_closes(n=300, base=100.0, step=0.01):
    return base + step * np.arange(n)


# ----------------------------------------------------------------------------- indicatori

def test_ema_matches_pandas():
    x = np.random.default_rng(1).normal(100, 2, 200)
    ref = pd.Series(x).ewm(span=20, adjust=False).mean().to_numpy()
    assert np.allclose(sl.ema(x, 20), ref)


def test_kama_constant_series_stays_constant():
    k = sl.kama(np.full(100, 50.0))
    assert np.allclose(k[9:], 50.0)
    assert np.isnan(k[:9]).all()


def test_atr_constant_range():
    n = 100
    h, l, c = np.full(n, 100.5), np.full(n, 99.5), np.full(n, 100.0)
    assert np.allclose(sl.atr_wilder(h, l, c)[20:], 1.0)


def test_sar_uptrend_is_below_lows():
    df = make_df(drift_closes(200, step=0.5))
    sar, up = sl.parabolic_sar(df["high"].to_numpy(), df["low"].to_numpy())
    assert up[30:].all()
    assert (sar[30:] < df["low"].to_numpy()[30:]).all()


def test_sar_has_no_lookahead():
    """sar_pre[i] non deve cambiare se si tagliano i dati dopo i-1."""
    rng = np.random.default_rng(3)
    df = make_df(100 + np.cumsum(rng.normal(0, 1, 300)))
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    full, _ = sl.parabolic_sar(h, l)
    cut, _ = sl.parabolic_sar(h[:201], l[:201])
    assert np.allclose(full[1:201], cut[1:201], equal_nan=True)


# ----------------------------------------------------------------------------- segnali

def noisy_uptrend(seed, n=700):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0008, 0.01, n)
    return 100 * np.exp(np.cumsum(r))


def test_signals_respect_rules_and_cooldown():
    p = sl.Params()
    total = 0
    for seed in range(12):
        pr = sl.prepare_ticker(make_df(noisy_uptrend(seed), rng_pct=0.006), p)
        assert pr is not None
        s = np.flatnonzero(pr["signal"])
        total += len(s)
        if len(s) > 1:
            assert np.diff(s).min() >= p.cooldown
        for i in s:
            assert pr["ema20"][i] > pr["ema50"][i]
            assert pr["close"][i] > pr["ema20"][i]
            assert pr["close"][i] > pr["open"][i]
            assert pr["close"][i] > pr["ema50"][i]
            assert pr["ao"][i] > pr["ao"][i - 1]
            assert pr["elig"][i] and pr["trend_ok"][i] and pr["pullback"][i]
            rng = pr["high"][i] - pr["low"][i]
            assert (pr["close"][i] - pr["open"][i]) >= p.body_min * rng - 1e-12
            assert (pr["high"][i] - pr["close"][i]) <= p.upper_shadow_max * rng + 1e-12
    assert total > 0, "con trend rialzista e rumore dovrebbe esserci almeno un segnale"


def test_signals_do_not_use_future_bars():
    """Il segnale alla barra t non cambia se si tagliano le barre successive."""
    p = sl.Params()
    df = make_df(noisy_uptrend(4), rng_pct=0.006)
    full = sl.prepare_ticker(df, p)
    cut = sl.prepare_ticker(df.iloc[:500], p)
    assert np.array_equal(full["signal"][:500], cut["signal"])


def test_no_signal_without_liquidity():
    p = sl.Params()
    pr = sl.prepare_ticker(make_df(noisy_uptrend(0), rng_pct=0.006, volume=0.0), p)
    assert not pr["signal"].any()
    assert not pr["elig"].any()


def test_no_signal_when_volatility_too_low():
    p = sl.Params()
    pr = sl.prepare_ticker(make_df(drift_closes(400, step=0.01), rng_pct=0.0002), p)
    assert pr is not None
    assert np.nanmax(pr["atr_pct"]) < p.min_atr_pct
    assert not pr["elig"].any()


# ----------------------------------------------------------------------------- motore

def world_from(frames, classes, start_offset=150, params=None, override=None):
    """frames: dict ticker -> DataFrame. override: dict ticker -> lista di indici di barra con segnale."""
    p = params or sl.Params()
    prepared = {t: sl.prepare_ticker(df, p) for t, df in frames.items()}
    for t, idxs in (override or {}).items():
        prepared[t]["signal"] = np.zeros(len(prepared[t]["close"]), dtype=bool)
        prepared[t]["signal"][list(idxs)] = True
    any_df = next(iter(frames.values()))
    start, end = any_df.index[start_offset], any_df.index[-1]
    return bt.build_world(prepared, classes, start, end), prepared


def test_entry_next_open_stop_and_costs():
    closes = drift_closes(300)
    closes[202:] = closes[202:] - 3.0      # crollo alla barra 202
    df = make_df(closes)
    p = sl.Params(use_rr_filter=False)
    w, prepared = world_from({"AAA": df}, {"AAA": "equity"}, params=p, override={"AAA": [200]})
    res = bt.simulate(w, p)
    tr = res["trades"]
    assert len(tr) == 1
    t = tr.iloc[0]
    atr200 = prepared["AAA"]["atr"][200]
    assert t["motivo"] == "stop"
    assert np.isclose(t["entry"], df["open"].iloc[201])           # ingresso all'apertura successiva
    assert np.isclose(t["D"], atr200)                              # stop piu' corto di 1 ATR -> 1 ATR
    assert np.isclose(t["stop_iniziale"], t["entry"] - atr200)
    assert np.isclose(t["shares"] * t["entry"], 0.10 * bt.CAPITAL, rtol=1e-6)   # tetto 10% per posizione
    assert -1.30 < t["R"] < -1.10                                  # -1R piu' costi 0,20%
    assert res["equity"].iloc[-1] < bt.CAPITAL


def test_gap_filter_skips_trade():
    df = make_df(drift_closes(300))
    p = sl.Params(use_rr_filter=False)
    prepared_tmp = sl.prepare_ticker(df, p)
    atr200 = prepared_tmp["atr"][200]
    df.iloc[201, df.columns.get_loc("open")] = df["close"].iloc[200] + 3 * atr200
    df.iloc[201, df.columns.get_loc("high")] = df["open"].iloc[201] * 1.005
    w, _ = world_from({"AAA": df}, {"AAA": "equity"}, params=p, override={"AAA": [200]})
    res = bt.simulate(w, p)
    assert len(res["trades"]) == 0
    assert res["rejections"]["gap"] == 1


def test_gap_through_stop_exits_at_open():
    closes = drift_closes(300)
    df = make_df(closes)
    p = sl.Params(use_rr_filter=False)
    df.iloc[202, df.columns.get_loc("open")] = 90.0
    df.iloc[202, df.columns.get_loc("low")] = 89.0
    df.iloc[202, df.columns.get_loc("high")] = 91.0
    w, _ = world_from({"AAA": df}, {"AAA": "equity"}, params=p, override={"AAA": [200]})
    tr = bt.simulate(w, p)["trades"]
    assert tr.iloc[0]["motivo"] == "stop_gap"
    assert np.isclose(tr.iloc[0]["exit"], 90.0)


def test_max_new_per_day_and_max_positions():
    frames = {f"T{k:02d}": make_df(drift_closes(300)) for k in range(20)}
    classes = {t: "equity" for t in frames}
    p = sl.Params(use_rr_filter=False, class_caps={"equity": 99}, max_per_cluster=99, max_position_pct=0.05)
    # giorno 1: 20 segnali -> al massimo 3 ingressi
    w, _ = world_from(frames, classes, params=p, override={t: [200] for t in frames})
    assert len(bt.simulate(w, p)["trades"]) == 3
    # segnali su piu' giorni -> tetto di 15 posizioni
    w, _ = world_from(frames, classes, params=p, override={t: [200, 201, 202, 203, 204, 205, 206] for t in frames})
    res = bt.simulate(w, p)
    assert res["n_positions"].max() == 15
    assert len(res["trades"]) == 15


def test_class_cap_for_bonds():
    frames = {f"B{k:02d}": make_df(drift_closes(300)) for k in range(10)}
    classes = {t: "bond" for t in frames}
    p = sl.Params(use_rr_filter=False, max_per_cluster=99, max_position_pct=0.05)
    w, _ = world_from(frames, classes, params=p, override={t: [200, 201, 202] for t in frames})
    res = bt.simulate(w, p)
    assert res["n_positions"].max() == 5      # tetto obbligazionari


def test_correlation_cluster_limit():
    base = noisy_uptrend(7, 300)
    frames = {f"C{k}": make_df(base) for k in range(4)}        # strumenti identici -> correlazione 1
    classes = {t: "equity" for t in frames}
    p = sl.Params(use_rr_filter=False, max_position_pct=0.05)
    w, _ = world_from(frames, classes, params=p, override={t: [200] for t in frames})
    res = bt.simulate(w, p)
    assert len(res["trades"]) == 2                               # max 2 per gruppo correlato
    assert res["rejections"]["cluster"] == 2


def test_rr_filter_rejects_when_target_too_close():
    df = make_df(drift_closes(300))
    p = sl.Params(use_rr_filter=True)
    w, _ = world_from({"AAA": df}, {"AAA": "equity"}, params=p, override={"AAA": [200]})
    res = bt.simulate(w, p)
    assert len(res["trades"]) == 0 and res["rejections"]["rr"] == 1


def test_costs_reduce_result():
    closes = drift_closes(300)
    closes[210:] = closes[210:] + 6.0
    df = make_df(closes)
    p0 = sl.Params(use_rr_filter=False, cost_per_side=0.0)
    p1 = dataclasses.replace(p0, cost_per_side=0.002)
    w, _ = world_from({"AAA": df}, {"AAA": "equity"}, params=p0, override={"AAA": [200]})
    r0 = bt.simulate(w, p0)["trades"].iloc[0]["R"]
    r1 = bt.simulate(w, p1)["trades"].iloc[0]["R"]
    assert r1 < r0


def test_random_baseline_runs_and_is_reproducible():
    frames = {f"T{k:02d}": make_df(noisy_uptrend(k), rng_pct=0.006) for k in range(6)}
    classes = {t: "equity" for t in frames}
    p = sl.Params()
    w, _ = world_from(frames, classes, start_offset=200, params=p)
    a = bt.simulate(w, p, random_seed=1)
    b = bt.simulate(w, p, random_seed=1)
    assert a["trades"].equals(b["trades"])
    assert a["equity"].equals(b["equity"])


def test_equal_weight_benchmark_follows_market():
    frames = {f"T{k}": make_df(drift_closes(300, step=0.2), rng_pct=0.006) for k in range(3)}
    classes = {t: "equity" for t in frames}
    p = sl.Params()
    w, _ = world_from(frames, classes, start_offset=200, params=p)
    ew = bt.equal_weight_benchmark(w)
    assert ew.iloc[-1] > bt.CAPITAL
