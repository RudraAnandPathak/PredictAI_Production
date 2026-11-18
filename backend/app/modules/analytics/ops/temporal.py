"""Time-based analysis: period aggregation, trend tests, moving averages and lagged relationships between series."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from ..constants import CORRELATION_NOTE, NOT_ENOUGH_EVIDENCE
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from .common import OpContext, evidence_for, num, result

FREQ = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}
MIN_PERIODS_TREND = 8
MAX_PERIODS = 2000


def pick_freq(span_days: float) -> str:
    return "day" if span_days <= 60 else "month" if span_days <= 1100 else "quarter" if span_days <= 3650 else "year"


def series_by_period(ctx: OpContext, date_col: str, measure: Optional[str], agg: str, freq: str) -> Tuple[pd.DataFrame, str, int]:
    """Aggregate to one row per period (gaps are kept as empty periods). Returns (frame[period_start, period_end, value, n], freq used, rows used)."""
    d = ctx.df[date_col]
    present = d.notna()
    if measure is not None:
        frame = pd.DataFrame({"d": d, "m": ctx.df[measure].astype(float)})[present]
    else:
        frame = pd.DataFrame({"d": d, "m": 1.0})[present]
        agg = "count"
    if frame.empty:
        return pd.DataFrame(columns=["start", "end", "value", "n"]), freq, 0
    span = (frame["d"].max() - frame["d"].min()) / pd.Timedelta(days=1)
    used = pick_freq(float(span)) if freq == "auto" else freq
    rule = FREQ[used]
    per = frame["d"].dt.to_period(rule)
    frame = frame.assign(p=per)
    grouped = frame.groupby("p")["m"]
    if agg == "count":
        value, n = grouped.size().astype(float), grouped.size()
    else:
        func = {"mean": "mean", "sum": "sum", "median": "median", "min": "min", "max": "max"}[agg]
        value, n = grouped.agg(func), grouped.count()
    full = pd.period_range(per.min(), per.max(), freq=rule)
    if len(full) > MAX_PERIODS:
        raise AnalysisError("TOO_MANY_PERIODS", f"This would create {len(full):,} periods; choose a coarser period (for example month or year) or narrow the date range.")
    value, n = value.reindex(full), n.reindex(full).fillna(0).astype(int)
    out = pd.DataFrame({"start": [p.start_time for p in full], "end": [p.end_time.normalize() for p in full], "value": value.to_numpy(), "n": n.to_numpy()})
    return out, used, int(len(frame))


def _trend_stats(values: np.ndarray, positions: np.ndarray) -> Dict[str, Any]:
    lr = stats.linregress(positions, values)
    k = len(values)
    crit = stats.t.ppf(0.975, k - 2)
    kt = stats.kendalltau(positions, values)
    return {"slope_per_period": num(lr.slope), "slope_ci": [num(lr.slope - crit * lr.stderr), num(lr.slope + crit * lr.stderr)], "confidence": 0.95, "slope_p_value": num(lr.pvalue),
            "r_squared": num(lr.rvalue ** 2), "kendall_tau": num(kt.statistic), "kendall_p_value": num(kt.pvalue), "periods_used": int(k), "intercept": num(lr.intercept)}


def trend(ctx: OpContext, date: str, measure: Optional[str] = None, agg: str = "mean", freq: str = "auto", window: int = 3, alpha: float = 0.05) -> Dict[str, Any]:
    frame, used, n_rows = series_by_period(ctx, date, measure, agg, freq)
    effective = "count" if measure is None else agg
    if frame.empty:
        return c.not_enough(ctx, [date] + ([measure] if measure else []), 0, MIN_PERIODS_TREND, "A trend analysis", "trend")
    valid = frame["value"].notna()
    vals, pos = frame.loc[valid, "value"].to_numpy(), np.flatnonzero(valid.to_numpy()).astype(float)
    ma = pd.Series(frame["value"]).rolling(window, min_periods=window).mean() if window > 1 else None
    series = [{"period_start": r.start.date().isoformat(), "period_end": r.end.date().isoformat(), "value": num(r.value), "n": int(r.n),
               "moving_average": num(ma.iloc[i]) if ma is not None else None} for i, r in enumerate(frame.itertuples())]
    data: Dict[str, Any] = {"date": date, "measure": measure, "agg": effective, "freq": used, "freq_chosen_automatically": freq == "auto", "series": series,
                            "periods": int(len(frame)), "empty_periods": int((~valid).sum()), "moving_average_window": window if ma is not None else None}
    if len(vals):
        hi, lo = int(np.argmax(vals)), int(np.argmin(vals))
        pi, pl = frame.loc[valid].iloc[hi], frame.loc[valid].iloc[lo]
        data["highest"] = {"period_start": pi.start.date().isoformat(), "value": num(pi.value)}
        data["lowest"] = {"period_start": pl.start.date().isoformat(), "value": num(pl.value)}
        if vals[0] != 0:
            data["change_first_to_last_pct"] = num(100 * (vals[-1] - vals[0]) / abs(vals[0]), 6)
    warnings: List[str] = []
    if len(vals) >= MIN_PERIODS_TREND and np.ptp(vals) > 0:
        tr = _trend_stats(vals, pos)
        clear = tr["kendall_p_value"] < alpha and tr["slope_p_value"] < alpha and np.sign(tr["slope_per_period"]) == np.sign(tr["kendall_tau"])
        tr["direction"] = ("increasing" if tr["slope_per_period"] > 0 else "decreasing") if clear else "no clear trend"
        tr["conclusion"] = ("A monotonic trend is supported by both the linear slope and Kendall's tau." if clear else NOT_ENOUGH_EVIDENCE)
        data["trend"] = tr
        if data["empty_periods"]:
            warnings.append(f"{data['empty_periods']} period(s) had no data and are not used in the trend.")
    else:
        data["trend"] = {"direction": "not assessed", "conclusion": NOT_ENOUGH_EVIDENCE, "reason": f"A trend test needs at least {MIN_PERIODS_TREND} periods with data and some variation; there are {len(vals)}."}
    ev = evidence_for(ctx, columns=[date] + ([measure] if measure else []), n=n_rows, method=f"{effective} per {used}; linear trend (OLS) and Kendall's tau",
                      formula="pandas Period grouping; scipy.stats.linregress; scipy.stats.kendalltau",
                      null_policy="rows with a missing date (or missing measure) are excluded; periods with no data are shown as gaps",
                      uncertainty={"available": "slope_ci" in data["trend"], "kind": "95% confidence interval for the slope per period", "ci": data["trend"].get("slope_ci")},
                      assumptions=[{"name": "independent period values", "status": "not_met_likely", "detail": "Consecutive periods of a time series are usually correlated (autocorrelation), which makes trend p-values too optimistic."},
                                   {"name": "trend shape", "status": "assumed", "detail": "The linear fit describes a straight-line change; seasonality and shifts are not modelled."}],
                      limitations=["Trend p-values ignore autocorrelation and seasonality: treat them as a screening signal, not proof.", CORRELATION_NOTE,
                                   "The period chosen changes the picture; check the series at another period."], practical="Compare the slope with what a change of that size means for your measure.")
    return result("ok", data, ev, warnings)


def temporal_correlation(ctx: OpContext, date: str, first: str, second: str, agg: str = "mean", freq: str = "auto", max_lag: int = 3) -> Dict[str, Any]:
    a, used, n1 = series_by_period(ctx, date, first, agg, freq)
    b, _, _ = series_by_period(ctx, date, second, agg, used)
    if a.empty or b.empty or len(a) < 8:
        return c.not_enough(ctx, [date, first, second], int(len(a)), 8, "A lagged relationship", "lagged correlation")
    sa = pd.Series(a["value"].to_numpy(), index=a["start"])
    sb = pd.Series(b["value"].to_numpy(), index=b["start"])
    lags = []
    for lag in range(-max_lag, max_lag + 1):
        ctx.check()
        both = pd.concat([sa, sb.shift(lag)], axis=1).dropna()
        if len(both) >= 8 and both.iloc[:, 0].std() > 0 and both.iloc[:, 1].std() > 0:
            r = float(np.corrcoef(both.iloc[:, 0], both.iloc[:, 1])[0, 1])
            lags.append({"lag_periods": lag, "r": num(r), "n": int(len(both)),
                         "reading": f"'{second}' {abs(lag)} {used}(s) {'earlier' if lag > 0 else 'later'} than '{first}'" if lag else "same period"})
    best = max(lags, key=lambda l: abs(l["r"])) if lags else None
    ev = evidence_for(ctx, columns=[date, first, second], n=n1, method=f"correlation of {agg} per {used} at lags -{max_lag}..{max_lag}", formula="pandas shift + numpy.corrcoef on period series",
                      null_policy="periods missing either series (after shifting) are excluded",
                      uncertainty={"available": False, "reason": "No p-values: neighbouring periods are correlated, so standard significance tests would overstate certainty."},
                      assumptions=[{"name": "stationarity", "status": "not_checked", "detail": "Trends in both series create spurious correlation."}],
                      limitations=[CORRELATION_NOTE, "Exploratory only. Two series that both trend upward correlate whatever their relationship.", "Several lags are tried; the best one is flattered by selection."])
    return result("ok", {"date": date, "first": first, "second": second, "freq": used, "lags": lags, "strongest": best, "label": "exploratory"}, ev,
                  ["Exploratory: lagged correlations of trending series can be spurious."])


SPECS = [
    OpSpec("trend", "Trend over time", {"date": P("column", required=True, role="datetime"), "measure": P("column", role="numeric"),
                                        "agg": P("enum", default="mean", choices=("mean", "sum", "median", "min", "max", "count")),
                                        "freq": P("enum", default="auto", choices=("auto", "day", "week", "month", "quarter", "year")),
                                        "window": P("int", default=3, lo=1, hi=24), "alpha": P("float", default=0.05, lo=0.001, hi=0.2)}, trend, inferential=True),
    OpSpec("temporal_correlation", "Lagged relationship between series", {"date": P("column", required=True, role="datetime"), "first": P("column", required=True, role="numeric"),
                                                                          "second": P("column", required=True, role="numeric"), "agg": P("enum", default="mean", choices=("mean", "sum", "median")),
                                                                          "freq": P("enum", default="auto", choices=("auto", "day", "week", "month", "quarter", "year")),
                                                                          "max_lag": P("int", default=3, lo=0, hi=12)}, temporal_correlation, inferential=True),
]
