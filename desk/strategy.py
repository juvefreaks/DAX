"""Two paper books on the same candles.

Rule (mean reversion around fair value):
  fair value = 20-period EMA. Enter when price is >= 1 ATR away from it (buy below, sell above),
  exit when price comes back to fair value. Position cap and fees come from the market profile.

Book A: always on, carries positions across rounds (flattened at session breaks if the market has them).
Book B: gated. Before each round a HOLD/FLAT call is made from the last 15 bars' autocorrelation
        and the previous round's P&L. FLAT = sit the round out. Book B is flat at every round boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame, ema_span: int = 20, atr_n: int = 14, interval_min: int = 1) -> pd.DataFrame:
    d = df.copy()
    d["ema"] = d["close"].ewm(span=ema_span, adjust=False).mean()
    pc = d["close"].shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / atr_n, adjust=False).mean()
    d["ret"] = np.log(d["close"]).diff()
    # an overnight / lunch gap is not a tradable return: don't let it pollute the autocorrelation
    d.loc[d["ts"].diff() > pd.Timedelta(minutes=interval_min * 1.5), "ret"] = 0.0
    return d


def autocorr(d: pd.DataFrame, i: int, n: int = 15) -> float:
    """Lag-1 autocorrelation of the n returns before bar i."""
    r = d["ret"].iloc[max(i - n, 1):i].values
    if len(r) < 5 or np.std(r[:-1]) == 0 or np.std(r[1:]) == 0:
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
    fee_buy_bps: float | None = None
    fee_sell_bps: float | None = None
    long_only: bool = False
    unit: str = "BTC"

    def equity(self, price: float) -> float:
        return self.cash + self.pos * price

    def trade(self, qty: float, price: float, ts, rnd: int, reason: str):
        bps = self.fee_buy_bps if qty > 0 else self.fee_sell_bps
        bps = self.fee_bps if bps is None else bps
        fee = abs(qty) * price * bps / 1e4
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
        q = f"{self.cap:g} {self.unit}"
        if abs(self.pos) < 1e-12:
            if edge <= -1:
                return self.trade(self.cap, c, ts, rnd,
                    f"price {c:,.0f} is {abs(edge):.2f} ATR below fair value {ema:,.0f}: buy {q}, expecting a bounce")
            if edge >= 1 and not self.long_only:
                return self.trade(-self.cap, c, ts, rnd,
                    f"price {c:,.0f} is {edge:.2f} ATR above fair value {ema:,.0f}: sell {q}, expecting a pullback")
        elif self.pos > 0 and c >= ema:
            return self.trade(-self.pos, c, ts, rnd, f"price {c:,.0f} is back at fair value {ema:,.0f}: close long")
        elif self.pos < 0 and c <= ema:
            return self.trade(-self.pos, c, ts, rnd, f"price {c:,.0f} is back at fair value {ema:,.0f}: close short")
        return None

    def flatten(self, price: float, ts, rnd: int, reason: str):
        if abs(self.pos) > 1e-12:
            return self.trade(-self.pos, price, ts, rnd, reason)
        return None


def gate(ac: float, prev_pnl: float, ac_limit: float = 0.20, loss_limit: float = 3.0, ccy: str = "$"):
    """HOLD keeps the rule running, FLAT sits the round out. Returns (call, plain-English reason)."""
    if ac > ac_limit:
        return "FLAT", (f"autocorrelation of the last 15 bars is {ac:+.2f} (> {ac_limit:+.2f}): price is trending, "
                        f"and a fair-value reversion rule tends to lose in trends")
    if prev_pnl < -loss_limit and ac > 0:
        return "FLAT", (f"previous round lost {ccy}{-prev_pnl:,.2f} and autocorrelation is {ac:+.2f} (not mean-reverting): "
                        f"sit this round out")
    return "HOLD", (f"autocorrelation {ac:+.2f} is not trending and previous round P&L was {ccy}{prev_pnl:+,.2f}: "
                    f"keep the rule running")


def play_round(d, s, e, A, B, rnd, prev_pnl_b, ac_limit=0.20, loss_bps=5.0, flatten_a=False, ccy="$"):
    """Play one round over bars s..e (inclusive). Mutates books A and B, returns the round record.
    The loss limit that trips the gate is `loss_bps` of the position's notional."""
    ac = autocorr(d, s)
    loss_limit = A.cap * float(d.close.iloc[s - 1]) * loss_bps / 1e4
    call, why = gate(ac, prev_pnl_b, ac_limit, loss_limit, ccy)
    eqA0, eqB0 = A.equity(d.close.iloc[s - 1]), B.equity(d.close.iloc[s - 1])
    nfA, nfB = len(A.fills), len(B.fills)
    for i in range(s, e + 1):
        row, ts = d.iloc[i], d.ts.iloc[i]
        A.step(row, ts, rnd)
        if call == "HOLD":
            B.step(row, ts, rnd)
    last = d.iloc[e]
    B.flatten(last.close, d.ts.iloc[e], rnd, "round boundary: Book B goes flat")
    if flatten_a:
        A.flatten(last.close, d.ts.iloc[e], rnd, "session break: Book A goes flat")
    pnlA, pnlB = A.equity(last.close) - eqA0, B.equity(last.close) - eqB0
    return dict(
        round=rnd, start=d.ts.iloc[s].isoformat(), end=d.ts.iloc[e].isoformat(),
        start_idx=s, end_idx=e, call=call, reason=why, autocorr=ac, prev_pnl_b=prev_pnl_b,
        pnl_a=pnlA, pnl_b=pnlB, pos_a=A.pos, pos_b=B.pos,
        equity_a=A.equity(last.close), equity_b=B.equity(last.close),
        fills=A.fills[nfA:] + B.fills[nfB:],
    )


def make_rounds(d: pd.DataFrame, bars_per_round: int, interval_min: int = 1, n_rounds: int | None = None,
                min_start: int = 60):
    """Cut the data into rounds that never straddle a session break (overnight, lunch, data gap).
    Rounds are aligned to the END of each session. Returns (start, end, first_in_session, last_in_session)."""
    seg = (d["ts"].diff() > pd.Timedelta(minutes=interval_min * 1.5)).cumsum().to_numpy()
    out = []
    for sid in np.unique(seg):
        idx = np.where(seg == sid)[0]
        a, e, chunks = idx[0], idx[-1], []
        while e - bars_per_round + 1 >= a:
            chunks.append((e - bars_per_round + 1, e))
            e -= bars_per_round
        chunks.reverse()
        for k, (s, e) in enumerate(chunks):
            if s >= min_start:
                out.append((s, e, k == 0, k == len(chunks) - 1))
    out.sort()
    return out if not n_rounds else out[-n_rounds:]


def replay(d: pd.DataFrame, n_rounds: int | None = 12, bars_per_round: int = 5, cap: float = 0.1,
           fee_bps: float = 2.0, ac_limit: float = 0.20, loss_bps: float = 5.0, *, interval_min: int = 1,
           fee_buy_bps=None, fee_sell_bps=None, long_only=False, flatten_breaks=False,
           unit="BTC", ccy="$", min_start: int = 60):
    """Replay the last n_rounds rounds (None/0 = all). Returns (rounds, bookA, bookB)."""
    kw = dict(cap=cap, fee_bps=fee_bps, fee_buy_bps=fee_buy_bps, fee_sell_bps=fee_sell_bps,
              long_only=long_only, unit=unit)
    A, B = Book("A", **kw), Book("B", **kw)
    rounds, prev = [], 0.0
    for r, (s, e, first, last) in enumerate(make_rounds(d, bars_per_round, interval_min, n_rounds, min_start), 1):
        if first:
            prev = 0.0  # new session: yesterday's last round says nothing about today
        rd = play_round(d, s, e, A, B, r, prev, ac_limit, loss_bps, flatten_a=flatten_breaks and last, ccy=ccy)
        prev = rd["pnl_b"]
        rounds.append(rd)
    return rounds, A, B
