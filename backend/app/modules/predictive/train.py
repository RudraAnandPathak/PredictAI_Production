"""The AutoML run: prepare → pick compatible algorithms → cross-validate each → (optionally) tune the best → rank → evaluate the winner on held-out rows →
explain it → package it. A pure function of (typed table, validated configuration, seed): no database, no network, no storage. The caller supplies a
`Reporter` for progress, cancellation and the time budget.

Leakage rules this file enforces (each is tested):
  * the held-out test rows are never used to choose, tune or rank anything; models are ranked by cross-validation inside the training split;
  * every statistic (median, scaling factor, category list, clipping fence) is learned from the training part of each fold only, because the whole
    preprocessing lives inside the pipeline that is fitted per fold;
  * there is no target-aware feature selection step."""
from __future__ import annotations

import io
import math
import pickle
import platform
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.model_selection import ParameterGrid, ParameterSampler

from . import explain as X_
from . import metrics as M
from . import prep, problem as prob, validation as V
from .catalog import CATALOG, default_space, grid_size, kind_of, select
from .constants import BACKGROUND_ROWS, LIMITS, MIN_ROWS, ML_VERSION, SCHEMA_REPORT, Problem
from .errors import Cancelled, MlError, OutOfTime
from .pipeline import build_pipeline, class_weight_for, feature_map, resolve_scale, schema_of

RESERVE_SHARE = 0.30            # of the time budget kept for evaluation and explanation after the models are trained
MAX_TRIALS_LISTED = 60
TRAIN_SCORE_ROWS = 2000


# ------------------------------------------------------------------------------------------------ progress, cancel, time
class Reporter:
    """Progress and control channel. `on_progress(dict)` is called at most a few times a second; `is_cancelled()` is polled between units of work."""

    def __init__(self, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None, is_cancelled: Optional[Callable[[], bool]] = None, time_budget_s: float = 120.0) -> None:
        self.on_progress, self.is_cancelled = on_progress, is_cancelled
        self.started = time.monotonic()
        self.budget = float(time_budget_s)
        self.total_steps = 1
        self.done_steps = 0
        self._last = 0.0
        self.phase = "preparing"
        self.message = ""

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return self.budget - self.elapsed()

    def late(self, share: float = 1.0) -> bool:
        """True once `share` of the budget has been used."""
        return self.elapsed() >= self.budget * share

    def cancel_check(self) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise Cancelled()

    def plan(self, total: int) -> None:
        self.total_steps = max(1, int(total))

    def step(self, message: str, phase: Optional[str] = None, n: int = 1) -> None:
        self.cancel_check()
        self.done_steps = min(self.total_steps, self.done_steps + n)
        self.set(message, phase)

    def set(self, message: str, phase: Optional[str] = None, force: bool = False) -> None:
        self.message = message
        if phase:
            self.phase = phase
        now = time.monotonic()
        if self.on_progress and (force or now - self._last >= 0.25):
            self._last = now
            self.on_progress({"phase": self.phase, "message": message, "fraction": round(min(0.99, self.done_steps / self.total_steps), 4), "elapsed_s": round(self.elapsed(), 2),
                              "steps_done": self.done_steps, "steps_total": self.total_steps})


@dataclass
class TrainResult:
    report: Dict[str, Any]
    model_bytes: bytes
    background_csv: bytes
    meta: Dict[str, Any]


# ------------------------------------------------------------------------------------------------ helpers
def _safe_error(exc: BaseException) -> str:
    """A reason that never contains a data value (library messages often quote the offending value)."""
    name = type(exc).__name__
    if isinstance(exc, MemoryError):
        return "The algorithm ran out of memory on this data."
    if isinstance(exc, ValueError):
        return "The algorithm could not be fitted on this data (it rejected the prepared inputs)."
    return f"The algorithm failed while fitting ({name})."


def _class_order(labels: pd.Series) -> List[str]:
    uniq = sorted(set(labels.dropna()))
    try:
        return sorted(uniq, key=lambda s: float(s))
    except ValueError:
        return uniq


def _default_positive(classes: Sequence[str], counts: Mapping[str, int]) -> str:
    low = {c.lower(): c for c in classes}
    for pair in (("0", "1"), ("false", "true"), ("no", "yes"), ("n", "y"), ("negative", "positive"), ("neg", "pos")):
        if set(low) == set(pair):
            return low[pair[1]]
    return min(classes, key=lambda c: (counts[c], c))


def _mean_std(vals: Sequence[Optional[float]]) -> Tuple[Optional[float], Optional[float], int]:
    v = [x for x in vals if x is not None]
    if not v:
        return None, None, 0
    return M.f(np.mean(v)), (M.f(np.std(v, ddof=1)) if len(v) > 1 else None), len(v)


def _model_params(model: Any) -> Dict[str, Any]:
    est = getattr(model, "estimator", model)
    out: Dict[str, Any] = {}
    for k, v in est.get_params(deep=False).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, float) else M.f(v, 8)
    return out


def _json_params(p: Mapping[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in p.items():
        if isinstance(v, (np.integer,)):
            v = int(v)
        elif isinstance(v, (np.floating,)):
            v = float(v)
        out[k] = v
    return out


def _versions() -> Dict[str, str]:
    import scipy
    return {"python": platform.python_version(), "scikit_learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "joblib": joblib.__version__}


@dataclass
class Prepared:
    problem: str
    classes: List[str]
    positive: Optional[int]
    X: pd.DataFrame
    y: np.ndarray
    used: List[Dict[str, Any]]
    dropped: List[Dict[str, Any]]
    leaks: List[Dict[str, Any]]
    facts: List[Dict[str, Any]]
    det: Dict[str, Any]
    balance: Optional[Dict[str, Any]]
    plan: Dict[str, Any]
    tfacts: Dict[str, Any]
    width: int
    ordered: bool
    notes: Dict[str, Any] = field(default_factory=dict)


def prepare(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], *, extended: bool, rep: Reporter) -> Prepared:
    """Everything decided before any model sees data: which rows, which problem, which columns, how they are treated."""
    i = 1 if extended else 0
    target = cfg["target"]
    seed = cfg["seed"]
    if len(df.columns) - 1 > LIMITS["max_input_columns"][i]:
        raise MlError("TOO_MANY_COLUMNS", f"The table has {len(df.columns) - 1} input columns; the limit is {LIMITS['max_input_columns'][i]}. Exclude columns you do not need.")
    notes: Dict[str, Any] = {"rows_in_table": int(len(df))}
    labelled = df[df[target].notna()]
    notes["rows_without_target"] = int(len(df) - len(labelled))
    det = prob.detect(labelled[target], types[target], cfg["problem_type"])
    if not det["valid"]:
        raise MlError(det["errors"][0]["code"], det["errors"][0]["message"])
    problem = det["chosen"]
    notes["rare_class_rows_dropped"] = 0
    if problem in Problem.CLASSIFICATION and det["rare_classes"]:
        rare = det["rare_classes"]
        if cfg["rare_classes"] != "drop":
            raise MlError("RARE_CLASSES", f"{len(rare)} class(es) have fewer than {prob.RARE_CLASS_MIN} rows, too few to train and check on. Choose 'drop rare classes' to remove their rows.")
        keep = ~prob.class_labels(labelled[target]).isin(rare)
        notes["rare_class_rows_dropped"] = int((~keep).sum())
        notes["rare_classes_dropped"] = rare
        labelled = labelled[keep]
        det = prob.detect(labelled[target], types[target], cfg["problem_type"])
        if not det["valid"]:
            raise MlError(det["errors"][0]["code"], det["errors"][0]["message"] + " (after removing the rare classes)")
        problem = det["chosen"]
    t_role = "categorical" if problem in Problem.CLASSIFICATION else "numeric"
    rep.set("Deciding how each column will be prepared", "preparing", force=True)
    used, dropped, facts, leaks, _assoc = prep.plan_features(labelled, types, cfg, target, t_role, seed)
    if not used:
        raise MlError("NO_FEATURES", "No input columns are left after preparation, so there is nothing to learn from.")
    cols = [u["name"] for u in used]
    width = sum(prep.encoded_width(u) for u in used)
    if width > LIMITS["max_encoded_features"][i]:
        raise MlError("TOO_WIDE", f"After encoding, the inputs would become {width:,} model columns; the limit is {LIMITS['max_encoded_features'][i]:,}. Exclude columns, or encode categories by frequency.")
    notes["duplicates_removed"] = 0
    if cfg["preprocessing"]["drop_duplicates"]:
        dup = labelled.duplicated(subset=cols + [target], keep="first")
        notes["duplicates_removed"] = int(dup.sum())
        labelled = labelled[~dup]
    if len(labelled) < MIN_ROWS:
        raise MlError("TOO_FEW_ROWS", f"Only {len(labelled)} usable rows remain after removing rows without a target, rare classes and duplicates; at least {MIN_ROWS} are needed.")
    if problem in Problem.CLASSIFICATION:
        lab = prob.class_labels(labelled[target])
        classes = _class_order(lab)
        y_all = lab.map({c: j for j, c in enumerate(classes)}).to_numpy(int)
    else:
        classes = []
        y_all = labelled[target].to_numpy(float)
    keep_pos, sampled = V.sample_rows(y_all, problem, int(cfg["max_train_rows"] or LIMITS["max_train_rows"][i]), seed)
    notes["sampled"] = sampled
    labelled, y_all = labelled.iloc[keep_pos], y_all[keep_pos]
    tc = cfg["validation"]["time_column"]
    strategy = V.resolve_strategy(cfg["validation"]["cv_strategy"], problem, tc)
    ordered = strategy == "time_series"
    if tc:
        col = labelled[tc]
        if col.isna().any():
            raise MlError("TIME_COLUMN_MISSING", "The column used to order rows by time has missing values. Remove those rows or choose another column.")
        key = (col - pd.Timestamp("1970-01-01")).dt.total_seconds().to_numpy() if np.issubdtype(col.dtype, np.datetime64) else col.to_numpy(float)
        order = np.argsort(key, kind="stable")                      # from here on, row order IS time order
        labelled, y_all = labelled.iloc[order], y_all[order]
    X = labelled[cols].reset_index(drop=True)
    balance = prob.imbalance(prob.class_table(pd.Series([classes[j] for j in y_all]))) if problem in Problem.CLASSIFICATION else None
    positive = None
    if problem == Problem.BINARY:
        counts = {c: int((y_all == j).sum()) for j, c in enumerate(classes)}
        want = cfg["positive_label"] or _default_positive(classes, counts)
        if want not in classes:
            raise MlError("POSITIVE_LABEL_INVALID", f"'{want}' is not one of the target's classes ({', '.join(classes)}).")
        positive = classes.index(want)
    tfacts = prob.describe_target(labelled[target], types[target])
    plan = prob.metric_plan(problem, cfg["primary_metric"], balance=balance, target=tfacts)
    return Prepared(problem, classes, positive, X, y_all, used, dropped, leaks, facts, det, balance, plan, tfacts, width, ordered, notes)


# ------------------------------------------------------------------------------------------------ the run
def run_automl(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], *, extended: bool, rep: Reporter, dataset: Optional[Mapping[str, Any]] = None) -> TrainResult:
    i = 1 if extended else 0
    seed = int(cfg["seed"])
    rep.budget = float(min(cfg["time_budget_s"] or LIMITS["time_budget_s"][i], LIMITS["time_budget_s"][i]))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _run(df, types, cfg, extended=extended, rep=rep, dataset=dataset or {}, seed=seed, i=i)
    kinds: Dict[str, int] = {}
    for w in caught:
        kinds[w.category.__name__] = kinds.get(w.category.__name__, 0) + 1
    result.report["library_warnings"] = {"counts": kinds, "note": "Warnings raised by scikit-learn while fitting (for example, a model needing more iterations to settle). They are counted, not shown, because they can quote data."} if kinds else {}
    return result


def _run(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], *, extended: bool, rep: Reporter, dataset: Mapping[str, Any], seed: int, i: int) -> TrainResult:
    P = prepare(df, types, cfg, extended=extended, rep=rep)
    classification = P.problem in Problem.CLASSIFICATION
    k = len(P.classes)
    primary = P.plan["primary"]
    n = len(P.y)
    val = cfg["validation"]
    cand_deadline = 1.0 - RESERVE_SHARE
    # ---- split: the test rows are set aside here and not looked at again until each model has been fitted
    train_pos, test_pos = V.holdout(P.y, P.problem, val["test_size"], seed, time_order=np.arange(n) if P.ordered else None, shuffle=val["shuffle"])
    X_tr, y_tr = P.X.iloc[train_pos].reset_index(drop=True), P.y[train_pos]
    X_te, y_te = P.X.iloc[test_pos].reset_index(drop=True), P.y[test_pos]
    has_test = len(test_pos) > 0
    strategy = V.resolve_strategy(val["cv_strategy"], P.problem, val["time_column"])
    cv, cv_desc = V.make_cv(strategy, P.problem, y_tr, folds=val["cv_folds"], repeats=val["cv_repeats"], seed=seed, shuffle=val["shuffle"])
    folds = V.fold_indices(cv, X_tr, y_tr)
    imbalanced = bool(P.balance and P.balance["recommend_class_weight"])
    algos, skipped = select(cfg["algorithms"], P.problem, len(X_tr), P.width)
    if not algos:
        raise MlError("NO_ALGORITHMS", "None of the chosen algorithms can be used for this problem and table size. " + (skipped[0]["reason"] if skipped else ""))
    hpo = cfg["hpo"]
    rep.plan(len(algos) * (len(folds) + 1) + 8 + (hpo["top_k"] * (hpo["n_iter"] * hpo["cv_folds"] + len(folds) + 1) if hpo["method"] != "none" else 0))
    score_all = (lambda y, o: M.score_all_classification(y, o, P.positive, k)) if classification else (lambda y, o: M.score_all_regression(y, o))
    overfit_key = "balanced_accuracy" if classification else "r2"
    max_bytes = LIMITS["max_model_bytes"][i]
    rng = np.random.default_rng([seed, 11])
    baseline = _baseline(P, y_tr, y_te, folds, primary, has_test)
    candidates: List[Dict[str, Any]] = []
    keep: Dict[str, Any] = {"key": None, "pipe": None, "meta": None, "rec": None, "oof": None}

    def class_weight(algo: str) -> Optional[str]:
        return class_weight_for(cfg["class_weight"], algo, imbalanced) if classification else None

    def evaluate(label: str, algo: str, params: Optional[Mapping[str, Any]], tuned: bool) -> Dict[str, Any]:
        a = CATALOG[algo]
        rec: Dict[str, Any] = {"id": f"{algo}{'+tuned' if tuned else ''}", "algorithm": algo, "label": label, "tuned": tuned, "status": "ok", "reason": "", "params": _json_params(params or {}),
                               "class_weight": class_weight(algo), "cost": a.cost}
        t_cv = time.monotonic()
        fold_scores: List[Dict[str, Optional[float]]] = []
        train_scores: List[Optional[float]] = []
        fit_times: List[float] = []
        oof_idx: List[np.ndarray] = []
        oof_out: List[np.ndarray] = []
        try:
            for j, (tr, va) in enumerate(folds):
                rep.cancel_check()
                if rep.late(cand_deadline):
                    raise OutOfTime()
                pipe, _ = build_pipeline(P.used, algo, P.problem, seed, class_weight=rec["class_weight"], params=params)
                t = time.monotonic()
                pipe.fit(X_tr.iloc[tr], y_tr[tr])
                fit_times.append(time.monotonic() - t)
                out = X_.predict_output(pipe, X_tr.iloc[va], P.problem, k)
                fold_scores.append(score_all(y_tr[va], out))
                sub = tr if len(tr) <= TRAIN_SCORE_ROWS else np.sort(rng.choice(tr, TRAIN_SCORE_ROWS, replace=False))
                train_scores.append(score_all(y_tr[sub], X_.predict_output(pipe, X_tr.iloc[sub], P.problem, k)).get(overfit_key))
                oof_idx.append(va)
                oof_out.append(out)
                rep.step(f"{label}: fold {j + 1} of {len(folds)}", "training")
            cv_s = time.monotonic() - t_cv
            pipe, meta = build_pipeline(P.used, algo, P.problem, seed, class_weight=rec["class_weight"], params=params)
            t = time.monotonic()
            pipe.fit(X_tr, y_tr)
            fit_s = time.monotonic() - t
            size = len(pickle.dumps(pipe, protocol=pickle.HIGHEST_PROTOCOL))
            rep.step(f"{label}: final fit", "training")
        except Cancelled:
            raise
        except OutOfTime:
            rec.update(status="skipped", reason="Not evaluated: the time limit was reached before this algorithm finished.")
            return rec
        except Exception as exc:  # noqa: BLE001  one algorithm failing must not stop the others; the reason is recorded without data values
            rec.update(status="failed", reason=_safe_error(exc))
            return rec
        names = sorted({m for fs in fold_scores for m in fs})
        agg = {m: dict(zip(("mean", "std", "folds"), _mean_std([fs.get(m) for fs in fold_scores]))) for m in names}
        pm = agg.get(primary, {}).get("mean")
        if pm is None:
            rec.update(status="failed", reason=f"The ranking measure ({prob.METRIC_LABEL[primary]}) could not be computed in the validation folds.")
            return rec
        if size > max_bytes:
            rec.update(status="skipped", reason=f"Not eligible: the fitted model would be {size / 1e6:.0f} MB, above your limit of {max_bytes // (1024 * 1024)} MB.", model_bytes=size)
            return rec
        tr_mean, _, _ = _mean_std(train_scores)
        va_mean = agg.get(overfit_key, {}).get("mean")
        rec["cv"] = {"folds": len(folds), "primary": {"metric": primary, **agg[primary], "scores": [fs.get(primary) for fs in fold_scores]}, "metrics": agg}
        rec["overfitting"] = M.overfit_verdict(tr_mean, va_mean, prob.METRIC_LABEL[overfit_key] if overfit_key in prob.METRIC_LABEL else overfit_key)
        rec["timing"] = {"cv_s": round(cv_s, 3), "fit_s": round(fit_s, 3), "fit_s_per_fold": round(float(np.mean(fit_times)), 3)}
        rec["model_bytes"] = size
        if has_test:
            t = time.monotonic()
            out_te = X_.predict_output(pipe, X_te, P.problem, k)
            pred_s = time.monotonic() - t
            ts = score_all(y_te, out_te)
            rec["test"] = {"scores": ts, "primary": ts.get(primary), "rows": int(len(y_te))}
            rec["timing"]["predict_ms_per_1000_rows"] = round(1000.0 * pred_s / max(1, len(y_te)) * 1000.0, 2)
            if ts.get(primary) is not None:
                diff = ts[primary] - pm
                sd = agg[primary].get("std") or 0.0
                rec["generalisation"] = {"cv_primary": pm, "test_primary": ts[primary], "difference": M.f(diff),
                                         "consistent": bool(abs(diff) <= max(2 * sd, 0.03 * max(abs(pm), 0.1))),
                                         "reading": "Test and cross-validation scores agree within normal variation." if abs(diff) <= max(2 * sd, 0.03 * max(abs(pm), 0.1)) else
                                                    "The test score differs from the cross-validation score by more than normal variation; with few rows this is common, so read both."}
        key = (pm, -fit_s)
        if keep["key"] is None or key > keep["key"]:
            oof = None
            if not has_test:
                idx = np.concatenate(oof_idx)
                out = np.vstack(oof_out) if oof_out[0].ndim == 2 else np.concatenate(oof_out)
                oof = (idx, out)
            keep.update(key=key, pipe=pipe, meta=meta, rec=rec, oof=oof, fit_s=fit_s)
        return rec

    # ---- 1. every compatible algorithm, cheapest first
    rep.set("Training candidate models", "training", force=True)
    for algo in algos:
        rep.cancel_check()
        if rep.late(cand_deadline):
            candidates.append({"id": algo, "algorithm": algo, "label": CATALOG[algo].label, "tuned": False, "status": "skipped", "reason": "Not evaluated: the time limit was reached.", "params": {}, "cost": CATALOG[algo].cost})
            continue
        candidates.append(evaluate(CATALOG[algo].label, algo, None, False))
    ok = [c for c in candidates if c["status"] == "ok"]
    if not ok:
        reason = next((c["reason"] for c in candidates if c["status"] == "failed"), "") or "No algorithm finished within the limits."
        raise MlError("NO_MODEL_TRAINED", "No model could be trained. " + reason)
    # ---- 2. hyper-parameter search on the strongest algorithms
    hpo_report = _tune(P, cfg, ok, candidates, evaluate, folds_main=folds, X_tr=X_tr, y_tr=y_tr, strategy=strategy, rep=rep, seed=seed, i=i, primary=primary, class_weight=class_weight)
    # ---- 3. rank by cross-validation only
    ranking = _rank([c for c in candidates if c["status"] == "ok"])
    best = ranking[0]
    if keep["rec"] is None or keep["rec"]["id"] != best["id"]:
        raise MlError("INTERNAL_RANKING", "The ranking and the retained model disagree.")
    pipe, meta, fit_s = keep["pipe"], keep["meta"], keep["fit_s"]
    rep.set("Evaluating the best model", "evaluating", force=True)
    rep.step("Evaluating the best model", "evaluating")
    fm = feature_map(pipe, meta)
    algo = CATALOG[best["algorithm"]]
    linear = algo.importance == "coef" and best["algorithm"] != "svm"
    n_params = None
    if linear:
        coef = np.asarray(pipe.named_steps["model"].coef_)
        n_params = int(np.count_nonzero(coef)) if best["algorithm"] == "lasso" else int(len(fm["names"]))
    # ---- 4. evaluate the winner on rows it never trained on
    if has_test:
        basis, y_eval, X_eval = "holdout", y_te, X_te
        out_eval = X_.predict_output(pipe, X_te, P.problem, k)
    else:
        basis, (idx, out_all) = "cross_validation", keep["oof"]
        uniq = np.unique(idx)
        if out_all.ndim == 2:
            out_eval = np.vstack([out_all[idx == u].mean(axis=0) for u in uniq])
        else:
            out_eval = np.array([out_all[idx == u].mean() for u in uniq])
        y_eval, X_eval = y_tr[uniq], X_tr.iloc[uniq].reset_index(drop=True)
    if classification:
        evaluation = M.classification_metrics(y_eval, out_eval, P.classes, P.positive)
        evaluation["curves"] = M.classification_curves(y_eval, out_eval, P.classes, P.positive)
        evaluation["calibration"] = M.calibration(y_eval, out_eval, P.positive, k)
    else:
        evaluation = M.regression_metrics(y_eval, out_eval, n_params=n_params, seed=seed, linear=linear)
    evaluation["basis"] = basis
    evaluation["basis_note"] = ("Computed on rows held out before any training." if has_test else "No test rows were held out, so these figures come from out-of-fold predictions during cross-validation.")
    evaluation["model"] = best["id"]
    # ---- 5. explainability
    rep.set("Measuring which columns the model relies on", "explaining", force=True)
    explain_guard = lambda: rep.late(0.95)
    perm = X_.permutation_importance(pipe, X_eval, y_eval, P.problem, primary, P.positive, k, seed=seed, guard=explain_guard, tick=lambda c: rep.cancel_check())
    rep.step("Permutation importance", "explaining")
    scaled = any(resolve_scale(u, algo.scaling) for u in P.used if u["role"] == "numeric")
    native = X_.native_importance(pipe, best["algorithm"], algo.importance, fm, scaled=scaled)
    roles = {u["name"]: u["role"] for u in P.used}
    pdp: List[Dict[str, Any]] = []
    if perm.get("available"):
        top = [r["feature"] for r in perm["features"] if (r["importance"] or 0) > 0][:5]
        for feat in top:
            rep.cancel_check()
            if rep.late(0.97):
                break
            pdp.append(X_.partial_dependence(pipe, X_eval, feat, roles[feat], P.problem, P.positive, P.classes, seed=seed))
    rep.step("Partial dependence", "explaining")
    # ---- 6. learning curve
    lc: Dict[str, Any]
    est = 9.0 * fit_s
    if rep.remaining() <= 0 or est > rep.remaining() * 0.8:
        lc = {"available": False, "reason": "Skipped to stay within the time limit."}
    else:
        rep.set("Drawing the learning curve", "explaining", force=True)
        lc_cv, _ = V.make_cv("time_series" if P.ordered else ("stratified_kfold" if classification else "kfold"), P.problem, y_tr, folds=3, repeats=1, seed=seed, shuffle=True)
        lc = X_.learning_curve(lambda: build_pipeline(P.used, best["algorithm"], P.problem, seed, class_weight=best["class_weight"], params=best["params"])[0], X_tr, y_tr, P.problem, primary, P.positive, k,
                               V.fold_indices(lc_cv, X_tr, y_tr), seed=seed, guard=lambda: rep.late(0.99), cancel=rep.cancel_check)
    rep.step("Learning curve", "explaining")
    # ---- 7. package
    rep.set("Saving the model", "finalising", force=True)
    input_schema = _input_schema(P, X_tr)
    summary = {"calibration": evaluation.get("calibration", {}).get("verdict") if classification else None, "typical_error": evaluation.get("typical_error"),
               "target_range": evaluation.get("target_range"), "primary_metric": primary}
    bundle = {"format": 1, "ml_version": ML_VERSION, "pipeline": pipe, "problem": P.problem, "classes": P.classes, "positive_index": P.positive, "target": cfg["target"],
              "schema": schema_of(P.used), "input_schema": input_schema, "summary": summary, "feature_map": fm, "algorithm": best["algorithm"], "tuned": best["tuned"], "versions": _versions()}
    buf = io.BytesIO()
    joblib.dump(bundle, buf, compress=("zlib", 3))
    model_bytes = buf.getvalue()
    if len(model_bytes) > max_bytes:
        raise MlError("MODEL_TOO_LARGE", f"The best model is {len(model_bytes) / 1e6:.0f} MB, above your limit of {max_bytes // (1024 * 1024)} MB.")
    bg = X_tr.sample(min(BACKGROUND_ROWS, len(X_tr)), random_state=seed)
    bg_buf = io.StringIO()
    bg.to_csv(bg_buf, index=False)
    rep.step("Saved", "finalising", n=rep.total_steps)
    report = _report(P, cfg, dataset, ranking, candidates, skipped, best, baseline, hpo_report, evaluation, perm, native, pdp, lc, fm, cv_desc, strategy, folds, train_pos, test_pos, has_test, rep, len(model_bytes), model_bytes_pickle=best.get("model_bytes"))
    meta_out = {"algorithm": best["algorithm"], "algorithm_label": best["label"], "tuned": best["tuned"], "problem": P.problem, "primary_metric": primary, "primary_label": prob.METRIC_LABEL[primary],
                "cv_primary": best["cv"]["primary"]["mean"], "test_primary": (best.get("test") or {}).get("primary"), "hyperparameters": _model_params(pipe.named_steps["model"]),
                "classes": P.classes, "positive_label": (P.classes[P.positive] if P.positive is not None else None), "n_features_in": len(P.used), "n_features_encoded": len(fm["names"]),
                "rows_train": int(len(train_pos)), "rows_test": int(len(test_pos)), "model_bytes": len(model_bytes), "seed": seed, "ml_version": ML_VERSION,
                "duration_s": round(rep.elapsed(), 2), "input_schema": input_schema, "summary": summary}
    return TrainResult(report, model_bytes, bg_buf.getvalue().encode("utf-8"), meta_out)


# ------------------------------------------------------------------------------------------------ baseline, tuning, ranking, report
def _input_schema(P: Prepared, X_tr: pd.DataFrame) -> List[Dict[str, Any]]:
    """What a person needs to fill in a prediction form: each input, how it is read, the values it took in training."""
    from .transformers import SchemaCoercer
    schema = schema_of(P.used)
    clean = SchemaCoercer(schema).fit(X_tr).transform(X_tr)
    out: List[Dict[str, Any]] = []
    for spec in schema:
        s = clean[spec["name"]]
        item: Dict[str, Any] = {**spec, "missing_share": M.f(float(s.isna().mean()), 4)}
        nn = s.dropna()
        if spec["role"] == "numeric" and len(nn):
            item.update(min=M.f(nn.min()), max=M.f(nn.max()), median=M.f(nn.median()), integer_only=bool(np.all(np.mod(nn.to_numpy(float), 1) == 0)))
        elif spec["role"] == "datetime" and len(nn):
            item.update(min=nn.min().isoformat(), max=nn.max().isoformat())
        elif spec["role"] == "categorical" and len(nn):
            counts = nn.astype(str).value_counts()
            item.update(levels=sorted(counts.index)[:300] if len(counts) <= 300 else None, level_count=int(len(counts)), most_common=str(counts.index[0]))
        out.append(item)
    return out


def _baseline(P: Prepared, y_tr: np.ndarray, y_te: np.ndarray, folds: Sequence[Tuple[np.ndarray, np.ndarray]], primary: str, has_test: bool) -> Dict[str, Any]:
    k = len(P.classes)
    if P.problem in Problem.CLASSIFICATION:
        per_fold = [M.baseline_classification(y_tr[tr], y_tr[va], k, P.positive)["scores"].get(primary) for tr, va in folds]
        test = M.baseline_classification(y_tr, y_te, k, P.positive) if has_test else None
    else:
        per_fold = [M.baseline_regression(y_tr[tr], y_tr[va])["scores"].get(primary) for tr, va in folds]
        test = M.baseline_regression(y_tr, y_te) if has_test else None
    mean, std, _ = _mean_std(per_fold)
    desc = "always predicts the most common class" if P.problem in Problem.CLASSIFICATION else "always predicts the training average"
    return {"description": f"A reference that ignores every input and {desc}. A useful model must clearly beat it.", "cv_primary": mean, "cv_primary_shown": shown(primary, mean), "cv_std": std, "test": test}


def _rank(ok: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranking = sorted(ok, key=lambda c: (-c["cv"]["primary"]["mean"], c["timing"]["fit_s"], c["id"]))
    best = ranking[0]
    se = (best["cv"]["primary"].get("std") or 0.0) / math.sqrt(max(1, best["cv"]["folds"]))
    for r, c in enumerate(ranking, 1):
        c["rank"] = r
        c["delta_vs_best"] = M.f(c["cv"]["primary"]["mean"] - best["cv"]["primary"]["mean"])
        c["within_one_se_of_best"] = bool(r > 1 and (best["cv"]["primary"]["mean"] - c["cv"]["primary"]["mean"]) <= se)
    best["rank"] = 1
    return ranking


def _tune(P: Prepared, cfg: Mapping[str, Any], ok: List[Dict[str, Any]], candidates: List[Dict[str, Any]], evaluate: Callable, *, folds_main: Sequence[Any], X_tr: pd.DataFrame, y_tr: np.ndarray,
          strategy: str, rep: Reporter, seed: int, i: int, primary: str, class_weight: Callable[[str], Optional[str]]) -> Optional[Dict[str, Any]]:
    h = cfg["hpo"]
    if h["method"] == "none":
        return None
    k = len(P.classes)
    classification = P.problem in Problem.CLASSIFICATION
    ranked = _rank(list(ok))
    targets = [c for c in ranked if CATALOG[c["algorithm"]].params and not c["tuned"]][: h["top_k"]]
    if not targets:
        return {"method": h["method"], "algorithms": [], "note": "None of the leading algorithms has settings that can be tuned."}
    hpo_cv, hpo_desc = V.make_cv(strategy, P.problem, y_tr, folds=h["cv_folds"], repeats=1, seed=seed, shuffle=cfg["validation"]["shuffle"])
    hpo_folds = V.fold_indices(hpo_cv, X_tr, y_tr)
    per_algo: List[Dict[str, Any]] = []
    plans: List[Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], bool]] = []
    for base in targets:
        algo = base["algorithm"]
        user_grid = h["param_grids"].get(algo)
        space = user_grid or (CATALOG[algo].grid if h["method"] == "grid" else default_space(algo))
        finite = all(isinstance(v, list) for v in space.values())
        cap = min(h["n_iter"], LIMITS["max_hpo_trials"][i])
        truncated = False
        if h["method"] == "grid":
            combos = list(ParameterGrid(space))
            if len(combos) > cap:
                pick = np.random.default_rng([seed, 5]).choice(len(combos), cap, replace=False)
                combos, truncated = [combos[j] for j in sorted(pick)], True
        else:
            size = grid_size(space) if finite else None
            combos = list(ParameterSampler(space, n_iter=min(cap, size) if size else cap, random_state=seed))
        plans.append((base, [_json_params(c) for c in combos], space, truncated))
    rep.plan(rep.done_steps + sum(len(c) * len(hpo_folds) + len(folds_main) + 1 for _, c, _, _ in plans) + 8)
    rep.set("Tuning hyper-parameters", "tuning", force=True)
    for base, combos, space, truncated in plans:
        algo = base["algorithm"]
        t0 = time.monotonic()
        trials: List[Dict[str, Any]] = []
        stopped = False
        for params in combos:
            rep.cancel_check()
            if rep.late(1.0 - RESERVE_SHARE + 0.1):
                stopped = True
                break
            scores, ft = [], []
            try:
                for tr, va in hpo_folds:
                    rep.cancel_check()
                    pipe, _ = build_pipeline(P.used, algo, P.problem, seed, class_weight=class_weight(algo), params=params)
                    t = time.monotonic()
                    pipe.fit(X_tr.iloc[tr], y_tr[tr])
                    ft.append(time.monotonic() - t)
                    out = X_.predict_output(pipe, X_tr.iloc[va], P.problem, k)
                    scores.append(X_.score_of(P.problem, primary, y_tr[va], out, P.positive, k))
                    rep.step(f"Tuning {CATALOG[algo].label}: {len(trials) + 1} of {len(combos)}", "tuning")
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                trials.append({"params": params, "mean": None, "std": None, "error": _safe_error(exc)})
                continue
            mean, std, _ = _mean_std(scores)
            trials.append({"params": params, "mean": mean, "std": std, "fit_s": round(float(np.sum(ft)), 3)})
        scored = [t for t in trials if t.get("mean") is not None]
        entry: Dict[str, Any] = {"algorithm": algo, "label": CATALOG[algo].label, "method": h["method"], "trials_planned": len(combos), "trials_run": len(trials), "stopped_early": stopped,
                                 "grid_truncated": truncated, "baseline_score": base["cv"]["primary"]["mean"], "space": _space_text(space), "duration_s": round(time.monotonic() - t0, 2),
                                 "trials": sorted(trials, key=lambda t: -(t["mean"] if t.get("mean") is not None else -1e9))[:MAX_TRIALS_LISTED]}
        if scored:
            best = max(scored, key=lambda t: (t["mean"], -(t["std"] or 0)))
            entry.update(best_params=best["params"], best_score=best["mean"], improvement=M.f(best["mean"] - base["cv"]["primary"]["mean"]))
            tuned = evaluate(f"{CATALOG[algo].label} (tuned)", algo, best["params"], True)
            candidates.append(tuned)
            entry["tuned_candidate"] = tuned["id"]
        else:
            entry["note"] = "No parameter set could be scored."
        per_algo.append(entry)
    return {"method": h["method"], "algorithms": per_algo, "validation": hpo_desc,
            "caution": "The best tuning score is the highest of several tries, so it is slightly optimistic. The held-out test score of the tuned model is the unbiased figure."}


def _space_text(space: Mapping[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in space.items():
        out[k] = [x for x in v] if isinstance(v, list) else "continuous range (log-uniform)"
    return out


def _report(P: Prepared, cfg: Mapping[str, Any], dataset: Mapping[str, Any], ranking, candidates, skipped, best, baseline, hpo_report, evaluation, perm, native, pdp, lc, fm, cv_desc, strategy,
            folds, train_pos, test_pos, has_test, rep: Reporter, stored_bytes: int, model_bytes_pickle: Optional[int]) -> Dict[str, Any]:
    classification = P.problem in Problem.CLASSIFICATION
    primary = P.plan["primary"]
    beat = None
    if baseline.get("cv_primary") is not None:
        beat = M.f(best["cv"]["primary"]["mean"] - baseline["cv_primary"])
    warnings_out: List[str] = []
    if beat is not None and beat <= 0.0:
        warnings_out.append("The best model does not beat the no-input baseline, so the inputs may carry little information about the target.")
    top_score = (best.get("test") or {}).get("primary") if has_test else best["cv"]["primary"]["mean"]
    if top_score is not None and primary in ("roc_auc", "average_precision", "accuracy", "balanced_accuracy", "f1", "f1_macro", "r2") and top_score >= 0.995:
        warnings_out.append("The score is almost perfect. That is unusual for real data; check that no input contains the answer (data leakage).")
    if classification and P.balance and P.balance["accuracy_warning"]:
        warnings_out.append(P.balance["message"])
    if len(train_pos) < 200:
        warnings_out.append(f"Only {len(train_pos)} rows were used for training, so every score is uncertain.")
    if best["overfitting"]["level"] == "high":
        warnings_out.append(best["overfitting"]["message"])
    if cv_desc.get("notes"):
        warnings_out.extend(cv_desc["notes"])
    close = [c for c in ranking if c.get("within_one_se_of_best")]
    simpler = min(close, key=lambda c: (c["cost"], c["timing"]["fit_s"]), default=None)
    return {
        "schema": SCHEMA_REPORT, "ml_version": ML_VERSION,
        "problem": {"type": P.problem, "recommended": P.det["recommended"], "chosen_by": "you" if cfg["problem_type"] != "auto" else "rule", "why": P.det["why"], "classes": P.classes,
                    "positive_label": (P.classes[P.positive] if P.positive is not None else None), "target": {"name": cfg["target"], **P.tfacts}},
        "dataset": dict(dataset),
        "data": {"rows_in_table": P.notes["rows_in_table"], "rows_without_target": P.notes["rows_without_target"], "rare_class_rows_dropped": P.notes["rare_class_rows_dropped"],
                 "duplicates_removed": P.notes["duplicates_removed"], "sampled": P.notes["sampled"], "rows_used": int(len(P.y)), "train_rows": int(len(train_pos)), "test_rows": int(len(test_pos)),
                 "class_balance": P.balance, "class_table": (prob.class_table(pd.Series([P.classes[j] for j in P.y])) if classification else None)},
        "features": {"used": [{k: v for k, v in u.items() if k not in ("reason",)} | {"why": u.get("reason", "")} for u in P.used], "dropped": [{"name": d["name"], "reason": d["reason"]} for d in P.dropped],
                     "encoded_columns": len(fm["names"]), "leakage": P.leaks, "settings": cfg["preprocessing"],
                     "principle": "Medians, scaling factors, category lists and clipping limits are learned from each training part only, inside every validation fold."},
        "validation": {"holdout": {"test_size": cfg["validation"]["test_size"], "train_rows": int(len(train_pos)), "test_rows": int(len(test_pos)), "stratified": classification and not P.ordered, "time_ordered": P.ordered},
                       "cross_validation": cv_desc, "seed": cfg["seed"], "selection_rule": "Models are ranked by their mean cross-validation score on the training rows. The held-out test rows are used only to report each model's final score, never to choose among models."},
        "metrics_plan": P.plan,
        "algorithms": {"trained": [c["algorithm"] for c in candidates if c["status"] == "ok" and not c["tuned"]], "skipped": skipped},
        "candidates": [_candidate_row(c) for c in candidates],
        "ranking": [{"rank": c["rank"], "id": c["id"], "label": c["label"], "tuned": c["tuned"], "cv_primary": c["cv"]["primary"]["mean"], "cv_std": c["cv"]["primary"].get("std"),
                     "cv_primary_shown": shown(primary, c["cv"]["primary"]["mean"]), "test_primary": (c.get("test") or {}).get("primary"),
                     "test_primary_shown": shown(primary, (c.get("test") or {}).get("primary")), "delta_vs_best": c["delta_vs_best"], "within_one_se_of_best": c["within_one_se_of_best"],
                     "overfitting": c["overfitting"]["level"], "fit_s": c["timing"]["fit_s"], "model_bytes": c["model_bytes"]} for c in ranking],
        "best": {"id": best["id"], "algorithm": best["algorithm"], "label": best["label"], "tuned": best["tuned"], "params": best["params"], "primary_metric": primary, "primary_label": prob.METRIC_LABEL[primary],
                 "cv_primary": best["cv"]["primary"]["mean"], "cv_std": best["cv"]["primary"].get("std"), "test_primary": (best.get("test") or {}).get("primary"), "beats_baseline_by": beat,
                 "cv_primary_shown": shown(primary, best["cv"]["primary"]["mean"]), "test_primary_shown": shown(primary, (best.get("test") or {}).get("primary")),
                 "lower_is_better": primary.startswith("neg_"),
                 "close_alternatives": [c["id"] for c in close], "simpler_alternative": (simpler["id"] if simpler and simpler["id"] != best["id"] else None),
                 "trained_on": "the training split only; the figures describe this exact model"},
        "baseline": baseline,
        "hpo": hpo_report,
        "evaluation": evaluation,
        "importance": {"permutation": perm, "native": native, "partial_dependence": pdp, "shap": X_.SHAP_STATUS, "note": X_.CAUSATION_NOTE},
        "learning_curve": lc,
        "limitations": _limitations(P, cfg, has_test, train_pos),
        "warnings": warnings_out,
        "reproducibility": {"seed": cfg["seed"], "ml_version": ML_VERSION, "versions": _versions(), "config": {k: v for k, v in cfg.items()},
                            "statement": "The same dataset version, configuration and seed reproduce the same split, folds and model on the same library versions."},
        "resources": {"duration_s": round(rep.elapsed(), 2), "time_budget_s": rep.budget, "stored_model_bytes": stored_bytes, "uncompressed_model_bytes": model_bytes_pickle},
    }


def shown(metric: str, value: Optional[float]) -> Optional[float]:
    """Error metrics are ranked as negatives (so that higher is always better) but people read them as ordinary positive errors."""
    if value is None:
        return None
    return M.f(-value) if metric.startswith("neg_") else value


def _candidate_row(c: Mapping[str, Any]) -> Dict[str, Any]:
    keep = ("id", "algorithm", "label", "tuned", "status", "reason", "params", "class_weight", "cost", "cv", "test", "overfitting", "generalisation", "timing", "model_bytes", "rank")
    return {k: c[k] for k in keep if k in c}


def _limitations(P: Prepared, cfg: Mapping[str, Any], has_test: bool, train_pos: np.ndarray) -> List[str]:
    out = ["The model describes patterns in the data it was trained on. It does not show what causes the outcome, and it can be wrong for new situations that differ from the training data.",
           "Scores come from one random split plus cross-validation; with different data or a different seed they will move by a normal amount."]
    if not has_test:
        out.append("No test rows were held out, so the reported figures come from cross-validation instead of unseen rows.")
    if P.problem in Problem.CLASSIFICATION:
        out.append("Predicted probabilities are model scores. Unless the calibration check is good, read them as rankings rather than as real frequencies, and never as guarantees.")
    else:
        out.append("A prediction is a single best estimate. The typical-error range comes from held-out rows and does not bound any individual prediction.")
    if P.ordered:
        out.append("Rows were ordered by time: the most recent rows were held out, so scores reflect predicting forward in time.")
    return out
