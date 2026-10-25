#!/usr/bin/env python3
"""swing_lib.py

Indicatori, parametri e generazione dei segnali del sistema swing.
Implementa la specifica numerica approvata (vedi README.md, sezioni 1-4).

Convenzioni:
- tutti gli array sono allineati alle barre giornaliere di UNO strumento, ordinate per data;
- ogni indicatore alla barra t usa solo dati fino a t (nessun look-ahead);
- il segnale alla barra t si genera sulla chiusura di t; l'ingresso e' all'apertura della barra successiva
  (lo gestisce backtest.py).
"""

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    # --- Universo e filtri (README sez. 1) ---
    min_bars: int = 150
    min_atr_pct: float = 0.003            # ATR14 / prezzo
    min_turnover: float = 50_000.0        # mediana 20gg di prezzo x volume, in EUR
    min_vol_days_frac: float = 0.80       # quota di giorni con volume > 0 negli ultimi 20
    anomaly_hi: float = 2.5
    anomaly_lo: float = 0.4
    anomaly_window: int = 250

    # --- Trend (sez. 2) ---
    ema_fast: int = 20
    ema_slow: int = 50
    slope_fast_bars: int = 5
    slope_slow_bars: int = 10
    kama_er: int = 10
    kama_fast: int = 2
    kama_slow: int = 30
    kama_lookback: int = 10
    kama_min_atr: float = 1.0
    atr_period: int = 14

    # --- Pullback e conferma (sez. 3) ---
    touch_bars: int = 3
    ema50_buffer_atr: float = 0.5
    body_min: float = 0.50
    upper_shadow_max: float = 0.25
    cooldown: int = 10

    # --- Esecuzione, stop, uscita (sez. 4) ---
    max_gap_atr: float = 1.0
    stop_buffer_atr: float = 0.25
    stop_min_atr: float = 1.0
    stop_max_atr: float = 3.0
    rr_min: float = 2.0
    rr_lookback: int = 60
    use_rr_filter: bool = True
    trail_activation_r: float = 1.0

    # --- Portafoglio (sez. 5) ---
    risk_per_trade: float = 0.005
    max_open_risk: float = 0.08
    max_positions: int = 15
    max_new_per_day: int = 3
    max_position_pct: float = 0.10
    max_exposure: float = 1.0
    class_caps: dict = field(default_factory=lambda: {"equity": 8, "bond": 5, "commodity": 3})
    corr_threshold: float = 0.85
    corr_window: int = 60
    max_per_cluster: int = 2
    rank_lookback: int = 60

    # --- Costi (sez. 6): per lato, quindi 0,10% x 2 = 0,20% a giro completo ---
    cost_per_side: float = 0.001


# Coppie (N ingresso, M uscita) del canale Donchian, parametri classici delle Tartarughe: non si ottimizzano.
DONCHIAN_PAIRS = [(55, 20), (20, 10)]


# ----------------------------------------------------------------------------
# Esclusione dei prodotti cripto
# ----------------------------------------------------------------------------
# La classificazione per asset class di core non distingue i prodotti cripto quotati su Xetra
# (risultano 'equity'): si esclude per emittente/nome e per tipo di prodotto.
_CRYPTO_NAME_RE = re.compile(
    r"21\s?shares|bitwise|coinshares|virtune|valour|hashdex|nxtassets|altarius|leon\.sec|issuance\.swiss"
    r"|dig\.ass|\bdda\b|bitcoin|\bbtc\b|ethereum|\bether\b|\bcrypto|solana|ripple|\bxrp\b|cardano"
    r"|polkadot|chainlink|avalanche|dogecoin|litecoin|hyperliquid|celestia|aptos",
    re.I,
)


def is_crypto_like(name, product_type, ticker):
    """True se lo strumento va escluso perche' cripto (o ETP Xetra, che in pratica sono prodotti cripto/strutturati)."""
    n = name or ""
    if product_type == "ETF" and re.search(r"blockchain", n, re.I):
        return False                    # ETF su aziende blockchain: sono azioni, non cripto
    if _CRYPTO_NAME_RE.search(n):
        return True
    return product_type == "ETP" and str(ticker).endswith(".DE")


# La classificazione di core lascia passare alcuni prodotti inversi o a leva (is_leveraged = falso):
# si escludono per nome. Pattern volutamente stretti: 'short duration', 'daily hedged' e simili restano dentro.
_LEVERAGED_NAME_RE = re.compile(
    r"(?<![\w.])-?[2-9]x(?![\w])|[1-9]xdaily short|\bdaily short\b|\bshort ?dax\b|\bsht daily\b"
    r"|\binv(erse)?\b.*\b(day|daily|dail)\b",
    re.I,
)


def is_leveraged_like(name):
    """True se il nome indica un prodotto a leva o inverso."""
    return bool(_LEVERAGED_NAME_RE.search(name or ""))


def is_excluded(name, product_type, ticker):
    """Esclusione di universo non coperta dai flag di core: cripto, ETP Xetra, prodotti inversi o a leva."""
    return is_crypto_like(name, product_type, ticker) or is_leveraged_like(name)


# ----------------------------------------------------------------------------
# Indicatori
# ----------------------------------------------------------------------------

def ema(x, span):
    return pd.Series(np.asarray(x, dtype=float)).ewm(span=span, adjust=False).mean().to_numpy()


def true_range(high, low, close):
    prev_close = np.concatenate(([close[0]], close[:-1]))
    return np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])


def atr_wilder(high, low, close, period=14):
    tr = true_range(high, low, close)
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def kama(close, er_n=10, fast=2, slow=30):
    """Kaufman Adaptive Moving Average. Le prime er_n-1 barre sono NaN."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if n <= er_n:
        return out
    fsc = 2.0 / (fast + 1)
    ssc = 2.0 / (slow + 1)
    diff = np.abs(np.diff(close, prepend=close[0]))
    vol = pd.Series(diff).rolling(er_n).sum().to_numpy()
    chg = np.abs(close - pd.Series(close).shift(er_n).to_numpy())
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(vol > 0, chg / vol, 0.0)
    sc = (er * (fsc - ssc) + ssc) ** 2
    out[er_n - 1] = close[er_n - 1]
    for i in range(er_n, n):
        out[i] = out[i - 1] + sc[i] * (close[i] - out[i - 1])
    return out


def awesome_oscillator(high, low, fast=5, slow=34):
    hl2 = (np.asarray(high, dtype=float) + np.asarray(low, dtype=float)) / 2.0
    s = pd.Series(hl2)
    return (s.rolling(fast).mean() - s.rolling(slow).mean()).to_numpy()


def parabolic_sar(high, low, af0=0.02, af_step=0.02, af_max=0.2, with_state=False):
    """SAR di Wilder, senza look-ahead.

    Restituisce due array allineati alle barre:
      sar_pre[i]  = livello SAR applicabile alla barra i, calcolato con i dati fino a i-1
      up_prev[i]  = True se alla fine della barra i-1 il trend SAR era rialzista

    Per usare il SAR come trailing stop alla barra i: se up_prev[i] e' True, lo stop e' sar_pre[i].
    Con with_state=True restituisce anche up_after[i] = stato del trend SAR a fine barra i
    (True = pallino sotto il prezzo, False = pallino sopra il prezzo): serve per l'uscita 'al primo pallino'.
    """
    n = len(high)
    sar_pre = np.full(n, np.nan)
    up_prev = np.zeros(n, dtype=bool)
    up_after = np.zeros(n, dtype=bool)
    if n < 3:
        return (sar_pre, up_prev, up_after) if with_state else (sar_pre, up_prev)
    up = high[1] > high[0]
    up_after[0] = up
    if up:
        sar, ep = low[0], high[0]
    else:
        sar, ep = high[0], low[0]
    af = af0
    for i in range(1, n):
        sar_i = sar + af * (ep - sar)
        if up:
            sar_i = min(sar_i, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
        else:
            sar_i = max(sar_i, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
        sar_pre[i] = sar_i
        up_prev[i] = up
        if up:
            if low[i] < sar_i:          # inversione al ribasso
                up = False
                sar, ep, af = ep, low[i], af0
            else:
                sar = sar_i
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_step, af_max)
        else:
            if high[i] > sar_i:         # inversione al rialzo
                up = True
                sar, ep, af = ep, high[i], af0
            else:
                sar = sar_i
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_step, af_max)
        up_after[i] = up
    return (sar_pre, up_prev, up_after) if with_state else (sar_pre, up_prev)


def _shift(x, k):
    """x[t-k] con NaN all'inizio."""
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[: len(x) - k]
    return out


def _apply_cooldown(raw, cooldown):
    """Un solo segnale ogni `cooldown` barre: tiene il primo, scarta i successivi entro la finestra."""
    out = np.zeros(len(raw), dtype=bool)
    last = -10 ** 9
    for i in np.flatnonzero(raw):
        if i - last >= cooldown:
            out[i] = True
            last = i
    return out


# ----------------------------------------------------------------------------
# Preparazione di uno strumento
# ----------------------------------------------------------------------------

def prepare_ticker(df, p=None):
    """Calcola indicatori, filtri e segnale per uno strumento.

    df: DataFrame con colonne open, high, low, close, volume e indice di date (o colonna 'date'),
        prezzi aggiustati, ordinato per data.
    Ritorna un dict di array numpy (allineati alle barre) oppure None se la storia e' troppo corta.
    """
    p = p or Params()
    if "date" in df.columns:
        df = df.set_index("date")
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.dropna(subset=["open", "high", "low", "close"])
    # barre non valide (prezzi <= 0, massimo < minimo): tipico di prezzi aggiustati andati fuori scala
    bad = (df[["open", "high", "low", "close"]] <= 0).any(axis=1) | (df["high"] < df["low"])
    if len(df) == 0 or bad.mean() > 0.02:
        return None                     # troppi dati non validi: strumento scartato
    df = df[~bad]
    n = len(df)
    if n < p.min_bars + 5:
        return None

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].fillna(0).to_numpy(float) if "volume" in df.columns else np.zeros(n)

    ema20 = ema(c, p.ema_fast)
    ema50 = ema(c, p.ema_slow)
    atr = atr_wilder(h, l, c, p.atr_period)
    kma = kama(c, p.kama_er, p.kama_fast, p.kama_slow)
    ao = awesome_oscillator(h, l)
    sar_pre, up_prev, up_after = parabolic_sar(h, l, with_state=True)

    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = atr / c

    # --- Universo e filtri (sez. 1) ---
    idx = np.arange(n)
    hist_ok = idx >= (p.min_bars - 1)
    vol_ok_atr = atr_pct >= p.min_atr_pct
    turnover = c * v
    med_turn = pd.Series(turnover).rolling(20).median().to_numpy()
    vol_days = pd.Series((v > 0).astype(float)).rolling(20).mean().to_numpy()
    liq_ok = (med_turn >= p.min_turnover) & (vol_days >= p.min_vol_days_frac)
    ratio = c / _shift(c, 1)
    anomaly = ((ratio > p.anomaly_hi) | (ratio < p.anomaly_lo)).astype(float)
    anomaly_recent = pd.Series(anomaly).rolling(p.anomaly_window, min_periods=1).max().to_numpy() > 0
    elig = hist_ok & vol_ok_atr & liq_ok & (~anomaly_recent)
    elig = np.nan_to_num(elig.astype(float)).astype(bool)

    # --- Trend (sez. 2) ---
    trend = (
        (ema20 > ema50)
        & (ema20 > _shift(ema20, p.slope_fast_bars))
        & (ema50 > _shift(ema50, p.slope_slow_bars))
        & (c > ema50)
        & ((kma - _shift(kma, p.kama_lookback)) >= p.kama_min_atr * atr)
    )
    trend = np.nan_to_num(trend.astype(float)).astype(bool)

    # --- Pullback e conferma (sez. 3) ---
    touch = pd.Series((l <= ema20).astype(float)).rolling(p.touch_bars).max().to_numpy() == 1
    eff_below = pd.Series((c < ema50 - p.ema50_buffer_atr * atr).astype(float)).rolling(p.touch_bars).max().to_numpy() == 1
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        body_ok = (c > o) & (rng > 0) & ((c - o) >= p.body_min * rng)
        shadow_ok = (h - c) <= p.upper_shadow_max * rng
    ao_up = ao > _shift(ao, 1)
    confirm = (c > ema20) & body_ok & shadow_ok & ao_up
    pullback = touch & (~eff_below) & confirm
    pullback = np.nan_to_num(pullback.astype(float)).astype(bool)

    # --- Sistema semplice: il prezzo taglia la EMA20 al rialzo, sopra EMA50, con SAR rialzista ---
    cross20 = (c > ema20) & (_shift(c, 1) <= _shift(ema20, 1))
    pool_simple = elig & (c > ema50) & up_after
    sig_simple = np.nan_to_num((cross20 & pool_simple).astype(float)).astype(bool)
    pool_simple = np.nan_to_num(pool_simple.astype(float)).astype(bool)

    # --- Canale Donchian: massimo/minimo delle N/M barre PRECEDENTI (la barra corrente e' esclusa) ---
    pool_trend = np.nan_to_num((elig & (c > ema50)).astype(float)).astype(bool)
    donchian = {}
    for n_in, n_out in DONCHIAN_PAIRS:
        hh = pd.Series(h).rolling(n_in).max().shift(1).to_numpy()
        ll = pd.Series(l).rolling(n_out).min().shift(1).to_numpy()
        donchian[f"don_{n_in}_{n_out}_sig"] = np.nan_to_num((elig & (c > hh)).astype(float)).astype(bool)
        donchian[f"don_{n_in}_{n_out}_exit"] = np.nan_to_num((c < ll).astype(float)).astype(bool)

    trend_ok = trend & elig
    raw_signal = trend_ok & pullback
    signal = _apply_cooldown(raw_signal, p.cooldown)

    # --- Grandezze usate da stop, R/R e ordinamento (sez. 4-5) ---
    swing_low3 = pd.Series(l).rolling(3, min_periods=1).min().to_numpy()
    high_n = pd.Series(h).rolling(p.rr_lookback, min_periods=1).max().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ret_n = c / _shift(c, p.rank_lookback) - 1.0
        rank_key = ret_n / atr_pct
    rank_key = np.where(np.isfinite(rank_key), rank_key, -np.inf)

    return {
        "dates": df.index.to_numpy("datetime64[ns]"),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "ema20": ema20, "ema50": ema50, "atr": atr, "kama": kma, "ao": ao,
        "sar_pre": sar_pre, "up_prev": up_prev, "up_after": up_after,
        "sig_simple": sig_simple, "pool_simple": pool_simple, "pool_trend": pool_trend,
        **donchian,
        "atr_pct": atr_pct, "med_turnover": med_turn,
        "elig": elig, "trend": trend, "trend_ok": trend_ok,
        "pullback": pullback, "signal": signal,
        "swing_low3": swing_low3, "high60": high_n, "rank_key": rank_key,
    }
