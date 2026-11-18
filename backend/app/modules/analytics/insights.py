"""Automated insights. An insight is a plain-language reading of a stored, evidence-backed analysis run: it never contains a number that
is not in a run's result, and it is never produced when the evidence does not support it. Dedupe keys keep a person's save/dismiss choice
when the same finding is regenerated."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..identity.authorization import Principal
from ..explanation import explain
from ..identity.tokens import new_id
from .constants import AEvent, CORRELATION_NOTE, Evidence, InsightState, NOT_ENOUGH_EVIDENCE, RunKind
from .errors import not_found, plan_invalid
from .plan import role_of
from .service import AnalyticsService, _json

MAX_INSIGHTS = 30
MIN_STRENGTH_FOR_INSIGHT = 0.2        # below this a pattern is "negligible/small"; it is not surfaced as a finding
SKEW_FLAG = 2.0
MISSING_FLAG_PCT = 5.0
TOP_STATISTICAL = 3


def _fmt(x: Any, places: int = 3) -> str:
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return f"{x:,}"
    if isinstance(x, float):
        if x != 0 and abs(x) < 10 ** -places:
            return f"{x:.2e}"
        return f"{x:,.{places}f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:,.0f}"
    return str(x)


def _p(x: Any) -> str:
    return "p < 0.001" if isinstance(x, (int, float)) and x < 0.001 else f"p = {_fmt(x, 3)}"


class InsightService:
    def __init__(self, analytics: AnalyticsService) -> None:
        self.an = analytics
        self.repo = analytics.repo
        self.ctx = analytics.ctx

    # ------------------------------------------------------------------ generate
    def generate(self, principal: Principal, asset_id: str, *, filters: Any = None, version_id: Optional[str] = None) -> Dict[str, Any]:
        schema = self.an.schema(principal, asset_id, version_id)
        cols = schema["columns"]
        numeric = [c["name"] for c in cols if c["role"] == "numeric"]
        dates = [c["name"] for c in cols if c["role"] == "datetime"]
        ops: List[Dict[str, Any]] = [{"op": "dataset_summary", "params": {}}, {"op": "data_quality", "params": {}}, {"op": "missing_values", "params": {}}]
        if numeric:
            ops.append({"op": "column_stats", "params": {"columns": numeric[:12]}})
        ops.append({"op": "discover", "params": {"top_k": 15}})
        if dates and numeric:
            ops.append({"op": "trend", "params": {"date": dates[0], "measure": numeric[0]}})
        first = self.an.run(principal, asset_id, kind=RunKind.INSIGHTS, operations=ops, filters=filters, version_id=version_id, event=AEvent.INSIGHTS)
        found: List[Dict[str, Any]] = []
        found += self._descriptive(first)
        found += self._quality(first)
        found += self._anomalies(first)
        patterns = self._patterns(first)
        found += patterns
        found += self._trend(first)
        statistical: Optional[Dict[str, Any]] = None
        tests = self._test_ops(first)
        if tests:
            statistical = self.an.run(principal, asset_id, kind=RunKind.HYPOTHESIS, operations=tests, filters=filters, version_id=version_id, event=AEvent.INSIGHTS,
                                      extra_notes=["These tests confirm patterns that were found by searching many column pairs; the family size below accounts for that search."])
            found += self._statistical(statistical)
        saved: List[Dict[str, Any]] = []
        for item in found[:MAX_INSIGHTS]:
            run = statistical if item.pop("_run", "first") == "statistical" and statistical else first
            saved.append(self._store(principal, run, item))
        self.ctx.audit.record(AEvent.INSIGHTS, "success", actor=principal.user_id, target=first["id"], metadata={"asset_id": asset_id, "count": len(saved)})
        out: Dict[str, Any] = {"insights": saved, "run_id": first["id"], "statistical_run_id": statistical["id"] if statistical else None, "count": len(saved),
                               "dataset_version": {"version_id": schema["version_id"], "version_number": schema["version_number"]}}
        if not [i for i in saved if i["category"] in ("relationship", "group_difference", "statistical", "trend", "anomaly")]:
            out["message"] = NOT_ENOUGH_EVIDENCE + " No relationship, group difference, trend or anomaly met the evidence thresholds."
        return out

    # ------------------------------------------------------------------ derivations (each reads a stored result, never the raw data)
    @staticmethod
    def _res(run: Mapping[str, Any], op: str) -> Optional[Dict[str, Any]]:
        for i, r in enumerate(run["result"]["results"]):
            if r["op"] == op and r["status"] == "ok":
                return {**r, "_index": i}
        return None

    def _base(self, category: str, statement: str, key: str, run: Mapping[str, Any], res: Mapping[str, Any], *, columns: Sequence[str], rows: Optional[Sequence[int]] = None,
              rows_count: Optional[int] = None, uncertainty: Any = None, extra: Optional[Dict[str, Any]] = None, run_tag: str = "first") -> Dict[str, Any]:
        ev = res.get("evidence") or {}
        plan = run["result"]["plan"]
        return {"category": category, "statement": statement, "dedupe_key": key, "_run": run_tag,
                "payload": {
                    "statement": statement, "category": category,
                    "supporting_evidence": {"run_id": run["id"], "operation_index": res["_index"], "operation": res["op"], "result_hash": run["result_hash"], **(extra or {})},
                    "affected": {"columns": list(columns), "row_numbers": list(rows or [])[:50], "rows_affected": rows_count, "rows_in_scope": run["rows_used"]},
                    "method": ev.get("method"), "uncertainty": uncertainty if uncertainty is not None else ev.get("uncertainty"),
                    "limitations": list(ev.get("limitations", []))[:6], "filters": plan.get("filters", []),
                    "dataset_version": {"asset_id": run["asset_id"], "version_id": run["version_id"], "checksum_sha256": run["result"]["evidence"]["source"]["checksum_sha256"]},
                    "lineage": {"plan_hash": run["plan_hash"], "calc_version": run["calc_version"], "run_id": run["id"], "code": run["result"]["evidence"]["calculation"]["operations"][res["_index"]].get("formula_ref"),
                                "libraries": run["result"]["evidence"]["calculation"]["libraries"], "transformations": run["result"]["evidence"]["inputs"]["transformation_lineage"]},
                    "evidence_status": run["evidence_status"]}}

    def _descriptive(self, run) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        s = self._res(run, "dataset_summary")
        if s:
            d = s["data"]
            out.append(self._base("descriptive", f"This dataset has {_fmt(d['rows_in_scope'])} rows and {_fmt(d['columns'])} columns; {_fmt(d['missing_cells']['pct'], 2)}% of all cells are empty.",
                                  "descriptive:overview", run, s, columns=[], extra={"values": {"rows": d["rows_in_scope"], "columns": d["columns"], "missing_pct": d["missing_cells"]["pct"]}}))
        cs = self._res(run, "column_stats")
        if cs:
            for col in cs["data"]["columns"]:
                sk, mean, med = col.get("skewness"), col.get("mean"), col.get("median")
                if isinstance(sk, (int, float)) and abs(sk) >= SKEW_FLAG and col.get("non_missing", 0) >= 30 and mean is not None and med is not None:
                    side = "high" if sk > 0 else "low"
                    out.append(self._base("descriptive", f"'{col['column']}' is strongly skewed towards {side} values (skewness {_fmt(sk, 2)}): the mean is {_fmt(mean)} but the median is {_fmt(med)}, "
                                          "so the median describes a typical value better.", f"descriptive:skew:{col['column']}", run, cs, columns=[col["column"]],
                                          uncertainty={"available": False, "reason": "A descriptive fact about this dataset, not an estimate about a wider population."},
                                          extra={"values": {"skewness": sk, "mean": mean, "median": med, "non_missing": col["non_missing"]}}))
        return out

    def _quality(self, run) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        mv = self._res(run, "missing_values")
        if mv:
            for col in mv["data"]["columns"]:
                if col["pct"] >= MISSING_FLAG_PCT:
                    out.append(self._base("data_quality", f"'{col['column']}' is empty in {_fmt(col['missing'])} rows ({_fmt(col['pct'], 1)}%). Results that use it ignore those rows.",
                                          f"quality:missing:{col['column']}", run, mv, columns=[col["column"]], rows_count=col["missing"],
                                          uncertainty={"available": False, "reason": "An exact count."}, extra={"values": col}))
        dq = self._res(run, "data_quality")
        if dq:
            for issue in dq["data"]["issues"]:
                if issue["code"] in ("OUTLIERS",):
                    continue
                out.append(self._base("data_quality", issue["message"], f"quality:{issue['code']}:{'|'.join(sorted(issue['columns']))}", run, dq, columns=issue["columns"],
                                      rows=issue.get("row_numbers"), rows_count=issue["count"], uncertainty={"available": False, "reason": "An exact count."}, extra={"values": issue}))
        return out

    def _anomalies(self, run) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        dq = self._res(run, "data_quality")
        cs = self._res(run, "column_stats")
        if not dq:
            return out
        mad = {c["column"]: c.get("outliers", {}) for c in (cs["data"]["columns"] if cs else [])}
        for issue in dq["data"]["issues"]:
            if issue["code"] != "OUTLIERS":
                continue
            col = issue["columns"][0]
            o = mad.get(col, {})
            confirmed = bool(o.get("mad_flagged_count"))
            out.append(self._base("anomaly", f"{_fmt(issue['count'])} value(s) of '{col}' sit far outside the usual range" + (" (confirmed by a second, outlier-resistant rule)" if confirmed else "") + ". "
                                  "They may be errors or genuine extremes; they are not removed from any calculation unless you filter them.",
                                  f"anomaly:{col}", run, dq, columns=[col], rows=issue.get("row_numbers"), rows_count=issue["count"],
                                  uncertainty={"available": False, "reason": "A rule-based flag, not a probability. Different rules flag different values."},
                                  extra={"values": {"rule": issue["method"], "count": issue["count"], "mad_flagged": o.get("mad_flagged_count"), "lower_fence": o.get("lower_fence"), "upper_fence": o.get("upper_fence")}}))
        return out

    def _patterns(self, run) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        d = self._res(run, "discover")
        if not d:
            return out
        for p in d["data"]["patterns"]:
            if p["abs_strength"] < MIN_STRENGTH_FOR_INSIGHT:
                continue
            x, y = p["x"], p["y"]
            if p["kind"] == "num_num":
                direction = "higher" if p["direction"] == "positive" else "lower"
                text = (f"Higher '{x}' tends to go with {direction} '{y}' (Spearman rho {_fmt(p['strength'], 2)}, {p['practical_relevance']} strength, n = {_fmt(p['n'])}, "
                        f"adjusted {_p(p['p_adjusted_bh'])}). This is an association, not evidence that one causes the other.")
                cat = "relationship"
            elif p["kind"] == "num_cat":
                text = (f"'{x}' differs between groups of '{y}' (eta {_fmt(p['strength'], 2)}, {p['practical_relevance']} strength, n = {_fmt(p['n'])}, adjusted {_p(p['p_adjusted_bh'])}). "
                        "This describes a difference in the data, not its cause.")
                cat = "group_difference"
            else:
                text = (f"'{x}' and '{y}' are associated (Cramer's V {_fmt(p['strength'], 2)}, {p['practical_relevance']} strength, n = {_fmt(p['n'])}, adjusted {_p(p['p_adjusted_bh'])}). "
                        "This is an association, not a cause.")
                cat = "relationship"
            out.append(self._base(cat, text, f"{cat}:{'|'.join(sorted((x, y)))}", run, d, columns=[x, y], rows_count=p["n"],
                                  uncertainty={"available": True, "kind": "BH-adjusted p-value over all pairs examined; agreement across five random subsets", "p_adjusted_bh": p["p_adjusted_bh"],
                                               "stability": p["stability"], "pairs_examined": d["data"]["pairs_examined"]},
                                  extra={"values": {k: p.get(k) for k in ("strength", "direction", "p_value", "p_adjusted_bh", "n", "coverage", "stability", "practical_relevance", "measure")}}))
        return out

    def _trend(self, run) -> List[Dict[str, Any]]:
        t = self._res(run, "trend")
        if not t:
            return []
        tr = t["data"]["trend"]
        if tr.get("conclusion") == NOT_ENOUGH_EVIDENCE or tr.get("slope_p_value") is None or tr["slope_p_value"] >= 0.05 or tr.get("kendall_p_value", 1) >= 0.05:
            return []
        d = t["data"]
        word = "rising" if tr["slope_per_period"] > 0 else "falling"
        return [self._base("trend", f"The {d['agg']} of '{d['measure']}' is {word} over time by {_fmt(abs(tr['slope_per_period']), 2)} per {d['freq']} "
                                    f"(95% CI {_fmt(tr['slope_ci'][0], 2)} to {_fmt(tr['slope_ci'][1], 2)}, {_p(tr['slope_p_value'])}, {tr['periods_used']} periods). "
                                    "A trend in past data does not predict the future.", f"trend:{d['date']}:{d['measure']}", run, t, columns=[d["date"], d["measure"]],
                           uncertainty={"available": True, "slope_ci": tr["slope_ci"], "confidence": tr["confidence"], "slope_p_value": tr["slope_p_value"], "kendall_p_value": tr["kendall_p_value"]},
                           extra={"values": tr})]

    def _test_ops(self, run) -> List[Dict[str, Any]]:
        d = self._res(run, "discover")
        if not d:
            return []
        family = max(1, int(d["data"]["pairs_examined"]))
        ops = []
        for p in d["data"]["patterns"]:
            if p["abs_strength"] < MIN_STRENGTH_FOR_INSIGHT or len(ops) >= TOP_STATISTICAL:
                continue
            params = {k: v for k, v in p["evidence_op"].items() if k not in ("op",)}
            params["family_size"] = min(family, 10000)
            ops.append({"op": "hypothesis", "params": params})
        return ops

    def _statistical(self, run) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i, r in enumerate(run["result"]["results"]):
            if r["op"] != "hypothesis" or r["status"] != "ok":
                continue
            r = {**r, "_index": i}
            data = r["data"]
            result = data.get("result")
            if not result or not data.get("executed"):
                continue                     # assumptions not established: no statistical claim is made
            h, es = data["hypothesis"], result.get("effect_size") or {}
            dec = result["decision"]
            ci = es.get("ci")
            size = (r.get("evidence") or {}).get("sample_size")
            text = (f"{result['test']}: {h['statement']} {result['statistic_name']} = {_fmt(result['statistic'], 2)}"
                    + (f", df = {', '.join(_fmt(v, 1) for v in (result['df'] if isinstance(result['df'], (list, tuple)) else [result['df']]))}" if result.get("df") is not None else "") + f", {_p(result['p_value'])}"
                    + (f"; effect size ({es.get('name', 'effect')}) {_fmt(es.get('value'), 2)}" + (f" (95% CI {_fmt(ci[0], 2)} to {_fmt(ci[1], 2)})" if ci else "") if es else "")
                    + f"; n = {_fmt(size)}. " + ("The data are unlikely under the null hypothesis. " if dec["reject_null"] else "The data do not rule out the null hypothesis. ")
                    + f"This was one of {data['multiple_testing']['family_size']} pairs examined, so a single p-value overstates certainty. {CORRELATION_NOTE}")
            if not dec["reject_null"]:
                continue
            cols = [v for k, v in data["design"].items() if k in ("outcome", "group", "x", "y", "second") and isinstance(v, str)]
            out.append(self._base("statistical", text, f"statistical:{data['hypothesis']['kind']}:{'|'.join(sorted(cols))}", run, r, columns=cols, rows_count=size,
                                  uncertainty={"available": True, "p_value": result["p_value"], "effect_size": es, "family_size": data["multiple_testing"]["family_size"]},
                                  extra={"values": {"test": result["test"], "statistic": result["statistic"], "df": result.get("df"), "p_value": result["p_value"], "effect_size": es},
                                         "assumption_checks": data.get("assumption_checks", [])}, run_tag="statistical"))
        return out

    # ------------------------------------------------------------------ storage and state
    def _store(self, principal: Principal, run: Mapping[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
        insight_id, _state = self.repo.upsert_insight(id=new_id(), run_id=run["id"], project_id=run["result"]["evidence"]["source"]["project_id"], asset_id=run["asset_id"],
                                                      version_id=run["version_id"], owner=principal.user_id, category=item["category"], statement=item["statement"],
                                                      dedupe_key=item["dedupe_key"], evidence_status=run["evidence_status"], payload=json.dumps(item["payload"]), now=self.ctx.clock.iso())
        return self.get(principal, insight_id)

    def _present(self, row) -> Dict[str, Any]:
        payload = _json(row["payload"], {})
        out = {"id": row["id"], "category": row["category"], "statement": row["statement"], "state": row["state"], "evidence_status": row["evidence_status"], "run_id": row["run_id"],
               "asset_id": row["asset_id"], "version_id": row["version_id"], "created_at": row["created_at"], "updated_at": row["updated_at"], **{k: v for k, v in payload.items() if k not in ("statement", "category", "evidence_status")}}
        out["explanation"] = explain("insight", out)
        return out

    def get(self, principal: Principal, insight_id: Any) -> Dict[str, Any]:
        row = self.repo.insight(principal.user_id, insight_id) if isinstance(insight_id, str) and 0 < len(insight_id) <= 64 else None
        if row is None:
            raise not_found("insight")
        self.an.assets.get_owned(principal, row["asset_id"])
        return self._present(row)

    def list(self, principal: Principal, asset_id: str, *, state: Optional[str] = None, version_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        asset = self.an.assets.get_owned(principal, asset_id)
        if state is not None and state not in InsightState.ALL:
            raise plan_invalid("state", "Use new, saved or dismissed.")
        limit = max(1, min(int(limit), 100))
        rows, total = self.repo.insights_for_asset(principal.user_id, asset["id"], state=state, version_id=version_id, limit=limit, offset=max(0, int(offset)))
        return {"items": [self._present(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def set_state(self, principal: Principal, insight_id: str, state: str) -> Dict[str, Any]:
        if state not in InsightState.ALL:
            raise plan_invalid("state", "Use new, saved or dismissed.")
        self.get(principal, insight_id)
        self.repo.set_insight_state(insight_id, state, self.ctx.clock.iso())
        self.ctx.audit.record(AEvent.INSIGHT_STATE, "success", actor=principal.user_id, target=insight_id, metadata={"state": state})
        return self.get(principal, insight_id)
