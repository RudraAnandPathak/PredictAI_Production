"""Forecasting on one regular series. Pure numpy: no data access, no randomness, no generated code.

Six transparent methods are compared by a rolling-origin backtest (each fold forecasts rows the method never saw). The method with the lowest mean MASE is used.
Intervals come from the backtest errors of that method. They are an estimate: with few folds they can be too narrow, so the coverage actually observed in the backtest is reported beside them.
A forecast is a projection of the pattern in the past. It is not a prediction of events, and it says nothing about causes."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

SEASON = {"day": 7, "week": 52, "month": 12, "quarter": 4, "year": 1}
MAX_FILL_SHARE = 0.30
MIN_POINTS = 12
MAX_FOLDS = 5
MIN_INTERVAL_ERRORS = 8
Z = {"80": 1.2816, "95": 1.96}
LABELS = {"naive": "Repeat the last value", "seasonal_naive": "Repeat the last season", "drift": "Straight line between first and last", "ses": "Exponential smoothing (level only)",
          "holt_damped": "Damped trend smoothing", "seasonal_trend": "Linear trend plus seasonal pattern"}
ORDER = ["naive", "seasonal_naive", "drift", "ses", "holt_damped", "seasonal_trend"]


def f(x: Any, d: int = 5) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, d) if math.isfinite(v) else None


def fill_gaps(y: np.ndarray) -> Tuple[np.ndarray, int]:
    """Empty periods are filled by straight-line interpolation (and counted). Too many gaps refuse the forecast rather than invent a series."""
    y = np.asarray(y, float)
    bad = ~np.isfinite(y)
    if bad.mean() > MAX_FILL_SHARE:
        raise ValueError("TOO_MANY_GAPS")
    if bad.any():
        idx = np.arange(len(y))
        y = y.copy()
        y[bad] = np.interp(idx[bad], idx[~bad], y[~bad])
    return y, int(bad.sum())


def seasonality(y: np.ndarray, m: int) -> Dict[str, Any]:
    n = len(y)
    if m < 2 or n < 2 * m + 2:
        return {"checked": False, "reason": "The series is too short to test for a repeating pattern of this length." if m >= 2 else "This period has no sub-year cycle to test."}
    d = np.diff(y)
    d = d - d.mean()
    denom = float(np.sum(d ** 2))
    if denom == 0:
        return {"checked": True, "detected": False, "strength": 0.0, "season_length": m}
    r = float(np.sum(d[m:] * d[:-m]) / denom)
    threshold = 2 / math.sqrt(len(d))
    return {"checked": True, "detected": bool(r > threshold), "strength": f(r, 3), "threshold": f(threshold, 3), "season_length": m,
            "note": "Autocorrelation of the changes at the season length; above the threshold means the pattern repeats more than chance would suggest."}


# ------------------------------------------------------------------------------------------------ methods
def _holt(y: np.ndarray, a: float, b: float, phi: float) -> Tuple[float, float, float]:
    level, trend, sse = y[0], (y[1] - y[0]), 0.0
    for t in range(1, len(y)):
        pred = level + phi * trend
        sse += (y[t] - pred) ** 2
        new = a * y[t] + (1 - a) * pred
        trend = b * (new - level) + (1 - b) * phi * trend
        level = new
    return level, trend, sse


def _ses(y: np.ndarray) -> Tuple[float, float]:
    best = (float("inf"), y[-1])
    for a in (0.1, 0.2, 0.3, 0.5, 0.7, 0.9):
        level, sse = y[0], 0.0
        for t in range(1, len(y)):
            sse += (y[t] - level) ** 2
            level = a * y[t] + (1 - a) * level
        if sse < best[0]:
            best = (sse, level)
    return best[1], best[0]


def predict(method: str, y: np.ndarray, h: int, m: int) -> np.ndarray:
    n = len(y)
    if method == "naive":
        return np.repeat(y[-1], h)
    if method == "seasonal_naive":
        return np.array([y[n - m + (i % m)] for i in range(h)])
    if method == "drift":
        slope = (y[-1] - y[0]) / max(n - 1, 1)
        return y[-1] + slope * np.arange(1, h + 1)
    if method == "ses":
        return np.repeat(_ses(y)[0], h)
    if method == "holt_damped":
        best = None
        for a in (0.2, 0.5, 0.8):
            for b in (0.05, 0.2, 0.4):
                for phi in (0.8, 0.9, 0.98):
                    level, trend, sse = _holt(y, a, b, phi)
                    if best is None or sse < best[0]:
                        best = (sse, level, trend, phi)
        _, level, trend, phi = best
        return level + np.array([sum(phi ** j for j in range(1, i + 1)) for i in range(1, h + 1)]) * trend
    if method == "seasonal_trend":
        t = np.arange(n)
        slope, icpt = np.polyfit(t, y, 1)
        resid = y - (icpt + slope * t)
        idx = np.array([resid[(np.arange(n) % m) == k].mean() for k in range(m)])
        idx -= idx.mean()
        tf = np.arange(n, n + h)
        return icpt + slope * tf + idx[tf % m]
    raise ValueError(method)


def available(n: int, m: int) -> List[str]:
    out = ["naive", "drift", "ses", "holt_damped"]
    if m >= 2 and n >= 2 * m + 2:
        out += ["seasonal_naive", "seasonal_trend"]
    return [k for k in ORDER if k in out]


# ------------------------------------------------------------------------------------------------ backtest
def _scale(y: np.ndarray, m: int) -> float:
    lag = m if (m >= 2 and len(y) > m + 1) else 1
    s = float(np.mean(np.abs(y[lag:] - y[:-lag])))
    return s if s > 0 else 1.0


def backtest(y: np.ndarray, h: int, m: int, methods: Sequence[str]) -> Dict[str, Any]:
    """Rolling origin: fold i trains on y[:cut_i] and forecasts the next h values. Cuts step back by h from the end."""
    n = len(y)
    min_train = max(MIN_POINTS - 2, 2 * m + 2 if m >= 2 else 8)
    cuts = [n - h - i * h for i in range(MAX_FOLDS)]
    cuts = [c for c in cuts if c >= min_train][::-1]
    if len(cuts) < 2:
        return {"folds": len(cuts), "scored": False, "reason": f"A backtest needs at least 2 folds of {h} periods after a training window of {min_train}; this series has {n} periods.", "methods": {}, "errors": {}}
    res: Dict[str, Any] = {}
    errs: Dict[str, List[np.ndarray]] = {}
    for meth in methods:
        use_m = m if meth in ("seasonal_naive", "seasonal_trend") else m
        fold_err = []
        for c in cuts:
            tr, te = y[:c], y[c:c + h]
            if meth in ("seasonal_naive", "seasonal_trend") and len(tr) < 2 * m + 2:
                continue
            fold_err.append(te - predict(meth, tr, h, use_m))
        if not fold_err:
            continue
        E = np.vstack(fold_err)
        A = np.vstack([y[c:c + h] for c in cuts][-len(fold_err):])
        sc = _scale(y[: cuts[0]], m)
        zero = bool(np.any(A == 0))
        res[meth] = {"label": LABELS[meth], "mae": f(np.mean(np.abs(E))), "rmse": f(np.sqrt(np.mean(E ** 2))), "mase": f(np.mean(np.abs(E)) / sc, 4),
                     "smape": f(100 * np.mean(2 * np.abs(E) / np.maximum(np.abs(A) + np.abs(A - E), 1e-12)), 3),
                     "mape": None if zero else f(100 * np.mean(np.abs(E) / np.abs(A)), 3), "folds": int(len(fold_err))}
        errs[meth] = E
    return {"folds": len(cuts), "scored": bool(res), "horizon": h, "methods": res, "errors": errs}


def pick(bt: Dict[str, Any]) -> Optional[str]:
    scored = {k: v["mase"] for k, v in bt.get("methods", {}).items() if v["mase"] is not None}
    if not scored:
        return None
    best = min(scored.values())
    return next(k for k in ORDER if k in scored and scored[k] <= best + 1e-12)


def intervals(point: np.ndarray, E: Optional[np.ndarray], actual_check: Optional[np.ndarray] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    h = len(point)
    rows = [{"point": f(p)} for p in point]
    if E is None or E.size < MIN_INTERVAL_ERRORS:
        return rows, {"available": False, "reason": f"Intervals need at least {MIN_INTERVAL_ERRORS} backtest errors; there were {0 if E is None else E.size}."}
    sd_by_step = []
    for j in range(h):
        pooled = E[:, : min(j + 1, E.shape[1])]
        sd_by_step.append(float(np.sqrt(np.mean(pooled ** 2))))
    widen = np.maximum.accumulate(np.array(sd_by_step))
    for j, r in enumerate(rows):
        for lvl, z in Z.items():
            r[f"lower{lvl}"], r[f"upper{lvl}"] = f(point[j] - z * widen[j]), f(point[j] + z * widen[j])
    cov = {}
    for lvl, z in Z.items():
        sd = float(np.sqrt(np.mean(E ** 2)))
        cov[lvl] = f(float(np.mean(np.abs(E) <= z * sd)), 3)
    return rows, {"available": True, "method": "Spread of the chosen method's backtest errors, never narrowing with the forecast distance.", "backtest_coverage": cov,
                  "caveat": "Coverage is measured on the same backtest errors the intervals came from, so real future coverage can be lower. With few folds treat the bands as rough."}
