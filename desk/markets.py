"""Market profiles: costs, sizing and session rules for each asset class."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Market:
    name: str
    symbol: str
    title: str
    unit: str            # what qty is counted in
    ccy: str             # prefix used in plain-English reasons
    ccy_label: str       # label on the dashboard
    fee_buy_bps: float
    fee_sell_bps: float
    cap: float           # max position, in `unit`
    long_only: bool
    flatten_breaks: bool  # flatten book A at every session break (close, lunch) instead of carrying
    surface: bool         # has a Deribit-style option surface
    dec: int              # decimals for money on the dashboard
    interval: str
    bars: int             # bars per round
    period: str | None = None

    @property
    def fee_label(self) -> str:
        b, s = self.fee_buy_bps, self.fee_sell_bps
        return f"{b:g} bps" if b == s else f"{b:g}/{s:g} bps"


def crypto(symbol: str = "BTCUSDT") -> Market:
    return Market("crypto", symbol, "BTC PAPER DESK", "BTC", "$", "USD", 2.0, 2.0, 0.1,
                  long_only=False, flatten_breaks=False, surface=True, dec=2, interval="1m", bars=5)


# Rough IDX retail costs (broker dependent): ~0.15% buy, ~0.25% sell incl. levy and sales tax.
def idx(ticker: str) -> Market:
    sym = ticker.upper()
    if not sym.endswith(".JK"):
        sym += ".JK"
    return Market("idx", sym, f"IDX PAPER DESK · {sym[:-3]}", "shares", "Rp", "IDR", 15.0, 25.0, 1000,
                  long_only=True, flatten_breaks=True, surface=False, dec=0, interval="15m", bars=4)


def make(market: str, ticker: str | None) -> Market:
    if market == "idx":
        return idx(ticker or "BBCA.JK")
    return crypto(ticker or "BTCUSDT")
