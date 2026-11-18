"""Chart data. A chart is a view of numbers computed by the same operations as everything else (aggregate, correlation, trend);
nothing is calculated in the browser. Every drawable element carries a `select` descriptor that the drill-down endpoint turns back
into the underlying rows, so a user can always go from a mark on a chart to the data and the calculation behind it."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from ..constants import MAX_CATEGORIES, MAX_SCATTER_POINTS
from ..errors import AnalysisError
from ..plan import OpSpec, P, role_of
from . import common as c
from . import descriptive, relationships, temporal
from .common import OpContext, evidence_for, num, result


def _table(columns: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
    return {"columns": columns, "rows": rows}


def _wrap(ctx: OpContext, inner: Dict[str, Any], chart: Dict[str, Any], columns: List[str], method: str, aggregation: Optional[str], n: Optional[int] = None,
          extra_warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    ev = dict(inner["evidence"])
    ev["chart"] = {"type": chart["type"], "selected_columns": columns, "aggregation": aggregation, "calculation_method": method, "export": "chart data table and underlying rows"}
    if n is not None:
        ev["sample_size"] = int(n)
    return result("ok", chart, c.py(ev), list(inner.get("warnings", [])) + list(extra_warnings or []))


def chart_bar(ctx: OpContext, x: str, y: Optional[str], agg: str, sort: str, top_n: int) -> Dict[str, Any]:
    agg = "count" if y is None else agg
    inner = descriptive.aggregate(ctx, agg=agg, measure=y, group_by=[x], sort=sort, top_n=top_n, include_ci=True)
    d = inner["data"]
    bars = [{"label": g["key"][x], "value": g["value"], "n": g["n"], "ci": g.get("ci"), "select": {"type": "category", "column": x, "value": g["key"][x]}} for g in d["groups"]]
    chart = {"type": "bar", "title": f"{agg} of {y} by {x}" if y else f"Number of rows by {x}", "x_label": x, "y_label": f"{agg}({y})" if y else "rows",
             "bars": bars, "categories_total": d["groups_total"], "categories_not_shown": max(0, d["groups_total"] - len(bars)), "overall": d["overall"],
             "data_table": _table([x, f"{agg}" + (f" of {y}" if y else ""), "n"], [[b["label"], b["value"], b["n"]] for b in bars])}
    return _wrap(ctx, inner, chart, [x] + ([y] if y else []), f"{agg} per category", agg, d["overall"]["n"])


def chart_histogram(ctx: OpContext, x: str, bins: Optional[int], density: bool) -> Dict[str, Any]:
    if role_of(ctx.types[x]) != "numeric":
        raise AnalysisError("PARAM_INVALID", "A histogram needs a numeric column.")
    vals = ctx.df[x].dropna().astype(float).to_numpy()
    if len(vals) == 0:
        return c.not_enough(ctx, [x], 0, 1, "A histogram", "histogram")
    h = c.histogram(vals, bins)
    n = len(vals)
    bars = []
    last = len(h["counts"]) - 1
    for i, cnt in enumerate(h["counts"]):
        lo, hi = h["edges"][i], h["edges"][i + 1] if len(h["edges"]) > i + 1 else h["edges"][i]
        width = (hi - lo) or 1.0
        bars.append({"lo": lo, "hi": hi, "count": cnt, "pct": c.share(cnt, n), "density": num(cnt / n / width), "select": {"type": "range", "column": x, "lo": lo, "hi": hi, "hi_inclusive": i == last}})
    chart = {"type": "histogram", "title": f"Distribution of {x}", "x_label": x, "y_label": "density" if density else "rows", "density": density, "bars": bars,
             "overlays": {"mean": num(vals.mean()), "median": num(np.median(vals))}, "binning": h.get("rule"),
             "data_table": _table(["bin_from", "bin_to", "count", "pct"], [[b["lo"], b["hi"], b["count"], b["pct"]] for b in bars])}
    ev = evidence_for(ctx, columns=[x], n=n, method="histogram", formula="numpy.histogram_bin_edges / numpy.histogram", null_policy="missing values are excluded and counted separately",
                      limitations=["The shape depends on the bin choice; try another number of bins before reading detail into it."], extra={"binning": h.get("rule")})
    return _wrap(ctx, {"evidence": ev}, chart, [x], "equal-width bins", "count", n, [f"{int(len(ctx.df) - n)} missing value(s) are not shown."] if len(ctx.df) - n else [])


def chart_scatter(ctx: OpContext, x: str, y: str, color: Optional[str], trend_line: bool) -> Dict[str, Any]:
    for col in (x, y):
        if role_of(ctx.types[col]) != "numeric":
            raise AnalysisError("PARAM_INVALID", "A scatter plot needs two numeric columns.")
    cols = [x, y] + ([color] if color else [])
    frame = pd.DataFrame({x: ctx.df[x].astype(float), y: ctx.df[y].astype(float)})
    if color:
        frame[color] = c.category(ctx, color)
    frame = frame.dropna(subset=[x, y])
    n = int(len(frame))
    warnings: List[str] = []
    sample = None
    plotted = frame
    if n > MAX_SCATTER_POINTS:
        pick = np.sort(ctx.rng(31).choice(n, size=MAX_SCATTER_POINTS, replace=False))
        plotted = frame.iloc[pick]
        sample = {"method": "uniform random sample without replacement", "shown": MAX_SCATTER_POINTS, "of": n, "seed": ctx.seed}
        warnings.append(f"Showing a random sample of {MAX_SCATTER_POINTS:,} of {n:,} points; statistics use all {n:,}.")
    levels = None
    if color:
        counts = frame[color].dropna().value_counts()
        levels = [str(k) for k in counts.index[:8]]
        if len(counts) > 8:
            warnings.append("Only the 8 largest colour groups are named; the rest are grouped as 'other'.")
    points = []
    for row, rec in plotted.iterrows():
        pt = {"row": int(row), "x": num(rec[x]), "y": num(rec[y])}
        if color:
            val = rec[color]
            pt["color"] = str(val) if str(val) in (levels or []) else ("(missing)" if pd.isna(val) else "other")
        points.append(pt)
    chart: Dict[str, Any] = {"type": "scatter", "title": f"{y} against {x}", "x_label": x, "y_label": y, "points": points, "color_by": color, "color_levels": levels, "sample": sample,
                             "select_brush": {"type": "xy_range", "x_column": x, "y_column": y},
                             "data_table": _table(["row", x, y] + ([color] if color else []), [[p["row"], p["x"], p["y"]] + ([p.get("color")] if color else []) for p in points])}
    unc: Dict[str, Any] = {"available": False, "reason": "A scatter plot shows the data itself; the trend line below carries the uncertainty."}
    if trend_line and n >= 10 and frame[x].std() > 0:
        lr = stats.linregress(frame[x], frame[y])
        sp = stats.spearmanr(frame[x], frame[y])
        crit = stats.t.ppf(0.975, n - 2)
        x0, x1 = float(frame[x].min()), float(frame[x].max())
        chart["overlays"] = {"trend_line": {"method": "ordinary least squares on all complete rows", "slope": num(lr.slope), "intercept": num(lr.intercept),
                                            "slope_ci": [num(lr.slope - crit * lr.stderr), num(lr.slope + crit * lr.stderr)], "r": num(lr.rvalue), "r_squared": num(lr.rvalue ** 2),
                                            "p_value": num(lr.pvalue), "spearman_rho": num(sp.statistic), "n": n,
                                            "line": [{"x": num(x0), "y": num(lr.intercept + lr.slope * x0)}, {"x": num(x1), "y": num(lr.intercept + lr.slope * x1)}],
                                            "caution": "A straight line is only one way to summarise the relationship; association is not causation."}}
        unc = {"available": True, "kind": "95% confidence interval for the slope", "ci": chart["overlays"]["trend_line"]["slope_ci"]}
    ev = evidence_for(ctx, columns=cols, n=n, method="scatter plot" + (" with OLS trend line" if trend_line else ""), formula="scipy.stats.linregress; scipy.stats.spearmanr", uncertainty=unc,
                      null_policy="rows missing x or y are excluded", limitations=["Points are drawn from a sample when there are many; the statistics use every complete row.", "Association is not causation."],
                      extra={"sample": sample, "seed": ctx.seed})
    return _wrap(ctx, {"evidence": ev}, chart, cols, "scatter of complete cases", None, n, warnings)


def chart_heatmap(ctx: OpContext, kind: str, columns: Optional[List[str]], x: Optional[str], y: Optional[str], value: Optional[str], agg: str, method: str) -> Dict[str, Any]:
    if kind == "correlation":
        cols = columns or [col for col in ctx.df.columns if role_of(ctx.types[col]) == "numeric"][:20]
        if len(cols) < 2:
            raise AnalysisError("PARAM_INVALID", "A correlation heatmap needs at least two numeric columns.")
        inner = relationships.correlation(ctx, cols, [method])
        m = inner["data"]["matrix"]
        cells = [{"x": m["columns"][j], "y": m["columns"][i], "value": m["values"][i][j], "select": {"type": "pair", "x_column": m["columns"][j], "y_column": m["columns"][i]}}
                 for i in range(len(cols)) for j in range(len(cols))]
        chart = {"type": "heatmap", "kind": "correlation", "title": f"{method.capitalize()} correlation", "x_labels": m["columns"], "y_labels": m["columns"], "cells": cells,
                 "scale": {"min": -1, "max": 1, "diverging": True}, "data_table": _table(["x", "y", method], [[cl["x"], cl["y"], cl["value"]] for cl in cells])}
        return _wrap(ctx, inner, chart, cols, f"{method} correlation matrix", None, inner["evidence"]["sample_size"])
    if not x or not y:
        raise AnalysisError("PARAM_INVALID", "A cross-tabulation heatmap needs two category columns.")
    a, b = c.category(ctx, x), c.category(ctx, y)
    frame = pd.DataFrame({"a": a, "b": b})
    if value:
        frame["v"] = ctx.df[value].astype(float)
    frame = frame.dropna(subset=["a", "b"])
    top = lambda s: set(s.value_counts().index[:15])  # noqa: E731
    warnings: List[str] = []
    if frame["a"].nunique() > 15 or frame["b"].nunique() > 15:
        warnings.append("Only the 15 most common categories of each column are shown.")
        frame = frame[frame["a"].isin(top(frame["a"])) & frame["b"].isin(top(frame["b"]))]
    use_agg = "count" if not value else agg
    cells = []
    for (av, bv), sub in frame.groupby(["a", "b"]):
        v = len(sub) if use_agg == "count" else {"mean": sub["v"].mean(), "sum": sub["v"].sum(), "median": sub["v"].median()}[use_agg]
        cells.append({"x": str(av), "y": str(bv), "value": num(v), "n": int(sub["v"].notna().sum()) if value else int(len(sub)),
                      "select": {"type": "cell", "x_column": x, "x_value": str(av), "y_column": y, "y_value": str(bv)}})
    chart = {"type": "heatmap", "kind": "crosstab", "title": f"{use_agg}" + (f" of {value}" if value else " of rows") + f": {x} by {y}", "x_labels": sorted(frame["a"].unique()),
             "y_labels": sorted(frame["b"].unique()), "cells": cells, "scale": {"min": 0, "diverging": False}, "data_table": _table([x, y, use_agg, "n"], [[cl["x"], cl["y"], cl["value"], cl["n"]] for cl in cells])}
    ev = evidence_for(ctx, columns=[x, y] + ([value] if value else []), n=int(len(frame)), method=f"cross-tabulation ({use_agg})", formula="pandas.groupby",
                      null_policy="rows missing either category (or the value) are excluded", limitations=["Cell values with small n are unstable: n is stored with each cell.", "Association is not causation."])
    return _wrap(ctx, {"evidence": ev}, chart, [x, y] + ([value] if value else []), "cross-tabulation", use_agg, int(len(frame)), warnings)


def chart_timeseries(ctx: OpContext, date: str, measure: Optional[str], agg: str, freq: str, window: int, trend_overlay: bool) -> Dict[str, Any]:
    inner = temporal.trend(ctx, date, measure, agg, freq, window)
    if inner["status"] != "ok":
        return inner
    d = inner["data"]
    pts = [{"start": s["period_start"], "end": s["period_end"], "value": s["value"], "n": s["n"], "moving_average": s["moving_average"],
            "select": {"type": "period", "column": date, "start": s["period_start"], "end": s["period_end"]}} for s in d["series"]]
    overlays: Dict[str, Any] = {"moving_average": d["moving_average_window"]}
    tr = d.get("trend", {})
    if trend_overlay and "slope_per_period" in tr:
        valid_idx = [i for i, p in enumerate(pts) if p["value"] is not None]
        overlays["trend_line"] = {"slope_per_period": tr["slope_per_period"], "intercept": tr["intercept"], "direction": tr["direction"], "conclusion": tr["conclusion"],
                                  "values": [num(tr["intercept"] + tr["slope_per_period"] * i) for i in range(len(pts))], "caution": "Linear summary of the whole period; it does not model seasonality."}
    chart = {"type": "timeseries", "title": f"{d['agg']} of {measure} per {d['freq']}" if measure else f"Rows per {d['freq']}", "x_label": date, "y_label": f"{d['agg']}({measure})" if measure else "rows",
             "points": pts, "overlays": overlays, "freq": d["freq"], "trend": tr, "date_range": {"from": pts[0]["start"] if pts else None, "to": pts[-1]["end"] if pts else None},
             "data_table": _table(["period_start", "period_end", "value", "n", "moving_average"], [[p["start"], p["end"], p["value"], p["n"], p["moving_average"]] for p in pts])}
    return _wrap(ctx, inner, chart, [date] + ([measure] if measure else []), "period aggregation", d["agg"], inner["evidence"]["sample_size"])


def chart(ctx: OpContext, type: str, x: Optional[str] = None, y: Optional[str] = None, color: Optional[str] = None, agg: str = "mean", sort: str = "value_desc", top_n: int = 20,
          bins: Optional[int] = None, density: bool = False, trend_line: bool = True, kind: str = "correlation", columns: Optional[List[str]] = None, value: Optional[str] = None,
          method: str = "spearman", freq: str = "auto", window: int = 3) -> Dict[str, Any]:
    def need(*names):
        missing = [n for n, v in zip(names, [locals_[n] for n in names]) if v is None]
        if missing:
            raise AnalysisError("PARAM_INVALID", f"A {type} chart needs: " + ", ".join(missing) + ".")
    locals_ = {"x": x, "y": y}
    if type == "bar":
        need("x")
        return chart_bar(ctx, x, y, agg, sort, top_n)
    if type == "histogram":
        need("x")
        return chart_histogram(ctx, x, bins, density)
    if type == "scatter":
        need("x", "y")
        return chart_scatter(ctx, x, y, color, trend_line)
    if type == "heatmap":
        return chart_heatmap(ctx, kind, columns, x, y, value, agg if agg in ("count", "mean", "sum", "median") else "mean", method)
    if type == "timeseries":
        need("x")
        return chart_timeseries(ctx, x, y, agg if agg in ("mean", "sum", "median", "min", "max", "count") else "mean", freq, window, trend_line)
    raise AnalysisError("PARAM_INVALID", "Unknown chart type.")


SPECS = [
    OpSpec("chart", "Chart", {
        "type": P("enum", required=True, choices=("bar", "scatter", "heatmap", "histogram", "timeseries")),
        "x": P("column"), "y": P("column"), "color": P("column", role="categorical"),
        "agg": P("enum", default="mean", choices=("mean", "sum", "count", "median", "min", "max")),
        "sort": P("enum", default="value_desc", choices=("value_desc", "value_asc", "label", "none")), "top_n": P("int", default=20, lo=1, hi=100),
        "bins": P("int", lo=2, hi=200), "density": P("bool", default=False), "trend_line": P("bool", default=True),
        "kind": P("enum", default="correlation", choices=("correlation", "crosstab")), "columns": P("columns", role="numeric", max_items=30), "value": P("column", role="numeric"),
        "method": P("enum", default="spearman", choices=("pearson", "spearman", "kendall")),
        "freq": P("enum", default="auto", choices=("auto", "day", "week", "month", "quarter", "year")), "window": P("int", default=3, lo=1, hi=24)}, chart, inferential=False),
]
