# BTC paper desk

A 24/7-style BTC **paper-trading** desk. No real orders are ever placed.

```bash
pip install -r requirements.txt
python run_desk.py                 # live Binance + Deribit data -> out/desk.html
python run_desk.py --profile       # which function slows the replay down
python run_desk.py --offline       # synthetic candles, no network
python webhook_receiver.py --port 8080 &
python run_desk.py --webhook http://localhost:8080/round
```

## Indonesian stocks (IDX)
```bash
pip install yfinance
python run_desk.py --market idx --ticker BBCA                       # 15m candles, 4 per round
python run_desk.py --market idx --ticker TLKM --interval 5m --bars 6 --rounds 20
python backtest_idx.py --intervals 5m,15m,60m                       # 12 LQ45 names -> out/idx_backtest.csv
```
IDX profile (`desk/markets.py`): fee 15 bps buy / 25 bps sell (rough retail estimate, override with
`--fee-buy-bps/--fee-sell-bps`), long-only, position in whole lots (default 1000 shares), rounds never straddle
the lunch break or the close, and Book A is flat at every session break. No option surface for IDX.
Yahoo data is delayed and limited (1m ~7 days, 5m/15m ~60 days, 60m ~2 years). `live.py` is crypto-only.

The timeframe is a parameter: `--interval` and `--bars` (candles per round).

## 24/7 live mode
```bash
python live.py                                  # wakes every 5 min, Ctrl-C to stop
python live.py --webhook http://localhost:8080/round
python live.py --once                           # single cycle (testing / cron)
```
Plays each newly finished 5-minute round, keeps state in `out/state.json` (Book A carries its position, P&L is
cumulative), appends to `out/live_rounds.jsonl`, and rewrites `out/desk.html` (auto-refreshes every 60 s).
After downtime it replays missed rounds (up to `--max-catchup`). Needs a machine that stays on.

## How it works
| Piece | File | What it does |
|---|---|---|
| Replay | `desk/strategy.py` | Last 60 one-minute candles as 12 five-minute rounds. Fair value = EMA20; enter at 1 ATR away, exit at fair value; cap 0.1 BTC; 2 bps fee per fill. |
| Two books | `desk/strategy.py` | **A** always on, carries positions. **B** gated and flat at each round boundary. |
| HOLD/FLAT | `desk/strategy.py::gate` | Before each round: autocorrelation of the last 15 bars + previous round P&L. Reason is plain English. |
| Webhook | `desk/webhook.py`, `webhook_receiver.py` | Every round (fills, fees, call, reason) is POSTed and appended to `out/rounds.jsonl`. |
| SVI | `desk/svi.py` | Raw-SVI fit per Deribit expiry; ATM 1M vol and 25-delta skew. Noisy fits (RMSE > 3 vol pts) are rejected. |
| Second opinion | `desk/second_opinion.py` | Walk-forward logistic regression on the same rounds. **Stand-in** for a nanochat model (see below). |
| Dashboard | `desk/dashboard.py` | Single HTML: candles + fair value + ATR band + fills, decision terminal, 3D vol surface. |

## Not done / honest notes
- **magic-trace** (Jane Street) needs Linux x86 with Intel PT, so `--profile` uses `cProfile` instead.
- **nanochat** trains a GPT from scratch on GPUs and is not wired in; `SecondOpinion` has a minimal interface so it can be swapped later.
- IDX backtest caveat: very cheap stocks (e.g. GOTO at Rp 23-50) are dominated by the Rp 1 tick size and flat candles; ignore them.
- One replay window is a demo, not a backtest. No claim of profitability; the first live run lost money on both books.
