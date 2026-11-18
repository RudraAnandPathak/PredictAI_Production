"""Small scikit-learn transformers used by the preprocessing pipeline. They are part of the saved model, so a prediction goes through
exactly the same steps as training. All learn from the training data only (inside each cross-validation fold) and implement
`get_feature_names_out`, which is how importance is traced back to the original columns."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

DATE_PARTS = ("year", "month", "day", "dayofweek", "quarter", "is_weekend", "dayofyear", "ordinal")
TIME_PARTS = ("hour",)


def _frame(X: Any, names: Optional[Sequence[str]] = None) -> pd.DataFrame:
    if isinstance(X, pd.DataFrame):
        return X
    return pd.DataFrame(np.asarray(X, dtype=object) if np.asarray(X).dtype == object else np.asarray(X), columns=list(names) if names is not None else None)


class SchemaCoercer(BaseEstimator, TransformerMixin):
    """First step of every pipeline. Turns raw input (strings from a form or a CSV, or the typed training table) into the typed columns the
    model was trained on: numbers, trimmed text categories, dates. Anything that does not fit its type becomes missing (and is then imputed
    like any missing value); the prediction service reports how many values that affected."""

    def __init__(self, schema: Sequence[Dict[str, Any]]) -> None:
        self.schema = schema

    def fit(self, X: Any, y: Any = None) -> "SchemaCoercer":
        self.feature_names_in_ = np.array([s["name"] for s in self.schema], dtype=object)
        return self

    def transform(self, X: Any) -> pd.DataFrame:
        df = _frame(X)
        out = pd.DataFrame(index=df.index)
        for spec in self.schema:
            name, role = spec["name"], spec["role"]
            col = df[name] if name in df.columns else pd.Series(np.nan, index=df.index, dtype=object)
            if role == "numeric":
                out[name] = pd.to_numeric(col, errors="coerce").astype(float)
            elif role == "datetime":
                out[name] = pd.to_datetime(col, errors="coerce")
            else:
                if spec.get("numeric_labels"):          # a numeric column used as categories: 3, 3.0 and "3" are the same label
                    num = pd.to_numeric(col, errors="coerce")
                    labels = num.map(lambda v: np.nan if pd.isna(v) else (str(int(v)) if float(v).is_integer() else repr(float(v))))
                else:
                    labels = col.map(lambda v: np.nan if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "" else str(v).strip())
                out[name] = labels.astype(object).where(labels.notna(), np.nan)
        return out

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.array([s["name"] for s in self.schema], dtype=object)


class DateFeatures(BaseEstimator, TransformerMixin):
    """Calendar features from date columns: year, month, day, weekday, quarter, weekend flag, day of year, and a running day count (so a trend can be learned)."""

    def __init__(self, parts: Sequence[str] = DATE_PARTS) -> None:
        self.parts = parts

    def fit(self, X: Any, y: Any = None) -> "DateFeatures":
        df = _frame(X)
        self.columns_ = [str(c) for c in df.columns]
        self.names_ = [f"{c}__{p}" for c in self.columns_ for p in self.parts]
        self.origins_ = [c for c in self.columns_ for _ in self.parts]
        return self

    def transform(self, X: Any) -> np.ndarray:
        df = _frame(X)
        blocks = []
        for c in self.columns_:
            d = pd.to_datetime(df[c], errors="coerce")
            for p in self.parts:
                if p == "year":
                    v = d.dt.year
                elif p == "month":
                    v = d.dt.month
                elif p == "day":
                    v = d.dt.day
                elif p == "dayofweek":
                    v = d.dt.dayofweek
                elif p == "quarter":
                    v = d.dt.quarter
                elif p == "is_weekend":
                    v = (d.dt.dayofweek >= 5).astype(float).where(d.notna())
                elif p == "dayofyear":
                    v = d.dt.dayofyear
                elif p == "hour":
                    v = d.dt.hour
                else:                                    # ordinal: whole days since 1970-01-01
                    v = (d - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86400.0
                blocks.append(v.astype(float).to_numpy())
        return np.column_stack(blocks) if blocks else np.empty((len(df), 0))

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.array(self.names_, dtype=object)


class FrequencyEncoder(BaseEstimator, TransformerMixin):
    """Replaces each category by how common it was in the training data (0 for a category never seen). Useful for categories with many levels."""

    def fit(self, X: Any, y: Any = None) -> "FrequencyEncoder":
        df = _frame(X)
        self.columns_ = [str(c) for c in df.columns]
        self.maps_ = [df.iloc[:, j].astype(object).value_counts(normalize=True).to_dict() for j in range(df.shape[1])]
        return self

    def transform(self, X: Any) -> np.ndarray:
        df = _frame(X)
        cols = [df.iloc[:, j].astype(object).map(self.maps_[j]).fillna(0.0).to_numpy(float) for j in range(df.shape[1])]
        return np.column_stack(cols) if cols else np.empty((len(df), 0))

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.array([f"{c}__frequency" for c in self.columns_], dtype=object)


class IQRClipper(BaseEstimator, TransformerMixin):
    """Limits extreme values to the Tukey fences (Q1 - k*IQR, Q3 + k*IQR) learned from the training data. Missing values pass through."""

    def __init__(self, k: float = 1.5) -> None:
        self.k = k

    def fit(self, X: Any, y: Any = None) -> "IQRClipper":
        a = np.asarray(X, dtype=float)
        q1, q3 = np.nanpercentile(a, 25, axis=0), np.nanpercentile(a, 75, axis=0)
        iqr = q3 - q1
        self.lower_, self.upper_ = q1 - self.k * iqr, q3 + self.k * iqr
        self.n_features_in_ = a.shape[1]
        return self

    def transform(self, X: Any) -> np.ndarray:
        return np.clip(np.asarray(X, dtype=float), self.lower_, self.upper_)

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.asarray(input_features, dtype=object)


class Log1pShift(BaseEstimator, TransformerMixin):
    """log(1 + x - minimum seen in training): compresses long right tails and is defined even if new data contains smaller values."""

    def fit(self, X: Any, y: Any = None) -> "Log1pShift":
        a = np.asarray(X, dtype=float)
        self.minimum_ = np.nanmin(a, axis=0)
        self.n_features_in_ = a.shape[1]
        return self

    def transform(self, X: Any) -> np.ndarray:
        return np.log1p(np.maximum(np.asarray(X, dtype=float) - self.minimum_, 0.0))

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.asarray(input_features, dtype=object)


class NullFlags(BaseEstimator, TransformerMixin):
    """1 where the original value was missing, else 0: lets a model learn from the fact that a value was missing."""

    def fit(self, X: Any, y: Any = None) -> "NullFlags":
        self.columns_ = [str(c) for c in _frame(X).columns]
        return self

    def transform(self, X: Any) -> np.ndarray:
        return _frame(X).isna().to_numpy(float)

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.array([f"{c}__was_missing" for c in self.columns_], dtype=object)


class CategoryImputer(BaseEstimator, TransformerMixin):
    """Fills missing categories with the most common training category (ties broken alphabetically, so the result never depends on row order)
    or with the fixed label 'missing'. A column that is entirely missing in the training data gets 'missing'. Always returns text columns."""

    def __init__(self, strategy: str = "most_frequent", fill_value: str = "missing") -> None:
        self.strategy = strategy
        self.fill_value = fill_value

    def fit(self, X: Any, y: Any = None) -> "CategoryImputer":
        df = _frame(X)
        self.columns_ = [str(c) for c in df.columns]
        fills: List[str] = []
        for j in range(df.shape[1]):
            seen = df.iloc[:, j].dropna().astype(str)
            if self.strategy == "most_frequent" and len(seen):
                counts = seen.value_counts()
                fills.append(sorted(counts[counts == counts.max()].index)[0])
            else:
                fills.append(self.fill_value)
        self.fill_ = fills
        return self

    def transform(self, X: Any) -> pd.DataFrame:
        df = _frame(X)
        cols = {}
        for j, c in enumerate(self.columns_):
            s = df.iloc[:, j].astype(object)
            cols[c] = s.where(s.notna(), self.fill_[j]).astype(str)
        return pd.DataFrame(cols, index=df.index)

    def get_feature_names_out(self, input_features: Any = None) -> np.ndarray:
        return np.array(self.columns_, dtype=object)
