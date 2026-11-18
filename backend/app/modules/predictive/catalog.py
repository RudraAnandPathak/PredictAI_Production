"""The algorithm catalogue: scikit-learn estimators only, chosen from a closed list. Nothing here is user-supplied code.
Each entry says which problems it fits, whether it needs scaled inputs, how its importance can be read, how expensive it is, and which
hyper-parameters may be tuned (with bounds, so a tuning request cannot ask for unbounded work)."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .constants import GB_MAX_CELLS, KNN_MAX_ROWS, Problem, SVM_MAX_ROWS
from .errors import config_invalid

CLS, REG = "classification", "regression"


@dataclass(frozen=True)
class Algo:
    key: str
    label: str
    kinds: Tuple[str, ...]
    build: Callable[[str, int, Optional[str]], Any]            # (kind, seed, class_weight) -> unfitted estimator
    scaling: bool                                              # benefits from standardised inputs
    importance: Optional[str]                                  # "tree" (feature_importances_), "coef" (coef_) or None
    cost: int                                                  # 1 (very cheap) .. 5 (expensive)
    description: str
    params: Mapping[str, Tuple[str, Any, Any]] = field(default_factory=dict)       # name -> (kind, lo/choices, hi)
    grid: Mapping[str, Sequence[Any]] = field(default_factory=dict)                # default grid
    class_weight: bool = False                                 # supports class_weight directly
    max_rows: Optional[int] = None
    auto: bool = True                                          # False: only trained when the person names it (never part of "auto")


def _lr(kind, seed, cw):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=2000, class_weight=cw, random_state=seed)


def _linreg(kind, seed, cw):
    from sklearn.linear_model import LinearRegression
    return LinearRegression()


def _ridge(kind, seed, cw):
    from sklearn.linear_model import Ridge
    return Ridge(alpha=1.0, random_state=seed)


def _lasso(kind, seed, cw):
    from sklearn.linear_model import Lasso
    return Lasso(alpha=0.01, max_iter=5000, random_state=seed)


def _tree(kind, seed, cw):
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    return DecisionTreeClassifier(random_state=seed, class_weight=cw, min_samples_leaf=3) if kind == CLS else DecisionTreeRegressor(random_state=seed, min_samples_leaf=3)


def _rf(kind, seed, cw):
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    return RandomForestClassifier(n_estimators=150, random_state=seed, class_weight=cw, n_jobs=1) if kind == CLS else RandomForestRegressor(n_estimators=150, random_state=seed, n_jobs=1)


def _et(kind, seed, cw):
    from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
    return ExtraTreesClassifier(n_estimators=150, random_state=seed, class_weight=cw, n_jobs=1) if kind == CLS else ExtraTreesRegressor(n_estimators=150, random_state=seed, n_jobs=1)


def _gb(kind, seed, cw):
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    return GradientBoostingClassifier(random_state=seed) if kind == CLS else GradientBoostingRegressor(random_state=seed)


def _hgb(kind, seed, cw):
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    return HistGradientBoostingClassifier(random_state=seed, class_weight=cw) if kind == CLS else HistGradientBoostingRegressor(random_state=seed)


def _svm(kind, seed, cw):
    from sklearn.svm import SVC, SVR
    if kind != CLS:
        return SVR()
    # SVC(probability=True) is deprecated in scikit-learn 1.9; calibrating with a sigmoid is the documented replacement and works in every supported version
    from sklearn.calibration import CalibratedClassifierCV
    return CalibratedClassifierCV(SVC(class_weight=cw, random_state=seed), method="sigmoid", cv=3, ensemble=False)


def _knn(kind, seed, cw):
    from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
    return KNeighborsClassifier(n_neighbors=7) if kind == CLS else KNeighborsRegressor(n_neighbors=7)


def _nb(kind, seed, cw):
    from sklearn.naive_bayes import GaussianNB
    return GaussianNB()


def _voting(kind, seed, cw):
    from sklearn.ensemble import VotingClassifier, VotingRegressor
    if kind == CLS:
        return VotingClassifier([("lr", _lr(kind, seed, cw)), ("rf", _rf(kind, seed, cw)), ("hgb", _hgb(kind, seed, cw))], voting="soft")
    return VotingRegressor([("ridge", _ridge(kind, seed, cw)), ("rf", _rf(kind, seed, cw)), ("hgb", _hgb(kind, seed, cw))])


def _stacking(kind, seed, cw):
    from sklearn.ensemble import StackingClassifier, StackingRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    if kind == CLS:
        return StackingClassifier([("rf", _rf(kind, seed, cw)), ("hgb", _hgb(kind, seed, cw))], final_estimator=LogisticRegression(max_iter=2000, random_state=seed), cv=3)
    return StackingRegressor([("rf", _rf(kind, seed, cw)), ("hgb", _hgb(kind, seed, cw))], final_estimator=Ridge(alpha=1.0, random_state=seed), cv=3)


_TREE_PARAMS = {"max_depth": ("int_or_none", 1, 40), "min_samples_leaf": ("int", 1, 100)}
_FOREST_PARAMS = {"n_estimators": ("int", 20, 500), "max_depth": ("int_or_none", 1, 40), "min_samples_leaf": ("int", 1, 100), "max_features": ("choice", ["sqrt", "log2", 0.3, 0.5, 0.8, 1.0], None)}

CATALOG: Dict[str, Algo] = {a.key: a for a in [
    Algo("logistic_regression", "Logistic regression", (CLS,), _lr, True, "coef", 1, "A simple, fast and easy-to-read baseline that draws a straight boundary between classes.",
         {"C": ("float", 1e-4, 1e4)}, {"C": [0.01, 0.1, 1.0, 10.0, 100.0]}, class_weight=True),
    Algo("linear_regression", "Linear regression", (REG,), _linreg, True, "coef", 1, "The simplest baseline: a straight-line (linear) relationship between the inputs and the target.", {}, {}),
    Algo("ridge", "Ridge regression", (REG,), _ridge, True, "coef", 1, "Linear regression that shrinks coefficients, which keeps it stable when inputs are correlated.",
         {"alpha": ("float", 1e-4, 1e4)}, {"alpha": [0.01, 0.1, 1.0, 10.0, 100.0]}),
    Algo("lasso", "Lasso regression", (REG,), _lasso, True, "coef", 1, "Linear regression that can shrink unhelpful coefficients to exactly zero, which performs a kind of automatic input selection.",
         {"alpha": ("float", 1e-5, 1e3)}, {"alpha": [0.0001, 0.001, 0.01, 0.1, 1.0]}),
    Algo("decision_tree", "Decision tree", (CLS, REG), _tree, False, "tree", 1, "A single tree of if/then rules. Easy to follow, but unstable and prone to memorising the training data.",
         _TREE_PARAMS, {"max_depth": [3, 5, 8, 12, None], "min_samples_leaf": [1, 5, 10, 20]}, class_weight=True),
    Algo("random_forest", "Random forest", (CLS, REG), _rf, False, "tree", 3, "Many decision trees voting together. Strong, robust default for tabular data.",
         _FOREST_PARAMS, {"max_depth": [None, 8, 16], "min_samples_leaf": [1, 3, 5], "max_features": ["sqrt", 0.5]}, class_weight=True),
    Algo("extra_trees", "Extra trees", (CLS, REG), _et, False, "tree", 3, "Like a random forest with extra randomness in how trees split, which is often faster and smoother.",
         _FOREST_PARAMS, {"max_depth": [None, 8, 16], "min_samples_leaf": [1, 3, 5], "max_features": ["sqrt", 0.5]}, class_weight=True),
    Algo("gradient_boosting", "Gradient boosting", (CLS, REG), _gb, False, "tree", 4, "Trees built one after another, each correcting the previous ones. Accurate, but sequential and slower on large tables.",
         {"n_estimators": ("int", 20, 400), "learning_rate": ("float", 0.005, 1.0), "max_depth": ("int", 1, 8), "subsample": ("float", 0.3, 1.0)},
         {"n_estimators": [100, 200], "learning_rate": [0.05, 0.1, 0.2], "max_depth": [2, 3, 4]}),
    Algo("hist_gradient_boosting", "Histogram gradient boosting", (CLS, REG), _hgb, False, None, 2, "A fast, modern boosting method that bins inputs; usually very strong on medium and large tables.",
         {"learning_rate": ("float", 0.005, 1.0), "max_leaf_nodes": ("int", 4, 128), "l2_regularization": ("float", 0.0, 100.0), "max_iter": ("int", 20, 500), "min_samples_leaf": ("int", 5, 200)},
         {"learning_rate": [0.03, 0.06, 0.1, 0.2], "max_leaf_nodes": [15, 31, 63], "l2_regularization": [0.0, 0.1, 1.0]}, class_weight=True),
    Algo("svm", "Support vector machine", (CLS, REG), _svm, True, None, 4, "Finds the best margin between classes (or a tube around the trend for regression). Needs scaled inputs and is slow on large tables.",
         {"C": ("float", 1e-3, 1e3), "gamma": ("choice", ["scale", "auto"], None), "kernel": ("choice", ["rbf", "linear"], None)},
         {"C": [0.1, 1.0, 10.0], "gamma": ["scale", "auto"]}, class_weight=True, max_rows=SVM_MAX_ROWS),
    Algo("knn", "k-nearest neighbours", (CLS, REG), _knn, True, None, 2, "Predicts from the most similar training rows. Simple, but sensitive to scaling and slow to predict on large tables.",
         {"n_neighbors": ("int", 1, 100), "weights": ("choice", ["uniform", "distance"], None)}, {"n_neighbors": [3, 5, 9, 15, 25], "weights": ["uniform", "distance"]}, max_rows=KNN_MAX_ROWS),
    Algo("naive_bayes", "Naive Bayes", (CLS,), _nb, False, None, 1, "A very fast probabilistic baseline that treats inputs as independent. Rarely the best, but a useful reference.",
         {"var_smoothing": ("float", 1e-12, 1e-1)}, {"var_smoothing": [1e-9, 1e-8, 1e-7, 1e-6, 1e-5]}),
    Algo("voting_ensemble", "Voting ensemble", (CLS, REG), _voting, True, None, 5, "Averages the predictions of a linear model, a random forest and a histogram boosting model. Often steadier than any one of them; slower to train. Only used when you choose it.",
         {}, {}, class_weight=True, auto=False),
    Algo("stacking_ensemble", "Stacking ensemble", (CLS, REG), _stacking, True, None, 5, "Trains a random forest and a histogram boosting model, then a simple model that learns how to combine them using cross-validated predictions. The slowest option. Only used when you choose it.",
         {}, {}, class_weight=True, auto=False),
]}

# distributions for random search over continuous hyper-parameters (used only when the person did not supply their own grid)
def default_space(algo: str) -> Dict[str, Any]:
    from scipy.stats import loguniform
    space: Dict[str, Any] = dict(CATALOG[algo].grid)
    for name, dist in {"C": loguniform(1e-2, 1e2), "alpha": loguniform(1e-4, 1e2), "var_smoothing": loguniform(1e-10, 1e-4), "learning_rate": loguniform(0.02, 0.3)}.items():
        if name in space:
            space[name] = dist
    return space


def estimator_params(algo: str, kind: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    """Hyper-parameter names as `set_params` wants them (the SVM classifier is wrapped in a probability calibrator)."""
    if algo == "svm" and kind == CLS:
        return {f"estimator__{k}": v for k, v in params.items()}
    return dict(params)


def kind_of(problem: str) -> str:
    return CLS if problem in Problem.CLASSIFICATION else REG


def compatible(key: str, problem: str, n_rows: int, n_features: int) -> Tuple[bool, str]:
    """Whether an algorithm is a sensible candidate for this problem and size; the reason is shown to the user either way."""
    a = CATALOG[key]
    kind = kind_of(problem)
    if kind not in a.kinds:
        return False, f"{a.label} is for {'classification' if kind == REG else 'regression'}, not {'classification' if kind == CLS else 'regression'}."
    if a.max_rows is not None and n_rows > a.max_rows:
        return False, f"Skipped to avoid needless computing: {a.label} becomes very slow above {a.max_rows:,} rows (this table has {n_rows:,})."
    if key == "gradient_boosting" and n_rows * max(1, n_features) > GB_MAX_CELLS:
        return False, "Skipped to avoid needless computing: classic gradient boosting is sequential and slow on a table this large; histogram gradient boosting covers the same idea faster."
    return True, "compatible"


def select(requested: Any, problem: str, n_rows: int, n_features: int) -> Tuple[List[str], List[Dict[str, str]]]:
    """Returns (algorithms to train, algorithms left out with reasons). Cheapest first, so a time limit always keeps the simple baselines."""
    keys = [k for k in CATALOG if CATALOG[k].auto] if requested == "auto" else list(requested)
    chosen, skipped = [], []
    for k in keys:
        ok, why = compatible(k, problem, n_rows, n_features)
        if ok:
            chosen.append(k)
        elif requested != "auto" or kind_of(problem) in CATALOG[k].kinds:      # in "auto" mode an algorithm for the other kind of problem is simply not applicable, not "skipped"
            skipped.append({"algorithm": k, "label": CATALOG[k].label, "reason": why})
    chosen.sort(key=lambda k: (CATALOG[k].cost, k))
    return chosen, skipped


# ------------------------------------------------------------------------------------------------ hyper-parameter grids
def _check_value(kind: str, a: Any, b: Any, value: Any, field_name: str) -> Any:
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not a <= value <= b:
            raise config_invalid(field_name, f"Use whole numbers from {a} to {b}.")
    elif kind == "int_or_none":
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not a <= value <= b):
            raise config_invalid(field_name, f"Use whole numbers from {a} to {b}, or null for no limit.")
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not a <= float(value) <= b:
            raise config_invalid(field_name, f"Use numbers from {a} to {b}.")
        value = float(value)
    elif kind == "choice":
        if value not in a:
            raise config_invalid(field_name, f"Use one of: {', '.join(str(x) for x in a)}.")
    return value


def validate_grid(algo: str, grid: Any, field_name: str, *, max_trials: int) -> Dict[str, List[Any]]:
    """A person-supplied search grid: only listed hyper-parameters, only values inside the bounds, at most 10 values each."""
    a = CATALOG[algo]
    if not isinstance(grid, dict) or not grid:
        raise config_invalid(field_name, "A grid is an object mapping hyper-parameter names to lists of values.")
    if not a.params:
        raise config_invalid(field_name, f"{a.label} has no hyper-parameters to tune.")
    out: Dict[str, List[Any]] = {}
    for name, values in grid.items():
        if name not in a.params:
            raise config_invalid(f"{field_name}.{name}", f"This is not a tunable setting of {a.label}. Choose from: {', '.join(a.params)}.")
        if not isinstance(values, list) or not 1 <= len(values) <= 10:
            raise config_invalid(f"{field_name}.{name}", "Give a list of 1 to 10 values.")
        kind, lo, hi = a.params[name]
        out[name] = [_check_value(kind, lo, hi, v, f"{field_name}.{name}") for v in values]
    size = math.prod(len(v) for v in out.values())
    if size > max(5000, max_trials):
        raise config_invalid(field_name, f"This grid has {size:,} combinations, which is more than the tool will consider.")
    return out


def grid_size(grid: Mapping[str, Sequence[Any]]) -> int:
    return math.prod(len(v) for v in grid.values()) if grid else 0
