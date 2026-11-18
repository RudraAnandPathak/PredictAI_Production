"""The training configuration: a closed vocabulary that is validated before anything runs. It is plain data (no code, no callables),
it is stored with every model, and the same configuration + dataset version + seed reproduces the same model."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..analytics.plan import role_of
from .constants import LIMITS, MAX_CLASSES, SEED_DEFAULT, Problem
from .errors import config_invalid

PROBLEM_CHOICES = ("auto",) + Problem.ALL
SCALING = ("auto", "standard", "robust", "minmax", "none")
NUM_IMPUTE = ("median", "mean", "most_frequent")
CAT_IMPUTE = ("most_frequent", "constant")
ENCODING = ("onehot", "ordinal", "frequency")
HIGH_CARD = ("frequency", "ordinal", "drop")
OUTLIERS = ("none", "clip_iqr")
LOG = ("auto", "none", "log1p")
CV_STRATEGIES = ("auto", "kfold", "stratified_kfold", "repeated_kfold", "time_series")
HPO_METHODS = ("none", "grid", "random")
CLASS_WEIGHT = ("auto", "balanced", "none")
RARE_CLASSES = ("error", "drop")
COLUMN_ROLES = ("use", "drop", "numeric", "categorical")
METRICS = ("auto", "accuracy", "balanced_accuracy", "f1", "f1_macro", "roc_auc", "average_precision", "neg_log_loss",
           "neg_root_mean_squared_error", "neg_mean_absolute_error", "r2")
CLASSIFICATION_METRICS = ("accuracy", "balanced_accuracy", "f1", "f1_macro", "roc_auc", "average_precision", "neg_log_loss")
REGRESSION_METRICS = ("neg_root_mean_squared_error", "neg_mean_absolute_error", "r2")

DEFAULT_CONFIG: Dict[str, Any] = {
    "target": None,
    "problem_type": "auto",
    "positive_label": None,
    "features": {"include": None, "exclude": [], "overrides": {}},
    "preprocessing": {
        "numeric_impute": "median", "categorical_impute": "most_frequent", "missing_indicator": False, "scaling": "auto", "encoding": "onehot",
        "max_onehot_levels": 20, "high_cardinality": "frequency", "rare_category_min_frequency": 0.01, "outliers": "none", "log_transform": "none",
        "datetime_features": True, "drop_duplicates": True, "max_missing_pct": 60.0,
    },
    "algorithms": "auto",
    "validation": {"test_size": 0.2, "cv_strategy": "auto", "cv_folds": 5, "cv_repeats": 2, "shuffle": True, "time_column": None},
    "hpo": {"method": "none", "n_iter": 10, "top_k": 2, "cv_folds": 3, "param_grids": {}},
    "primary_metric": "auto",
    "class_weight": "auto",
    "rare_classes": "error",
    "seed": SEED_DEFAULT,
    "time_budget_s": None,
    "max_train_rows": None,
}


def _enum(value: Any, field: str, choices: Sequence[Any]) -> Any:
    if value not in choices:
        raise config_invalid(field, f"Choose one of: {', '.join(str(c) for c in choices)}.")
    return value


def _int(value: Any, field: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise config_invalid(field, "Enter a whole number.")
    if not lo <= value <= hi:
        raise config_invalid(field, f"Enter a number from {lo} to {hi}.")
    return value


def _float(value: Any, field: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise config_invalid(field, "Enter a number.")
    if not lo <= float(value) <= hi:
        raise config_invalid(field, f"Enter a number from {lo} to {hi}.")
    return float(value)


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise config_invalid(field, "Use true or false.")
    return value


def _section(raw: Mapping[str, Any], key: str, allowed: Mapping[str, Any]) -> Dict[str, Any]:
    section = raw.get(key, {})
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise config_invalid(key, "This section must be an object.")
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise config_invalid(f"{key}.{unknown[0]}", "This setting does not exist.")
    return section


def merge_defaults(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if raw is not None and not isinstance(raw, Mapping):
        raise config_invalid("config", "The settings must be an object of name/value pairs.")
    raw = dict(raw or {})
    unknown = sorted(set(raw) - set(DEFAULT_CONFIG))
    if unknown:
        raise config_invalid(unknown[0], "This setting does not exist.")
    return raw


def parse_config(raw: Optional[Mapping[str, Any]], columns: Mapping[str, str], *, extended: bool = False, require_target: bool = True) -> Dict[str, Any]:
    """Validates and normalises a configuration against the dataset's columns. Returns a fully populated config (every default made explicit)."""
    raw = merge_defaults(raw)
    i = 1 if extended else 0
    cfg = copy.deepcopy(DEFAULT_CONFIG)

    target = raw.get("target")
    if target is None and require_target:
        raise config_invalid("target", "Choose the column you want to predict.")
    if target is not None:
        if not isinstance(target, str) or target not in columns:
            raise config_invalid("target", "Choose a column that exists in this dataset.")
        cfg["target"] = target
    cfg["problem_type"] = _enum(raw.get("problem_type", "auto"), "problem_type", PROBLEM_CHOICES)
    pl = raw.get("positive_label")
    if pl is not None:
        if not isinstance(pl, (str, int, float)) or isinstance(pl, bool) or len(str(pl)) > 200:
            raise config_invalid("positive_label", "Enter the value of the class you want treated as 'positive'.")
        cfg["positive_label"] = str(pl)

    feats = _section(raw, "features", cfg["features"])
    for key in ("include", "exclude"):
        v = feats.get(key, cfg["features"][key])
        if v is None:
            cfg["features"][key] = None if key == "include" else []
            continue
        if not isinstance(v, list) or len(v) > 600 or any(not isinstance(x, str) or x not in columns for x in v):
            raise config_invalid(f"features.{key}", "Choose columns that exist in this dataset.")
        cfg["features"][key] = list(dict.fromkeys(v))
    if target is not None and (target in (cfg["features"]["include"] or [])):
        raise config_invalid("features.include", "The column you want to predict cannot also be used as an input.")
    overrides = feats.get("overrides", {}) or {}
    if not isinstance(overrides, dict) or len(overrides) > 600:
        raise config_invalid("features.overrides", "Overrides must be an object keyed by column name.")
    clean: Dict[str, Any] = {}
    for col, ov in overrides.items():
        if col not in columns:
            raise config_invalid(f"features.overrides.{col}", "This column does not exist in the dataset.")
        if not isinstance(ov, dict):
            raise config_invalid(f"features.overrides.{col}", "Settings for a column must be an object.")
        allowed = {"role", "impute", "encoding", "transform", "scale"}
        bad = sorted(set(ov) - allowed)
        if bad:
            raise config_invalid(f"features.overrides.{col}.{bad[0]}", "This setting does not exist.")
        o: Dict[str, Any] = {}
        if "role" in ov:
            o["role"] = _enum(ov["role"], f"features.overrides.{col}.role", COLUMN_ROLES)
        if "impute" in ov:
            o["impute"] = _enum(ov["impute"], f"features.overrides.{col}.impute", NUM_IMPUTE + ("constant",))
        if "encoding" in ov:
            o["encoding"] = _enum(ov["encoding"], f"features.overrides.{col}.encoding", ENCODING)
        if "transform" in ov:
            o["transform"] = _enum(ov["transform"], f"features.overrides.{col}.transform", ("none", "log1p"))
        if "scale" in ov:
            o["scale"] = _enum(ov["scale"], f"features.overrides.{col}.scale", SCALING)
        clean[col] = o
    cfg["features"]["overrides"] = clean

    pp = _section(raw, "preprocessing", cfg["preprocessing"])
    out = cfg["preprocessing"]
    out["numeric_impute"] = _enum(pp.get("numeric_impute", out["numeric_impute"]), "preprocessing.numeric_impute", NUM_IMPUTE)
    out["categorical_impute"] = _enum(pp.get("categorical_impute", out["categorical_impute"]), "preprocessing.categorical_impute", CAT_IMPUTE)
    out["missing_indicator"] = _bool(pp.get("missing_indicator", out["missing_indicator"]), "preprocessing.missing_indicator")
    out["scaling"] = _enum(pp.get("scaling", out["scaling"]), "preprocessing.scaling", SCALING)
    out["encoding"] = _enum(pp.get("encoding", out["encoding"]), "preprocessing.encoding", ENCODING)
    out["max_onehot_levels"] = _int(pp.get("max_onehot_levels", out["max_onehot_levels"]), "preprocessing.max_onehot_levels", 2, 60)
    out["high_cardinality"] = _enum(pp.get("high_cardinality", out["high_cardinality"]), "preprocessing.high_cardinality", HIGH_CARD)
    out["rare_category_min_frequency"] = _float(pp.get("rare_category_min_frequency", out["rare_category_min_frequency"]), "preprocessing.rare_category_min_frequency", 0.0, 0.2)
    out["outliers"] = _enum(pp.get("outliers", out["outliers"]), "preprocessing.outliers", OUTLIERS)
    out["log_transform"] = _enum(pp.get("log_transform", out["log_transform"]), "preprocessing.log_transform", LOG)
    out["datetime_features"] = _bool(pp.get("datetime_features", out["datetime_features"]), "preprocessing.datetime_features")
    out["drop_duplicates"] = _bool(pp.get("drop_duplicates", out["drop_duplicates"]), "preprocessing.drop_duplicates")
    out["max_missing_pct"] = _float(pp.get("max_missing_pct", out["max_missing_pct"]), "preprocessing.max_missing_pct", 5.0, 100.0)

    algos = raw.get("algorithms", "auto")
    if algos != "auto":
        from .catalog import CATALOG
        if not isinstance(algos, list) or not algos or len(algos) > 20 or any(not isinstance(a, str) or a not in CATALOG for a in algos):
            raise config_invalid("algorithms", "Use 'auto' or a list of algorithm names from the catalogue.")
        algos = list(dict.fromkeys(algos))
    cfg["algorithms"] = algos

    val = _section(raw, "validation", cfg["validation"])
    v = cfg["validation"]
    ts = val.get("test_size", v["test_size"])
    v["test_size"] = 0.0 if ts == 0 else _float(ts, "validation.test_size", 0.05, 0.5)
    v["cv_strategy"] = _enum(val.get("cv_strategy", v["cv_strategy"]), "validation.cv_strategy", CV_STRATEGIES)
    v["cv_folds"] = _int(val.get("cv_folds", v["cv_folds"]), "validation.cv_folds", 2, LIMITS["max_cv_folds"][i])
    v["cv_repeats"] = _int(val.get("cv_repeats", v["cv_repeats"]), "validation.cv_repeats", 1, LIMITS["max_cv_repeats"][i])
    v["shuffle"] = _bool(val.get("shuffle", v["shuffle"]), "validation.shuffle")
    tc = val.get("time_column")
    if tc is not None:
        if not isinstance(tc, str) or tc not in columns or role_of(columns[tc]) not in ("datetime", "numeric"):
            raise config_invalid("validation.time_column", "Choose a date or numeric column to order the rows by.")
        v["time_column"] = tc
    if v["cv_strategy"] == "time_series" and v["shuffle"] and "shuffle" in val:
        raise config_invalid("validation.shuffle", "Shuffling would put the future in the training data; turn it off for time-ordered validation.")

    hp = _section(raw, "hpo", cfg["hpo"])
    h = cfg["hpo"]
    h["method"] = _enum(hp.get("method", h["method"]), "hpo.method", HPO_METHODS)
    h["n_iter"] = _int(hp.get("n_iter", h["n_iter"]), "hpo.n_iter", 1, LIMITS["max_hpo_trials"][i])
    h["top_k"] = _int(hp.get("top_k", h["top_k"]), "hpo.top_k", 1, LIMITS["max_hpo_algorithms"][i])
    h["cv_folds"] = _int(hp.get("cv_folds", h["cv_folds"]), "hpo.cv_folds", 2, LIMITS["max_cv_folds"][i])
    grids = hp.get("param_grids", {}) or {}
    if not isinstance(grids, dict) or len(grids) > 20:
        raise config_invalid("hpo.param_grids", "Parameter grids must be an object keyed by algorithm name.")
    from .catalog import CATALOG, validate_grid
    h["param_grids"] = {}
    for algo, grid in grids.items():
        if algo not in CATALOG:
            raise config_invalid(f"hpo.param_grids.{algo}", "This algorithm is not in the catalogue.")
        h["param_grids"][algo] = validate_grid(algo, grid, f"hpo.param_grids.{algo}", max_trials=LIMITS["max_hpo_trials"][i])

    cfg["primary_metric"] = _enum(raw.get("primary_metric", "auto"), "primary_metric", METRICS)
    cfg["class_weight"] = _enum(raw.get("class_weight", "auto"), "class_weight", CLASS_WEIGHT)
    cfg["rare_classes"] = _enum(raw.get("rare_classes", "error"), "rare_classes", RARE_CLASSES)
    cfg["seed"] = _int(raw.get("seed", SEED_DEFAULT), "seed", 0, 2 ** 31 - 1)
    tb = raw.get("time_budget_s")
    cfg["time_budget_s"] = None if tb is None else _int(tb, "time_budget_s", 10, LIMITS["time_budget_s"][i])
    mt = raw.get("max_train_rows")
    cfg["max_train_rows"] = None if mt is None else _int(mt, "max_train_rows", 100, LIMITS["max_train_rows"][i])
    return cfg
