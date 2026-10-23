#!/usr/bin/env python3
"""fetch_prices.py

Scarica lo storico giornaliero (prezzi aggiustati) dell'universo swing e lo salva come snapshot unico.

Universo: letto da core (data/tickers_universe.json), filtrato come da specifica:
  product_type ETF/ETP/ETC, non a leva, esclusi cripto, short e monetari. Obbligazionari inclusi.

Output:
  data/prices_snapshot.parquet   storico OHLCV (NON si committa: il workflow lo pubblica nella Release "prices")
  data/universe_swing.json       strumenti scaricati con successo + anagrafica (si committa, e' piccolo)
  data/prices_meta.json          statistiche del download (si committa)

Perche' uno snapshot completo e non un aggiornamento incrementale: i prezzi aggiustati per i dividendi
cambiano retroattivamente a ogni distribuzione; accodare barre nuove a barre vecchie scaricate in momenti
diversi creerebbe salti artificiali. Lo snapshot si rigenera per intero.

Variabili d'ambiente:
  HISTORY_PERIOD   periodo yfinance (default 10y)
  LIMIT_TICKERS    solo test: campione casuale di N strumenti
"""

import json
import os
import random
import sys
import time
import urllib.request
import datetime

import pandas as pd
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UNIVERSE_URL = "https://raw.githubusercontent.com/Giorgiogoldoni/core/main/data/tickers_universe.json"

HISTORY_PERIOD = os.environ.get("HISTORY_PERIOD", "10y")
SLEEP_BETWEEN_TICKERS = 0.3
RETRIES = 3
MIN_SUCCESS_RATIO = 0.70      # sotto questa soglia il run e' considerato guasto e non scrive nulla

ALLOWED_TYPES = {"ETF", "ETP", "ETC"}
EXCLUDED_CLASSES = {"crypto", "leva_short"}


def fetch_universe():
    with urllib.request.urlopen(UNIVERSE_URL, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("instruments", [])


def select_universe(instruments):
    out = []
    for i in instruments:
        if i.get("product_type") not in ALLOWED_TYPES:
            continue
        if i.get("is_leveraged"):
            continue
        if i.get("asset_class") in EXCLUDED_CLASSES:
            continue
        if i.get("is_money_market"):
            continue
        if not i.get("ticker_yf"):
            continue
        out.append(i)
    return out


def fetch_one(ticker):
    """Ritorna (DataFrame, None) oppure (None, motivo)."""
    last_err = "nessun dato"
    for attempt in range(RETRIES):
        try:
            hist = yf.Ticker(ticker).history(period=HISTORY_PERIOD, interval="1d", auto_adjust=True, timeout=25)
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
        except Exception as e:  # rate limit, timeout, simboli non piu' validi
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(1.5 * (attempt + 1))
    return None, last_err


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    instruments = select_universe(fetch_universe())
    print(f"Universo swing: {len(instruments)} strumenti dopo i filtri")

    limit = os.environ.get("LIMIT_TICKERS", "").strip()
    if limit:
        random.seed(0)
        instruments = random.sample(instruments, min(int(limit), len(instruments)))
        print(f"LIMIT_TICKERS={limit}: campione di {len(instruments)} strumenti")

    frames, ok_instruments, errors = [], [], []
    t0 = time.time()
    for n, inst in enumerate(instruments, 1):
        t = inst["ticker_yf"]
        df, err = fetch_one(t)
        if df is None:
            errors.append({"ticker": t, "motivo": err})
        else:
            df = df.reset_index().rename(columns={"index": "date"})
            if "date" not in df.columns:
                df = df.rename(columns={df.columns[0]: "date"})
            df.insert(0, "ticker", t)
            frames.append(df)
            ok_instruments.append(inst)
        if n % 100 == 0:
            print(f"  {n}/{len(instruments)}  ok={len(ok_instruments)}  errori={len(errors)}  ({time.time() - t0:.0f}s)")
        time.sleep(SLEEP_BETWEEN_TICKERS)

    ratio = len(ok_instruments) / len(instruments) if instruments else 0.0
    print(f"Scaricati {len(ok_instruments)}/{len(instruments)} ({ratio:.0%}) in {time.time() - t0:.0f}s")
    if ratio < MIN_SUCCESS_RATIO:
        print(f"[ERRORE] successo sotto il {MIN_SUCCESS_RATIO:.0%}: probabile rate limit di Yahoo. Nessun file scritto.",
              file=sys.stderr)
        sys.exit(1)

    prices = pd.concat(frames, ignore_index=True)
    prices["ticker"] = prices["ticker"].astype("category")
    prices.to_parquet(os.path.join(DATA_DIR, "prices_snapshot.parquet"), compression="zstd", index=False)

    with open(os.path.join(DATA_DIR, "universe_swing.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "count": len(ok_instruments), "instruments": ok_instruments}, f, ensure_ascii=False)

    meta = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "history_period": HISTORY_PERIOD,
        "instruments_selected": len(instruments),
        "ok": len(ok_instruments),
        "errors": len(errors),
        "error_details": errors[:500],
        "rows": int(len(prices)),
        "first_date": str(prices["date"].min().date()),
        "last_date": str(prices["date"].max().date()),
        "note": "Prezzi aggiustati (auto_adjust=True). Snapshot pubblicato nella Release 'prices'.",
    }
    with open(os.path.join(DATA_DIR, "prices_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"Righe: {meta['rows']}  periodo {meta['first_date']} -> {meta['last_date']}")


if __name__ == "__main__":
    main()
