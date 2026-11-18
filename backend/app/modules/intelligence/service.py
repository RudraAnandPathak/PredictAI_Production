"""Advanced Intelligence: root-cause exploration, metric explanations, scenario analysis and causal-method candidates.

Every result is stored unchanged (adv_runs) with five separate registers: retrieved facts, calculated results, model interpretations, suggestions and unresolved questions, and with the
seed, library versions, input fingerprint and timestamp needed to reproduce it. Nothing in this module says a correlation is a cause."""
from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from ...core.errors import AppError
from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page, sha256_text
from ..reports import composer as C
from ..reports import evidence as ev
from . import methods as M

MAX_ROWS = 200_000
REGISTERS = ("retrieved_facts", "calculated_results", "model_interpretations", "suggestions", "unresolved_questions")
METRIC_WORDS = {
    "accuracy": ("Share of rows the model labelled correctly.", "Misleading when one outcome is much rarer than the other."),
    "precision": ("Of the rows the model flagged as positive, the share that really were.", "Says nothing about positives the model missed."),
    "recall": ("Of the rows that really were positive, the share the model found.", "Says nothing about false alarms."),
    "f1": ("A single score that balances precision and recall.", "Hides which of the two is weaker."),
    "roc_auc": ("How well the model ranks a positive row above a negative one (0.5 is chance, 1 is perfect).", "Does not say whether the predicted probabilities are well calibrated."),
    "mae": ("The average size of the model's miss, in the units of the outcome.", "Treats a large miss and a small miss in proportion."),
    "rmse": ("A typical size of the model's miss, in the units of the outcome; large misses count extra.", "A few very large misses can dominate it."),
    "mse": ("The average squared miss.", "In squared units, so it is hard to read directly."),
    "r2": ("The share of the outcome's variation the model accounts for on held-out rows (1 is perfect, 0 is no better than the average).", "A high value does not mean the inputs produce the outcome."),
}


class IntelligenceService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver, registry: Any) -> None:
        super().__init__(ctx, ws)
        self.access, self.registry = access, registry

    # ------------------------------------------------------------------ plumbing
    def _frame(self, principal: Principal, asset_id: Any, version_id: Any = None):
        asset, version, _declared, _types = self.ws.analytics._open(principal, asset_id, version_id)    # owner-only: raises not-found for anyone else
        with self.ws.storage.open(version["canonical_key"]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            try:
                df = pd.read_csv(text, nrows=MAX_ROWS + 1, low_memory=False)
            finally:
                text.detach()
        truncated = len(df) > MAX_ROWS
        return asset, version, df.head(MAX_ROWS), truncated

    @staticmethod
    def _registers() -> Dict[str, List[Dict[str, Any]]]:
        return {k: [] for k in REGISTERS}

    def _store(self, principal: Principal, *, kind: str, project_id: str, title: str, params: Dict[str, Any], result: Dict[str, Any], regs: Dict[str, Any], asset_id: Any = None, version_id: Any = None,
               model_id: Any = None, seed: int = 42, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        for k in REGISTERS:
            if not regs.get(k) and k != "unresolved_questions":
                regs.setdefault(k, [])
        now = self.now()
        repro = {"seed": seed, "versions": M.library_versions(), "created_at": now, "inputs": inputs or {}, "params_sha256": sha256_text(jdump(params)), "how": "Run the same method on the same dataset version with the same parameters and seed; the result hash must match."}
        digest = sha256_text(jdump({"kind": kind, "params": params, "result": result, "inputs": inputs or {}}))
        rid = new_id()
        with self.ctx.conn:
            self.ctx.conn.execute("INSERT INTO adv_runs (id, project_id, owner_user_id, kind, asset_id, version_id, model_id, title, params, result, registers, reproduction, result_sha256, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (rid, project_id, principal.user_id, kind, asset_id, version_id, model_id, title, jdump(params), jdump(result), jdump(regs), jdump(repro), digest, now))
        self.audit(E.ADV_RUN, principal, rid, kind=kind)
        return self.get_run(principal, rid)

    def get_run(self, principal: Principal, run_id: str) -> Dict[str, Any]:
        acc = self.access.resolve(principal, "analysis", run_id, "view")
        r = self.conn.execute("SELECT * FROM adv_runs WHERE id = ?", (run_id,)).fetchone()
        return {"id": r["id"], "kind": r["kind"], "title": r["title"], "project_id": r["project_id"], "asset_id": r["asset_id"], "version_id": r["version_id"], "model_id": r["model_id"], "params": jload(r["params"], {}),
                "result": jload(r["result"], {}), "registers": jload(r["registers"], {}), "reproduction": jload(r["reproduction"], {}), "result_sha256": r["result_sha256"], "created_at": r["created_at"], "your_access": acc.level}

    def list_runs(self, principal: Principal, project_id: Optional[str] = None, *, kind: Any = None, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        lim, off = page(limit, offset)
        if project_id:
            self.project(principal, project_id)
        sql, args = "FROM adv_runs WHERE owner_user_id = ?", [principal.user_id]
        if project_id:
            sql += " AND project_id = ?"
            args.append(project_id)
        if kind:
            sql += " AND kind = ?"
            args.append(choice(kind, "kind", ("root_cause", "metric", "scenario", "causal")))
        total = self.conn.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]
        rows = self.conn.execute("SELECT id, kind, title, created_at " + sql + " ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, lim, off)).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "limit": lim, "offset": off, "shared_with_me": self.access.shared_with(principal, "analysis")}

    @staticmethod
    def _col(df: pd.DataFrame, name: Any, field: str) -> str:
        if not isinstance(name, str) or name not in df.columns:
            raise invalid(field, "Choose a column that exists in the dataset.")
        return name

    # ------------------------------------------------------------------ root cause exploration
    def root_cause(self, principal: Principal, asset_id: str, *, outcome: Any, top_k: Any = 8, question: Any = None, version_id: Any = None) -> Dict[str, Any]:
        """Which columns go with the outcome, how strongly, and what the data cannot say. Candidate explanations are hypotheses, never conclusions."""
        self.limit("p7_intel:user", principal)
        asset, version, df, truncated = self._frame(principal, asset_id, version_id)
        outcome = self._col(df, outcome, "outcome")
        k = top_k if isinstance(top_k, int) and not isinstance(top_k, bool) and 1 <= top_k <= 20 else 8
        try:
            res = M.associations(df, outcome, k)
        except ValueError as exc:
            raise invalid("outcome", str(exc)) from None
        regs = self._registers()
        regs["retrieved_facts"] += [{"text": f"Dataset “{asset['name']}” version {version['version_number']}: {res['rows_used']} rows had a value for “{outcome}”.", "source": "dataset"}]
        if truncated:
            regs["retrieved_facts"].append({"text": f"Only the first {MAX_ROWS:,} rows were read.", "source": "limit"})
        for a in res["associations"]:
            regs["calculated_results"].append({"text": f"“{a['column']}”: {a['measure']} = {a['value']} (n = {a['n']}, adjusted p = {a['p_adjusted']}).", "column": a["column"], "n": a["n"]})
            tag = "is statistically distinguishable from chance after allowing for the number of columns tested" if a["supported"] else "could be chance once the number of columns tested is allowed for"
            regs["model_interpretations"].append({"text": f"“{a['column']}” goes with “{outcome}” ({a['direction']}); the pattern {tag}. {M.ASSOCIATION_NOTE}", "column": a["column"], "label": "association"})
        for o in res["overlap"]:
            regs["model_interpretations"].append({"text": f"“{o['columns'][0]}” and “{o['columns'][1]}” move together (rank correlation {o['correlation']}). {o['note']}", "label": "overlap"})
        if res["associations"]:
            top = res["associations"][0]
            regs["suggestions"].append({"text": f"Check how “{top['column']}” is recorded and whether it is known before “{outcome}” is determined; if it is, it is a sensible first candidate to examine further."})
            regs["suggestions"].append({"text": "To test whether any of these columns changes the outcome, compare groups in a planned experiment or use the causal-method screening in this module; the figures above cannot settle it."})
        regs["unresolved_questions"] += [{"text": "Which of these columns existed before the outcome, and which were recorded afterwards?"}, {"text": "Are there factors that are not in this dataset but affect both a listed column and the outcome?"},
                                         {"text": f"{res['tests_run']} columns were tested, so a few will look related by chance; do they hold on a second period or a second dataset?"}]
        if question:
            regs["retrieved_facts"].append({"text": "Your question: " + clean(question, "question", maximum=300), "source": "user"})
        seed = 42
        return self._store(principal, kind="root_cause", project_id=asset["project_id"], title=f"What goes with “{outcome}”", params={"outcome": outcome, "top_k": k}, result=res, regs=regs, asset_id=asset["id"], version_id=version["id"], seed=seed,
                           inputs={"dataset_version_id": version["id"], "dataset_sha256": version["checksum_sha256"], "rows": res["rows_used"]})

    # ------------------------------------------------------------------ metric explanation
    def explain_metric(self, principal: Principal, asset_id: Any = None, *, column: Any = None, statistic: Any = "mean", by: Any = None, model_version_id: Any = None, metric: Any = None, version_id: Any = None) -> Dict[str, Any]:
        self.limit("p7_intel:user", principal)
        regs = self._registers()
        if model_version_id is not None:
            v = self.registry.get_version(principal, model_version_id)
            m = self.registry.get(principal, v["model_id"])
            evl = v["evaluation"]
            tm = ((evl.get("test") or {}).get("metrics")) or {}
            metric = metric or v["metadata"].get("primary_metric")
            if metric not in tm:
                raise invalid("metric", "Choose one of: " + ", ".join(sorted(tm)) + ".")
            meaning, caution = METRIC_WORDS.get(metric, ("A score reported by the evaluation.", "Check how it is defined before comparing."))
            val = tm[metric]
            regs["retrieved_facts"].append({"text": f"{m['name']} version {v['version']} was evaluated on held-out rows: {metric} = {val}.", "source": "registry"})
            regs["calculated_results"].append({"text": f"{metric} = {val} on the held-out rows; cross-validation score on the training rows = {(evl.get('validation') or {}).get('score')}.", "column": None})
            regs["model_interpretations"].append({"text": f"{meaning} {caution}", "label": "definition"})
            regs["unresolved_questions"].append({"text": "This score describes the held-out rows only; how the model performs on new data has not been measured."})
            regs["suggestions"].append({"text": "Compare with the other held-out metrics before judging the model on this one."})
            return self._store(principal, kind="metric", project_id=m["project_id"], title=f"What {metric} means for {m['name']} v{v['version']}", params={"model_version_id": model_version_id, "metric": metric},
                               result={"metric": metric, "value": val, "meaning": meaning, "caution": caution, "basis": "hold-out"}, regs=regs, model_id=v["source_model_id"] if v.get("source_model_id") else None,
                               inputs={"artifact_sha256": v["artifact"]["sha256"]})
        stat = choice(statistic, "statistic", ("mean", "median", "sum", "min", "max", "std", "count", "rate"))
        asset, version, df, truncated = self._frame(principal, asset_id, version_id)
        col = self._col(df, column, "column")
        grp = self._col(df, by, "by") if by else None
        try:
            res = M.describe_metric(df, col, stat, grp)
        except ValueError as exc:
            raise invalid("column", str(exc)) from None
        word = {"mean": "average", "median": "middle value", "sum": "total", "min": "smallest value", "max": "largest value", "std": "spread (standard deviation)", "count": "number of filled-in values", "rate": "share of rows that are “yes”"}[stat]
        regs["retrieved_facts"].append({"text": f"Dataset “{asset['name']}” version {version['version_number']}, column “{col}”, {res['rows']} rows, {res['missing']} missing.", "source": "dataset"})
        regs["calculated_results"].append({"text": f"The {word} of “{col}” is {res['value']}. Rows with no value are left out of the calculation.", "column": col})
        if res.get("distribution"):
            d = res["distribution"]
            regs["calculated_results"].append({"text": f"Spread: minimum {d['min']}, middle half between {d['q1']} and {d['q3']}, maximum {d['max']}.", "column": col})
        if res.get("outliers"):
            regs["model_interpretations"].append({"text": f"{res['outliers']} value(s) lie far from the rest, and the {word} may be pulled by them" + (f" (the mean without them is {res['outlier_effect']['mean_without_outliers']})." if res.get("outlier_effect") else "."), "label": "outliers"})
        if res["missing_share"] > 0.05:
            regs["unresolved_questions"].append({"text": f"{res['missing_share'] * 100:.0f}% of “{col}” is missing; are the missing rows different from the rest?"})
        regs["suggestions"].append({"text": "Check the unit and the period the column covers before quoting this figure."})
        if "by" in res:
            regs["calculated_results"].append({"text": "By “" + res["by"]["column"] + "”: " + "; ".join(f"{g['group']} = {g['value']} (n={g['n']})" for g in res["by"]["groups"][:6]) + ".", "column": col})
            regs["model_interpretations"].append({"text": "Groups differ in this column's figure. " + M.ASSOCIATION_NOTE, "label": "association"})
        return self._store(principal, kind="metric", project_id=asset["project_id"], title=f"{stat.title()} of {col}", params={"column": col, "statistic": stat, "by": grp}, result=res, regs=regs, asset_id=asset["id"], version_id=version["id"],
                           inputs={"dataset_version_id": version["id"], "dataset_sha256": version["checksum_sha256"], "rows": res["rows"]})

    # ------------------------------------------------------------------ scenario analysis
    @staticmethod
    def _score(p: Dict[str, Any]) -> Dict[str, Any]:
        if p.get("positive"):
            return {"measure": f"probability of “{p['positive']['class']}”", "value": float(p["positive"]["probability"]), "label": p["prediction"].get("label")}
        pr = p.get("prediction") or {}
        return {"measure": "predicted value", "value": float(pr.get("value")) if pr.get("value") is not None else None, "label": None}

    def _check_inputs(self, schema: Sequence[Dict[str, Any]], values: Any, where: str, *, partial: bool = False):
        if not isinstance(values, dict) or not values:
            raise invalid(where, "Give the inputs as name/value pairs.")
        by = {c["name"]: c for c in schema}
        warn: List[Dict[str, Any]] = []
        out: Dict[str, Any] = {}
        for k, v in values.items():
            c = by.get(k)
            if c is None:
                raise invalid(where, f"“{k}” is not an input of this model. Inputs: " + ", ".join(by) + ".")
            if c["role"] == "numeric":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise invalid(where, f"“{k}” must be a finite number.")
                lo, hi = c["min"], c["max"]
                width = (hi - lo) or abs(hi) or 1.0
                if v < lo - 10 * width or v > hi + 10 * width:
                    raise invalid(where, f"“{k}” = {v} is more than ten times the training range away ({lo} to {hi}); a model cannot say anything useful there.")
                if v < lo or v > hi:
                    warn.append({"input": k, "value": v, "training_range": [lo, hi], "message": f"“{k}” = {v} is outside the range seen in training ({lo} to {hi}). The model is extrapolating; treat the result as unreliable."})
            else:
                if str(v) not in [str(x) for x in c.get("levels", [])]:
                    raise invalid(where, f"“{k}” = {v!r} was never seen in training. Seen: " + ", ".join(map(str, c.get("levels", []))) + ".")
            out[k] = v
        if not partial:
            missing = [n for n in by if n not in out]
            if missing:
                raise invalid(where, "Missing inputs: " + ", ".join(missing) + ".")
        return out, warn

    def scenario(self, principal: Principal, model_id: Any = None, *, version_id: Any = None, baseline: Any, scenarios: Any, sensitivity: Any = None, label: Any = None) -> Dict[str, Any]:
        """Compare what the model returns for a baseline row and for changed copies of it. The baseline is stored and never altered."""
        self.limit("p7_intel:user", principal)
        if (model_id is None) == (version_id is None):
            raise invalid("model_id", "Give either a trained model or a registered version.")
        lineage: Dict[str, Any] = {}
        if version_id is not None:
            v = self.registry.get_version(principal, version_id)
            if v.get("source_model_id") is None:
                raise invalid("version_id", "This version has no live model behind it.")
            m5 = self.ws.predictive.get_model(principal, v["source_model_id"])
            lineage = {"registry_version_id": version_id, "artifact_sha256": v["artifact"]["sha256"], "version": v["version"]}
        else:
            m5 = self.ws.predictive.get_model(principal, model_id)
        model_id = m5["id"]
        schema = m5["input_schema"]
        base, bwarn = self._check_inputs(schema, baseline, "baseline")
        if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 8:
            raise invalid("scenarios", "Give one to eight scenarios.")
        predict = lambda vals: self.ws.predictive.predict(principal, model_id, vals, explain_local=False, save=False)  # noqa: E731
        base_pred = predict(base)
        base_score = self._score(base_pred)
        out_sc, flags = [], list(bwarn)
        for i, sc in enumerate(scenarios):
            if not isinstance(sc, dict) or not isinstance(sc.get("changes"), dict) or not sc["changes"]:
                raise invalid("scenarios", f"Scenario {i + 1} needs a name and a set of changes.")
            changes, w = self._check_inputs(schema, sc["changes"], f"scenarios[{i}]", partial=True)
            vals = {**base, **changes}
            sp = predict(vals)
            s = self._score(sp)
            d = None if s["value"] is None or base_score["value"] is None else round(s["value"] - base_score["value"], 6)
            out_sc.append({"name": clean(sc.get("name"), "name", maximum=80, required=False) or f"Scenario {i + 1}", "changes": changes, "inputs": vals, "score": s, "difference_from_baseline": d, "warnings": w,
                           "out_of_range": bool(w), "notes": [n["message"] if isinstance(n, dict) else str(n) for n in sp.get("input_issues", [])][:4]})
            flags += w
        sens: List[Dict[str, Any]] = []
        sp_ = sensitivity if isinstance(sensitivity, dict) else {}
        pct = sp_.get("percent", 10)
        if sp_.get("enabled", True) is not False:
            if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 1 <= pct <= 50:
                raise invalid("sensitivity.percent", "Use a percentage from 1 to 50.")
            wanted = sp_.get("features")
            cols = [c for c in schema if c["name"] in base and (wanted is None or c["name"] in wanted)][:6]
            for c in cols:
                try:
                    if c["role"] == "numeric":
                        step = (abs(base[c["name"]]) * pct / 100) or ((c["max"] - c["min"]) * pct / 100) or 1.0
                        pts = [max(c["min"], base[c["name"]] - step), min(c["max"], base[c["name"]] + step)]
                        res = []
                        for x in pts:
                            if x == base[c["name"]]:
                                continue
                            sc = self._score(predict({**base, c["name"]: x}))
                            res.append({"value": round(float(x), 6), "score": sc["value"], "difference": None if sc["value"] is None or base_score["value"] is None else round(sc["value"] - base_score["value"], 6)})
                        sens.append({"input": c["name"], "type": "numeric", "step": f"±{pct}% (kept inside the training range)", "points": res, "largest_change": max([abs(r["difference"]) for r in res if r["difference"] is not None] or [0.0])})
                    else:
                        res = []
                        for lv in c.get("levels", [])[:5]:
                            if str(lv) == str(base[c["name"]]):
                                continue
                            sc = self._score(predict({**base, c["name"]: lv}))
                            res.append({"value": lv, "score": sc["value"], "difference": None if sc["value"] is None or base_score["value"] is None else round(sc["value"] - base_score["value"], 6)})
                        sens.append({"input": c["name"], "type": "categorical", "step": "each other category", "points": res, "largest_change": max([abs(r["difference"]) for r in res if r["difference"] is not None] or [0.0])})
                except AppError:
                    continue
            sens.sort(key=lambda s: -s["largest_change"])
        regs = self._registers()
        regs["retrieved_facts"] += [{"text": f"Model “{m5['name']}” ({m5['algorithm_label']}) predicting “{m5['target']}”; hold-out {m5['primary_metric']} = {m5['test_score']}.", "source": "model"}, {"text": "Baseline inputs: " + ", ".join(f"{k} = {v}" for k, v in base.items()) + ".", "source": "user"}]
        regs["calculated_results"].append({"text": f"Baseline: {base_score['measure']} = {round(base_score['value'], 4) if base_score['value'] is not None else 'n/a'}.", "column": None})
        for s in out_sc:
            regs["calculated_results"].append({"text": f"{s['name']} ({', '.join(f'{k} = {v}' for k, v in s['changes'].items())}): {s['score']['measure']} = {round(s['score']['value'], 4)} ({'+' if (s['difference_from_baseline'] or 0) >= 0 else ''}{s['difference_from_baseline']} from baseline).", "column": None})
        for s in sens[:3]:
            regs["model_interpretations"].append({"text": f"The model's output is most sensitive to “{s['input']}” (largest change {round(s['largest_change'], 4)} for {s['step']}). This describes how the model responds, not what would happen in the real world.", "label": "sensitivity"})
        regs["model_interpretations"].append({"text": "A scenario shows what the trained model returns for different inputs. The model learned patterns in past data; changing an input here does not show that changing it in practice would change the outcome.", "label": "scope"})
        if flags:
            regs["unresolved_questions"] += [{"text": f["message"]} for f in flags[:6]]
        regs["unresolved_questions"].append({"text": "Which of these changes could actually be made, and would other inputs change with them?"})
        regs["suggestions"].append({"text": "Prefer scenarios inside the training range; where a scenario is outside it, collect data there before relying on the number."})
        result = {"baseline": {"inputs": base, "score": base_score, "warnings": bwarn}, "scenarios": out_sc, "sensitivity": sens, "out_of_range_warnings": flags, "model": {"id": model_id, "name": m5["name"], "target": m5["target"], "problem_type": m5["problem_type"], **lineage}}
        return self._store(principal, kind="scenario", project_id=m5["project_id"], title=clean(label, "label", maximum=120, required=False) or f"Scenarios for {m5['name']}", params={"model_id": model_id, "version_id": version_id, "baseline": base, "scenarios": [{"name": s["name"], "changes": s["changes"]} for s in out_sc], "sensitivity": {"percent": pct}},
                           result=result, regs=regs, asset_id=m5["asset_id"], model_id=model_id, seed=m5["seed"], inputs={"model_sha256": m5["model_sha256"], **lineage})

    # ------------------------------------------------------------------ causal-method candidates
    def causal_candidates(self, principal: Principal, asset_id: str, *, treatment: Any, outcome: Any, covariates: Any = None, time_column: Any = None, group_column: Any = None, seed: Any = 42, version_id: Any = None) -> Dict[str, Any]:
        """Which causal methods could be considered for this data, what each assumes, which assumptions the data can partly check, and an *association* estimate. It never reports a causal effect."""
        self.limit("p7_intel:user", principal)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2_000_000_000:
            raise invalid("seed", "Use a whole number.")
        asset, version, df, truncated = self._frame(principal, asset_id, version_id)
        t = self._col(df, treatment, "treatment")
        o = self._col(df, outcome, "outcome")
        cov = [self._col(df, c, "covariates") for c in (covariates or [])][:12] if isinstance(covariates, list) or covariates is None else None
        if cov is None:
            raise invalid("covariates", "Give a list of column names.")
        if t == o or t in cov or o in cov:
            raise invalid("covariates", "Treatment, outcome and covariates must be different columns.")
        tcol = self._col(df, time_column, "time_column") if time_column else None
        gcol = self._col(df, group_column, "group_column") if group_column else None
        try:
            est = M.adjusted_difference(df, t, o, cov, seed)
        except ValueError as exc:
            raise invalid("treatment", str(exc)) from None
        n = est["n_treated"] + est["n_untreated"]
        imbalance = [b for b in est["balance"] if abs(b["standardised_difference"]) > 0.1]
        balanced = not imbalance
        cands = [
            {"method": "Analysis of a randomised experiment", "status": "needs_information", "why": "Only valid if treatment was assigned at random. The data cannot show that.",
             "checks": [{"name": "Groups look alike on the covariates you gave", "passed": balanced if cov else None, "detail": ("No covariate differs by more than 0.1 standard deviations." if balanced else f"{len(imbalance)} covariate(s) differ by more than 0.1 standard deviations.") if cov else "No covariates were given."}],
             "assumptions": ["Treatment was assigned at random (or as good as random).", "No one received a different treatment than assigned."]},
            {"method": "Regression adjustment", "status": "possible" if cov and n >= 50 else "needs_information", "why": "Compares treated and untreated rows after accounting for the covariates you listed.",
             "checks": [{"name": "At least 50 rows", "passed": n >= 50, "detail": f"{n} rows"}, {"name": "Covariates supplied", "passed": bool(cov), "detail": f"{len(cov)} covariate(s)"}],
             "assumptions": ["Every factor that affects both treatment and outcome is among the covariates (this cannot be tested).", "The covariates were measured before treatment."]},
            {"method": "Propensity-score matching or weighting", "status": "possible" if cov and min(est["n_treated"], est["n_untreated"]) >= 30 else "needs_information", "why": "Pairs or weights rows by how likely they were to be treated.",
             "checks": [{"name": "At least 30 rows in each group", "passed": min(est["n_treated"], est["n_untreated"]) >= 30, "detail": f"{est['n_treated']} treated, {est['n_untreated']} untreated"}],
             "assumptions": ["No unmeasured factor affects both treatment and outcome.", "Every kind of row had some chance of either treatment (overlap)."]},
            {"method": "Difference-in-differences", "status": "possible" if tcol and gcol else "needs_information", "why": "Compares the change over time between treated and untreated groups.",
             "checks": [{"name": "A time column and a group column were given", "passed": bool(tcol and gcol), "detail": f"time = {tcol}, group = {gcol}"}],
             "assumptions": ["Without treatment, both groups would have moved in parallel (cannot be tested after the fact; look at the periods before treatment)."]},
            {"method": "Instrumental variable", "status": "needs_information", "why": "Needs a column that changes treatment but affects the outcome only through treatment. None can be identified from the data alone.", "checks": [], "assumptions": ["The instrument is strongly related to treatment.", "The instrument affects the outcome only through treatment (cannot be tested)."]},
            {"method": "Regression discontinuity", "status": "needs_information", "why": "Needs treatment to switch on at a sharp cut-off of a measured score.", "checks": [], "assumptions": ["Rows just either side of the cut-off are otherwise alike."]},
        ]
        regs = self._registers()
        regs["retrieved_facts"].append({"text": f"Dataset “{asset['name']}” version {version['version_number']}: {n} rows; treatment “{t}” (group “{est['treated_value']}” = treated, {est['n_treated']} rows), outcome “{o}”.", "source": "dataset"})
        regs["calculated_results"] += [{"text": f"Unadjusted difference in “{o}” between treated and untreated rows: {est['unadjusted_difference']} (95% bootstrap interval {est['unadjusted_interval_95']}).", "column": o},
                                       {"text": f"Difference after adjusting for {', '.join(cov) if cov else 'nothing (no covariates given)'}: {est['adjusted_difference']} (95% bootstrap interval {est['adjusted_interval_95']}).", "column": o}]
        regs["model_interpretations"].append({"text": "Both figures are associations between group membership and the outcome in this data. " + M.ASSOCIATION_NOTE, "label": "association"})
        if cov and abs(est["adjusted_difference"] - est["unadjusted_difference"]) > 0.25 * (abs(est["unadjusted_difference"]) or 1):
            regs["model_interpretations"].append({"text": "The figure changes noticeably once the covariates are included, which means the groups differ in ways that matter; the remaining difference may still reflect factors not in the data.", "label": "confounding signal"})
        regs["suggestions"].append({"text": "If the treatment can be assigned, a randomised comparison is the strongest design; otherwise list every factor that could influence both treatment and outcome and add it as a covariate."})
        regs["unresolved_questions"] += [{"text": "Was treatment chosen by the people involved, and on what basis?"}, {"text": "Are there factors that influence both treatment and outcome that are not in this dataset?"}, {"text": "Was every covariate measured before the treatment began?"}]
        result = {"estimates": est, "candidates": cands, "label": "Association estimates, not causal effects", "limits": M.ASSOCIATION_NOTE}
        return self._store(principal, kind="causal", project_id=asset["project_id"], title=f"Causal-method candidates: {t} and {o}", params={"treatment": t, "outcome": o, "covariates": cov, "time_column": tcol, "group_column": gcol, "seed": seed}, result=result, regs=regs,
                           asset_id=asset["id"], version_id=version["id"], seed=seed, inputs={"dataset_version_id": version["id"], "dataset_sha256": version["checksum_sha256"], "rows": n})

    # ------------------------------------------------------------------ for reports
    def report_pool(self, principal: Principal, project_id: str, run_ids: Sequence[str], es: ev.EvidenceSet) -> Dict[str, Dict[str, Any]]:
        summary, method, findings, analysis, limits, unresolved, recs, subs = [], [], [], [], [], [], [], []
        for rid in run_ids:
            r = self.get_run(principal, rid)
            if r["project_id"] != project_id:
                raise conflict("PROJECT_MISMATCH", "All sources of a report must come from the same project.")
            regs = r["registers"]
            ref = ev.calculation(es, key=f"adv:{rid}", title=r["title"], source_type="intelligence_run", calculation_ref=f"adv_run:{rid}", when=r["created_at"], dataset_version=r.get("version_id"),
                                 passage="; ".join(x["text"] for x in regs.get("calculated_results", [])[:4])[:900] or r["title"], limitation=M.ASSOCIATION_NOTE if r["kind"] in ("root_cause", "causal", "metric") else "A scenario shows how the trained model responds, not what would happen in practice.",
                                 uncertainty="; ".join(x["text"] for x in regs.get("unresolved_questions", [])[:2])[:300] or None, source_id=rid)
            summary.append(C.claim(f"{r['title']}.", [ref], "calculation"))
            method.append(C.claim(f"Run {rid[:8]} used seed {r['reproduction']['seed']}, {', '.join(f'{k} {v}' for k, v in list(r['reproduction']['versions'].items())[:4])}, and can be reproduced from the same inputs (result fingerprint {r['result_sha256'][:12]}).", [ref], "process"))
            findings += [C.claim(x["text"], [ref], "calculation") for x in regs.get("calculated_results", [])]
            analysis += [C.claim(x["text"], [ref], "interpretation") for x in regs.get("model_interpretations", [])]
            recs += [C.claim(x["text"], [], "suggestion") for x in regs.get("suggestions", [])]
            unresolved += [C.claim(x["text"], [ref], "process") for x in regs.get("unresolved_questions", [])]
            limits.append(C.claim(M.ASSOCIATION_NOTE if r["kind"] != "scenario" else "Scenario results describe the trained model, not the real world.", [ref], "process"))
            if r["kind"] == "scenario":
                sc = r["result"]["scenarios"]
                subs.append({"heading": r["title"], "blocks": [C.table("Scenarios against the baseline (model output)", ["Scenario", "Changes", "Output", "Difference from baseline", "Outside training range"],
                                                                 [["Baseline", "", round(r["result"]["baseline"]["score"]["value"], 4), 0, "no"]] + [[s["name"], ", ".join(f"{k}={v}" for k, v in s["changes"].items()), round(s["score"]["value"], 4), s["difference_from_baseline"], "yes" if s["out_of_range"] else "no"] for s in sc], [ref])], "status": "complete", "why": None})
        pool = {"summary": C.sec([C.para(summary)]), "method": C.sec([C.para(method)]), "findings": C.sec([C.para(findings)] if findings else [], subsections=subs), "analysis": C.sec([C.para(analysis)]) if analysis else C.empty("No interpretation was recorded."),
                "limitations": C.sec([C.para(limits)]), "unresolved": C.sec([C.para(unresolved)]) if unresolved else C.empty("No open questions were recorded."), "recommendations": C.sec([C.para(recs)]) if recs else C.empty("No suggestions were recorded."),
                "appendix": C.sec([C.note("The registers (retrieved facts, calculated results, interpretations, suggestions and open questions) are stored with each run.")])}
        return pool
