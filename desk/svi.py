"""Raw-SVI fit per expiry:  w(k) = a + b*(rho*(k-m) + sqrt((k-m)^2 + sigma^2)),  w = IV^2 * T."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares
from scipy.stats import norm


def svi_w(k, p):
    a, b, rho, m, s = p
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + s**2))


def fit_svi(k: np.ndarray, w: np.ndarray):
    lo = [-1.0, 1e-4, -0.999, -1.5, 1e-3]
    hi = [5.0, 5.0, 0.999, 1.5, 2.0]
    best = None
    for rho0 in (-0.5, 0.0, 0.5):
        for s0 in (0.05, 0.3):
            x0 = [max(float(w.min()) * 0.5, 1e-6), 0.1, rho0, 0.0, s0]
            res = least_squares(lambda p: svi_w(k, p) - w, x0, bounds=(lo, hi))
            if best is None or res.cost < best.cost:
                best = res
    return best.x


def iv_from_w(w, T):
    return np.sqrt(np.maximum(w, 1e-12) / T)


def _delta_k(p, T, target_delta: float, call: bool) -> float:
    """Log-moneyness k where the BS delta equals the target (using the SVI smile)."""
    def f(k):
        s = float(iv_from_w(svi_w(k, p), T))
        d1 = -k / (s * np.sqrt(T)) + s * np.sqrt(T) / 2
        return (norm.cdf(d1) - target_delta) if call else (norm.cdf(d1) - (1 - target_delta))
    a, b = (0.0, 1.5) if call else (-1.5, 0.0)
    return brentq(f, a, b)


def skew25(p, T) -> float:
    """25-delta skew in vol points: IV(25d call) - IV(25d put)."""
    try:
        kc, kp = _delta_k(p, T, 0.25, True), _delta_k(p, T, 0.25, False)
    except ValueError:
        return float("nan")
    return float(iv_from_w(svi_w(kc, p), T) - iv_from_w(svi_w(kp, p), T)) * 100


def fit_surface(chain: pd.DataFrame, grid_n: int = 41, min_points: int = 6, max_rmse: float = 3.0):
    """Fit SVI for every expiry; return params table, IV grid and headline stats."""
    fits, kgrid = [], np.linspace(-0.35, 0.35, grid_n)
    for exp, g in chain.groupby("expiry"):
        if len(g) < min_points:
            continue
        T, F = float(g["T"].iloc[0]), float(g["F"].iloc[0])
        k = np.log(g["K"].values / F)
        w = g["iv"].values ** 2 * T
        p = fit_svi(k, w)
        rmse = float(np.sqrt(np.mean((iv_from_w(svi_w(k, p), T) - g["iv"].values) ** 2))) * 100
        fits.append(dict(expiry=exp, T=T, F=F, n=len(g), params=p, rmse_vol_pts=rmse,
                         atm_iv=float(iv_from_w(svi_w(0.0, p), T)), skew25=skew25(p, T),
                         # only draw where there is data (+2%): no extrapolated spikes on short expiries
                         iv_grid=np.where((kgrid >= k.min() - 0.02) & (kgrid <= k.max() + 0.02),
                                          iv_from_w(svi_w(kgrid, p), T) * 100, np.nan).tolist()))
    n_rejected = sum(f["rmse_vol_pts"] > max_rmse for f in fits)   # noisy very-short expiries spike the surface
    fits = sorted((f for f in fits if f["rmse_vol_pts"] <= max_rmse), key=lambda d: d["T"])
    if not fits:
        return None
    Ts = np.array([f["T"] for f in fits])
    w_atm = np.array([f["atm_iv"] ** 2 * f["T"] for f in fits])
    t1m = 30 / 365
    atm_1m = float(np.sqrt(np.interp(t1m, Ts, w_atm) / t1m)) * 100
    sk = np.array([f["skew25"] for f in fits])
    ok = ~np.isnan(sk)
    skew_1m = float(np.interp(t1m, Ts[ok], sk[ok])) if ok.any() else float("nan")
    return dict(
        fits=fits, k_grid=kgrid.tolist(), atm_1m=atm_1m, skew25_1m=skew_1m,
        n_strikes=int(len(chain)), n_expiries=len(fits), n_rejected=int(n_rejected),
    )
