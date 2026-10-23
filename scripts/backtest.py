#!/usr/bin/env python3
"""backtest.py

Backtest del sistema swing (specifica: README.md) con simulazione di portafoglio.

Modalita' (protocollo di test, README sez. 6):
  calibrate  solo statistiche descrittive (distribuzioni di ATR%, liquidita', numero di segnali) sul
             periodo PRECEDENTE a --oos-start. Nessuna simulazione di P&L.
  explore    simulazione completa sul periodo PRECEDENTE a --oos-start (per verificare la pipeline).
             I parametri NON si ottimizzano: sono quelli della specifica.
  test       UNA sola esecuzione sul periodo da --oos-start in poi, con tutte le varianti pre-dichiarate
             (con/senza filtro R/R, costi 0,20% e 0,40%), baseline casuale e buy&hold equamente pesato.
             Si rifiuta di rigirare se results/test_result.json esiste (serve --force).

Semplificazioni dichiarate:
  - i candidati vengono valutati in ordine di ranking all'apertura del giorno successivo al segnale;
    gap, distanza stop e filtro R/R si applicano con il prezzo di apertura reale;
  - se lo strumento non ha barra il giorno dell'ingresso (festivita' di una sola piazza) il segnale cade;
  - posizioni ancora aperte a fine dati vengono chiuse all'ultima chiusura (motivo 'fine_dati') e contate.
"""

import argparse
import dataclasses
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swing_lib import Params, prepare_ticker  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPITAL = 100_000.0

F_KEYS = ["open", "high", "low", "close", "atr", "ema20", "ema50", "sar_pre",
          "swing_low3", "high60", "rank_key"]
B_KEYS = ["signal", "elig", "trend_ok", "up_prev"]


# ----------------------------------------------------------------------------
# Caricamento dati
# ----------------------------------------------------------------------------

def load_prepared(prices_path, universe_path, p, limit=None, seed=0):
    """Legge lo snapshot prezzi e prepara ogni strumento. Ritorna (prepared, asset_class)."""
    with open(universe_path, encoding="utf-8") as f:
        uni = json.load(f)
    instruments = uni["instruments"] if isinstance(uni, dict) else uni
    asset_class = {i["ticker_yf"]: i.get("asset_class", "?") for i in instruments}

    prices = pd.read_parquet(prices_path)
    tickers = sorted(prices["ticker"].unique())
    if limit:
        rng = np.random.default_rng(seed)
        tickers = sorted(rng.choice(tickers, size=min(limit, len(tickers)), replace=False))
    prices = prices[prices["ticker"].isin(tickers)]

    prepared, skipped = {}, 0
    t0 = time.time()
    for t, g in prices.groupby("ticker", observed=True):
        pr = prepare_ticker(g.drop(columns="ticker"), p)
        if pr is None:
            skipped += 1
            continue
        prepared[t] = pr
    print(f"Strumenti preparati: {len(prepared)} (storia troppo corta: {skipped}) in {time.time() - t0:.0f}s")
    return prepared, asset_class


# ----------------------------------------------------------------------------
# Mondo di simulazione (matrici date x strumenti)
# ----------------------------------------------------------------------------

class World:
    pass


def build_world(prepared, asset_class, start, end):
    tickers = sorted(prepared)
    lo = pd.Timestamp(start) - pd.Timedelta(days=130)
    hi = pd.Timestamp(end)
    all_dates = np.unique(np.concatenate([prepared[t]["dates"] for t in tickers]))
    master = pd.DatetimeIndex(all_dates)
    master = master[(master >= lo) & (master <= hi)]
    n, m = len(master), len(tickers)

    M = {k: np.full((n, m), np.nan) for k in F_KEYS}
    B = {k: np.zeros((n, m), dtype=bool) for k in B_KEYS}
    valid = np.zeros((n, m), dtype=bool)
    for j, t in enumerate(tickers):
        pr = prepared[t]
        idx = master.get_indexer(pd.DatetimeIndex(pr["dates"]))
        ok = idx >= 0
        ii = idx[ok]
        valid[ii, j] = True
        for k in F_KEYS:
            M[k][ii, j] = pr[k][ok]
        for k in B_KEYS:
            B[k][ii, j] = pr[k][ok]

    close_ff = pd.DataFrame(M["close"]).ffill().to_numpy()
    ret = np.zeros_like(close_ff)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret[1:] = close_ff[1:] / close_ff[:-1] - 1.0
    ret = np.nan_to_num(ret, nan=0.0, posinf=0.0, neginf=0.0)

    w = World()
    w.master, w.tickers, w.M, w.B, w.valid = master, tickers, M, B, valid
    w.close_ff, w.ret = close_ff, ret
    w.classes = [asset_class.get(t, "?") for t in tickers]
    w.sim_idx = np.flatnonzero(master >= pd.Timestamp(start))
    if len(w.sim_idx) and w.sim_idx[0] < 1:
        w.sim_idx = w.sim_idx[w.sim_idx >= 1]
    return w


def random_signals(w, seed, cooldown):
    """Segnali casuali sugli stessi giorni/strumenti con trend valido, con la stessa frequenza dei segnali reali."""
    sim = np.zeros(len(w.master), dtype=bool)
    sim[w.sim_idx] = True
    n_sig = int((w.B["signal"] & sim[:, None]).sum())
    n_trend = int((w.B["trend_ok"] & sim[:, None]).sum())
    prob = n_sig / n_trend if n_trend else 0.0
    rng = np.random.default_rng(seed)
    S = w.B["trend_ok"] & (rng.random(w.B["trend_ok"].shape) < prob)
    for j in np.flatnonzero(S.any(axis=0)):
        last = -10 ** 9
        for i in np.flatnonzero(S[:, j]):
            if i - last < cooldown:
                S[i, j] = False
            else:
                last = i
    return S


# ----------------------------------------------------------------------------
# Simulazione di portafoglio
# ----------------------------------------------------------------------------

def simulate(w, p, capital=CAPITAL, random_seed=None):
    sig = w.B["signal"] if random_seed is None else random_signals(w, random_seed, p.cooldown)
    M, valid, close_ff, ret = w.M, w.valid, w.close_ff, w.ret
    up_prev, elig = w.B["up_prev"], w.B["elig"]
    op, hi, lo, cl = M["open"], M["high"], M["low"], M["close"]
    atr, e20, e50, sar = M["atr"], M["ema20"], M["ema50"], M["sar_pre"]
    sl3, h60, rk = M["swing_low3"], M["high60"], M["rank_key"]
    c = p.cost_per_side

    cash = capital
    positions = {}
    trades = []
    eq_idx, eq_val, expo_val, npos_val = [], [], [], []
    rej = {k: 0 for k in ("gap", "stop_lontano", "rr", "posizioni", "nuove_giorno", "classe",
                          "cluster", "rischio_aperto", "dimensione", "senza_barra")}
    n_cand = 0

    def close_pos(j, i, px, reason):
        nonlocal cash
        pos = positions.pop(j)
        net_exit = px * (1 - c)
        net_entry = pos["entry"] * (1 + c)
        cash += pos["shares"] * net_exit
        trades.append({
            "ticker": w.tickers[j], "asset_class": w.classes[j],
            "entry_date": pos["entry_date"], "exit_date": w.master[i],
            "entry": pos["entry"], "exit": px, "stop_iniziale": pos["stop0"],
            "shares": pos["shares"], "D": pos["D"],
            "R": (net_exit - net_entry) / pos["D"],
            "pnl": pos["shares"] * (net_exit - net_entry),
            "bars": i - pos["entry_i"], "motivo": reason,
        })

    def eod_update(pos, i, j):
        if cl[i, j] >= pos["entry"] + p.trail_activation_r * pos["D"]:
            pos["trail_active"] = True
        if (cl[i, j] < e50[i, j] - p.ema50_buffer_atr * atr[i, j]) or (e20[i, j] < e50[i, j]):
            pos["exit_at_open"] = True

    def corr(i, j, k):
        win = p.corr_window
        if i - win < 1:
            return 0.0
        a, b = ret[i - win:i, j], ret[i - win:i, k]
        sa, sb = a.std(), b.std()
        if sa == 0 or sb == 0:
            return 0.0
        return float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))

    for i in w.sim_idx:
        # 1) posizioni aperte: uscite durante la barra i
        for j in list(positions):
            if not valid[i, j]:
                continue
            pos = positions[j]
            if pos["exit_at_open"]:
                close_pos(j, i, op[i, j], "invalidazione")
                continue
            if pos["trail_active"] and up_prev[i, j] and np.isfinite(sar[i, j]) and sar[i, j] > pos["stop"]:
                pos["stop"] = sar[i, j]
            if op[i, j] <= pos["stop"]:
                close_pos(j, i, op[i, j], "stop_gap")
            elif lo[i, j] <= pos["stop"]:
                close_pos(j, i, pos["stop"], "stop")
            else:
                eod_update(pos, i, j)

        # 2) nuovi ingressi all'apertura (segnali della barra precedente)
        cands = np.flatnonzero(sig[i - 1])
        n_cand += len(cands)
        if len(cands):
            cands = cands[np.argsort(-rk[i - 1, cands], kind="stable")]
        new_count = 0
        for j in cands:
            if not valid[i, j] or j in positions:
                rej["senza_barra"] += 1
                continue
            if new_count >= p.max_new_per_day:
                rej["nuove_giorno"] += 1
                continue
            if len(positions) >= p.max_positions:
                rej["posizioni"] += 1
                continue
            entry = op[i, j]
            a = atr[i - 1, j]
            if not (np.isfinite(entry) and np.isfinite(a) and a > 0):
                rej["senza_barra"] += 1
                continue
            if entry > cl[i - 1, j] + p.max_gap_atr * a:
                rej["gap"] += 1
                continue
            stop = sl3[i - 1, j] - p.stop_buffer_atr * a
            D = entry - stop
            if D > p.stop_max_atr * a:
                rej["stop_lontano"] += 1
                continue
            if D < p.stop_min_atr * a:
                D = p.stop_min_atr * a
                stop = entry - D
            if p.use_rr_filter and (h60[i - 1, j] - entry) < p.rr_min * D:
                rej["rr"] += 1
                continue
            cls = w.classes[j]
            cap = p.class_caps.get(cls, 0)
            if sum(1 for k in positions if w.classes[k] == cls) >= cap:
                rej["classe"] += 1
                continue
            if sum(1 for k in positions if corr(i, j, k) > p.corr_threshold) >= p.max_per_cluster:
                rej["cluster"] += 1
                continue

            mv = sum(pos["shares"] * close_ff[i - 1, k] for k, pos in positions.items())
            equity_now = cash + mv
            shares = equity_now * p.risk_per_trade / D
            shares = min(shares, p.max_position_pct * equity_now / entry)
            shares = min(shares, max(0.0, p.max_exposure * equity_now - mv) / entry)
            shares = min(shares, cash / (entry * (1 + c)))
            if shares * entry < 0.005 * equity_now:
                rej["dimensione"] += 1
                continue
            open_risk = sum(pos["shares"] * pos["D"] for pos in positions.values())
            if open_risk + shares * D > p.max_open_risk * equity_now:
                rej["rischio_aperto"] += 1
                continue

            cash -= shares * entry * (1 + c)
            pos = {"entry": entry, "stop": stop, "stop0": stop, "D": D, "shares": shares,
                   "entry_i": i, "entry_date": w.master[i], "trail_active": False, "exit_at_open": False}
            positions[j] = pos
            new_count += 1
            # stessa barra: lo stop puo' essere colpito subito
            if lo[i, j] <= pos["stop"]:
                close_pos(j, i, pos["stop"], "stop")
            else:
                eod_update(pos, i, j)

        # 3) valorizzazione a fine giornata
        mv = sum(pos["shares"] * close_ff[i, k] for k, pos in positions.items())
        equity = cash + mv
        eq_idx.append(w.master[i])
        eq_val.append(equity)
        expo_val.append(mv / equity if equity > 0 else 0.0)
        npos_val.append(len(positions))

    if len(w.sim_idx):
        last = w.sim_idx[-1]
        for j in list(positions):
            close_pos(j, last, close_ff[last, j], "fine_dati")
        # l'ultima valorizzazione resta quella pre-chiusura (la chiusura a mercato non cambia l'equity oltre i costi)
        if eq_val:
            eq_val[-1] = cash

    return {
        "equity": pd.Series(eq_val, index=pd.DatetimeIndex(eq_idx), name="equity"),
        "exposure": pd.Series(expo_val, index=pd.DatetimeIndex(eq_idx)),
        "n_positions": pd.Series(npos_val, index=pd.DatetimeIndex(eq_idx)),
        "trades": pd.DataFrame(trades),
        "rejections": rej,
        "n_candidates": int(n_cand),
    }


def equal_weight_benchmark(w, capital=CAPITAL):
    """Acquisto e mantenimento equamente pesato (ribilanciato ogni giorno) degli strumenti eleggibili."""
    vals, idx = [], []
    eq = capital
    for i in w.sim_idx:
        mask = w.B["elig"][i - 1] & w.valid[i] & w.valid[i - 1]
        r = w.ret[i, mask]
        eq *= 1.0 + (float(r.mean()) if len(r) else 0.0)
        vals.append(eq)
        idx.append(w.master[i])
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name="equity_ew")


# ----------------------------------------------------------------------------
# Metriche
# ----------------------------------------------------------------------------

def _num(x):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return x
    return None if (math.isnan(x) or math.isinf(x)) else round(x, 4)


def equity_metrics(eq, capital=CAPITAL):
    if len(eq) < 2:
        return {}
    ret = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / capital - 1.0
    cagr = (eq.iloc[-1] / capital) ** (1.0 / years) - 1.0 if years > 0 and eq.iloc[-1] > 0 else None
    dd = (eq / eq.cummax() - 1.0).min()
    vol = ret.std() * math.sqrt(252)
    return {
        "rendimento_totale": _num(total), "cagr": _num(cagr), "drawdown_massimo": _num(dd),
        "volatilita_annua": _num(vol),
        "rendimento_su_volatilita": _num(ret.mean() * 252 / vol) if vol and vol > 0 else None,
        "anni": _num(years),
    }


def trade_metrics(tr):
    out = {"n_trade": int(len(tr))}
    if not len(tr):
        return out
    R = tr["R"]
    wins, losses = R[R > 0], R[R <= 0]
    out.update({
        "win_rate": _num((R > 0).mean()),
        "aspettativa_R": _num(R.mean()),
        "mediana_R": _num(R.median()),
        "R_medio_vincenti": _num(wins.mean()) if len(wins) else None,
        "R_medio_perdenti": _num(losses.mean()) if len(losses) else None,
        "profit_factor": _num(wins.sum() / abs(losses.sum())) if losses.sum() < 0 else None,
        "barre_medie": _num(tr["bars"].mean()),
    })
    yr = tr.groupby(pd.DatetimeIndex(tr["entry_date"]).year)["R"].agg(["count", "mean"])
    out["per_anno"] = {int(k): {"n": int(v["count"]), "R_medio": _num(v["mean"])} for k, v in yr.iterrows()}
    cl = tr.groupby("asset_class")["R"].agg(["count", "mean"])
    out["per_classe"] = {k: {"n": int(v["count"]), "R_medio": _num(v["mean"])} for k, v in cl.iterrows()}
    out["motivi_uscita"] = {k: int(v) for k, v in tr["motivo"].value_counts().items()}
    return out


def summarize(res, capital=CAPITAL):
    s = trade_metrics(res["trades"])
    s.update(equity_metrics(res["equity"], capital))
    s["esposizione_media"] = _num(res["exposure"].mean()) if len(res["exposure"]) else None
    s["posizioni_max"] = int(res["n_positions"].max()) if len(res["n_positions"]) else 0
    s["candidati_totali"] = res["n_candidates"]
    s["scartati_per_motivo"] = res["rejections"]
    return s


def acceptance(summary):
    """Criteri di accettazione pre-dichiarati (README sez. 6), valutati sul caso base."""
    n = summary.get("n_trade", 0)
    exp = summary.get("aspettativa_R")
    pf = summary.get("profit_factor")
    dd = summary.get("drawdown_massimo")
    checks = {
        "oltre_150_trade": bool(n > 150),
        "aspettativa_R_oltre_0.15": bool(exp is not None and exp > 0.15),
        "profit_factor_oltre_1.3": bool(pf is not None and pf > 1.3),
        "drawdown_sotto_15%": bool(dd is not None and dd > -0.15),
    }
    checks["tutti_soddisfatti"] = all(checks.values())
    return checks


# ----------------------------------------------------------------------------
# Calibrazione descrittiva
# ----------------------------------------------------------------------------

def calibrate(prepared, asset_class, p, start, end):
    rows = []
    for t, pr in prepared.items():
        d = pd.DatetimeIndex(pr["dates"])
        m = (d >= start) & (d <= end)
        if m.sum() < 30:
            continue
        rows.append({
            "ticker": t, "classe": asset_class.get(t, "?"), "barre": int(m.sum()),
            "atr_pct_mediano": float(np.nanmedian(pr["atr_pct"][m])),
            "turnover_mediano": float(np.nanmedian(pr["med_turnover"][m])),
            "quota_eleggibile": float(pr["elig"][m].mean()),
            "quota_trend_ok": float(pr["trend_ok"][m].mean()),
            "n_segnali": int(pr["signal"][m].sum()),
            "date_segnali": list(pd.DatetimeIndex(pr["dates"][m][pr["signal"][m]]).year),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {"errore": "nessuno strumento nel periodo"}
    pct = [5, 25, 50, 75, 95]

    def dist(s):
        return {f"p{q}": _num(np.nanpercentile(s, q)) for q in pct}

    years = pd.Series([y for ys in df["date_segnali"] for y in ys]).value_counts().sort_index()
    return {
        "periodo": [str(start.date()), str(end.date())],
        "strumenti": int(len(df)),
        "per_classe": {k: int(v) for k, v in df["classe"].value_counts().items()},
        "atr_pct_mediano": dist(df["atr_pct_mediano"]),
        "turnover_mediano_eur": dist(df["turnover_mediano"]),
        "atr_pct_per_classe": {k: dist(g["atr_pct_mediano"]) for k, g in df.groupby("classe")},
        "quota_strumenti_sopra_soglia_atr": _num((df["atr_pct_mediano"] >= p.min_atr_pct).mean()),
        "quota_strumenti_sopra_soglia_turnover": _num((df["turnover_mediano"] >= p.min_turnover).mean()),
        "quota_media_barre_eleggibili": _num(df["quota_eleggibile"].mean()),
        "quota_media_barre_trend_ok": _num(df["quota_trend_ok"].mean()),
        "segnali_totali": int(df["n_segnali"].sum()),
        "segnali_per_anno": {int(k): int(v) for k, v in years.items()},
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["calibrate", "explore", "test"], required=True)
    ap.add_argument("--oos-start", default="2023-01-01", help="inizio del periodo di test mai guardato")
    ap.add_argument("--prices", default=os.path.join(BASE_DIR, "data", "prices_snapshot.parquet"))
    ap.add_argument("--universe", default=os.path.join(BASE_DIR, "data", "universe_swing.json"))
    ap.add_argument("--outdir", default=os.path.join(BASE_DIR, "results"))
    ap.add_argument("--limit", type=int, default=None, help="solo debug: campione casuale di strumenti")
    ap.add_argument("--seeds", type=int, default=5, help="numero di simulazioni con entrate casuali")
    ap.add_argument("--force", action="store_true", help="consente di rigirare il test finale")
    args = ap.parse_args()

    oos = pd.Timestamp(args.oos_start)
    test_path = os.path.join(args.outdir, "test_result.json")
    if args.mode == "test" and os.path.exists(test_path) and not args.force:
        print(f"[ERRORE] {test_path} esiste gia': il test finale si esegue una sola volta (usa --force per rigirarlo).",
              file=sys.stderr)
        sys.exit(2)

    p = Params()
    prepared, asset_class = load_prepared(args.prices, args.universe, p, limit=args.limit)
    if not prepared:
        print("[ERRORE] nessuno strumento preparato", file=sys.stderr)
        sys.exit(1)
    first = min(pd.Timestamp(pr["dates"][0]) for pr in prepared.values())
    last = max(pd.Timestamp(pr["dates"][-1]) for pr in prepared.values())
    print(f"Dati dal {first.date()} al {last.date()}; inizio periodo di test: {oos.date()}")

    if args.mode == "calibrate":
        out = calibrate(prepared, asset_class, p, first, oos - pd.Timedelta(days=1))
        _write(os.path.join(args.outdir, "calibrazione.json"), out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.mode == "explore":
        start, end = first, oos - pd.Timedelta(days=1)
        w = build_world(prepared, asset_class, start, end)
        res = simulate(w, p)
        out = {"periodo": [str(start.date()), str(end.date())], "caso_base": summarize(res),
               "buy_hold_equamente_pesato": equity_metrics(equal_weight_benchmark(w))}
        _write(os.path.join(args.outdir, "explore_result.json"), out)
        if len(res["trades"]):
            res["trades"].to_csv(os.path.join(args.outdir, "explore_trades.csv"), index=False)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    # --- test finale: una sola esecuzione con tutte le varianti pre-dichiarate ---
    start, end = oos, last
    w = build_world(prepared, asset_class, start, end)
    out = {"periodo": [str(start.date()), str(end.date())], "generato": pd.Timestamp.now().isoformat(timespec="seconds")}
    base_res = None
    for name, rr, cost in [("rr_costi_0.20%", True, 0.001), ("senza_rr_costi_0.20%", False, 0.001),
                           ("rr_costi_0.40%", True, 0.002), ("senza_rr_costi_0.40%", False, 0.002)]:
        res = simulate(w, dataclasses.replace(p, use_rr_filter=rr, cost_per_side=cost))
        out[name] = summarize(res)
        if name == "rr_costi_0.20%":
            base_res = res
    out["criteri_accettazione_caso_base"] = acceptance(out["rr_costi_0.20%"])

    rnd = []
    for s in range(args.seeds):
        res = simulate(w, p, random_seed=s)
        rnd.append(summarize(res))
    keys = ["n_trade", "aspettativa_R", "profit_factor", "drawdown_massimo", "rendimento_totale"]
    out["entrata_casuale_stessi_filtri"] = {
        "simulazioni": len(rnd),
        **{f"{k}_medio": _num(np.nanmean([r.get(k) if r.get(k) is not None else np.nan for r in rnd])) for k in keys},
    }
    out["buy_hold_equamente_pesato"] = equity_metrics(equal_weight_benchmark(w))

    _write(test_path, out)
    if base_res is not None and len(base_res["trades"]):
        base_res["trades"].to_csv(os.path.join(args.outdir, "test_trades.csv"), index=False)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
