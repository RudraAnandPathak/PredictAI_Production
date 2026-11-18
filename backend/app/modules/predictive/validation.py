"""How rows are divided between learning, tuning and the final check, so that no score is computed on rows a model learned from.

    all labelled rows ──► train split (models learn here; cross-validation happens INSIDE it)
                     └──► test split  (touched once per model, for the final report; never used to choose a model)

Everything is seeded, so the same data, settings and seed give the same split."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, RepeatedKFold, RepeatedStratifiedKFold, StratifiedKFold, TimeSeriesSplit, train_test_split

from .constants import Problem
from .errors import MlError

MIN_TRAIN_PER_FOLD = 3


def resolve_strategy(strategy: str, problem: str, time_column: Optional[str]) -> str:
    if strategy == "stratified_kfold" and problem == Problem.REGRESSION:
        raise MlError("CV_INVALID", "Stratified folds keep the class mix in every fold, so they only apply to classification. Choose k-fold for regression.")
    if strategy == "auto":
        if time_column:
            return "time_series"
        return "stratified_kfold" if problem in Problem.CLASSIFICATION else "kfold"
    if strategy in ("kfold", "repeated_kfold") and time_column:
        raise MlError("CV_INVALID", "A time column is set, so folds must respect time order. Choose time-ordered validation, or remove the time column.")
    return strategy


def holdout(y: np.ndarray, problem: str, test_size: float, seed: int, *, time_order: Optional[np.ndarray] = None, shuffle: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """Row positions (0..n-1) of the train and test splits. With a time order the most recent rows become the test set."""
    n = len(y)
    idx = np.arange(n)
    if test_size <= 0:
        return idx, np.array([], dtype=int)
    n_test = max(1, int(round(n * test_size)))
    if time_order is not None:
        order = np.argsort(time_order, kind="stable")
        return np.sort(order[: n - n_test]), np.sort(order[n - n_test:])
    k = len(np.unique(y)) if problem in Problem.CLASSIFICATION else 0
    if problem in Problem.CLASSIFICATION:
        if n_test < k or n - n_test < k:
            raise MlError("TOO_FEW_ROWS", f"With {n} rows and {k} classes the held-out part cannot hold every class. Use more rows, fewer classes, or a smaller test size.")
        tr, te = train_test_split(idx, test_size=n_test, stratify=y, random_state=seed, shuffle=True)
    else:
        tr, te = train_test_split(idx, test_size=n_test, random_state=seed, shuffle=shuffle)
    return np.sort(tr), np.sort(te)


def make_cv(strategy: str, problem: str, y_train: np.ndarray, *, folds: int, repeats: int, seed: int, shuffle: bool) -> Tuple[Any, Dict[str, Any]]:
    """Returns (scikit-learn splitter, description). The folds are reduced when the smallest class is too small for them, and that is reported."""
    n = len(y_train)
    notes: List[str] = []
    requested = folds
    if strategy in ("stratified_kfold", "repeated_kfold") and problem in Problem.CLASSIFICATION or strategy == "stratified_kfold":
        smallest = int(np.bincount(y_train).min()) if problem in Problem.CLASSIFICATION else n
        if smallest < folds:
            folds = smallest
            notes.append(f"Folds reduced from {requested} to {folds} because the smallest class has only {smallest} training rows.")
    if n // max(folds, 1) < MIN_TRAIN_PER_FOLD:
        folds = max(2, n // MIN_TRAIN_PER_FOLD)
        notes.append(f"Folds reduced to {folds} because there are few training rows.")
    if folds < 2:
        raise MlError("TOO_FEW_ROWS", "There are too few rows in the smallest class to cross-validate. Remove or merge that class, or collect more data.")
    if strategy == "time_series":
        if n <= folds + 1:
            raise MlError("TOO_FEW_ROWS", "There are too few rows for time-ordered validation with this many folds.")
        return TimeSeriesSplit(n_splits=folds), {"strategy": "time_series", "folds": folds, "repeats": 1, "notes": notes + ["Each fold validates on rows that come after the rows it learned from."]}
    if strategy == "repeated_kfold":
        cls = RepeatedStratifiedKFold if problem in Problem.CLASSIFICATION else RepeatedKFold
        return cls(n_splits=folds, n_repeats=repeats, random_state=seed), {"strategy": "repeated_stratified_kfold" if problem in Problem.CLASSIFICATION else "repeated_kfold", "folds": folds, "repeats": repeats, "notes": notes}
    if strategy == "stratified_kfold":
        return StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed), {"strategy": "stratified_kfold", "folds": folds, "repeats": 1, "notes": notes}
    return KFold(n_splits=folds, shuffle=shuffle, random_state=seed if shuffle else None), {"strategy": "kfold", "folds": folds, "repeats": 1, "notes": notes + ([] if shuffle else ["Rows are not shuffled: folds follow the stored row order."])}


def fold_indices(cv: Any, X: Any, y: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
    return [(tr, va) for tr, va in cv.split(np.zeros(len(y)), y)]


def sample_rows(y: np.ndarray, problem: str, n_max: int, seed: int) -> Tuple[np.ndarray, Optional[Dict[str, Any]]]:
    """If there are more labelled rows than the training limit, a seeded sample (stratified for classification) is used; the fact is recorded."""
    n = len(y)
    if n <= n_max:
        return np.arange(n), None
    if problem in Problem.CLASSIFICATION:
        keep, _ = train_test_split(np.arange(n), train_size=n_max, stratify=y, random_state=seed)
    else:
        keep = np.random.default_rng(seed).choice(n, n_max, replace=False)
    return np.sort(keep), {"method": "seeded random sample" + (" (class mix preserved)" if problem in Problem.CLASSIFICATION else ""), "rows_available": int(n), "rows_used": int(n_max), "seed": seed}
