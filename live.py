"""24/7 live paper desk. Wakes up every 5 minutes, plays each newly finished round, keeps state.

    python live.py                                   # runs forever, Ctrl-C to stop
    python live.py --webhook http://localhost:8080/round
    python live.py --once                            # one cycle, then exit (for testing / cron)

State lives in out/state.json, history in out/live_rounds.jsonl, dashboard in out/desk.html (auto-refreshes).
Book A carries its position and P&L across rounds; Book B is gated and flat at every round boundary.
P&L shown is cumulative since the first live round.
"""
import argparse
import datetime as dt
import json
import pathlib
import time

from desk import dashboard, data, second_opinion, strategy, svi, webhook

BARS = 5


def now():
    return dt.datetime.now(dt.timezone.utc)


def load_state(path):
    if path.exists():
        return json.loads(path.read_text())
    z = dict(pos=0.0, cash=0.0, fees=0.0)
    return dict(last_end=None, round_no=0, prev_pnl_b=0.0, A=dict(z), B=dict(z))


def save_state(path, st):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(path)


def history(path, n):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines()[-n:]]


def cycle(a, out, st, cache):
    d = strategy.add_indicators(data.fetch_candles())
    ends = [i for i in range(60, len(d)) if d.ts.iloc[i].minute % BARS == BARS - 1]
    pending = [i for i in ends if st["last_end"] is None or d.ts.iloc[i].isoformat() > st["last_end"]]
    pending = pending[-1:] if st["last_end"] is None else pending[-a.max_catchup:]
    so = second_opinion.SecondOpinion(d) if pending else None
    log = out / "live_rounds.jsonl"
    for e in pending:
        A = strategy.Book("A", a.cap, a.fee_bps, **st["A"])
        B = strategy.Book("B", a.cap, a.fee_bps, **st["B"])
        rd = strategy.play_round(d, e - BARS + 1, e, A, B, st["round_no"] + 1, st["prev_pnl_b"])
        rd["second_call"], rd["second_prob"] = so(e - BARS + 1)
        code = webhook.send_round(a.webhook, rd, log)
        st.update(last_end=rd["end"], round_no=rd["round"], prev_pnl_b=rd["pnl_b"],
                  A=dict(pos=A.pos, cash=A.cash, fees=A.fees), B=dict(pos=B.pos, cash=B.cash, fees=B.fees))
        save_state(out / "state.json", st)
        print(f"{now():%H:%M:%S} R{rd['round']:04d} {rd['start'][11:16]} {rd['call']:4s} ac={rd['autocorr']:+.2f} "
              f"A{rd['pnl_a']:+8.2f} B{rd['pnl_b']:+8.2f} | cum A{rd['equity_a']:+.2f} B{rd['equity_b']:+.2f} "
              f"| 2nd {rd['second_call']} | webhook {code}", flush=True)
    if not pending:
        print(f"{now():%H:%M:%S} no new round yet", flush=True)

    # dashboard: refresh the vol surface every N cycles, keep the old one if Deribit fails
    cache["n"] += 1
    if not a.no_surface and (cache["surface"] is None or cache["n"] % a.surface_every == 0):
        try:
            cache["surface"] = svi.fit_surface(data.fetch_option_chain()) or cache["surface"]
        except Exception as ex:
            print(f"[surface] skipped: {ex}")
    cand = d.iloc[-90:]
    rounds = [r for r in history(log, 12) if r["start"] >= cand.ts.iloc[0].isoformat()]
    if not rounds:
        return
    A_, B_ = st["A"], st["B"]
    last = float(d.close.iloc[-1])
    dashboard.render(dict(
        meta=dict(symbol="BTCUSDT", rounds=len(rounds), bars_per_round=BARS, fee_bps=a.fee_bps, cap=a.cap,
                  source="Binance BTCUSDT 1m (live)", generated=f"{now():%Y-%m-%d %H:%M UTC}"),
        candles=[dict(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, ema=r.ema, atr=r.atr)
                 for r in cand.itertuples()],
        rounds=rounds, fills=[f for r in rounds for f in r["fills"]], surface=cache["surface"], profile=[],
        summary=dict(last=last, pnl_a=A_["cash"] + A_["pos"] * last, pnl_b=B_["cash"] + B_["pos"] * last,
                     fees_a=A_["fees"], fees_b=B_["fees"], holds=sum(r["call"] == "HOLD" for r in rounds),
                     flats=sum(r["call"] == "FLAT" for r in rounds),
                     agree=sum(r["call"] == r.get("second_call") for r in rounds)),
    ), out / "desk.html", refresh=60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--webhook", default=None)
    ap.add_argument("--out", default="out")
    ap.add_argument("--cap", type=float, default=0.1)
    ap.add_argument("--fee-bps", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-surface", action="store_true")
    ap.add_argument("--surface-every", type=int, default=6, help="refresh vol surface every N cycles")
    ap.add_argument("--max-catchup", type=int, default=12, help="max missed rounds to replay after downtime")
    ap.add_argument("--delay", type=int, default=8, help="seconds to wait after each 5-min boundary")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(exist_ok=True)
    st, cache = load_state(out / "state.json"), dict(n=0, surface=None)
    print(f"live desk started; state: round {st['round_no']}, dashboard: {out / 'desk.html'}", flush=True)
    while True:
        try:
            cycle(a, out, st, cache)
        except Exception as ex:  # network hiccup: log and try again next cycle
            print(f"{now():%H:%M:%S} cycle failed: {ex}", flush=True)
        if a.once:
            return
        t = now()
        nxt = (t + dt.timedelta(minutes=BARS - t.minute % BARS)).replace(second=0, microsecond=0)
        time.sleep(max(1.0, (nxt - t).total_seconds() + a.delay))


if __name__ == "__main__":
    main()
