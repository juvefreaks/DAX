"""Second opinion on each round's HOLD/FLAT call.

A tiny walk-forward logistic regression in numpy, standing in for a small LLM. The interface is minimal
(`SecondOpinion(d)(round_start) -> (call, prob)`), so a small model trained with karpathy/nanochat can
replace `LogReg` later. Features use only bars before the round (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class LogReg:
    def fit(self, X, y, lr=0.1, steps=600, l2=1e-2):
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mu) / self.sd
        self.w, self.b = np.zeros(Z.shape[1]), 0.0
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(Z @ self.w + self.b)))
            self.w -= lr * (Z.T @ (p - y) / len(y) + l2 * self.w)
            self.b -= lr * float((p - y).mean())
        return self

    def proba(self, x):
        z = (np.asarray(x) - self.mu) / self.sd
        return float(1 / (1 + np.exp(-(z @ self.w + self.b))))


class SecondOpinion:
    def __init__(self, d: pd.DataFrame, horizon: int = 5, ac_n: int = 15):
        c, ema, atr, ret = (d[k].to_numpy() for k in ("close", "ema", "atr", "ret"))
        n = len(d)
        self.h = horizon
        self.z = (c - ema) / atr                                   # signed distance from fair, in ATR
        self.close = c
        ac = np.zeros(n)
        for i in range(ac_n + 1, n):                               # autocorr of the ac_n returns before bar i
            r = ret[i - ac_n:i]
            s = r.std()
            ac[i] = 0.0 if s == 0 else np.corrcoef(r[:-1], r[1:])[0, 1]
        mom = np.zeros(n)
        mom[5:] = (c[5:] / c[:-5] - 1) * 1e4
        self.ac, self.mom, self.atr_bps = ac, mom, atr / c * 1e4

    def _feat(self, i):
        j = i - 1
        return [abs(self.z[j]), self.ac[i], self.atr_bps[j], abs(self.mom[j])]

    def __call__(self, round_start: int):
        X, y = [], []
        for t in range(40, round_start - self.h - 1):
            z = self.z[t - 1]
            if abs(z) < 0.5:
                continue
            fwd = self.close[t - 1 + self.h] - self.close[t - 1]
            X.append(self._feat(t))
            y.append(1.0 if np.sign(fwd) == -np.sign(z) else 0.0)
        if len(y) < 50:
            return "HOLD", 0.5
        p = LogReg().fit(np.array(X), np.array(y)).proba(self._feat(round_start))
        return ("HOLD" if p >= 0.5 else "FLAT"), p
