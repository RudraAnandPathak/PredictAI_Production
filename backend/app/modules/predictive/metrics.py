"""Model evaluation numbers. Pure functions over arrays: no data access, no randomness except where a seed is passed.

Every function returns plain JSON-safe values and says, in `not_computed`, which metric it left out and why (never a silent omission and never a made-up zero).
Class labels are indices 0..k-1 into the `classes` list; for binary problems `positive` is the index of the class treated as 'positive'."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from sklearn import metrics as skm
from sklearn.calibration import calibration_curve

from .constants import CURVE_POINTS, SCATTER_POINTS, Problem

MIN_CALIBRATION_ROWS = 50
MAX_CURVE_CLASSES = 8


def f(x: Any, digits: int = 6) -> Optional[float]:
    """JSON-safe float: None for NaN/inf."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def thin(arrays: Sequence[np.ndarray], n: int = CURVE_POINTS) -> List[List[Optional[float]]]:
    """Keeps at most n evenly spaced points (always including both ends) so a curve stays small without changing its shape."""
    length = len(arrays[0])
    idx = np.arange(length) if length <= n else np.unique(np.linspace(0, length - 1, n).round().astype(int))
    return [[f(a[i], 5) for i in idx] for a in arrays]


# ------------------------------------------------------------------------------------------------ classification
def normalise(proba: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(proba, dtype=float), 0.0, 1.0)
    s = p.sum(axis=1, keepdims=True)
    s[s == 0] = 1.0
    return p / s


def score_all_classification(y: np.ndarray, proba: np.ndarray, positive: Optional[int], k: int) -> Dict[str, Optional[float]]:
    """Every scorer-named metric that applies, used for fold-by-fold comparison. NaN-free: a metric that cannot be computed is None."""
    proba = normalise(proba)
    pred = proba.argmax(axis=1)
    labels = list(range(k))
    out: Dict[str, Optional[float]] = {"accuracy": f(skm.accuracy_score(y, pred)), "balanced_accuracy": f(skm.balanced_accuracy_score(y, pred))}
    out["f1_macro"] = f(skm.f1_score(y, pred, average="macro", labels=labels, zero_division=0))
    present = len(np.unique(y))
    try:
        out["neg_log_loss"] = f(-skm.log_loss(y, np.clip(proba, 1e-15, 1 - 1e-15), labels=labels))
    except ValueError:
        out["neg_log_loss"] = None
    if k == 2:
        p = int(positive if positive is not None else 1)
        out["f1"] = f(skm.f1_score(y, pred, pos_label=p, zero_division=0))
        if present == 2:
            out["roc_auc"] = f(skm.roc_auc_score((y == p).astype(int), proba[:, p]))
            out["average_precision"] = f(skm.average_precision_score((y == p).astype(int), proba[:, p]))
        else:
            out["roc_auc"] = out["average_precision"] = None
    else:
        out["roc_auc"] = f(skm.roc_auc_score(y, proba, multi_class="ovr", average="macro", labels=labels)) if present == k else None
    return out


def classification_metrics(y: np.ndarray, proba: np.ndarray, classes: Sequence[str], positive: Optional[int]) -> Dict[str, Any]:
    """The full evaluation of one model on held-out rows."""
    k = len(classes)
    proba = normalise(proba)
    pred = proba.argmax(axis=1)
    labels = list(range(k))
    binary = k == 2
    p = int(positive if positive is not None else 1)
    metrics: Dict[str, Optional[float]] = {}
    nc: Dict[str, str] = {}
    metrics["accuracy"] = f(skm.accuracy_score(y, pred))
    metrics["balanced_accuracy"] = f(skm.balanced_accuracy_score(y, pred))
    if binary:
        metrics["precision"] = f(skm.precision_score(y, pred, pos_label=p, zero_division=0))
        metrics["recall"] = f(skm.recall_score(y, pred, pos_label=p, zero_division=0))
        metrics["f1"] = f(skm.f1_score(y, pred, pos_label=p, zero_division=0))
        metrics["specificity"] = f(skm.recall_score(y, pred, pos_label=1 - p, zero_division=0))
    for avg in ("macro", "weighted"):
        metrics[f"precision_{avg}"] = f(skm.precision_score(y, pred, average=avg, labels=labels, zero_division=0))
        metrics[f"recall_{avg}"] = f(skm.recall_score(y, pred, average=avg, labels=labels, zero_division=0))
        metrics[f"f1_{avg}"] = f(skm.f1_score(y, pred, average=avg, labels=labels, zero_division=0))
    present = len(np.unique(y))
    if binary:
        if present == 2:
            yb = (y == p).astype(int)
            metrics["roc_auc"] = f(skm.roc_auc_score(yb, proba[:, p]))
            metrics["average_precision"] = f(skm.average_precision_score(yb, proba[:, p]))
            metrics["brier_score"] = f(skm.brier_score_loss(yb, proba[:, p]))
        else:
            nc["roc_auc"] = nc["average_precision"] = "The evaluation rows contain only one class, so a ranking measure is undefined."
    else:
        if present == k:
            metrics["roc_auc"] = f(skm.roc_auc_score(y, proba, multi_class="ovr", average="macro", labels=labels))
        else:
            nc["roc_auc"] = "Some classes are absent from the evaluation rows, so one-vs-rest ROC-AUC is undefined."
        nc["average_precision"] = "PR-AUC is reported for binary problems; per-class precision and recall are in the classification report."
    try:
        metrics["log_loss"] = f(skm.log_loss(y, np.clip(proba, 1e-15, 1 - 1e-15), labels=labels))
    except ValueError:
        nc["log_loss"] = "Log loss could not be computed for these rows."
    cm = skm.confusion_matrix(y, pred, labels=labels)
    rows = cm.sum(axis=1, keepdims=True)
    cm_norm = np.divide(cm, rows, out=np.zeros(cm.shape, dtype=float), where=rows > 0)
    report = skm.classification_report(y, pred, labels=labels, target_names=list(classes), output_dict=True, zero_division=0)
    clean_report = {name: ({m: (f(v) if m != "support" else int(v)) for m, v in vals.items()} if isinstance(vals, dict) else f(vals)) for name, vals in report.items()}
    return {"n": int(len(y)), "metrics": metrics, "not_computed": nc,
            "confusion_matrix": {"labels": list(classes), "counts": cm.astype(int).tolist(), "row_normalised": [[f(v, 4) for v in r] for r in cm_norm],
                                 "note": "Rows are the true class, columns the predicted class."},
            "classification_report": clean_report, "support": {classes[i]: int((y == i).sum()) for i in range(k)}}


def classification_curves(y: np.ndarray, proba: np.ndarray, classes: Sequence[str], positive: Optional[int]) -> Dict[str, Any]:
    proba = normalise(proba)
    k = len(classes)
    curves: Dict[str, Any] = {"roc": [], "pr": []}
    targets = [int(positive if positive is not None else 1)] if k == 2 else list(range(min(k, MAX_CURVE_CLASSES)))
    for c in targets:
        yb = (y == c).astype(int)
        if yb.min() == yb.max():
            continue
        fpr, tpr, _ = skm.roc_curve(yb, proba[:, c])
        prec, rec, _ = skm.precision_recall_curve(yb, proba[:, c])
        fx, tx = thin([fpr, tpr])
        rx, px = thin([rec, prec])
        curves["roc"].append({"class": classes[c], "fpr": fx, "tpr": tx, "auc": f(skm.roc_auc_score(yb, proba[:, c]))})
        curves["pr"].append({"class": classes[c], "recall": rx, "precision": px, "average_precision": f(skm.average_precision_score(yb, proba[:, c])), "prevalence": f(yb.mean())})
    if k > MAX_CURVE_CLASSES:
        curves["note"] = f"Curves are drawn for the first {MAX_CURVE_CLASSES} classes only."
    return curves


def calibration(y: np.ndarray, proba: np.ndarray, positive: Optional[int], k: int) -> Dict[str, Any]:
    """Do predicted probabilities match how often the event really happens? Binary only; needs enough rows."""
    if k != 2:
        return {"available": False, "verdict": "unknown", "reason": "Calibration is checked for binary problems; for several classes the scores should be read as relative rankings."}
    if len(y) < MIN_CALIBRATION_ROWS:
        return {"available": False, "verdict": "unknown", "reason": f"At least {MIN_CALIBRATION_ROWS} held-out rows are needed to check calibration."}
    p = int(positive if positive is not None else 1)
    yb = (y == p).astype(int)
    if yb.min() == yb.max():
        return {"available": False, "verdict": "unknown", "reason": "The held-out rows contain only one class."}
    score = normalise(proba)[:, p]
    bins = int(min(10, max(3, len(y) // 20)))
    frac, mean_pred = calibration_curve(yb, score, n_bins=bins, strategy="quantile")
    gap = float(np.mean(np.abs(frac - mean_pred)))
    verdict = "good" if gap <= 0.05 else "fair" if gap <= 0.10 else "poor"
    return {"available": True, "bins": int(len(frac)), "mean_predicted": [f(v, 4) for v in mean_pred], "observed_rate": [f(v, 4) for v in frac], "mean_gap": f(gap, 4), "verdict": verdict}


# ------------------------------------------------------------------------------------------------ regression
def score_all_regression(y: np.ndarray, pred: np.ndarray) -> Dict[str, Optional[float]]:
    return {"neg_root_mean_squared_error": f(-math.sqrt(skm.mean_squared_error(y, pred))), "neg_mean_absolute_error": f(-skm.mean_absolute_error(y, pred)), "r2": f(skm.r2_score(y, pred))}


def regression_metrics(y: np.ndarray, pred: np.ndarray, *, n_params: Optional[int], seed: int = 0, linear: bool = False) -> Dict[str, Any]:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    n = len(y)
    resid = pred - y
    mse = float(skm.mean_squared_error(y, pred))
    metrics: Dict[str, Optional[float]] = {"mae": f(skm.mean_absolute_error(y, pred)), "mse": f(mse), "rmse": f(math.sqrt(mse)), "r2": f(skm.r2_score(y, pred)),
                                           "median_absolute_error": f(skm.median_absolute_error(y, pred)), "max_error": f(skm.max_error(y, pred)), "bias": f(resid.mean())}
    nc: Dict[str, str] = {}
    if np.all(y != 0):
        metrics["mape"] = f(float(np.mean(np.abs(resid / y))))
    else:
        nc["mape"] = "The actual values include zeros, and a percentage error divides by the actual value."
    if linear and n_params is not None and n - n_params - 1 > 0:
        r2 = skm.r2_score(y, pred)
        metrics["adjusted_r2"] = f(1 - (1 - r2) * (n - 1) / (n - n_params - 1))
    elif not linear:
        nc["adjusted_r2"] = "Adjusted R² charges a penalty per fitted coefficient, which only exists for linear models; R² is reported instead."
    else:
        nc["adjusted_r2"] = "There are too few evaluation rows for the number of inputs."
    ae = np.abs(resid)
    rng = np.random.default_rng(seed)
    sel = np.arange(n) if n <= SCATTER_POINTS else np.sort(rng.choice(n, SCATTER_POINTS, replace=False))
    lo, hi = float(resid.min()), float(resid.max())
    if hi - lo < 1e-12:
        hi = lo + 1.0
    counts, edges = np.histogram(resid, bins=20, range=(lo, hi))
    return {"n": int(n), "metrics": metrics, "not_computed": nc,
            "typical_error": {"within_80": f(np.quantile(ae, 0.8)), "within_95": f(np.quantile(ae, 0.95)), "basis": "absolute errors on held-out rows"},
            "residuals": {"mean": f(resid.mean()), "std": f(resid.std(ddof=1)) if n > 1 else None, "histogram": {"edges": [f(e, 5) for e in edges], "counts": [int(c) for c in counts]}},
            "scatter": {"actual": [f(v, 5) for v in y[sel]], "predicted": [f(v, 5) for v in pred[sel]], "residual": [f(v, 5) for v in resid[sel]], "points": int(len(sel)), "of": int(n)},
            "target_range": {"min": f(y.min()), "max": f(y.max()), "std": f(y.std(ddof=1)) if n > 1 else None}}


# ------------------------------------------------------------------------------------------------ baselines and overfitting
def baseline_classification(y_train: np.ndarray, y_eval: np.ndarray, k: int, positive: Optional[int]) -> Dict[str, Any]:
    """A model that ignores the inputs and always answers with the training class mix. Any real model should beat this."""
    prior = np.bincount(y_train, minlength=k) / len(y_train)
    proba = np.tile(prior, (len(y_eval), 1))
    s = score_all_classification(y_eval, proba, positive, k)
    return {"name": "baseline (always the most common class)", "scores": s, "majority_class_share": f(prior.max())}


def baseline_regression(y_train: np.ndarray, y_eval: np.ndarray) -> Dict[str, Any]:
    pred = np.full(len(y_eval), float(np.mean(y_train)))
    return {"name": "baseline (always the training average)", "scores": score_all_regression(y_eval, pred)}


def overfit_verdict(train: Optional[float], validation: Optional[float], metric_name: str) -> Dict[str, Any]:
    """Compare the score on the rows a model learned from with its score on rows it had not seen. A large positive gap means memorisation."""
    if train is None or validation is None:
        return {"level": "unknown", "gap": None, "metric": metric_name, "message": "The comparison could not be computed."}
    gap = float(train - validation)
    level = "low" if gap <= 0.05 else "moderate" if gap <= 0.15 else "high"
    msg = {"low": "The model scores about the same on rows it learned from and rows it had not seen.",
           "moderate": "The model scores noticeably better on rows it learned from than on new rows, a sign of some memorisation.",
           "high": "The model scores far better on rows it learned from than on new rows: it has memorised the training data and is likely to disappoint on new data."}[level]
    return {"level": level, "gap": f(gap), "metric": metric_name, "message": msg}
