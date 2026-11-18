"""Dataset preparation for modelling: decide, column by column, how each input will be used, and say why.

The decisions use only the INPUT columns (never the target) and are fixed before the data is split, so they cannot leak the answer.
The numbers inside each treatment (medians, scaling factors, category lists, clipping fences, frequencies) are learned from the training
rows only, inside every validation fold, by the pipeline in `pipeline.py`. Leakage detection is the one target-aware step and it is only
a warning/exclusion that the person can see and reverse; it never feeds a model.

Nothing here changes the dataset: it works on an in-memory copy, and the same rules applied to the same version give the same decisions."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..analytics.ops.mixed import pair_measure
from ..analytics.plan import role_of
from . import problem as prob
from .catalog import CATALOG, select
from .constants import LIMITS, MAX_CLASSES, MIN_ROWS, SCHEMA_ASSESSMENT, SMALL_DATA_ROWS, Problem
from .errors import MlError

LEAK_HIGH = 0.99
LEAK_WARN = 0.95
LEAK_MIN_GROUP = 5
ID_MIN_ROWS = 50
FREE_TEXT_LEN = 50
NEAR_CONSTANT = 0.995
ASSOC_SAMPLE = 20_000


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


# ------------------------------------------------------------------------------------------------ one column
def _numeric_facts(s: pd.Series) -> Dict[str, Any]:
    v = s.dropna().to_numpy(float)
    if len(v) == 0:
        return {}
    q1, q3 = np.percentile(v, [25, 75])
    iqr = q3 - q1
    out_share = float(((v < q1 - 1.5 * iqr) | (v > q3 + 1.5 * iqr)).mean()) if iqr > 0 else 0.0
    skew = float(pd.Series(v).skew()) if len(v) > 2 and v.std() > 0 else 0.0
    return {"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()), "median": float(np.median(v)), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
            "skewness": round(skew, 4), "outlier_share": round(out_share, 4)}


def _is_consecutive_ids(s: pd.Series) -> bool:
    v = s.dropna().to_numpy(float)
    return bool(len(v) > 1 and np.all(np.diff(v) > 0) and np.all(np.mod(v, 1) == 0))


def analyse_column(df: pd.DataFrame, name: str, declared: str, cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Facts and the default treatment for one input column (before any person-made override)."""
    pp = cfg["preprocessing"]
    s = df[name]
    n = len(df)
    nn = int(s.notna().sum())
    role = role_of(declared)
    info: Dict[str, Any] = {"name": name, "declared_type": declared, "source_role": role, "rows": n, "missing": n - nn, "missing_pct": _pct(n - nn, n)}
    info["distinct"] = int(s.nunique(dropna=True))
    flags: List[str] = []
    action, reason, treat_role = "use", "", role
    top_share = 0.0
    if nn:
        top_share = float(s.value_counts(normalize=True, dropna=True).iloc[0])
    info["top_value_share"] = round(top_share, 4)
    if role == "numeric":
        info.update(_numeric_facts(s))
    elif role == "categorical" and nn:
        top = s.value_counts().head(5)
        info["top_values"] = [{"value": str(k), "count": int(v)} for k, v in top.items()]
        info["avg_length"] = round(float(s.dropna().astype(str).str.len().mean()), 1)
    elif role == "datetime" and nn:
        d = pd.to_datetime(s.dropna())
        info["min"], info["max"] = d.min().isoformat(), d.max().isoformat()
        info["has_time"] = bool(((d.dt.hour != 0) | (d.dt.minute != 0)).any())

    if nn == 0:
        action, reason = "drop", "Every value is missing."
    elif info["distinct"] <= 1:
        action, reason = "drop", "Constant: it has a single value, so it cannot help tell rows apart."
    elif info["missing_pct"] > float(pp["max_missing_pct"]):
        action, reason = "drop", f"Mostly missing ({info['missing_pct']}% of rows are empty; the limit is {pp['max_missing_pct']}%)."
    elif nn >= 100 and top_share >= NEAR_CONSTANT:
        action, reason = "drop", "Near-constant: one value fills almost every row."
    elif role == "datetime":
        if not pp["datetime_features"]:
            action, reason = "drop", "Date features are turned off."
        else:
            reason = "Date column: calendar parts (year, month, weekday, ...) and a running day count are extracted."
    elif role == "numeric":
        if declared == "integer" and info["distinct"] == nn and nn > ID_MIN_ROWS:
            action = "drop"
            reason = ("Looks like a row number or identifier: whole numbers in strictly increasing order." if _is_consecutive_ids(s) else
                      "Looks like an identifier: every whole-number value is unique, so it would let a model memorise rows.")
        else:
            reason = "Numeric input."
            if declared == "integer" and info["distinct"] <= 5:
                flags.append("few_distinct_values")
    else:
        unique_ratio = info["distinct"] / max(1, nn)
        if info.get("avg_length", 0) > FREE_TEXT_LEN and unique_ratio > 0.5:
            action, reason = "drop", "Free text: long, mostly unique values are not usable as a category."
        elif unique_ratio >= 0.95 and nn > ID_MIN_ROWS:
            action, reason = "drop", "Looks like an identifier: nearly every value is unique, so it would let a model memorise rows."
        elif info["distinct"] > int(pp["max_onehot_levels"]):
            hc = pp["high_cardinality"]
            flags.append("high_cardinality")
            if hc == "drop":
                action, reason = "drop", f"High cardinality ({info['distinct']} categories) and the setting is to drop such columns."
            else:
                reason = f"High cardinality ({info['distinct']} categories): encoded by {'category frequency' if hc == 'frequency' else 'category number'} instead of one column per category."
        else:
            reason = f"Category column with {info['distinct']} values."
    info["default"] = {"action": action, "reason": reason}
    info["flags"] = flags
    return info


def treatment(info: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Turns facts + settings + the person's override into the final treatment of one column."""
    pp, ov = cfg["preprocessing"], (cfg["features"]["overrides"].get(info["name"]) or {})
    excluded = info["name"] in (cfg["features"]["exclude"] or []) or (cfg["features"]["include"] is not None and info["name"] not in cfg["features"]["include"])
    action, reason = info["default"]["action"], info["default"]["reason"]
    role = info["source_role"]
    notes: List[str] = []
    forced = ov.get("role") in ("use", "numeric", "categorical")
    if ov.get("role") == "drop" or (excluded and not forced):
        action, reason = "drop", "Excluded by you."
    elif forced and action == "drop":
        action, reason = "use", "Kept because you chose to use it, although it was flagged: " + info["default"]["reason"]
        notes.append("overridden")
    if ov.get("role") in ("numeric", "categorical") and action == "use":
        if ov["role"] == "numeric" and role == "categorical":
            raise MlError("COLUMN_ROLE_INVALID", f"'{info['name']}' contains text, so it cannot be used as a number.")
        if ov["role"] == "categorical" and role == "numeric" and info["distinct"] > MAX_CLASSES * 2:
            raise MlError("COLUMN_ROLE_INVALID", f"'{info['name']}' has {info['distinct']} distinct numbers, which is too many to treat as categories.")
        if ov["role"] == "categorical" and role == "datetime":
            raise MlError("COLUMN_ROLE_INVALID", f"'{info['name']}' is a date column and cannot be used as a category.")
        if ov["role"] == "numeric" and role == "datetime":
            raise MlError("COLUMN_ROLE_INVALID", f"'{info['name']}' is a date column; use it as a date.")
        role = ov["role"]
    spec: Dict[str, Any] = {"name": info["name"], "action": action, "reason": reason, "role": role, "source_type": info["declared_type"], "notes": notes, "flags": list(info["flags"])}
    if action == "drop":
        return spec
    if role == "numeric":
        impute = ov.get("impute") or pp["numeric_impute"]
        want_log = ov.get("transform") or ("log1p" if pp["log_transform"] == "log1p" else "none")
        if want_log == "none" and pp["log_transform"] == "auto" and "transform" not in ov:
            if info.get("skewness", 0) > 2 and info.get("min", 0) >= 0 and info["distinct"] > 10:
                want_log = "log1p"
                notes.append("log applied because the values are strongly right-skewed and not negative")
        spec.update({"impute": impute, "transform": want_log, "clip": pp["outliers"] == "clip_iqr", "scale": ov.get("scale") or pp["scaling"],
                     "missing_indicator": bool(pp["missing_indicator"] and info["missing"] > 0)})
    elif role == "datetime":
        spec.update({"impute": "median", "parts": ["year", "month", "day", "dayofweek", "quarter", "is_weekend", "dayofyear", "ordinal"] + (["hour"] if info.get("has_time") else []),
                     "scale": ov.get("scale") or pp["scaling"], "missing_indicator": bool(pp["missing_indicator"] and info["missing"] > 0)})
    else:
        high = info["distinct"] > int(pp["max_onehot_levels"]) and ov.get("role") != "categorical" or "high_cardinality" in info["flags"]
        encoding = ov.get("encoding") or (pp["high_cardinality"] if high and pp["high_cardinality"] != "drop" else pp["encoding"])
        if encoding == "onehot" and info["distinct"] > int(pp["max_onehot_levels"]):
            encoding = pp["high_cardinality"] if pp["high_cardinality"] != "drop" else "frequency"
            notes.append("one-hot replaced because of the number of categories")
        spec.update({"impute": ov.get("impute") if ov.get("impute") in ("most_frequent", "constant") else pp["categorical_impute"], "encoding": encoding,
                     "numeric_labels": bool(info["source_role"] == "numeric"), "missing_indicator": bool(pp["missing_indicator"] and info["missing"] > 0),
                     "min_frequency": float(pp["rare_category_min_frequency"]), "max_levels": int(pp["max_onehot_levels"]), "levels": info["distinct"]})
    return spec


def resolve_features(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], target: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (used specs, dropped specs, column facts) for every column except the target."""
    facts, used, dropped = [], [], []
    for name in df.columns:
        if name == target:
            continue
        info = analyse_column(df, name, types[name], cfg)
        facts.append(info)
        spec = treatment(info, cfg)
        (used if spec["action"] == "use" else dropped).append(spec)
    return used, dropped, facts


def encoded_width(spec: Mapping[str, Any]) -> int:
    if spec["role"] == "numeric":
        return 1 + int(bool(spec.get("missing_indicator")))
    if spec["role"] == "datetime":
        return len(spec["parts"]) + int(bool(spec.get("missing_indicator")))
    width = min(int(spec.get("levels", 1)), int(spec.get("max_levels", 20)) + 1) if spec["encoding"] == "onehot" else 1
    return width + int(bool(spec.get("missing_indicator")))


# ------------------------------------------------------------------------------------------------ target-aware diagnostics (warnings only)
def _as_assoc_series(s: pd.Series, role: str) -> Tuple[pd.Series, str]:
    if role == "datetime":
        return (pd.to_datetime(s) - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86400.0, "numeric"
    if role == "numeric":
        return s.astype(float), "numeric"
    return s.astype(object), "categorical"


def target_associations(df: pd.DataFrame, facts: Sequence[Mapping[str, Any]], target: str, target_role: str, rng: np.random.Generator) -> Dict[str, Dict[str, Any]]:
    """How strongly each input relates to the target, with the SAME measures as the mixed-variable analysis (analytics `mixed_association`)."""
    work = df if len(df) <= ASSOC_SAMPLE else df.sample(ASSOC_SAMPLE, random_state=int(rng.integers(0, 2 ** 31 - 1)))
    t_series = prob.class_labels(work[target]) if target_role == "categorical" else work[target].astype(float)
    out: Dict[str, Dict[str, Any]] = {}
    for info in facts:
        name, role = info["name"], info["source_role"]
        if info["missing"] == len(df) or info["distinct"] < 2:
            continue
        if role == "categorical" and (info["distinct"] > MAX_CLASSES or len(work) / max(1, info["distinct"]) < LEAK_MIN_GROUP):
            out[name] = {"measure": None, "reason": "too many categories for a reliable association"}
            continue
        x, x_role = _as_assoc_series(work[name], role)
        frame = pd.DataFrame({"x": x, "t": t_series}).dropna()
        if len(frame) < 20:
            continue
        m = pair_measure(frame["x"], frame["t"], x_role, target_role, rng)
        if m:
            out[name] = {"measure": m["measure"], "value": m["value"], "abs_value": m["abs_value"], "ci": m["ci"], "p_value": m["p_value"], "n": m["n"], "pair_type": m["pair_type"]}
    return out


def leakage(df: pd.DataFrame, facts: Sequence[Mapping[str, Any]], target: str, target_role: str, assoc: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Inputs that look like they contain the answer. `high` ones are excluded by default (reversible); `warning` ones are only flagged."""
    found: List[Dict[str, Any]] = []
    tname = target.lower()
    t_str = df[target].map(lambda v: "" if pd.isna(v) else prob.label_of(v))
    for info in facts:
        name = info["name"]
        if info["missing"] == len(df):
            continue
        same = (df[name].map(lambda v: "" if pd.isna(v) else prob.label_of(v)) == t_str)
        both = df[name].notna() & df[target].notna()
        if both.sum() >= 20 and bool((same & both).sum() / both.sum() >= 0.999):
            found.append({"column": name, "severity": "high", "reason": "It holds the same value as the target in almost every row, so it is the answer under another name.", "measure": "identical", "value": 1.0, "action": "exclude"})
            continue
        a = assoc.get(name) or {}
        v = a.get("abs_value")
        if v is not None and v >= LEAK_HIGH and a.get("n", 0) >= 30:
            found.append({"column": name, "severity": "high", "reason": f"It predicts the target almost perfectly ({a['measure']} = {a['abs_value']:.3f}). That usually means it is calculated from the target or is only known after the outcome.",
                          "measure": a["measure"], "value": a["abs_value"], "action": "exclude"})
        elif v is not None and v >= LEAK_WARN and a.get("n", 0) >= 30:
            found.append({"column": name, "severity": "warning", "reason": f"It is very strongly related to the target ({a['measure']} = {a['abs_value']:.3f}). Check that it will really be known when you make a prediction.",
                          "measure": a["measure"], "value": a["abs_value"], "action": "keep"})
        elif len(name) >= 3 and (tname in name.lower() or (len(tname) >= 3 and name.lower() in tname)):
            found.append({"column": name, "severity": "warning", "reason": "Its name contains the target's name, which can mean it is derived from the target.", "measure": "name", "value": None, "action": "keep"})
    return found


def duplicate_info(df: pd.DataFrame, features: Sequence[str], target: str) -> Dict[str, Any]:
    cols = list(features) + [target]
    dup = int(df.duplicated(subset=cols).sum()) if len(df) else 0
    return {"rows": dup, "share": round(dup / max(1, len(df)), 6),
            "why_it_matters": "Identical rows can land in both the training and the test part, which makes the test score look better than it really is."}


def plan_features(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], target: str, target_role: Optional[str], seed: int
                  ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """The one place where the final feature list is decided, shared by the assessment preview and by training so they can never disagree.
    Returns (used, dropped, facts, leakage findings, target associations). `target_role` is None when the target is not usable (then no leakage check runs)."""
    used, dropped, facts = resolve_features(df, types, cfg, target)
    assoc: Dict[str, Dict[str, Any]] = {}
    leaks: List[Dict[str, Any]] = []
    if target_role is not None:
        labelled = df[df[target].notna()]
        assoc = target_associations(labelled, facts, target, target_role, np.random.default_rng([seed, 7]))
        leaks = leakage(labelled, facts, target, target_role, assoc)
        by_name = {u["name"]: u for u in used}
        for lk in leaks:
            override = (cfg["features"]["overrides"].get(lk["column"]) or {}).get("role")
            if lk["severity"] == "high" and lk["column"] in by_name and override not in ("use", "numeric", "categorical"):
                spec = by_name.pop(lk["column"])
                dropped.append({**spec, "action": "drop", "reason": "Excluded as suspected data leakage: " + lk["reason"], "notes": list(spec.get("notes", [])) + ["leakage"]})
            elif lk["severity"] == "high" and lk["column"] in by_name:
                lk["action"] = "keep (you chose to use it)"
        used = [u for u in used if u["name"] in by_name]
    return used, dropped, facts, leaks, assoc


# ------------------------------------------------------------------------------------------------ the assessment
def assess(df: pd.DataFrame, types: Mapping[str, str], cfg: Mapping[str, Any], *, extended: bool, seed: int, snapshot_info: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The full 'is this dataset ready for modelling, and how will it be prepared' report for one target."""
    i = 1 if extended else 0
    target = cfg["target"]
    det = prob.detect(df[target], types[target], cfg["problem_type"])
    notes: List[str] = []
    issues: List[Dict[str, Any]] = []
    blockers: List[str] = []
    cautions: List[str] = []
    for e in det["errors"]:
        blockers.append(e["message"])
        issues.append({"code": e["code"], "severity": "error", "message": e["message"], "columns": [target]})
    if len(df.columns) - 1 > LIMITS["max_input_columns"][i]:
        msg = f"The table has {len(df.columns) - 1} input columns; the limit is {LIMITS['max_input_columns'][i]}. Exclude columns you do not need."
        blockers.append(msg)
        issues.append({"code": "TOO_MANY_COLUMNS", "severity": "error", "message": msg, "columns": []})
    tfacts = det["target"]
    t_role = "categorical" if (det.get("chosen") in Problem.CLASSIFICATION) else "numeric"
    used, dropped, facts, leaks, assoc = plan_features(df, types, cfg, target, t_role if det["valid"] else None, seed)
    if not used:
        blockers.append("No input columns are left after preparation, so there is nothing to learn from. Review the columns that were excluded and why.")
        issues.append({"code": "NO_FEATURES", "severity": "error", "message": blockers[-1], "columns": []})
    width = sum(encoded_width(u) for u in used)
    if width > LIMITS["max_encoded_features"][i]:
        msg = f"After encoding, the inputs would become {width:,} model columns; the limit is {LIMITS['max_encoded_features'][i]:,}. Exclude columns, or encode categories by frequency."
        blockers.append(msg)
        issues.append({"code": "TOO_WIDE", "severity": "error", "message": msg, "columns": []})
    labelled_rows = tfacts["labelled"]
    if tfacts["missing_pct"] > 20 and det["valid"]:
        cautions.append(f"{tfacts['missing_pct']}% of rows have no target value and will be left out of training.")
    imbalance = None
    rare: List[str] = []
    if det["valid"] and det["chosen"] in Problem.CLASSIFICATION:
        imbalance = prob.imbalance(det["classes"])
        rare = det["rare_classes"]
        if rare:
            msg = f"{len(rare)} class(es) have fewer than {prob.RARE_CLASS_MIN} rows ({', '.join(rare[:5])}{'...' if len(rare) > 5 else ''}), too few to train and check on."
            if cfg["rare_classes"] == "drop":
                cautions.append(msg + " Their rows will be dropped, as you chose.")
            else:
                blockers.append(msg + " Choose 'drop rare classes' to remove their rows, or group them into a larger class first.")
                issues.append({"code": "RARE_CLASSES", "severity": "error", "message": msg, "columns": [target]})
        if imbalance["level"] in ("moderate", "severe"):
            cautions.append(imbalance["message"])
    dup = duplicate_info(df[df[target].notna()], [u["name"] for u in used], target) if det["valid"] else {"rows": 0, "share": 0.0}
    if dup["rows"] and dup["share"] > 0.01:
        cautions.append(f"{dup['rows']:,} rows ({round(100 * dup['share'], 1)}%) repeat another row exactly. " + ("They will be removed before splitting." if cfg["preprocessing"]["drop_duplicates"] else "They are kept, which can inflate scores."))
    if labelled_rows and labelled_rows < SMALL_DATA_ROWS and det["valid"]:
        cautions.append(f"With only {labelled_rows:,} labelled rows, any model's score will be uncertain; treat results as indicative.")
    if used and labelled_rows and len(used) > labelled_rows / 5 and det["valid"]:
        cautions.append("There are many inputs relative to the number of rows, which makes overfitting more likely.")
    high_leaks = [lk for lk in leaks if lk["severity"] == "high"]
    if high_leaks:
        cautions.append(f"{len(high_leaks)} input column(s) look like they contain the answer and are excluded unless you override: {', '.join(l['column'] for l in high_leaks[:5])}.")
    problem = det.get("chosen")
    algos, skipped = ([], [])
    if problem and used:
        algos, skipped = select(cfg["algorithms"], problem, min(labelled_rows, int(cfg["max_train_rows"] or LIMITS["max_train_rows"][i])), width)
    plan = prob.metric_plan(problem, cfg["primary_metric"], balance=imbalance, target=tfacts) if problem else None
    verdict = "not_suitable" if blockers else ("caution" if cautions else "ready")
    columns = []
    spec_by = {u["name"]: u for u in used}
    drop_by = {d["name"]: d for d in dropped}
    for info in facts:
        name = info["name"]
        spec = spec_by.get(name) or drop_by.get(name) or {}
        columns.append({**{k: v for k, v in info.items() if k != "default"}, "decision": {k: v for k, v in spec.items() if k not in ("name", "source_type", "flags")},
                        "association": assoc.get(name), "leakage": next((lk for lk in leaks if lk["column"] == name), None)})
    return {
        "schema": SCHEMA_ASSESSMENT,
        "dataset": {"rows": int(len(df)), "columns": int(len(df.columns)), "labelled_rows": labelled_rows, **(snapshot_info or {})},
        "target": {"name": target, **tfacts},
        "problem": {k: det[k] for k in ("recommended", "chosen", "why", "alternatives", "errors", "warnings", "valid") if k in det} | ({"classes": det["classes"]} if "classes" in det else {}),
        "columns": columns,
        "preprocessing": {"used": len(used), "dropped": len(dropped), "encoded_features_estimate": width, "settings": cfg["preprocessing"],
                          "principle": "Decisions use only the input columns and are fixed before splitting; medians, scaling factors and category lists are learned from the training rows only."},
        "leakage": leaks, "imbalance": imbalance, "duplicates": dup, "issues": issues,
        "metrics": plan,
        "algorithms": {"selected": [{"key": k, "label": CATALOG[k].label, "cost": CATALOG[k].cost, "description": CATALOG[k].description} for k in algos], "skipped": skipped},
        "suitability": {"verdict": verdict, "blockers": blockers, "cautions": cautions,
                        "summary": {"ready": "The dataset looks ready for modelling.", "caution": "The dataset can be modelled, with the cautions listed.", "not_suitable": "The dataset cannot be modelled as configured; see the reasons."}[verdict]},
        "proposed_config": _proposed(cfg, det, rare, imbalance, leaks),
        "notes": notes + ["The original dataset is not changed; this report is computed on an in-memory copy."],
    }


def _proposed(cfg: Mapping[str, Any], det: Mapping[str, Any], rare: Sequence[str], imbalance: Optional[Mapping[str, Any]], leaks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The configuration the tool recommends for this target (what the person gets by pressing 'use recommended settings')."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}
    if det.get("chosen"):
        out["problem_type"] = "auto"
    if rare:
        out["rare_classes"] = "drop"
    if imbalance and imbalance["level"] != "balanced":
        out["class_weight"] = "auto"
    if det.get("classes"):
        smallest = min(det["classes"], key=lambda c: c["count"])
        if det.get("chosen") == Problem.BINARY and not cfg.get("positive_label"):
            out["positive_label"] = smallest["label"]
    return out
