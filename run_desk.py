"""Build a paper desk: replay -> two books -> HOLD/FLAT calls -> webhook -> (SVI) -> dashboard.

    python run_desk.py                                          # BTC, live data -> out/desk.html
    python run_desk.py --market idx --ticker BBCA.JK            # Indonesian stock, 15m candles
    python run_desk.py --market idx --ticker TLKM --interval 5m --bars 6 --rounds 20
    python run_desk.py --webhook http://localhost:8080/round
    python run_desk.py --offline                                # synthetic BTC candles, no network
    python run_desk.py --profile                                # which function slows the replay down
"""
import argparse
import cProfile
import datetime as dt
import pathlib
import pstats

from desk import dashboard, data, markets, second_opinion, strategy, svi, webhook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["crypto", "idx"], default="crypto")
    ap.add_argument("--ticker", default=None, help="BTCUSDT, or an IDX code such as BBCA / BBCA.JK")
    ap.add_argument("--interval", default=None, help="candle size: 1m 5m 15m 60m (default 1m crypto, 15m idx)")
    ap.add_argument("--period", default=None, help="IDX history window for Yahoo, e.g. 5d 60d")
    ap.add_argument("--rounds", type=int, default=12, help="rounds to replay (0 = all available)")
    ap.add_argument("--bars", type=int, default=None, help="candles per round")
    ap.add_argument("--cap", type=float, default=None, help="max position (BTC, or shares for IDX)")
    ap.add_argument("--fee-bps", type=float, default=None, help="same fee for buy and sell")
    ap.add_argument("--fee-buy-bps", type=float, default=None)
    ap.add_argument("--fee-sell-bps", type=float, default=None)
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--no-surface", action="store_true")
    a = ap.parse_args()

    m = markets.make(a.market, a.ticker)
    m.interval = a.interval or m.interval
    m.bars = a.bars or m.bars
    m.cap = a.cap or m.cap
    m.period = a.period
    if a.fee_bps is not None:
        m.fee_buy_bps = m.fee_sell_bps = a.fee_bps
    m.fee_buy_bps = a.fee_buy_bps if a.fee_buy_bps is not None else m.fee_buy_bps
    m.fee_sell_bps = a.fee_sell_bps if a.fee_sell_bps is not None else m.fee_sell_bps
    if m.name == "idx" and m.cap % 100:
        raise SystemExit("IDX position cap must be a multiple of 100 shares (1 lot)")
    im = data.interval_minutes(m.interval)
    out = pathlib.Path(a.out)
    out.mkdir(exist_ok=True)

    if m.name == "idx":
        df, source = data.fetch_idx_candles(m.symbol, m.interval, m.period), f"Yahoo Finance {m.symbol} {m.interval} (delayed)"
    elif a.offline:
        df, source = data.synthetic_candles(), "synthetic (offline)"
    else:
        try:
            df, source = data.fetch_candles(m.symbol, interval=m.interval), f"Binance {m.symbol} {m.interval}"
        except Exception as e:
            print(f"[data] live candles failed ({e}); using synthetic data")
            df, source = data.synthetic_candles(), "synthetic (offline)"
    d = strategy.add_indicators(df, interval_min=im)

    prof = cProfile.Profile() if a.profile else None
    if prof:
        prof.enable()
    rounds, A, B = strategy.replay(
        d, a.rounds, m.bars, cap=m.cap, fee_bps=m.fee_buy_bps, interval_min=im, fee_buy_bps=m.fee_buy_bps,
        fee_sell_bps=m.fee_sell_bps, long_only=m.long_only, flatten_breaks=m.flatten_breaks, unit=m.unit, ccy=m.ccy)
    if not rounds:
        raise SystemExit("not enough candles for even one round")

    # chart window: at most 400 candles; terminal/webhook only show rounds inside it
    first_bar = max(rounds[0]["start_idx"] - 20, len(d) - 400, 0)
    shown = [r for r in rounds if r["start_idx"] >= first_bar]
    so = second_opinion.SecondOpinion(d)
    for rd in shown:
        rd["second_call"], rd["second_prob"] = so(rd["start_idx"])
    if prof:
        prof.disable()
    profile = []
    if prof:
        st = pstats.Stats(prof)
        rows = sorted(st.stats.items(), key=lambda kv: kv[1][3], reverse=True)[:10]
        profile = [dict(func=f"{pathlib.Path(k[0]).name}:{k[2]}", calls=v[1], tot=v[2], cum=v[3]) for k, v in rows]

    log = out / "rounds.jsonl"
    log.unlink(missing_ok=True)
    for rd in shown:
        webhook.send_round(a.webhook, rd, log)

    surface = None
    if m.surface and not (a.offline or a.no_surface):
        try:
            surface = svi.fit_surface(data.fetch_option_chain())
        except Exception as e:
            print(f"[surface] skipped: {e}")

    cand = d.iloc[first_bar:]
    payload = dict(
        meta=dict(symbol=m.symbol, title=m.title, interval=m.interval, rounds=len(shown), bars_per_round=m.bars,
                  fee_label=m.fee_label, cap=m.cap, unit=m.unit, ccy_label=m.ccy_label, dec=m.dec,
                  hide_surface=not m.surface, session_breaks=m.flatten_breaks, source=source,
                  generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        candles=[dict(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, ema=r.ema, atr=r.atr)
                 for r in cand.itertuples()],
        rounds=shown, fills=[f for rd in shown for f in rd["fills"]], surface=surface, profile=profile,
        summary=dict(last=float(d.close.iloc[-1]), pnl_a=rounds[-1]["equity_a"], pnl_b=rounds[-1]["equity_b"],
                     fees_a=A.fees, fees_b=B.fees, holds=sum(r["call"] == "HOLD" for r in rounds),
                     flats=sum(r["call"] == "FLAT" for r in rounds),
                     agree=sum(r["call"] == r.get("second_call") for r in shown)),
    )
    dashboard.render(payload, out / "desk.html")

    for r in shown:
        print(f"R{r['round']:03d} {r['start'][5:16]} {r['call']:4s} ac={r['autocorr']:+.2f} "
              f"A{r['pnl_a']:+12,.2f} B{r['pnl_b']:+12,.2f} | 2nd {r['second_call']} {r['second_prob']:.0%}")
    s = payload["summary"]
    print(f"\n{m.symbol} {m.interval} x{m.bars} bars/round, {len(rounds)} rounds, fee {m.fee_label}")
    print(f"Book A {s['pnl_a']:+,.2f} {m.ccy_label} (fees {s['fees_a']:,.2f}) | "
          f"Book B {s['pnl_b']:+,.2f} {m.ccy_label} (fees {s['fees_b']:,.2f})")
    if surface:
        print(f"1M ATM vol {surface['atm_1m']:.2f}% | 25d skew {surface['skew25_1m']:.2f} pts | "
              f"{surface['n_strikes']} strikes / {surface['n_expiries']} expiries fitted "
              f"({surface['n_rejected']} noisy expiries rejected)")
    for p in profile[:5]:
        print(f"  profile {p['func']:35s} cum {p['cum']:.4f}s")
    print(f"\nDashboard: {out / 'desk.html'}   Round log: {log}")


if __name__ == "__main__":
    main()
