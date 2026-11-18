"""Using a saved model: validate inputs against the schema it was trained on, predict one row or a whole table, and say honestly what the
numbers are. Runs inside the isolated worker; a model file is only ever opened there, and only after the caller verified its SHA-256.

What a prediction is, and is not:
  * classification: a predicted class plus model scores per class. Scores are called probabilities only as far as the calibration check supports
    (see `probability_reading`); they are never guarantees.
  * regression: a single best estimate, with a typical-error range measured on held-out rows. It is not a prediction interval for this row."""
from __future__ import annotations

import csv
import io
import warnings
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
import sklearn

from ..analytics.csvsafe import csv_safe
from . import explain as X_
from . import metrics as M
from .constants import MAX_SINGLE_VALUE_LEN, ML_VERSION, SCHEMA_PREDICTION, Problem
from .errors import Cancelled, MlError

CHUNK = 2000
MAX_CLASS_COLUMNS = 12
DRIFT_SHARE = 0.10


def load_bundle(data: bytes) -> Dict[str, Any]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bundle = joblib.load(io.BytesIO(data))
    if not isinstance(bundle, dict) or bundle.get("format") != 1 or "pipeline" not in bundle:
        raise MlError("MODEL_UNREADABLE", "This model file is not in a format this version understands. Train the model again.")
    bundle["_notes"] = []
    trained = (bundle.get("versions") or {}).get("scikit_learn")
    if trained and trained != sklearn.__version__ or any("Version" in w.category.__name__ for w in caught):
        bundle["_notes"].append(f"This model was trained with scikit-learn {trained} and the server now runs {sklearn.__version__}. Results may differ slightly; train it again to be certain.")
    return bundle


# ------------------------------------------------------------------------------------------------ input checks
def _clean_levels(s: pd.Series, spec: Mapping[str, Any]) -> pd.Series:
    if spec.get("numeric_labels"):
        num = pd.to_numeric(s, errors="coerce")
        return num.map(lambda v: np.nan if pd.isna(v) else (str(int(v)) if float(v).is_integer() else repr(float(v))))
    return s.map(lambda v: np.nan if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "" else str(v).strip())


def inspect_column(s: pd.Series, spec: Mapping[str, Any]) -> Dict[str, np.ndarray]:
    """Boolean masks per row: missing / invalid (present but not readable as the column's type) / unseen category / outside the training range."""
    present = s.notna() & (s.astype(str).str.strip() != "")
    if spec["role"] == "numeric":
        num = pd.to_numeric(s, errors="coerce")
        bad = present & num.isna()
        rng = np.zeros(len(s), dtype=bool)
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None:
            rng = ((num < lo) | (num > hi)).fillna(False).to_numpy()
        return {"missing": (~present).to_numpy(), "invalid": bad.to_numpy(), "unseen": np.zeros(len(s), bool), "range": rng}
    if spec["role"] == "datetime":
        d = pd.to_datetime(s, errors="coerce")
        bad = present & d.isna()
        rng = np.zeros(len(s), dtype=bool)
        if spec.get("min") and spec.get("max"):
            rng = ((d < pd.Timestamp(spec["min"])) | (d > pd.Timestamp(spec["max"]))).fillna(False).to_numpy()
        return {"missing": (~present).to_numpy(), "invalid": bad.to_numpy(), "unseen": np.zeros(len(s), bool), "range": rng}
    labels = _clean_levels(s, spec)
    levels = spec.get("levels")
    unseen = (labels.notna() & ~labels.isin(levels)).to_numpy() if levels is not None else np.zeros(len(s), bool)
    return {"missing": labels.isna().to_numpy(), "invalid": np.zeros(len(s), bool), "unseen": unseen, "range": np.zeros(len(s), bool)}


MESSAGES = {
    "missing": "No value was given, so the typical value from training was used.",
    "invalid": "This is not a valid {kind}, so it was treated as missing.",
    "unseen": "This category was not in the training data, so the model has nothing to compare it with.",
    "range": "This value is outside the range seen in training, so the prediction is an extrapolation and less reliable.",
}


def _issues_for_row(masks: Mapping[str, Mapping[str, np.ndarray]], schema: Sequence[Mapping[str, Any]], i: int) -> List[Dict[str, str]]:
    out = []
    for spec in schema:
        for kind in ("invalid", "unseen", "range", "missing"):
            if masks[spec["name"]][kind][i]:
                out.append({"column": spec["name"], "kind": kind, "severity": "info" if kind == "missing" else "warning",
                            "message": MESSAGES[kind].format(kind={"numeric": "number", "datetime": "date"}.get(spec["role"], "value"))})
    return out


# ------------------------------------------------------------------------------------------------ single prediction
def _prob_reading(bundle: Mapping[str, Any]) -> str:
    verdict = (bundle.get("summary") or {}).get("calibration")
    if bundle["problem"] == Problem.MULTICLASS:
        return "These are the model's scores for each class; they add up to 100% but were not checked for calibration, so read them as relative rankings, not as exact chances."
    return {"good": "On rows held out from training, predicted probabilities were close to how often the outcome really happened. They are still estimates, not guarantees.",
            "fair": "On held-out rows the predicted probabilities were only roughly in line with how often the outcome happened. Treat them as approximate scores, not guarantees.",
            "poor": "On held-out rows the predicted probabilities did NOT match how often the outcome happened. Use them to rank cases, not as chances.",
            }.get(verdict, "Calibration could not be checked for this model, so treat the numbers as scores for ranking cases, not as chances or guarantees.")


def predict_one(bundle: Mapping[str, Any], values: Mapping[str, Any], background: Optional[pd.DataFrame], *, explain: bool, seed: int = 0) -> Dict[str, Any]:
    schema = bundle["input_schema"]
    names = [s["name"] for s in schema]
    if not isinstance(values, Mapping):
        raise MlError("INPUT_INVALID", "Inputs must be given as name/value pairs.")
    unknown = sorted(set(values) - set(names))
    if unknown:
        raise MlError("UNKNOWN_INPUT", f"These inputs are not part of the model: {', '.join(unknown[:5])}.")
    row: Dict[str, Any] = {}
    for n in names:
        v = values.get(n)
        if isinstance(v, bool):
            v = "true" if v else "false"
        if v is not None and not isinstance(v, (str, int, float)):
            raise MlError("INPUT_INVALID", f"The value for '{n}' must be text or a number.")
        if isinstance(v, str) and len(v) > MAX_SINGLE_VALUE_LEN:
            raise MlError("INPUT_INVALID", f"The value for '{n}' is too long.")
        row[n] = v
    frame = pd.DataFrame([row], columns=names, dtype=object)
    masks = {s["name"]: inspect_column(frame[s["name"]], s) for s in schema}
    if all(masks[n]["missing"][0] or masks[n]["invalid"][0] for n in names):
        raise MlError("NO_INPUT", "Enter at least one value to get a prediction.")
    issues = _issues_for_row(masks, schema, 0)
    pipe = bundle["pipeline"]
    k = len(bundle["classes"])
    problem = bundle["problem"]
    out = X_.predict_output(pipe, frame, problem, k)
    res: Dict[str, Any] = {"schema": SCHEMA_PREDICTION, "problem": problem, "model": {"algorithm": bundle["algorithm"], "tuned": bundle["tuned"], "ml_version": bundle.get("ml_version")},
                           "inputs": {n: (None if row[n] is None or str(row[n]).strip() == "" else str(row[n])[:MAX_SINGLE_VALUE_LEN]) for n in names}, "input_issues": issues,
                           "notes": list(bundle.get("_notes", []))}
    if problem in Problem.CLASSIFICATION:
        p = out[0]
        order = np.argsort(-p)
        top = int(order[0])
        res["prediction"] = {"label": bundle["classes"][top], "probability": M.f(p[top], 5)}
        res["probabilities"] = [{"class": bundle["classes"][int(j)], "probability": M.f(p[int(j)], 5)} for j in order[:MAX_CLASS_COLUMNS]]
        if bundle.get("positive_index") is not None:
            res["positive"] = {"class": bundle["classes"][bundle["positive_index"]], "probability": M.f(p[bundle["positive_index"]], 5)}
        res["probability_reading"] = _prob_reading(bundle)
        res["calibration"] = (bundle.get("summary") or {}).get("calibration") or "unknown"
        res["margin"] = M.f(p[top] - (p[int(order[1])] if k > 1 else 0.0), 5)
    else:
        v = float(out[0])
        te = (bundle.get("summary") or {}).get("typical_error") or {}
        res["prediction"] = {"value": M.f(v, 6)}
        if te.get("within_80") is not None:
            res["typical_error"] = {"within_80": te["within_80"], "range_80": [M.f(v - te["within_80"], 6), M.f(v + te["within_80"], 6)], "within_95": te.get("within_95"),
                                    "reading": "On held-out rows, about 80% of the model's predictions were within this distance of the true value. It is a typical error, not a guaranteed interval for this row."}
        tr = (bundle.get("summary") or {}).get("target_range")
        if tr and tr.get("min") is not None and (v < tr["min"] or v > tr["max"]):
            res["input_issues"].append({"column": "(prediction)", "kind": "range", "severity": "warning", "message": "The prediction is outside the range of target values seen in training."})
    if explain and background is not None and len(background):
        res["local"] = X_.local_effects(pipe, frame, background[names], problem, bundle.get("positive_index"), bundle["classes"], seed=seed)
    elif explain:
        res["local"] = {"available": False, "reason": "No reference rows are stored with this model, so a per-prediction explanation cannot be computed."}
    return res


# ------------------------------------------------------------------------------------------------ batch prediction
def predict_table(bundle: Mapping[str, Any], df: pd.DataFrame, *, source_rows: Optional[Sequence[int]] = None, progress: Optional[Callable[[int, int], None]] = None,
                  cancel: Optional[Callable[[], None]] = None, max_rows: Optional[int] = None) -> Tuple[bytes, Dict[str, Any]]:
    schema = bundle["input_schema"]
    names = [s["name"] for s in schema]
    missing_cols = [n for n in names if n not in df.columns]
    if missing_cols:
        raise MlError("MISSING_COLUMNS", f"The dataset does not have the columns this model needs: {', '.join(missing_cols[:8])}{'...' if len(missing_cols) > 8 else ''}.")
    n = len(df)
    if max_rows is not None and n > max_rows:
        raise MlError("BATCH_TOO_LARGE", f"This dataset has {n:,} rows; your limit for one batch is {max_rows:,}.")
    if n == 0:
        raise MlError("BATCH_EMPTY", "The dataset has no rows to predict.")
    problem, classes, pipe = bundle["problem"], bundle["classes"], bundle["pipeline"]
    k = len(classes)
    X = df[names]
    masks = {s["name"]: inspect_column(X[s["name"]], s) for s in schema}
    rows_num = list(source_rows) if source_rows is not None else list(range(1, n + 1))
    classification = problem in Problem.CLASSIFICATION
    preds: List[Any] = []
    probs = np.zeros((n, k)) if classification else None
    for start in range(0, n, CHUNK):
        if cancel:
            cancel()
        chunk = X.iloc[start:start + CHUNK]
        out = X_.predict_output(pipe, chunk, problem, k)
        if classification:
            probs[start:start + len(chunk)] = out
        else:
            preds.extend(out.tolist())
        if progress:
            progress(min(n, start + CHUNK), n)
    if classification:
        top = probs.argmax(axis=1)
        preds = [classes[j] for j in top]
    flag_cols = ("invalid", "unseen", "range")
    row_flags: List[str] = []
    for i in range(n):
        f = []
        for s in schema:
            for kind in flag_cols:
                if masks[s["name"]][kind][i]:
                    f.append(f"{kind}:{s['name']}")
        row_flags.append("; ".join(f))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    class_cols = classes if (classification and k <= MAX_CLASS_COLUMNS) else []
    taken = set(names)

    def unique(name: str) -> str:                                  # an input column called "prediction" or "flags" must not collide with ours
        while name in taken:
            name += "_model"
        taken.add(name)
        return name
    head = [unique("row")] + names + [unique("prediction")] + ([unique("probability")] if classification else []) + [unique(f"probability_{c}") for c in class_cols] + [unique("flags")]
    w.writerow([csv_safe(h) for h in head])
    for i in range(n):
        base = [rows_num[i]] + [csv_safe(_cell(X.iloc[i][c])) for c in names]
        pv = csv_safe(preds[i]) if classification else M.f(preds[i], 8)
        extra = [M.f(probs[i].max(), 5)] + [M.f(probs[i][j], 5) for j in range(len(class_cols))] if classification else []
        w.writerow(base + [pv] + extra + [csv_safe(row_flags[i])])
    by_col: Dict[str, Dict[str, int]] = {}
    for s in schema:
        counts = {kind: int(masks[s["name"]][kind].sum()) for kind in ("missing", "invalid", "unseen", "range")}
        if any(counts.values()):
            by_col[s["name"]] = counts
    flagged = int(sum(1 for f in row_flags if f))
    drift = [c for c, v in by_col.items() if (v["unseen"] + v["range"] + v["invalid"]) / n > DRIFT_SHARE]
    summary: Dict[str, Any] = {"rows": int(n), "problem": problem, "rows_with_warnings": flagged, "issues_by_column": by_col,
                               "possible_drift": drift, "notes": list(bundle.get("_notes", []))}
    if drift:
        summary["notes"].append(f"For {len(drift)} column(s), more than {int(DRIFT_SHARE * 100)}% of rows have unseen categories, out-of-range or unreadable values. The new data may differ from the training data, so these predictions deserve extra care.")
    if classification:
        counts = pd.Series(preds).value_counts()
        summary["prediction_counts"] = [{"class": str(c), "rows": int(v), "share": M.f(v / n, 5)} for c, v in counts.items()]
        summary["probability_reading"] = _prob_reading(bundle)
        summary["mean_top_probability"] = M.f(probs.max(axis=1).mean(), 5)
    else:
        a = np.asarray(preds, float)
        summary["prediction_summary"] = {"min": M.f(a.min()), "p25": M.f(np.quantile(a, 0.25)), "median": M.f(np.median(a)), "p75": M.f(np.quantile(a, 0.75)), "max": M.f(a.max()), "mean": M.f(a.mean())}
        te = (bundle.get("summary") or {}).get("typical_error")
        if te:
            summary["typical_error"] = te
    return buf.getvalue().encode("utf-8"), summary


def _cell(v: Any) -> Any:
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.isoformat()
    if isinstance(v, (np.floating, float)):
        return repr(float(v))
    return v
