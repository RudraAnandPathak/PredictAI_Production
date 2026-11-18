"""Builds the scikit-learn pipeline for one algorithm from the resolved column treatments (`prep.resolve_features`).

    Pipeline([ SchemaCoercer -> ColumnTransformer(one block per kind of treatment) -> estimator ])

The whole pipeline is fitted on the training rows only: every median, scaling factor, clipping fence, category list and frequency table is
learned inside `fit`, so during cross-validation each fold learns them from its own training part and nothing from validation or test rows
reaches a model. The fitted pipeline is exactly what is saved, so a prediction goes through the same steps as training.
There is no target-aware feature selection anywhere in this file; the decisions that shape the blocks were made from the input columns alone."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OneHotEncoder, OrdinalEncoder, RobustScaler, StandardScaler

from .catalog import CATALOG, estimator_params, kind_of
from .transformers import CategoryImputer, DateFeatures, FrequencyEncoder, IQRClipper, Log1pShift, NullFlags, SchemaCoercer
from .constants import Problem

SCALERS = {"standard": StandardScaler, "robust": RobustScaler, "minmax": MinMaxScaler}


def resolve_scale(spec: Mapping[str, Any], needs_scaling: bool) -> Optional[str]:
    """'auto' scales only for algorithms that depend on feature size (distance and margin based ones, linear models)."""
    choice = spec.get("scale", "auto")
    if choice == "auto":
        return "standard" if needs_scaling else None
    return None if choice == "none" else choice


def _scaler(name: Optional[str]) -> List[Tuple[str, Any]]:
    return [("scale", SCALERS[name]())] if name else []


def _num_impute(strategy: str) -> SimpleImputer:
    if strategy == "constant":
        return SimpleImputer(strategy="constant", fill_value=0.0, keep_empty_features=True)
    return SimpleImputer(strategy=strategy, keep_empty_features=True)


def schema_of(specs: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The input contract of a model: which columns it needs and how each is read. Saved with the model and used to validate predictions."""
    keep = ("name", "role", "source_type", "numeric_labels")
    return [{k: s[k] for k in keep if k in s} for s in specs]


def build_preprocessor(specs: Sequence[Mapping[str, Any]], needs_scaling: bool) -> Tuple[ColumnTransformer, List[Dict[str, Any]]]:
    """Returns (unfitted column transformer, block descriptions). Blocks that map input columns one-to-one are grouped; one-hot gets one block per
    column so every encoded column can be traced back to the column it came from."""
    blocks: List[Tuple[str, Any, List[str]]] = []
    meta: List[Dict[str, Any]] = []

    def add(name: str, transformer: Any, columns: List[str], kind: str) -> None:
        blocks.append((name, transformer, columns))
        meta.append({"block": name, "kind": kind, "columns": columns})

    numeric: Dict[Tuple, List[str]] = {}
    dates: Dict[Tuple, List[str]] = {}
    ordinal: List[str] = []
    frequency: List[str] = []
    flags: List[str] = []
    cat_fill: Dict[str, List[str]] = {}
    onehot: List[Mapping[str, Any]] = []
    for s in specs:
        scale = resolve_scale(s, needs_scaling)
        if s["role"] == "numeric":
            numeric.setdefault((s.get("impute", "median"), s.get("transform", "none"), bool(s.get("clip")), scale), []).append(s["name"])
        elif s["role"] == "datetime":
            dates.setdefault((tuple(s["parts"]), scale), []).append(s["name"])
        else:
            enc = s["encoding"]
            if enc == "onehot":
                onehot.append(s)
            elif enc == "ordinal":
                ordinal.append(s["name"])
            else:
                frequency.append(s["name"])
        if s.get("missing_indicator"):
            flags.append(s["name"])

    for j, ((impute, transform, clip, scale), cols) in enumerate(sorted(numeric.items(), key=lambda kv: str(kv[0]))):
        steps: List[Tuple[str, Any]] = [("impute", _num_impute(impute))]
        if clip:
            steps.append(("clip", IQRClipper()))
        if transform == "log1p":
            steps.append(("log", Log1pShift()))
        steps += _scaler(scale)
        add(f"num{j}", Pipeline(steps), cols, "one_to_one")
    for j, ((parts, scale), cols) in enumerate(sorted(dates.items(), key=lambda kv: str(kv[0]))):
        add(f"dt{j}", Pipeline([("dates", DateFeatures(parts)), ("impute", SimpleImputer(strategy="median", keep_empty_features=True))] + _scaler(scale)), cols, "dates")
    for j, s in enumerate(onehot):
        strategy = s.get("impute", "most_frequent")
        mf = float(s.get("min_frequency", 0.0))
        encoder = OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=mf if mf > 0 else None, max_categories=int(s.get("max_levels", 20)), sparse_output=False)
        add(f"oh{j}", Pipeline([("impute", CategoryImputer(strategy)), ("encode", encoder)]), [s["name"]], "onehot")
    if ordinal:
        add("ord", Pipeline([("impute", CategoryImputer("most_frequent")), ("encode", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]), ordinal, "one_to_one")
    if frequency:
        add("freq", Pipeline([("impute", CategoryImputer("most_frequent")), ("encode", FrequencyEncoder())]), frequency, "one_to_one")
    if flags:
        add("flag", NullFlags(), flags, "one_to_one")
    ct = ColumnTransformer(blocks, remainder="drop", sparse_threshold=0.0, verbose_feature_names_out=True)
    return ct, meta


def class_weight_for(cfg_value: str, algo_key: str, imbalanced: bool) -> Optional[str]:
    a = CATALOG[algo_key]
    if cfg_value == "none" or not a.class_weight:
        return None
    if cfg_value == "balanced" or (cfg_value == "auto" and imbalanced):
        return "balanced"
    return None


def build_pipeline(specs: Sequence[Mapping[str, Any]], algo_key: str, problem: str, seed: int, *, class_weight: Optional[str] = None, params: Optional[Mapping[str, Any]] = None) -> Tuple[Pipeline, List[Dict[str, Any]]]:
    algo = CATALOG[algo_key]
    kind = kind_of(problem)
    estimator = algo.build(kind, seed, class_weight)
    if params:
        estimator.set_params(**estimator_params(algo_key, kind, params))
    ct, meta = build_preprocessor(specs, algo.scaling)
    pipe = Pipeline([("coerce", SchemaCoercer(schema_of(specs))), ("prep", ct), ("model", estimator)])
    return pipe, meta


def feature_map(pipe: Pipeline, meta: Sequence[Mapping[str, Any]]) -> Dict[str, List[Any]]:
    """After fitting: the name of every encoded model column and the original input column it came from."""
    ct: ColumnTransformer = pipe.named_steps["prep"]
    names = [str(n) for n in ct.get_feature_names_out()]
    origins: List[Optional[str]] = [None] * len(names)
    for m in meta:
        sl = ct.output_indices_[m["block"]]
        idx = list(range(sl.start, sl.stop))
        if m["kind"] == "dates":
            fitted = ct.named_transformers_[m["block"]].named_steps["dates"]
            og = list(fitted.origins_)
        elif m["kind"] == "onehot":
            og = [m["columns"][0]] * len(idx)
        else:
            og = list(m["columns"])
        for i, o in zip(idx, og):
            origins[i] = o
    display = [n.split("__", 1)[1] if "__" in n else n for n in names]
    return {"names": display, "origins": origins}


def transformed_matrix(pipe: Pipeline, X: Any) -> np.ndarray:
    """Input rows after every preprocessing step (what the estimator actually sees)."""
    return pipe[:-1].transform(X)
