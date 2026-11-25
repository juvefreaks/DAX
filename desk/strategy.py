"""Two paper books on the same candles.

Rule (mean reversion around fair value):
  fair value = 20-period EMA. Enter when price is >= 1 ATR away from it (buy below, sell above),
  position cap 0.1 BTC, 2 bps fee per fill, exit when price comes back to fair value.

Book A: always on, carries positions across round boundaries.
Book B: gated. Before each round a HOLD/FLAT call is made from the last 15 bars' autocorrelation
        and the previous round's P&L. FLAT = sit the round out. Book B is flat at every round boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame, ema_span: int = 20, atr_n: int = 14) -> pd.DataFrame:
    d = df.copy()
    d["ema"] = d["close"].ewm(span=ema_span, adjust=False).mean()
    pc = d["close"].shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / atr_n, adjust=False).mean()
    d["ret"] = np.log(d["close"]).diff()
    return d


def autocorr(d: pd.DataFrame, i: int, n: int = 15) -> float:
    """Lag-1 autocorrelation of the n one-minute returns before bar i."""
    r = d["ret"].iloc[max(i - n, 1):i].values
    if len(r) < 5 or np.std(r) == 0:
        return 0.0
    c = np.corrcoef(r[:-1], r[1:])[0, 1]
    return 0.0 if np.isnan(c) else float(c)


@dataclass
class Book:
    name: str
    cap: float = 0.1
    fee_bps: float = 2.0
    pos: float = 0.0
    cash: float = 0.0
    fees: float = 0.0
    fills: list = field(default_factory=list)

    def equity(self, price: float) -> float:
        return self.cash + self.pos * price

    def trade(self, qty: float, price: float, ts, rnd: int, reason: str):
        fee = abs(qty) * price * self.fee_bps / 1e4
        self.cash -= qty * price + fee
        self.pos += qty
        self.fees += fee
        f = dict(ts=ts.isoformat(), book=self.name, round=rnd, side="BUY" if qty > 0 else "SELL",
                 qty=abs(qty), price=price, fee=fee, reason=reason)
        self.fills.append(f)
        return f

    def step(self, row, ts, rnd: int):
        """Apply the fair-value rule on one finished bar."""
        c, ema, atr = row.close, row.ema, row.atr
        edge = (c - ema) / atr if atr > 0 else 0.0
        if abs(self.pos) < 1e-12:
            if edge <= -1:
                return self.trade(self.cap, c, ts, rnd,
                    f"price {c:,.0f} is {abs(edge):.2f} ATR below fair value {ema:,.0f}: buy {self.cap} BTC, expecting a bounce")
            if edge >= 1:
                return self.trade(-self.cap, c, ts, rnd,
                    f"price {c:,.0f} is {edge:.2f} ATR above fair value {ema:,.0f}: sell {self.cap} BTC, expecting a pullback")
        elif self.pos > 0 and c >= ema:
            return self.trade(-self.pos, c, ts, rnd, f"price {c:,.0f} is back at fair value {ema:,.0f}: close long")
        elif self.pos < 0 and c <= ema:
            return self.trade(-self.pos, c, ts, rnd, f"price {c:,.0f} is back at fair value {ema:,.0f}: close short")
        return None

    def flatten(self, price: float, ts, rnd: int, reason: str):
        if abs(self.pos) > 1e-12:
            return self.trade(-self.pos, price, ts, rnd, reason)
        return None


def gate(ac: float, prev_pnl: float, ac_limit: float = 0.20, loss_limit: float = 3.0):
    """HOLD keeps the rule running, FLAT sits the round out. Returns (call, plain-English reason)."""
    if ac > ac_limit:
        return "FLAT", (f"autocorrelation of the last 15 bars is {ac:+.2f} (> {ac_limit:+.2f}): price is trending, "
                        f"and a fair-value reversion rule tends to lose in trends")
    if prev_pnl < -loss_limit and ac > 0:
        return "FLAT", (f"previous round lost ${-prev_pnl:,.2f} and autocorrelation is {ac:+.2f} (not mean-reverting): "
                        f"sit this round out")
    return "HOLD", (f"autocorrelation {ac:+.2f} is not trending and previous round P&L was ${prev_pnl:+,.2f}: "
                    f"keep the rule running")


def play_round(d, s, e, A, B, rnd, prev_pnl_b, ac_limit=0.20, loss_limit=3.0):
    """Play one round over bars s..e (inclusive). Mutates books A and B, returns the round record."""
    ac = autocorr(d, s)
    call, why = gate(ac, prev_pnl_b, ac_limit, loss_limit)
    eqA0, eqB0 = A.equity(d.close.iloc[s - 1]), B.equity(d.close.iloc[s - 1])
    nfA, nfB = len(A.fills), len(B.fills)
    for i in range(s, e + 1):
        row, ts = d.iloc[i], d.ts.iloc[i]
        A.step(row, ts, rnd)
        if call == "HOLD":
            B.step(row, ts, rnd)
    last = d.iloc[e]
    B.flatten(last.close, d.ts.iloc[e], rnd, "round boundary: Book B goes flat")
    pnlA, pnlB = A.equity(last.close) - eqA0, B.equity(last.close) - eqB0
    return dict(
        round=rnd, start=d.ts.iloc[s].isoformat(), end=d.ts.iloc[e].isoformat(),
        start_idx=s, end_idx=e, call=call, reason=why, autocorr=ac, prev_pnl_b=prev_pnl_b,
        pnl_a=pnlA, pnl_b=pnlB, pos_a=A.pos, pos_b=B.pos,
        equity_a=A.equity(last.close), equity_b=B.equity(last.close),
        fills=A.fills[nfA:] + B.fills[nfB:],
    )


def replay(d: pd.DataFrame, n_rounds: int = 12, bars_per_round: int = 5, cap: float = 0.1,
           fee_bps: float = 2.0, ac_limit: float = 0.20, loss_limit: float = 3.0):
    """Replay the last n_rounds*bars_per_round bars as rounds. Returns (rounds, bookA, bookB)."""
    start0 = len(d) - n_rounds * bars_per_round
    A, B = Book("A", cap, fee_bps), Book("B", cap, fee_bps)
    rounds, prev = [], 0.0
    for r in range(n_rounds):
        s = start0 + r * bars_per_round
        rd = play_round(d, s, s + bars_per_round - 1, A, B, r + 1, prev, ac_limit, loss_limit)
        prev = rd["pnl_b"]
        rounds.append(rd)
    return rounds, A, B
