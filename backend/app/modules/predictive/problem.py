"""Problem detection: what kind of prediction is this, why, and what would be invalid. The recommendation follows from the target column
alone (its type, how many distinct values it has, how they are spread); the person can override it, and an override that cannot work
(for example regression on a text target) is refused with a clear reason instead of failing later."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from ..analytics.plan import role_of
from .constants import MAX_CLASSES, MIN_ROWS, Problem
from .errors import unsuitable

RARE_CLASS_MIN = 3                 # a class needs at least this many labelled rows to appear in training, validation and test
INT_CLASS_MAX = 10                 # whole-number targets with at most this many values are probably class codes


def label_of(value: Any) -> str:
    """Class labels are text. Whole-number floats lose their '.0' so 1.0 and 1 are the same class."""
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def class_labels(series: pd.Series) -> pd.Series:
    return series.map(lambda v: np.nan if v is None or (not isinstance(v, str) and pd.isna(v)) else label_of(v)).astype(object)


def _integer_like(y: pd.Series) -> bool:
    arr = y.to_numpy(float)
    return bool(len(arr) and np.all(np.mod(arr, 1) == 0))


def describe_target(series: pd.Series, declared_type: str) -> Dict[str, Any]:
    """Facts about the target column; `problem` is computed from them separately."""
    role = role_of(declared_type)
    y = series.dropna()
    out: Dict[str, Any] = {"role": role, "type": declared_type, "rows": int(len(series)), "labelled": int(len(y)), "missing": int(len(series) - len(y)),
                           "missing_pct": round(100 * (len(series) - len(y)) / max(1, len(series)), 2), "distinct": int(y.nunique())}
    if role == "numeric" and len(y):
        v = y.to_numpy(float)
        q1, q3 = np.percentile(v, [25, 75])
        iqr = q3 - q1
        outliers = float(((v < q1 - 1.5 * iqr) | (v > q3 + 1.5 * iqr)).mean()) if iqr > 0 else 0.0
        skew = float(pd.Series(v).skew()) if len(v) > 2 and v.std() > 0 else 0.0
        out.update({"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()), "median": float(np.median(v)), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                    "skewness": skew, "outlier_share": outliers, "zeros": int((v == 0).sum()), "negatives": int((v < 0).sum()), "integer_like": _integer_like(y)})
    return out


def class_table(labels: pd.Series) -> List[Dict[str, Any]]:
    counts = labels.dropna().value_counts()
    total = int(counts.sum())
    return [{"label": str(k), "count": int(v), "share": round(float(v) / total, 6)} for k, v in counts.items()]


def detect(series: pd.Series, declared_type: str, requested: str = "auto") -> Dict[str, Any]:
    """Never raises. `valid` is False when no workable problem can be built; `errors` then explains each reason in plain words."""
    facts = describe_target(series, declared_type)
    role, n, distinct = facts["role"], facts["labelled"], facts["distinct"]
    errors: List[Dict[str, str]] = []
    warnings: List[str] = []
    alternatives: List[Dict[str, str]] = []
    recommended: Optional[str] = None
    why = ""

    def err(code: str, message: str) -> None:
        errors.append({"code": code, "message": message})

    if role == "datetime":
        err("TARGET_UNSUPPORTED", "A date column cannot be predicted directly. Predict a number or a category instead (for example, days until an event).")
    elif n < MIN_ROWS:
        err("TOO_FEW_ROWS", f"Only {n} rows have a value for the target; at least {MIN_ROWS} are needed to train and check a model.")
    elif distinct < 2:
        err("TARGET_CONSTANT", "The target has a single value, so there is nothing to predict.")
    else:
        if role == "numeric":
            if distinct == 2:
                recommended, why = Problem.BINARY, "The target has exactly two distinct values, so this is a yes/no style (binary) classification."
            elif facts["integer_like"] and distinct <= INT_CLASS_MAX and distinct / n <= 0.05:
                recommended = Problem.MULTICLASS
                why = f"The target is made of whole numbers with only {distinct} distinct values, which usually means categories coded as numbers."
                alternatives.append({"problem": Problem.REGRESSION, "why": "Choose this instead if the numbers are quantities (a count or a score) rather than category codes."})
            else:
                recommended = Problem.REGRESSION
                why = "The target is numeric with many distinct values, so the task is to predict a number (regression)."
                if facts["integer_like"] and distinct <= INT_CLASS_MAX:
                    alternatives.append({"problem": Problem.MULTICLASS, "why": f"The target has only {distinct} whole-number values; choose this if they are category codes."})
        else:
            if distinct == 2:
                recommended, why = Problem.BINARY, "The target has exactly two categories, so this is a binary classification."
            elif distinct <= MAX_CLASSES:
                recommended, why = Problem.MULTICLASS, f"The target has {distinct} categories, so this is a multiclass classification."
            else:
                err("TOO_MANY_CLASSES", f"The target has {distinct} distinct text values, which is too many categories to classify (limit {MAX_CLASSES}). Group similar values first, or choose another target.")
    chosen = recommended
    if requested != "auto" and not errors:
        chosen = requested
        if requested == Problem.REGRESSION and role != "numeric":
            err("PROBLEM_INVALID", "Regression predicts a number, but this target contains text. Choose classification, or a numeric target.")
        elif requested in Problem.CLASSIFICATION:
            if distinct > MAX_CLASSES:
                err("PROBLEM_INVALID", f"The target has {distinct} distinct values, which looks continuous: classification would treat each value as its own class (limit {MAX_CLASSES}). Choose regression, or group the target into ranges first.")
            elif requested == Problem.BINARY and distinct != 2:
                err("PROBLEM_INVALID", f"Binary classification needs exactly two target values but this target has {distinct}. Choose multiclass classification.")
            elif requested == Problem.MULTICLASS and distinct == 2:
                err("PROBLEM_INVALID", "This target has only two values, so it is a binary classification. Choose binary classification.")
        if requested != recommended and not errors:
            warnings.append(f"You chose {requested.replace('_', ' ')} although the recommendation is {recommended.replace('_', ' ')}. Make sure that is what you intend.")
    out: Dict[str, Any] = {"recommended": recommended, "chosen": chosen if not errors else None, "why": why, "alternatives": alternatives, "errors": errors, "warnings": warnings,
                           "valid": not errors, "target": facts}
    if chosen in Problem.CLASSIFICATION and not errors:
        table = class_table(class_labels(series))
        out["classes"] = table
        out["rare_classes"] = [c["label"] for c in table if c["count"] < RARE_CLASS_MIN]
    return out


def require_valid(det: Mapping[str, Any]) -> str:
    if not det["valid"]:
        e = det["errors"][0]
        raise unsuitable(e["code"], e["message"], details=[{"field": "target", "issue": x["code"], "message": x["message"]} for x in det["errors"]])
    return str(det["chosen"])


# ------------------------------------------------------------------------------------------------ class balance
def imbalance(table: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Class balance with plain verdicts. The ratio compares the largest and smallest class."""
    counts = [int(c["count"]) for c in table]
    k = len(counts)
    smallest, largest = min(counts), max(counts)
    share = smallest / sum(counts)
    ratio = largest / max(1, smallest)
    uniform = 1.0 / k
    rel = share / uniform
    if rel >= 0.7:
        level = "balanced"
    elif rel >= 0.4:
        level = "mild"
    elif share >= 0.05:
        level = "moderate"
    else:
        level = "severe"
    minority = min(table, key=lambda c: c["count"])["label"]
    out = {"level": level, "ratio": round(ratio, 3), "smallest_class": minority, "smallest_share": round(share, 6), "classes": k,
           "accuracy_warning": level in ("moderate", "severe"),
           "recommend_class_weight": level in ("mild", "moderate", "severe")}
    out["message"] = {
        "balanced": "The classes are roughly balanced, so accuracy is a fair first measure alongside the others.",
        "mild": "The classes are somewhat uneven. Class weighting is applied automatically and accuracy is read together with balanced accuracy.",
        "moderate": "One class is clearly rarer than the rest. Accuracy can look good even if the model misses the rare class, so precision, recall, F1 and PR-AUC matter more here. Class weighting is applied.",
        "severe": "One class is very rare. A model that always predicts the common class would score high accuracy while being useless, so accuracy is not used to choose models. Class weighting is applied.",
    }[level]
    return out


# ------------------------------------------------------------------------------------------------ metric choice
CLASS_SCORERS = {"accuracy": "accuracy", "balanced_accuracy": "balanced_accuracy", "f1": "f1", "f1_macro": "f1_macro", "roc_auc": "roc_auc", "average_precision": "average_precision",
                 "neg_log_loss": "neg_log_loss"}
REG_SCORERS = {"neg_root_mean_squared_error": "neg_root_mean_squared_error", "neg_mean_absolute_error": "neg_mean_absolute_error", "r2": "r2"}
METRIC_LABEL = {"accuracy": "accuracy", "balanced_accuracy": "balanced accuracy", "f1": "F1", "f1_macro": "macro F1", "roc_auc": "ROC-AUC", "average_precision": "PR-AUC",
                "neg_log_loss": "log loss", "neg_root_mean_squared_error": "RMSE", "neg_mean_absolute_error": "MAE", "r2": "R²"}


def scorer_name(metric: str, problem: str) -> str:
    if problem == Problem.MULTICLASS and metric == "roc_auc":
        return "roc_auc_ovr"
    return metric


def validate_metric(metric: str, problem: str) -> None:
    ok = {Problem.BINARY: set(CLASS_SCORERS), Problem.MULTICLASS: set(CLASS_SCORERS) - {"f1", "average_precision"}, Problem.REGRESSION: set(REG_SCORERS)}[problem]
    if metric not in ok:
        hint = {"f1": "use macro F1 for more than two classes", "average_precision": "PR-AUC is defined here for binary problems"}.get(metric, "it does not apply to this kind of problem")
        raise unsuitable("METRIC_INVALID", f"'{METRIC_LABEL.get(metric, metric)}' cannot be used for {problem.replace('_', ' ')}: {hint}.")


def metric_plan(problem: str, requested: str, *, balance: Optional[Mapping[str, Any]] = None, target: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Which metric ranks the models, which are reported, and which are deliberately not computed (with the reason)."""
    reasons: List[str] = []
    not_computed: List[Dict[str, str]] = []
    if problem == Problem.REGRESSION:
        heavy = bool(target and (abs(target.get("skewness", 0.0)) > 2 or target.get("outlier_share", 0.0) > 0.05))
        auto = "neg_mean_absolute_error" if heavy else "neg_root_mean_squared_error"
        reasons.append("The target has a long tail or many extreme values, so models are ranked by MAE, which is not dominated by a few huge errors." if heavy else
                       "Models are ranked by RMSE, which is in the units of the target and penalises large misses.")
        computed = ["MAE", "MSE", "RMSE", "R²", "adjusted R²", "median absolute error"]
        if target and (target.get("zeros", 0) > 0):
            not_computed.append({"metric": "MAPE", "reason": "The target contains zeros, and a percentage error divides by the true value."})
        else:
            computed.append("MAPE")
    elif problem == Problem.BINARY:
        level = (balance or {}).get("level", "balanced")
        if level in ("moderate", "severe"):
            auto = "average_precision"
            reasons.append("The positive class is rare, so models are ranked by PR-AUC (precision against recall); accuracy would reward ignoring the rare class.")
        elif level == "mild":
            auto = "roc_auc"
            reasons.append("The classes are somewhat uneven, so models are ranked by ROC-AUC, which does not depend on the class mix or a cut-off.")
        else:
            auto = "roc_auc"
            reasons.append("The classes are balanced; models are ranked by ROC-AUC, which judges how well the model separates the two classes at any cut-off.")
        computed = ["accuracy", "balanced accuracy", "precision", "recall", "F1", "ROC-AUC", "PR-AUC", "log loss", "confusion matrix", "classification report"]
        if (balance or {}).get("accuracy_warning"):
            reasons.append("Accuracy is reported but flagged as unreliable for this class mix.")
    else:
        auto = "f1_macro"
        reasons.append("With several classes, models are ranked by macro F1, which gives every class equal weight so rare classes are not ignored.")
        computed = ["accuracy", "balanced accuracy", "macro and weighted precision / recall / F1", "ROC-AUC (one-vs-rest)", "log loss", "confusion matrix", "classification report"]
        not_computed.append({"metric": "PR-AUC", "reason": "Reported for binary problems only; per-class precision and recall are in the classification report."})
    primary = auto if requested == "auto" else requested
    if requested != "auto":
        validate_metric(requested, problem)
        reasons = [f"You chose {METRIC_LABEL[requested]} to rank models."]
    return {"primary": primary, "primary_label": METRIC_LABEL[primary], "chosen_by": "you" if requested != "auto" else "rule", "reasons": reasons, "computed": computed, "not_computed": not_computed,
            "higher_is_better": True, "note": "Scores are ranked so that higher is always better (error metrics are shown with their sign flipped inside the ranking and as ordinary positive numbers in tables)."}
