"""Does coverage move with a number in the person's own data? An explainable association check, NOT a causal analysis.

What is computed
  For each lag from -L to +L days, the Spearman rank correlation between a daily coverage series (article counts or mean tone for a topic or a name) and a daily series from the
  person's dataset, with the coverage shifted so a positive lag means "coverage came first". Rank correlation needs no straight-line assumption.

How it is judged
  * p-values come from a circular-shift permutation test: one series is rotated by many random amounts, which keeps its day-to-day persistence (autocorrelation) and breaks any real
    alignment, so slowly drifting series are not mistaken for related ones. Ordinary correlation p-values assume independent days and would be far too optimistic.
  * Because several lags are looked at, the chosen lag's p-value is the share of permutations whose BEST lag is as strong as the best observed one (max-statistic correction). Benjamini-
    Hochberg q-values for each lag are also reported.
  * Both series are also checked for a steady trend. If either trends strongly, the analysis is repeated on day-to-day changes; the result is called robust only if it survives.

What is claimed
  "temporal"     coverage rose or fell BEFORE the value did, at a lag that survives the checks (an ordering in time, nothing more)
  "statistical"  they move together (or the ordering is unclear / not robust), nothing more
  "none"         no association that survives the checks
  Causation is never claimed: `causal_status` is always "not established", with the reasons that apply."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import rankdata

METHOD = "lagged-spearman-circular-permutation/1"
MAX_LAG = 14
MIN_POINTS = 20
PERMUTATIONS = 999
ALPHA = 0.05
TREND_RHO = 0.6

NOT_CAUSAL = ["A pattern in time does not show that one thing changed the other.",
              "Something else (a season, a holiday, an event both react to) can move both series.",
              "Coverage depends on which feeds were collected and when they were fetched.",
              "Several lags were examined; the p-value is adjusted for that, but a chance pattern is still possible."]
WOULD_HELP = ["Check whether the pattern repeats in a period that was not used here.", "Look for a known event that could drive both series.", "Compare with a similar dataset or region that had no such coverage."]


def _rho(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return 0.0
    rx, ry = rankdata(x), rankdata(y)
    rx -= rx.mean()
    ry -= ry.mean()
    d = math.sqrt(float((rx * rx).sum()) * float((ry * ry).sum()))
    return float((rx * ry).sum() / d) if d else 0.0


def _diff(series: Dict[str, float]) -> Dict[str, float]:
    days = sorted(series)
    out = {}
    for a, b in zip(days, days[1:]):
        if (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days == 1:
            out[b] = series[b] - series[a]
    return out


def _trend(series: Dict[str, float]) -> float:
    days = sorted(series)
    if len(days) < 5:
        return 0.0
    t = np.asarray([(datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(days[0], "%Y-%m-%d")).days for d in days], dtype=float)
    return _rho(t, np.asarray([series[d] for d in days], dtype=float))


def bh_qvalues(p: Sequence[float]) -> List[float]:
    m = len(p)
    order = sorted(range(m), key=lambda i: p[i])
    q = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


def _dense_arrays(series: Dict[str, float]) -> Tuple[np.ndarray, int]:
    """(values by consecutive day with NaN for missing days, day number of the first day)."""
    days = sorted(series)
    d0 = datetime.strptime(days[0], "%Y-%m-%d").toordinal()
    n = datetime.strptime(days[-1], "%Y-%m-%d").toordinal() - d0 + 1
    arr = np.full(n, np.nan)
    for d, v in series.items():
        arr[datetime.strptime(d, "%Y-%m-%d").toordinal() - d0] = v
    return arr, d0


def _lags(news: Dict[str, float], series: Dict[str, float], max_lag: int, min_points: int, permutations: int, seed: int) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    lags = list(range(-max_lag, max_lag + 1))
    out: Dict[str, Any] = {"lags": [], "tested": [], "skipped": []}
    if not news or not series:
        out["skipped"] = lags
        return out
    X, dx0 = _dense_arrays(news)
    Y, dy0 = _dense_arrays(series)
    plan: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    observed: Dict[int, float] = {}
    for k in lags:
        ix = np.arange(len(X))
        iy = dx0 + ix + k - dy0                                                                 # the value-series position for coverage on day ix, k days later
        ok = (iy >= 0) & (iy < len(Y))
        ix, iy = ix[ok], iy[ok]
        good = np.isfinite(X[ix]) & np.isfinite(Y[iy])
        if int(good.sum()) < min_points:
            out["skipped"].append(k)
            continue
        plan[k] = (ix, iy)
        observed[k] = _rho(X[ix][good], Y[iy][good])
    usable = sorted(plan)
    out["tested"] = usable
    if not usable:
        return out
    n_y = len(Y)
    min_shift = max(3, min(n_y // 10, 14))
    if n_y <= 2 * min_shift:
        out["note"] = "The value series is too short to shift, so no permutation test could be run."
        return out
    count_ge = {k: 0 for k in usable}
    count_max = 0
    obs_max = max(abs(v) for v in observed.values())
    for _ in range(permutations):
        s = int(rng.integers(min_shift, n_y - min_shift + 1))
        rolled = np.roll(Y, s)                                                                  # circular shift: keeps the series' own persistence, breaks any real alignment
        best = 0.0
        for k in usable:
            ix, iy = plan[k]
            x, y = X[ix], rolled[iy]
            good = np.isfinite(x) & np.isfinite(y)
            r = abs(_rho(x[good], y[good])) if int(good.sum()) >= 3 else 0.0
            best = max(best, r)
            if r >= abs(observed[k]) - 1e-12:
                count_ge[k] += 1
        if best >= obs_max - 1e-12:
            count_max += 1
    p = {k: (1 + count_ge[k]) / (1 + permutations) for k in usable}
    q = dict(zip(usable, bh_qvalues([p[k] for k in usable])))
    p_selected = (1 + count_max) / (1 + permutations)
    out.update(permutations=permutations, p_selected=p_selected)
    n_of = {k: int((np.isfinite(X[plan[k][0]]) & np.isfinite(Y[plan[k][1]])).sum()) for k in usable}
    out["lags"] = [{"lag": k, "n": n_of[k], "rho": round(observed[k], 4), "p": round(p[k], 4), "q": round(q[k], 4)} for k in usable]
    best_k = max(usable, key=lambda k: (abs(observed[k]), -abs(k)))
    out["best"] = {"lag": best_k, "rho": round(observed[best_k], 4), "n": n_of[best_k], "p_adjusted": round(p_selected, 4)}
    return out


def analyse(news: Dict[str, float], series: Dict[str, float], *, max_lag: int = 7, min_points: int = MIN_POINTS, permutations: int = PERMUTATIONS, seed: int = 20260101,
            alpha: float = ALPHA) -> Dict[str, Any]:
    """news and series map YYYY-MM-DD to a number. Returns the full, explainable result (see the module docstring)."""
    max_lag = max(0, min(int(max_lag), MAX_LAG))
    permutations = max(99, min(int(permutations), 2999))
    warnings: List[str] = []
    common_days = sorted(set(news) & set(series))
    result: Dict[str, Any] = {"method": METHOD, "max_lag": max_lag, "permutations_requested": permutations, "seed": seed, "alpha": alpha, "causal_status": "not established",
                              "why_not_causal": NOT_CAUSAL, "what_would_help": WOULD_HELP, "overlap_days": len(common_days)}
    if len(common_days) < min_points:
        result.update(evidence="none", status="insufficient_data", warnings=[f"Only {len(common_days)} day(s) have both coverage and a value; at least {min_points} are needed."], raw=None, differenced=None)
        return result
    trend_news, trend_series = _trend(news), _trend(series)
    trending = abs(trend_news) >= TREND_RHO or abs(trend_series) >= TREND_RHO
    if trending:
        warnings.append("At least one series has a steady trend. Two trending series look related even when they are not, so the analysis was repeated on day-to-day changes.")
    if len(common_days) < 30:
        warnings.append("Fewer than 30 days overlap, so only strong patterns can be detected.")
    raw = _lags(news, series, max_lag, min_points, permutations, seed)
    diff = _lags(_diff(news), _diff(series), max_lag, max(10, min_points // 2), permutations, seed + 1) if trending else None

    def significant(r: Optional[Dict[str, Any]]) -> bool:
        return bool(r and r.get("best") and r["best"]["p_adjusted"] < alpha)

    sig_raw, sig_diff = significant(raw), significant(diff) if diff is not None else None
    robust = bool(sig_raw and (diff is None or sig_diff))
    best = raw.get("best")
    if not sig_raw:
        evidence = "none"
    elif best and best["lag"] > 0 and robust:
        evidence = "temporal"
    else:
        evidence = "statistical"
    if sig_raw and diff is not None and not sig_diff:
        warnings.append("The association disappears when day-to-day changes are used, so it may only reflect the shared trend.")
    result.update(evidence=evidence, status="ok", robust_to_trend=robust if trending else None, trend={"coverage": round(trend_news, 3), "value": round(trend_series, 3), "strong": trending},
                  raw=raw, differenced=diff, warnings=warnings, best=best,
                  reading=_reading(evidence, best, robust if trending else None))
    return result


def _reading(evidence: str, best: Optional[Dict[str, Any]], robust: Optional[bool]) -> str:
    if evidence == "none" or not best:
        return "No association that survives the checks was found between coverage and the value."
    direction = "higher" if best["rho"] > 0 else "lower"
    when = ("on the same day" if best["lag"] == 0 else f"{abs(best['lag'])} day(s) {'after' if best['lag'] > 0 else 'before'} the coverage")
    base = f"When coverage is higher, the value tends to be {direction} {when} (rank correlation {best['rho']:+.2f}, adjusted p {best['p_adjusted']:.3f}, {best['n']} days)."
    if evidence == "temporal":
        return base + " Coverage came first, which is an ordering in time and nothing more."
    return base + " This is a statistical association only."


def result_hash(result: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
