"""Market data: BTC 1m candles (Binance public mirror) and the Deribit BTC option chain."""
from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd
import requests

BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
DERIBIT_SUMMARY = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"


def interval_minutes(interval: str) -> int:
    """'1m' -> 1, '15m' -> 15, '1h' -> 60."""
    n = int(interval[:-1])
    return n * 60 if interval.endswith("h") else n


def fetch_candles(symbol: str = "BTCUSDT", limit: int = 1000, interval: str = "1m") -> pd.DataFrame:
    """Latest `limit` candles; the still-forming last candle is dropped."""
    mins = interval_minutes(interval)
    r = requests.get(
        BINANCE_KLINES,
        params={"symbol": symbol, "interval": f"{mins // 60}h" if mins % 60 == 0 else f"{mins}m", "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(
        [row[:6] for row in rows], columns=["ts", "open", "high", "low", "close", "volume"]
    )
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df.iloc[:-1].reset_index(drop=True)


# Yahoo Finance history limits per interval
_YF_PERIOD = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d", "60m": "730d"}


def fetch_idx_candles(ticker: str, interval: str = "15m", period: str | None = None) -> pd.DataFrame:
    """IDX stock candles from Yahoo Finance (delayed, trading hours only), timestamps in Asia/Jakarta."""
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install yfinance") from e
    mins = interval_minutes(interval)
    yf_int = f"{mins}m"
    df = yf.download(ticker, period=period or _YF_PERIOD.get(yf_int, "60d"), interval=yf_int,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no data for {ticker} {interval} (Yahoo rate limit or wrong ticker?)")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns=lambda c: str(c).lower())
    df = df.rename(columns={df.columns[0]: "ts"})
    df = df[["ts", "open", "high", "low", "close", "volume"]].dropna()
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Jakarta")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[-1] + pd.Timedelta(minutes=mins) > pd.Timestamp.now(tz="UTC"):
        df = df.iloc[:-1]  # bar still forming
    return df.reset_index(drop=True)


def synthetic_candles(n: int = 1000, seed: int = 7, start_price: float = 60000.0) -> pd.DataFrame:
    """Offline fallback: mean-reverting random walk. Only for testing the plumbing."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp.now(tz="UTC").floor("5min")
    ts = pd.date_range(end=end, periods=n, freq="1min", tz="UTC")
    fair, px, rows = start_price, start_price, []
    for _ in range(n):
        fair += rng.normal(0, 8)
        o = px
        c = o + 0.15 * (fair - o) + rng.normal(0, 22)
        h = max(o, c) + abs(rng.normal(0, 8))
        l = min(o, c) - abs(rng.normal(0, 8))
        rows.append((o, h, l, c, abs(rng.normal(10, 3))))
        px = c
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "ts", ts)
    return df


_NAME = re.compile(r"^BTC-(\d{1,2}[A-Z]{3}\d{2})-(\d+)-([CP])$")


def fetch_option_chain() -> pd.DataFrame:
    """Deribit BTC options with mark IV. Columns: expiry, T (years), K, F, cp, iv (decimal)."""
    r = requests.get(DERIBIT_SUMMARY, params={"currency": "BTC", "kind": "option"}, timeout=30)
    r.raise_for_status()
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for it in r.json()["result"]:
        m = _NAME.match(it["instrument_name"])
        iv = it.get("mark_iv")
        if not m or not iv or iv <= 0:
            continue
        exp = dt.datetime.strptime(m.group(1), "%d%b%y").replace(
            hour=8, tzinfo=dt.timezone.utc
        )  # Deribit expires 08:00 UTC
        T = (exp - now).total_seconds() / (365 * 86400)
        if T < 1.0 / 365:  # skip < 1 day: the smile is dominated by noise
            continue
        rows.append(
            dict(expiry=exp, T=T, K=float(m.group(2)), F=float(it["underlying_price"]),
                 cp=m.group(3), iv=iv / 100.0)
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # keep out-of-the-money quotes only (puts below forward, calls above): the liquid side
    otm = ((df.cp == "P") & (df.K < df.F)) | ((df.cp == "C") & (df.K >= df.F))
    return df[otm].reset_index(drop=True)
