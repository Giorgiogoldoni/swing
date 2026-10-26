#!/usr/bin/env python3
"""scan_signals.py

Calcola i segnali del canale Donchian (55/20 e 20/10) sull'universo swing e scrive data/signals.json,
letto da index.html.

Per ogni strumento, alla chiusura dell'ultima barra, lo stato è uno di:
  INGRESSO      la chiusura ha superato il massimo delle N barre precedenti: si compra all'apertura di domani
  USCITA        la chiusura ha rotto il minimo delle M barre precedenti con posizione aperta: si vende all'apertura di domani
  IN POSIZIONE  rottura avvenuta in precedenza e nessuna uscita da allora
  FUORI         nessuna posizione (strumento eleggibile)

Lo stato è quello teorico dello strumento, senza i vincoli di portafoglio (massimo 15 posizioni, ordinamento).

Uso:
  python scripts/scan_signals.py                              # scarica 2 anni di prezzi per ogni strumento (circa 1 ora)
  python scripts/scan_signals.py --from-snapshot data/prices_snapshot.parquet   # senza scaricare (sviluppo e prova)

Variabili d'ambiente: SCAN_PERIOD (default 2y), LIMIT_TICKERS (solo prova).
"""

import argparse
import datetime
import json
import os
import random
import sys
import time
import urllib.parse

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swing_lib import Params, prepare_ticker, is_excluded, DONCHIAN_PAIRS  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UNIVERSE_PATH = os.path.join(DATA_DIR, "universe_swing.json")
OUT_PATH = os.path.join(DATA_DIR, "signals.json")

SCAN_PERIOD = os.environ.get("SCAN_PERIOD", "2y")
SLEEP_BETWEEN_TICKERS = 0.3
RETRIES = 3
MIN_SUCCESS_RATIO = 0.70      # sotto questa soglia il run è guasto e non scrive nulla
MAX_STALE_DAYS = 6            # l'ultima data dei dati non può essere più vecchia di cosi' rispetto a oggi
STALE_ROW_DAYS = 5            # righe con ultimo dato più vecchio di cosi' (rispetto alla data dei dati) vengono scartate

TV_PREFIX = {"MI": "MIL", "DE": "XETR", "PA": "EURONEXT", "L": "LSE"}


def tradingview_url(ticker):
    """Link TradingView con prefisso di borsa (.MI -> MIL:, .DE -> XETR:, .PA -> EURONEXT:, .L -> LSE:)."""
    if "." in ticker:
        sym, suf = ticker.rsplit(".", 1)
        prefix = TV_PREFIX.get(suf.upper())
        symbol = f"{prefix}:{sym}" if prefix else sym
    else:
        symbol = ticker
    return "https://www.tradingview.com/chart/?symbol=" + urllib.parse.quote(symbol, safe="")


def position_state(sig, exit_flag):
    """Macchina a stati senza vincoli di portafoglio, a fine ultima barra.

    Ritorna (stato, indice della barra del segnale di ingresso o -1).
    """
    n = len(sig)
    pos, entry, exit_idx = False, -1, -1
    for t in range(n):
        if pos and exit_flag[t]:
            pos, exit_idx = False, t
        elif (not pos) and sig[t]:
            pos, entry = True, t
    last = n - 1
    if pos:
        return ("INGRESSO" if entry == last else "IN POSIZIONE"), entry
    return ("USCITA" if exit_idx == last else "FUORI"), -1


def _num(x, nd=4):
    if x is None:
        return None
    x = float(x)
    return None if (np.isnan(x) or np.isinf(x)) else round(x, nd)


def build_rows(prepared, meta, n_in, n_out):
    rows = []
    key_sig, key_exit = f"don_{n_in}_{n_out}_sig", f"don_{n_in}_{n_out}_exit"
    for t, pr in prepared.items():
        n = len(pr["close"])
        last = n - 1
        stato, entry = position_state(pr[key_sig], pr[key_exit])
        if stato == "FUORI" and not pr["elig"][last]:
            continue                                  # non eleggibile e senza posizione: non interessa
        close, prev = pr["close"][last], pr["close"][last - 1]
        # Livelli: per gli ingressi/uscite del giorno quello appena rotto (barre PRECEDENTI, oggi escluso);
        # per gli altri stati quello valido per la seduta di domani (ultime barre, oggi incluso).
        if stato == "INGRESSO":
            hi_n = float(np.max(pr["high"][max(0, n - 1 - n_in):n - 1]))
        else:
            hi_n = float(np.max(pr["high"][max(0, n - n_in):n]))
        if stato == "USCITA":
            lo_m = float(np.min(pr["low"][max(0, n - 1 - n_out):n - 1]))
        else:
            lo_m = float(np.min(pr["low"][max(0, n - n_out):n]))
        info = meta.get(t, {})
        row = {
            "ticker": t,
            "tv": tradingview_url(t),
            "nome": info.get("name", ""),
            "isin": info.get("isin", ""),
            "classe": info.get("asset_class", ""),
            "tipo": info.get("product_type", ""),
            "piazza": t.rsplit(".", 1)[-1] if "." in t else "",
            "stato": stato,
            "chiusura": _num(close),
            "var_1g": _num((close / prev - 1.0) * 100, 2),
            "livello_rottura": _num(hi_n),
            "dist_rottura": _num((hi_n / close - 1.0) * 100, 2),
            "livello_uscita": _num(lo_m),
            "dist_uscita": _num((close / lo_m - 1.0) * 100, 2),
            "ranking": _num(pr["rank_key"][last], 2) if np.isfinite(pr["rank_key"][last]) else None,
            "ultimo_dato": str(pd.Timestamp(pr["dates"][last]).date()),
            "giorni": None, "ingresso_prezzo": None, "ingresso_data": None, "rend_da_ingresso": None,
        }
        if stato == "IN POSIZIONE" and entry + 1 <= last:
            ent_price = float(pr["open"][entry + 1])
            row["giorni"] = int(last - entry)
            row["ingresso_prezzo"] = _num(ent_price)
            row["ingresso_data"] = str(pd.Timestamp(pr["dates"][entry + 1]).date())
            row["rend_da_ingresso"] = _num((close / ent_price - 1.0) * 100, 2)
        rows.append(row)
    return rows


def fetch_one(ticker):
    import yfinance as yf
    last_err = "nessun dato"
    for attempt in range(RETRIES):
        try:
            hist = yf.Ticker(ticker).history(period=SCAN_PERIOD, interval="1d", auto_adjust=True, timeout=25)
            if hist is not None and not hist.empty:
                df = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
                df.columns = ["open", "high", "low", "close", "volume"]
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                df.index = idx.normalize()
                df = df[~df.index.duplicated(keep="last")].dropna(subset=["close"])
                if len(df):
                    return df, None
            last_err = "nessun dato"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(1.5 * (attempt + 1))
    return None, last_err


def load_universe(path):
    with open(path, encoding="utf-8") as f:
        uni = json.load(f)
    inst = uni["instruments"] if isinstance(uni, dict) else uni
    return [i for i in inst if not is_excluded(i.get("name"), i.get("product_type"), i.get("ticker_yf"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-snapshot", default=None, help="legge i prezzi da uno snapshot parquet invece di scaricarli")
    ap.add_argument("--universe", default=UNIVERSE_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--limit", type=int, default=int(os.environ.get("LIMIT_TICKERS") or 0) or None)
    args = ap.parse_args()

    p = Params()
    instruments = load_universe(args.universe)
    meta = {i["ticker_yf"]: i for i in instruments}
    tickers = sorted(meta)
    if args.limit:
        random.seed(0)
        tickers = sorted(random.sample(tickers, min(args.limit, len(tickers))))
    print(f"Universo: {len(tickers)} strumenti")

    prepared, errors = {}, []
    t0 = time.time()
    if args.from_snapshot:
        prices = pd.read_parquet(args.from_snapshot)
        prices = prices[prices["ticker"].isin(tickers)]
        for t, g in prices.groupby("ticker", observed=True):
            pr = prepare_ticker(g.drop(columns="ticker"), p)
            if pr is None:
                errors.append({"ticker": t, "motivo": "storia troppo corta"})
            else:
                prepared[t] = pr
    else:
        for n, t in enumerate(tickers, 1):
            df, err = fetch_one(t)
            if df is None:
                errors.append({"ticker": t, "motivo": err})
            else:
                pr = prepare_ticker(df, p)
                if pr is None:
                    errors.append({"ticker": t, "motivo": "storia troppo corta"})
                else:
                    prepared[t] = pr
            if n % 200 == 0:
                print(f"  {n}/{len(tickers)} ok={len(prepared)} errori={len(errors)} ({time.time() - t0:.0f}s)")
            time.sleep(SLEEP_BETWEEN_TICKERS)

    ratio = len(prepared) / len(tickers) if tickers else 0.0
    print(f"Strumenti con dati validi: {len(prepared)}/{len(tickers)} ({ratio:.0%}) in {time.time() - t0:.0f}s")
    if ratio < MIN_SUCCESS_RATIO and not args.limit:
        print(f"[ERRORE] successo sotto il {MIN_SUCCESS_RATIO:.0%}: probabile rate limit di Yahoo. data/signals.json NON aggiornato.",
              file=sys.stderr)
        sys.exit(1)
    if not prepared:
        print("[ERRORE] nessuno strumento valido", file=sys.stderr)
        sys.exit(1)

    last_dates = {t: pd.Timestamp(pr["dates"][-1]) for t, pr in prepared.items()}
    data_date = max(last_dates.values())
    today = pd.Timestamp(datetime.datetime.now(datetime.timezone.utc).date())
    if not args.from_snapshot and (today - data_date).days > MAX_STALE_DAYS:
        print(f"[ERRORE] dati troppo vecchi: ultima data {data_date.date()}, oggi {today.date()}. data/signals.json NON aggiornato.",
              file=sys.stderr)
        sys.exit(1)
    fresh = {t: pr for t, pr in prepared.items() if (data_date - last_dates[t]).days <= STALE_ROW_DAYS}
    print(f"Data dei dati: {data_date.date()}; strumenti con ultimo dato recente: {len(fresh)} (scartati {len(prepared) - len(fresh)} non aggiornati)")

    out = {
        "generato": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "data_dati": str(data_date.date()),
        "strumenti_analizzati": len(fresh),
        "errori": len(errors),
        "avvertenza": ("Sistema in osservazione, non validato: sui dati 2016-2022 il Donchian 55/20 ha reso quanto "
                       "l'acquisto e mantenimento di un paniere equamente pesato. Non è consulenza finanziaria."),
        "coppie": {},
    }
    for n_in, n_out in DONCHIAN_PAIRS:
        rows = build_rows(fresh, meta, n_in, n_out)
        counts = {s: sum(1 for r in rows if r["stato"] == s) for s in ("INGRESSO", "USCITA", "IN POSIZIONE", "FUORI")}
        out["coppie"][f"{n_in}/{n_out}"] = {
            "ingresso_barre": n_in, "uscita_barre": n_out, "contatori": counts, "righe": rows,
        }
        print(f"  Donchian {n_in}/{n_out}: {counts}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Scritto {args.out} ({os.path.getsize(args.out) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
