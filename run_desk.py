"""Build the BTC paper desk: replay -> two books -> HOLD/FLAT calls -> webhook -> SVI -> dashboard.

    python run_desk.py                      # live data, writes out/desk.html
    python run_desk.py --webhook http://localhost:8080/round
    python run_desk.py --offline            # synthetic candles, no network
    python run_desk.py --profile            # which function slows the replay down
"""
import argparse
import cProfile
import datetime as dt
import pathlib
import pstats

from desk import dashboard, data, second_opinion, strategy, svi, webhook


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--bars", type=int, default=5, help="1m bars per round")
    ap.add_argument("--cap", type=float, default=0.1)
    ap.add_argument("--fee-bps", type=float, default=2.0)
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--no-surface", action="store_true")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(exist_ok=True)

    source = "synthetic (offline)"
    if a.offline:
        df = data.synthetic_candles()
    else:
        try:
            df, source = data.fetch_candles(), "Binance BTCUSDT 1m"
        except Exception as e:
            print(f"[data] live candles failed ({e}); using synthetic data")
            df = data.synthetic_candles()
    d = strategy.add_indicators(df)

    prof = cProfile.Profile() if a.profile else None
    if prof:
        prof.enable()
    rounds, A, B = strategy.replay(d, a.rounds, a.bars, a.cap, a.fee_bps)
    so = second_opinion.SecondOpinion(d)
    for rd in rounds:
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
    for rd in rounds:
        webhook.send_round(a.webhook, rd, log)

    surface = None
    if not (a.offline or a.no_surface):
        try:
            surface = svi.fit_surface(data.fetch_option_chain())
        except Exception as e:
            print(f"[surface] skipped: {e}")

    cand = d.iloc[rounds[0]["start_idx"] - 20:]
    fills = [f for rd in rounds for f in rd["fills"]]
    payload = dict(
        meta=dict(symbol="BTCUSDT", rounds=a.rounds, bars_per_round=a.bars, fee_bps=a.fee_bps, cap=a.cap,
                  source=source, generated=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        candles=[dict(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, ema=r.ema, atr=r.atr)
                 for r in cand.itertuples()],
        rounds=rounds, fills=fills, surface=surface, profile=profile,
        summary=dict(last=float(d.close.iloc[-1]), pnl_a=rounds[-1]["equity_a"], pnl_b=rounds[-1]["equity_b"],
                     fees_a=A.fees, fees_b=B.fees, holds=sum(r["call"] == "HOLD" for r in rounds),
                     flats=sum(r["call"] == "FLAT" for r in rounds),
                     agree=sum(r["call"] == r["second_call"] for r in rounds)),
    )
    dashboard.render(payload, out / "desk.html")

    for r in rounds:
        print(f"R{r['round']:02d} {r['start'][11:16]} {r['call']:4s} ac={r['autocorr']:+.2f} "
              f"A{r['pnl_a']:+8.2f} B{r['pnl_b']:+8.2f} | 2nd {r['second_call']} {r['second_prob']:.0%}")
    s = payload["summary"]
    print(f"\nBook A {s['pnl_a']:+.2f} USD (fees {s['fees_a']:.2f}) | Book B {s['pnl_b']:+.2f} USD (fees {s['fees_b']:.2f})")
    if surface:
        print(f"1M ATM vol {surface['atm_1m']:.2f}% | 25d skew {surface['skew25_1m']:.2f} pts | "
              f"{surface['n_strikes']} strikes / {surface['n_expiries']} expiries fitted ({surface['n_rejected']} noisy expiries rejected)")
    for p in profile[:5]:
        print(f"  profile {p['func']:35s} cum {p['cum']:.4f}s")
    print(f"\nDashboard: {out / 'desk.html'}   Round log: {log}")


if __name__ == "__main__":
    main()
