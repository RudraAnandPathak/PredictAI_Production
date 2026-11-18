"""Model-level explainability on top of the fitted pipeline. Everything works on the ORIGINAL input columns (the pipeline takes raw rows), so
importance is reported per column a person recognises, not per encoded column.

What each tool tells you, and what it does not:
  * permutation importance - how much the score drops when one column's values are shuffled. Model-agnostic; measured on held-out rows.
    It reflects what THIS model relies on, not what causes the outcome. Correlated columns can share or hide importance.
  * native importance - what the model's own structure reports (tree split gains, linear coefficients on scaled inputs). Available for some models only.
  * partial dependence - the average prediction as one column is varied while the others keep their real values. Shows the model's behaviour, not a causal effect.
  * local effects - for one prediction, how much the prediction changes if one column's value is replaced by typical values. It is a one-column-at-a-time
    comparison (like occlusion). It is NOT SHAP: effects need not add up to the prediction and interactions between columns are not split fairly.
SHAP is not used: the library is not installed in this build, and nothing here claims it."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from . import metrics as M
from .constants import Problem
from .errors import Cancelled

SHAP_STATUS = {"available": False, "reason": "SHAP is not part of this build, so no SHAP values are computed or claimed. Permutation importance, partial dependence and one-column-at-a-time local effects are provided instead."}
CAUSATION_NOTE = "Importance describes what the model uses to predict. It does not show that a column causes the outcome, and correlated columns can share importance between them."


def proba_full(pipe: Any, X: pd.DataFrame, k: int) -> np.ndarray:
    """Class probabilities as an (n, k) array even if a training fold had not seen every class."""
    p = np.asarray(pipe.predict_proba(X), dtype=float)
    cls = np.asarray(pipe.classes_).astype(int)
    if p.shape[1] == k and np.array_equal(cls, np.arange(k)):
        return M.normalise(p)
    full = np.zeros((len(X), k))
    full[:, cls] = p
    return M.normalise(full)


def predict_output(pipe: Any, X: pd.DataFrame, problem: str, k: int) -> np.ndarray:
    return proba_full(pipe, X, k) if problem in Problem.CLASSIFICATION else np.asarray(pipe.predict(X), dtype=float)


def score_of(problem: str, key: str, y: np.ndarray, out: np.ndarray, positive: Optional[int], k: int) -> Optional[float]:
    s = M.score_all_classification(y, out, positive, k) if problem in Problem.CLASSIFICATION else M.score_all_regression(y, out)
    return s.get(key)


# ------------------------------------------------------------------------------------------------ permutation importance
def permutation_importance(pipe: Any, X: pd.DataFrame, y: np.ndarray, problem: str, metric: str, positive: Optional[int], k: int, *, seed: int,
                           repeats: int = 5, max_rows: int = 1500, guard: Optional[Callable[[], bool]] = None, tick: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """`guard()` returns True when time is up; the result then lists only the columns measured so far and says so."""
    rng = np.random.default_rng(seed)
    n = len(X)
    sel = np.arange(n) if n <= max_rows else np.sort(rng.choice(n, max_rows, replace=False))
    Xs, ys = X.iloc[sel].reset_index(drop=True), y[sel]
    base = score_of(problem, metric, ys, predict_output(pipe, Xs, problem, k), positive, k)
    if base is None:
        return {"available": False, "reason": "The score could not be computed on the evaluation rows (for example, only one class is present)."}
    if len(Xs.columns) > 40:
        repeats = min(repeats, 3)
    rows: List[Dict[str, Any]] = []
    stopped = False
    for col in Xs.columns:
        if guard and guard():
            stopped = True
            break
        drops = []
        for _ in range(repeats):
            Xp = Xs.copy()
            Xp[col] = rng.permutation(Xs[col].to_numpy())
            sc = score_of(problem, metric, ys, predict_output(pipe, Xp, problem, k), positive, k)
            if sc is not None:
                drops.append(base - sc)
        if drops:
            rows.append({"feature": str(col), "importance": M.f(np.mean(drops)), "std": M.f(np.std(drops, ddof=1)) if len(drops) > 1 else None})
        if tick:
            tick(str(col))
    pos_total = sum(max(0.0, r["importance"] or 0.0) for r in rows) or 1.0
    for r in rows:
        r["share"] = M.f(max(0.0, r["importance"] or 0.0) / pos_total, 4)
        r["reliable"] = bool(r["std"] is not None and (r["importance"] or 0) > 2 * r["std"])
    rows.sort(key=lambda r: -(r["importance"] if r["importance"] is not None else -1e9))
    return {"available": True, "metric": metric, "baseline_score": M.f(base), "rows_used": int(len(Xs)), "repeats": int(repeats), "features": rows, "complete": not stopped,
            "reading": "Importance is the drop in the score when a column's values are shuffled; a drop near zero means the model barely uses that column. 'reliable' means the drop is more than twice its run-to-run variation.",
            "note": CAUSATION_NOTE, **({"stopped_reason": "The time limit was reached before every column was measured."} if stopped else {})}


# ------------------------------------------------------------------------------------------------ native importance
def native_importance(pipe: Any, algo_key: str, importance_kind: Optional[str], fmap: Mapping[str, List[Any]], *, scaled: bool) -> Dict[str, Any]:
    model = pipe.named_steps["model"]
    if importance_kind is None:
        return {"available": False, "reason": "This kind of model has no built-in importance measure; use permutation importance."}
    if importance_kind == "tree" and hasattr(model, "feature_importances_"):
        raw = np.asarray(model.feature_importances_, dtype=float)
        basis = "How much each column's splits reduce impurity (or error) across the trees."
    elif importance_kind == "coef" and hasattr(model, "coef_"):
        c = np.abs(np.asarray(model.coef_, dtype=float))
        raw = c.mean(axis=0) if c.ndim > 1 else c
        basis = "Size of each coefficient" + (" (inputs were standardised, so sizes are comparable)." if scaled else " (inputs were NOT standardised, so sizes depend on each column's units and are only roughly comparable).")
    else:
        return {"available": False, "reason": "This fitted model does not expose a built-in importance measure."}
    if len(raw) != len(fmap["names"]):
        return {"available": False, "reason": "The model's internal columns could not be matched to the inputs."}
    total = raw.sum() or 1.0
    by_origin: Dict[str, float] = {}
    for v, o in zip(raw, fmap["origins"]):
        by_origin[str(o)] = by_origin.get(str(o), 0.0) + float(v)
    feats = sorted(({"feature": o, "importance": M.f(v, 6), "share": M.f(v / total, 4)} for o, v in by_origin.items()), key=lambda r: -(r["importance"] or 0))
    encoded = sorted(({"feature": n, "origin": str(o), "share": M.f(v / total, 4)} for v, n, o in zip(raw, fmap["names"], fmap["origins"])), key=lambda r: -(r["share"] or 0))[:15]
    return {"available": True, "kind": importance_kind, "basis": basis, "features": feats, "top_encoded": encoded, "note": CAUSATION_NOTE}


# ------------------------------------------------------------------------------------------------ partial dependence
def _grid_for(col: pd.Series, role: str, max_points: int) -> List[Any]:
    s = col.dropna()
    if role == "numeric":
        u = np.unique(s.to_numpy(float))
        if len(u) <= max_points:
            return [float(v) for v in u]
        q = np.unique(np.quantile(s.to_numpy(float), np.linspace(0.05, 0.95, max_points)))
        return [float(v) for v in q]
    top = s.astype(str).value_counts().head(12)
    return [str(v) for v in top.index]


def partial_dependence(pipe: Any, X: pd.DataFrame, feature: str, role: str, problem: str, positive: Optional[int], classes: Sequence[str], *, max_points: int = 20,
                       max_rows: int = 300, seed: int = 0) -> Dict[str, Any]:
    if role == "datetime":
        return {"feature": feature, "available": False, "reason": "Partial dependence is not drawn for date columns; their calendar parts are combined inside the model."}
    k = len(classes)
    rng = np.random.default_rng(seed)
    bg = X if len(X) <= max_rows else X.iloc[np.sort(rng.choice(len(X), max_rows, replace=False))]
    bg = bg.reset_index(drop=True)
    grid = _grid_for(X[feature], role, max_points)
    if len(grid) < 2:
        return {"feature": feature, "available": False, "reason": "The column has fewer than two distinct values."}
    series: Dict[str, List[Optional[float]]] = {}
    low: List[Optional[float]] = []
    high: List[Optional[float]] = []
    wanted = [positive if positive is not None else 1] if k == 2 else list(range(min(k, 6)))
    means = {c: [] for c in (wanted if problem in Problem.CLASSIFICATION else [0])}
    for v in grid:
        Xg = bg.copy()
        Xg[feature] = v
        out = predict_output(pipe, Xg, problem, k)
        if problem in Problem.CLASSIFICATION:
            for c in wanted:
                means[c].append(M.f(out[:, c].mean(), 5))
            main = out[:, wanted[0]]
        else:
            means[0].append(M.f(out.mean(), 5))
            main = out
        low.append(M.f(np.quantile(main, 0.1), 5))
        high.append(M.f(np.quantile(main, 0.9), 5))
    if problem in Problem.CLASSIFICATION:
        series = {classes[c]: means[c] for c in wanted}
    else:
        series = {"prediction": means[0]}
    return {"feature": feature, "available": True, "kind": role, "grid": grid, "series": series, "low": low, "high": high, "rows_used": int(len(bg)),
            "y_label": ("Predicted probability" if problem in Problem.CLASSIFICATION else "Predicted value"),
            "note": "Average prediction when this column is set to each value and the other columns keep their real values. The band spans the 10th to 90th percentile of predictions across rows. This describes the model, not a cause."}


# ------------------------------------------------------------------------------------------------ local effects (single prediction)
def local_effects(pipe: Any, row: pd.DataFrame, background: pd.DataFrame, problem: str, positive: Optional[int], classes: Sequence[str], *, top: int = 8, per_feature: int = 40, seed: int = 0) -> Dict[str, Any]:
    """For one input row: prediction minus the average prediction when one column is replaced by typical values from the training sample."""
    k = len(classes)
    rng = np.random.default_rng(seed)
    out_row = predict_output(pipe, row, problem, k)
    focus = (int(np.argmax(out_row[0])) if problem in Problem.CLASSIFICATION and k > 2 else (positive if positive is not None else 1)) if problem in Problem.CLASSIFICATION else 0
    base = float(out_row[0][focus]) if problem in Problem.CLASSIFICATION else float(out_row[0])
    effects: List[Dict[str, Any]] = []
    bg = background.reset_index(drop=True)
    for col in row.columns:
        take = bg[col].dropna()
        if len(take) == 0:
            continue
        vals = take.sample(min(per_feature, len(take)), random_state=int(rng.integers(0, 2 ** 31 - 1))).to_numpy()
        batch = pd.concat([row] * len(vals), ignore_index=True)
        batch[col] = vals
        o = predict_output(pipe, batch, problem, k)
        ref = float(o[:, focus].mean()) if problem in Problem.CLASSIFICATION else float(o.mean())
        effects.append({"feature": str(col), "value": None if pd.isna(row.iloc[0][col]) else _plain(row.iloc[0][col]), "effect": M.f(base - ref, 5)})
    effects.sort(key=lambda e: -abs(e["effect"] or 0.0))
    for e in effects:
        e["direction"] = "raises" if (e["effect"] or 0) > 0 else "lowers" if (e["effect"] or 0) < 0 else "no change"
    return {"method": "one-column-at-a-time replacement (not SHAP)", "focus": classes[focus] if problem in Problem.CLASSIFICATION else "predicted value",
            "prediction": M.f(base, 5), "effects": effects[:top], "features_checked": len(effects),
            "reading": "Each effect is how much the prediction differs from what the model predicts when only that column is replaced by typical values. Effects need not add up to the prediction, and columns that work together are not split fairly.",
            "note": "These effects describe how the model behaves. They do not show what would happen if a real-world value changed."}


def _plain(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return M.f(v, 6)
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return str(v)


# ------------------------------------------------------------------------------------------------ learning curve
def learning_curve(make_pipe: Callable[[], Any], X: pd.DataFrame, y: np.ndarray, problem: str, metric: str, positive: Optional[int], k: int, folds: Sequence[Any], *, seed: int,
                   fractions: Sequence[float] = (0.2, 0.4, 0.6, 0.8, 1.0), guard: Optional[Callable[[], bool]] = None, cancel: Optional[Callable[[], None]] = None,
                   max_train_score_rows: int = 1500) -> Dict[str, Any]:
    """Score on training rows vs held-out rows as the amount of training data grows. A gap that closes means more data would not help much;
    two lines that stay far apart and close slowly mean more data may help; both low means the model is too simple or the inputs are weak."""
    rng = np.random.default_rng(seed)
    points = []
    for frac in fractions:
        tr_scores, va_scores, sizes = [], [], []
        for tr, va in folds:
            if cancel:
                cancel()
            if guard and guard():
                return {"available": bool(points), "points": points, "complete": False, "reason": "The time limit was reached." if not points else None,
                        "metric": metric}
            order = rng.permutation(tr)
            sub = np.sort(order[: max(5, int(round(len(order) * frac)))])
            if problem in Problem.CLASSIFICATION and len(np.unique(y[sub])) < k:
                continue
            pipe = make_pipe()
            pipe.fit(X.iloc[sub], y[sub])
            va_s = score_of(problem, metric, y[va], predict_output(pipe, X.iloc[va], problem, k), positive, k)
            probe = sub if len(sub) <= max_train_score_rows else sub[:max_train_score_rows]
            tr_s = score_of(problem, metric, y[probe], predict_output(pipe, X.iloc[probe], problem, k), positive, k)
            if va_s is not None and tr_s is not None:
                tr_scores.append(tr_s)
                va_scores.append(va_s)
                sizes.append(len(sub))
        if va_scores:
            points.append({"train_rows": int(round(np.mean(sizes))), "train_score": M.f(np.mean(tr_scores)), "validation_score": M.f(np.mean(va_scores)),
                           "validation_std": M.f(np.std(va_scores, ddof=1)) if len(va_scores) > 1 else None})
    if len(points) < 2:
        return {"available": False, "reason": "Too few usable points could be computed.", "metric": metric}
    last, first = points[-1], points[0]
    gap = (last["train_score"] or 0) - (last["validation_score"] or 0)
    slope = (last["validation_score"] or 0) - (points[-2]["validation_score"] or 0)
    if gap > 0.15 and slope > 0.005:
        verdict = "more_data_may_help"
    elif gap > 0.15:
        verdict = "memorising"
    elif (last["validation_score"] or 0) - (first["validation_score"] or 0) < 0.01:
        verdict = "plateau"
    else:
        verdict = "converging"
    return {"available": True, "metric": metric, "points": points, "complete": True, "verdict": verdict, "final_gap": M.f(gap), "last_step_gain": M.f(slope)}
