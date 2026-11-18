"""Forecast one measure of a dataset over time. Read-only: nothing is stored, trained or deployed, so the same request always gives the same answer.

The calculation is the pure numpy code in `predictive/forecast.py` (no generated code, no data leaves the process). It runs in-process because it is bounded: at most MAX_PERIODS periods and 24 forecast steps."""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from ...core.errors import AppError
from ..analytics.frame import load_snapshot
from ..analytics.ops.temporal import FREQ, pick_freq
from ..analytics.plan import role_of
from ..identity.authorization import Principal
from ..p7 import Service7, choice, conflict, invalid
from ..predictive import forecast as F

MAX_PERIODS = 1000
MAX_HORIZON = 24
AGGS = ("mean", "sum", "median", "min", "max", "count")
FREQS = ("auto", "day", "week", "month", "quarter", "year")
EVENT = "p7.forecast.run"


class ForecastService(Service7):
    def __init__(self, ctx: Any, ws: Any) -> None:
        super().__init__(ctx, ws)
        self.storage = ws.storage

    def run(self, principal: Principal, asset_id: str, date: Any, measure: Any = None, *, agg: Any = "mean", freq: Any = "auto", horizon: Any = 6, version_id: Optional[str] = None) -> Dict[str, Any]:
        self.limit("monitor:user", principal)
        agg, freq = choice(agg, "agg", AGGS), choice(freq, "freq", FREQS)
        try:
            h = int(horizon)
        except (TypeError, ValueError):
            raise invalid("horizon", "Use a whole number of periods.") from None
        if not 1 <= h <= MAX_HORIZON:
            raise invalid("horizon", f"Forecast between 1 and {MAX_HORIZON} periods.")
        asset, version, declared, types = self.ws.analytics.open_dataset(principal, asset_id, version_id)
        if date not in types or role_of(types[date]) != "datetime":
            raise invalid("date", "Choose a date column.")
        if agg != "count":
            if measure not in types or role_of(types[measure]) != "numeric":
                raise invalid("measure", "Choose a numeric column to forecast, or use 'count' to forecast how many rows there are per period.")
        else:
            measure = None
        with self.storage.open(version["canonical_key"]) as stream:
            snap = load_snapshot(stream, declared, rows_total=int(version["row_count"] or 0), max_rows=500_000, max_cells=20_000_000)
        df = snap.df
        d = pd.to_datetime(df[date], errors="coerce")
        keep = d.notna()
        if not keep.any():
            raise conflict("NO_DATES", "This column has no readable dates.")
        frame = pd.DataFrame({"d": d[keep], "m": pd.to_numeric(df[measure], errors="coerce")[keep] if measure else 1.0})
        span = (frame["d"].max() - frame["d"].min()) / pd.Timedelta(days=1)
        used = pick_freq(float(span)) if freq == "auto" else freq
        rule = FREQ[used]
        per = frame["d"].dt.to_period(rule)
        g = frame.assign(p=per).groupby("p")["m"]
        value = g.size().astype(float) if agg == "count" else getattr(g, agg)()
        full = pd.period_range(per.min(), per.max(), freq=rule)
        if len(full) > MAX_PERIODS:
            raise AppError("TOO_MANY_PERIODS", f"This would create {len(full):,} periods; choose a coarser period or narrow the data.", 422)
        raw = value.reindex(full).to_numpy(float)
        if len(raw) < F.MIN_POINTS:
            raise AppError("SERIES_TOO_SHORT", f"A forecast needs at least {F.MIN_POINTS} periods; this series has {len(raw)}. Try a finer period.", 422)
        try:
            y, filled = F.fill_gaps(raw)
        except ValueError:
            raise AppError("TOO_MANY_GAPS", f"More than {int(F.MAX_FILL_SHARE * 100)}% of the periods are empty, so a series would have to be invented. Use a coarser period.", 422) from None
        m = F.SEASON[used]
        if m > len(y) // 2:
            m = 1                                                  # too short to see even two cycles: no seasonal methods
        seas = F.seasonality(y, m)
        methods = F.available(len(y), m if m >= 2 else 1)
        bt = F.backtest(y, min(h, max(1, len(y) // 4)), m if m >= 2 else 1, methods)
        chosen = F.pick(bt)
        validated = chosen is not None
        chosen = chosen or "naive"
        point = F.predict(chosen, y, h, m if m >= 2 else 1)
        rows, interval_info = F.intervals(point, bt.get("errors", {}).get(chosen))
        future = pd.period_range(full[-1] + 1, periods=h, freq=rule)
        for r, p in zip(rows, future):
            r["period"] = str(p)
        limits = ["A forecast continues the pattern of the past. It does not know about events, decisions or changes that have not happened yet.",
                  "The method was chosen by how well it predicted the most recent periods; that is no guarantee for the next ones."]
        if not validated:
            limits.insert(0, "The series was too short to backtest, so the repeat-the-last-value method was used and its accuracy is unknown.")
        if filled:
            limits.append(f"{filled} empty period(s) were filled by a straight line between neighbours.")
        self.audit(EVENT, principal, asset["id"], periods=int(len(y)), horizon=h, method=chosen, validated=validated)
        return {"asset_id": asset["id"], "name": asset["name"], "version_id": version["id"], "date": date, "measure": measure, "aggregation": agg, "frequency": used,
                "history": {"periods": [str(p) for p in full[-60:]], "values": [F.f(v) for v in y[-60:]], "total_periods": int(len(y)), "filled_periods": filled},
                "seasonality": seas, "method": {"key": chosen, "label": F.LABELS[chosen], "validated_by_backtest": validated},
                "backtest": {"folds": bt["folds"], "scored": bt["scored"], "horizon": bt.get("horizon"), "reason": bt.get("reason"), "methods": bt.get("methods", {}),
                             "scale": "MASE below 1 means the method beat repeating the previous period (or season) in the past."},
                "forecast": rows, "intervals": interval_info, "limitations": limits}
