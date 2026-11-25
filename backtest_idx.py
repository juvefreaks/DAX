"""Compare the desk across Indonesian stocks and timeframes (replay over all history Yahoo gives).

    python backtest_idx.py                                   # 12 LQ45 names, 15m
    python backtest_idx.py --intervals 5m,15m,60m --bars 4
    python backtest_idx.py --tickers BBCA,TLKM --fee-buy-bps 10 --fee-sell-bps 20

Return % = net P&L / (position cap x average price), i.e. return on one full-size position.
"""
import argparse
import pathlib
import time

import pandas as pd

from desk import data, markets, strategy

DEFAULT = "BBCA,BBRI,BMRI,BBNI,TLKM,ASII,UNVR,ICBP,INDF,ANTM,ADRO,GOTO"


def run_one(ticker, interval, bars, cap, fee_buy, fee_sell):
    m = markets.idx(ticker)
    im = data.interval_minutes(interval)
    df = data.fetch_idx_candles(m.symbol, interval)
    d = strategy.add_indicators(df, interval_min=im)
    rounds, A, B = strategy.replay(
        d, 0, bars, cap=cap, fee_bps=fee_buy, interval_min=im, fee_buy_bps=fee_buy, fee_sell_bps=fee_sell,
        long_only=True, flatten_breaks=True, unit="shares", ccy="Rp")
    if not rounds:
        return None
    notional = cap * float(d.close.mean())
    row = dict(ticker=m.symbol[:-3], interval=interval, bars=len(d),
               days=d.ts.dt.date.nunique(), rounds=len(rounds),
               hold_pct=100 * sum(r["call"] == "HOLD" for r in rounds) / len(rounds))
    for k, b in (("A", A), ("B", B)):
        net = b.cash  # flat at the end
        row[f"{k}_trades"] = len(b.fills) // 2
        row[f"{k}_net"] = net
        row[f"{k}_gross"] = net + b.fees
        row[f"{k}_fees"] = b.fees
        row[f"{k}_ret_pct"] = 100 * net / notional
        row[f"{k}_gross_pct"] = 100 * (net + b.fees) / notional
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=DEFAULT)
    ap.add_argument("--intervals", default="15m")
    ap.add_argument("--bars", type=int, default=4, help="candles per round")
    ap.add_argument("--cap", type=float, default=1000, help="shares (multiple of 100)")
    ap.add_argument("--fee-buy-bps", type=float, default=15.0)
    ap.add_argument("--fee-sell-bps", type=float, default=25.0)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    rows = []
    for itv in a.intervals.split(","):
        for t in a.tickers.split(","):
            try:
                r = run_one(t.strip(), itv.strip(), a.bars, a.cap, a.fee_buy_bps, a.fee_sell_bps)
            except Exception as e:
                print(f"skip {t} {itv}: {e}")
                continue
            if r:
                rows.append(r)
            time.sleep(0.5)  # be gentle with Yahoo
    if not rows:
        raise SystemExit("no results")
    df = pd.DataFrame(rows)
    pathlib.Path(a.out).mkdir(exist_ok=True)
    df.to_csv(pathlib.Path(a.out) / "idx_backtest.csv", index=False)
    show = df[["ticker", "interval", "days", "rounds", "hold_pct", "A_trades", "A_gross_pct", "A_ret_pct",
               "B_trades", "B_gross_pct", "B_ret_pct"]]
    pd.options.display.float_format = "{:,.2f}".format
    print(show.to_string(index=False))
    print("\nPer interval (mean across tickers; gross = before fees, ret = after fees, % of one full position)")
    g = df.groupby("interval")[["A_gross_pct", "A_ret_pct", "B_gross_pct", "B_ret_pct"]].mean()
    g["A_positive"] = df.groupby("interval")["A_ret_pct"].apply(lambda s: f"{(s > 0).sum()}/{len(s)}")
    g["B_positive"] = df.groupby("interval")["B_ret_pct"].apply(lambda s: f"{(s > 0).sum()}/{len(s)}")
    print(g.to_string())
    print(f"\nSaved {pathlib.Path(a.out) / 'idx_backtest.csv'}")


if __name__ == "__main__":
    main()
