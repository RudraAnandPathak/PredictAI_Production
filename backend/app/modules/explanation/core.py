"""The universal Explanation mechanism.

Every analytical result in the application can carry a separate section titled exactly "Explanation": a plain-English reading of the
REAL result, written beside (never instead of) the technical numbers. This module is the one reusable mechanism; each domain
(analytics operations, insights, the assistant, predictive modelling, ...) registers explainers for its own kinds of result.

Guarantees, enforced here rather than promised in prose:
  * An explainer writes through `Say`. Every number it prints must be found in the structured result it was given (to rounding),
    otherwise `Unverifiable` is raised and the explanation becomes "unavailable" instead of showing an unsupported figure.
  * After writing, `finish()` scans the finished text: any numeric token that was not produced through `Say` fails the whole explanation.
  * An explainer can never change the result: it receives a read-only view and returns new text; nothing is written back into `data`.
  * Anything unexpected (no explainer, odd shape, failed check) yields {"status": "unavailable", "reason": ...}. It never raises and never guesses.
No model, network or randomness is involved, so the same result always produces the same Explanation."""
from __future__ import annotations

import bisect
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

SCHEMA = "predictai.explanation/1"
TITLE = "Explanation"
MAX_POINTS = 6
MAX_CAUTIONS = 4
MAX_LEAVES = 60_000

log = logging.getLogger("predictai.explanation")
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")


class Unverifiable(Exception):
    """A number (or claim) the explainer wanted to state is not supported by the result."""


# ------------------------------------------------------------------------------------------------ number lookup
class Leaves:
    """Every numeric value anywhere in a structured result, sorted for tolerant membership tests."""

    def __init__(self, payload: Any) -> None:
        values: List[float] = []
        stack = [payload]
        while stack and len(values) < MAX_LEAVES:
            cur = stack.pop()
            if isinstance(cur, bool) or cur is None:
                continue
            if isinstance(cur, (int, float)):
                if math.isfinite(cur):
                    values.append(float(cur))
            elif isinstance(cur, Mapping):
                stack.extend(cur.values())
            elif isinstance(cur, (list, tuple)):
                stack.extend(cur)
        self.values = sorted(values)
        self.capped = len(values) >= MAX_LEAVES

    def has(self, v: float) -> bool:
        vals = self.values
        i = bisect.bisect_left(vals, v - max(abs(v) * 1e-6, 1e-9))
        while i < len(vals) and vals[i] <= v + max(abs(v) * 1e-6, 1e-9):
            if math.isclose(vals[i], v, rel_tol=1e-6, abs_tol=1e-9):
                return True
            i += 1
        return False


def _group(n: int) -> str:
    return f"{n:,}"


def _trim(text: str) -> str:
    return text.rstrip("0").rstrip(".") if "." in text else text


class Say:
    """Formats numbers for prose and remembers each one, so the finished text can be checked against the result."""

    def __init__(self, payload: Any) -> None:
        self._leaves = Leaves(payload)
        self.tokens: set = set()
        self.literals: set = set()
        self.cited: List[Dict[str, Any]] = []

    # -- numbers that must exist in the result
    def _need(self, v: Any, what: str) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise Unverifiable(f"{what} is not a number in the result")
        if not self._leaves.has(float(v)) and not self._leaves.capped:
            raise Unverifiable(f"{what}={v} is not in the result")
        return float(v)

    def _remember(self, text: str, value: float, what: str) -> str:
        self.tokens.add(text.lstrip("+").replace(",", ""))
        self.cited.append({"label": what, "shown": text})
        return text

    def count(self, v: Any, what: str = "count") -> str:
        f = self._need(v, what)
        return self._remember(_group(int(round(f))), f, what)

    def num(self, v: Any, digits: int = 3, what: str = "value") -> str:
        """General number: thousands separators for large values, `digits` decimals otherwise, trailing zeros trimmed."""
        f = self._need(v, what)
        if f != 0 and abs(f) < 10 ** -digits:
            text = f"{f:.2e}"
        elif abs(f) >= 1000:
            text = _group(int(round(f)))
        else:
            text = _trim(f"{f:.{digits}f}")
        if text in ("-0", "+0"):
            text = "0"
        return self._remember(text, f, what)

    def pct(self, v: Any, digits: int = 1, what: str = "percentage") -> str:
        """`v` is already a percentage (41.33 -> "41.3%")."""
        f = self._need(v, what)
        return self._remember(_trim(f"{f:.{digits}f}"), f, what) + "%"

    def share(self, v: Any, digits: int = 0, what: str = "share") -> str:
        """`v` is a proportion in the result (0.413 -> "41%")."""
        f = self._need(v, what)
        return self._remember(_trim(f"{f * 100:.{digits}f}"), f, what) + "%"

    def p(self, v: Any, what: str = "p-value") -> str:
        f = self._need(v, what)
        if f < 0.001:
            self.tokens.add("0.001")
            return "p < 0.001"
        return "p = " + self._remember(_trim(f"{f:.3f}") if f >= 0.001 else f"{f:.2e}", f, what)

    def derived(self, value: float, *, from_values: Sequence[Any], digits: int = 3, what: str = "derived value") -> str:
        """A number computed from result values (a difference, a ratio). Its inputs must be in the result."""
        for src in from_values:
            self._need(src, what)
        if not math.isfinite(value):
            raise Unverifiable(f"{what} is not finite")
        text = _group(int(round(value))) if abs(value) >= 1000 else _trim(f"{value:.{digits}f}")
        return self._remember(text, value, what)

    def items(self, n: int, what: str = "number of items") -> str:
        """A count of things that is itself derived from the result's structure (how many groups, pairs or issues were listed)."""
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise Unverifiable(f"{what} is not a count")
        text = _group(n)
        return self._remember(text, float(n), what)

    def conf(self, level: Optional[float] = None) -> str:
        """The confidence level of an interval ("95%"). Taken from the result when it states one, otherwise the engine's fixed 0.95."""
        if level is not None:
            return self.share(level, 0, "confidence level")
        self.tokens.add("95")
        self.cited.append({"label": "confidence level (engine default)", "shown": "95", "convention": True})
        return "95%"

    def const(self, v: float, why: str) -> str:
        """A convention stated in the text and not a finding (for example the usual 0.05 threshold). Always described as such."""
        text = _trim(f"{v:g}")
        self.tokens.add(text)
        self.cited.append({"label": why, "shown": text, "convention": True})
        return text

    # -- words and quoted engine text
    def lit(self, text: Any) -> str:
        """Names and labels taken from the data (column names, categories). Digits inside them are not statistical claims."""
        s = str(text)
        if len(s) > 80:
            s = s[:77] + "..."
        if s:
            self.literals.add(s)
        return s

    def quote(self, text: Any, cap: Optional[bool] = None) -> str:
        """A sentence the calculation engine itself wrote into the result: its numbers are the result's own.
        `cap` True/False upper/lower-cases the first letter so the quote fits into a sentence."""
        s = str(text)
        if cap is True and s:
            s = s[0].upper() + s[1:]
        elif cap is False and s and not s[:2].isupper():
            s = s[0].lower() + s[1:]
        for m in _NUMBER.finditer(s):
            self.tokens.add(m.group(0).lstrip("+").replace(",", ""))
        return s


# ------------------------------------------------------------------------------------------------ drafts and finishing
@dataclass
class Draft:
    summary: str
    points: List[str] = field(default_factory=list)
    terms: List[Dict[str, str]] = field(default_factory=list)       # technical terms kept in the result, defined in plain words
    cautions: List[str] = field(default_factory=list)


Explainer = Callable[[Say, Mapping[str, Any]], Optional[Draft]]
REGISTRY: Dict[str, Explainer] = {}


def explainer(*kinds: str) -> Callable[[Explainer], Explainer]:
    def deco(fn: Explainer) -> Explainer:
        for kind in kinds:
            if kind in REGISTRY:
                raise RuntimeError(f"duplicate explainer {kind}")
            REGISTRY[kind] = fn
        return fn
    return deco


def unavailable(kind: str, reason: str) -> Dict[str, Any]:
    return {"schema": SCHEMA, "title": TITLE, "status": "unavailable", "kind": kind, "reason": reason, "summary": None, "points": [], "terms": [], "cautions": [],
            "basis": {"method": "rules", "numbers_cited": 0}}


def _scan(texts: Iterable[str], say: Say) -> Optional[str]:
    """Returns the first numeric token in the finished prose that was not produced through Say (None = every number is accounted for).
    Names taken from the data (`Say.lit`) are removed first, but only as whole words: a column called "0.2" must not hide the 5 in "0.25"."""
    lits = sorted(say.literals, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(?<!\d\.)(?:" + "|".join(re.escape(l) for l in lits) + r")(?!\w)(?!\.\d)") if lits else None
    for text in texts:
        if pattern is not None:
            text = pattern.sub(" ", text)
        for m in _NUMBER.finditer(text):
            tok = m.group(0).lstrip("+").replace(",", "")
            if tok not in say.tokens:
                return m.group(0)
    return None


def finish(kind: str, say: Say, draft: Draft) -> Dict[str, Any]:
    points = [p for p in draft.points if p][:MAX_POINTS]
    cautions = [c for c in draft.cautions if c][:MAX_CAUTIONS]
    bad = _scan([draft.summary, *points, *cautions], say)
    if bad is not None:
        log.warning("explanation %s rejected: unaccounted number %r", kind, bad)
        return unavailable(kind, "A figure in the draft explanation could not be matched to the calculated result, so no explanation is shown. The technical result above is unaffected.")
    seen, terms = set(), []
    for t in draft.terms:
        if t["term"] not in seen:
            seen.add(t["term"])
            terms.append({"term": t["term"], "meaning": t["meaning"]})
    return {"schema": SCHEMA, "title": TITLE, "status": "available", "kind": kind, "summary": draft.summary, "points": points, "terms": terms, "cautions": cautions,
            "basis": {"method": "rules", "numbers_cited": len(say.cited), "note": "Written from the calculated result by fixed rules. No figure is added, rounded away or inferred; the technical result above is unchanged."}}


def explain(kind: str, result: Mapping[str, Any]) -> Dict[str, Any]:
    """The single entry point. `result` is the structured result (JSON-like). Never raises."""
    fn = REGISTRY.get(kind)
    if fn is None:
        return unavailable(kind, "No plain-English explanation has been written for this kind of result yet.")
    try:
        say = Say(result)
        draft = fn(say, result)
        if draft is None:
            return unavailable(kind, "This result does not contain enough information to explain it reliably.")
        return finish(kind, say, draft)
    except Unverifiable as exc:
        log.warning("explanation %s unverifiable: %s", kind, exc)
        return unavailable(kind, "A figure needed for the explanation is not present in the result, so no explanation is shown. The technical result above is unaffected.")
    except Exception as exc:  # noqa: BLE001  (a malformed result must never break the page that shows it)
        log.warning("explanation %s failed: %s", kind, type(exc).__name__)
        return unavailable(kind, "The result has a shape this explanation does not recognise, so no explanation is shown. The technical result above is unaffected.")


# ------------------------------------------------------------------------------------------------ vocabulary shared by explainers
TERMS = {
    "p-value": "How surprising the data would be if there were really no effect. A small p-value means the data are hard to square with 'no effect'; it is not the chance that the finding is true and says nothing about size.",
    "confidence interval": "A range of values that is consistent with the data. A narrow range means the estimate is precise; a wide range means it is uncertain.",
    "effect size": "How large a difference or relationship is, regardless of sample size. A tiny effect can be 'significant' in a big dataset, so size matters as well as p.",
    "correlation": "How closely two numbers move together, from -1 (opposite) through 0 (unrelated) to +1 (together). It describes a pattern, not a cause.",
    "Spearman rho": "A correlation based on ranks, so it copes with skewed data and outliers and captures any steadily rising or falling pattern.",
    "Pearson r": "A correlation that measures how well a straight line fits the two variables. Outliers can distort it.",
    "median": "The middle value when the data are sorted: half the values are below it, half above.",
    "mean": "The ordinary average (sum divided by count). It is pulled toward extreme values.",
    "standard deviation": "A typical distance of values from the average. Larger means more spread out.",
    "IQR": "The spread of the middle half of the values (75th percentile minus 25th percentile).",
    "skewness": "Whether one tail is longer. Positive means a few very large values; negative means a few very small ones.",
    "Cramér's V": "The strength of association between two categories, from 0 (none) to 1 (perfect), adjusted for table size.",
    "eta (correlation ratio)": "How much of the variation in a number is explained by which group a row belongs to, from 0 (none) to 1 (all).",
    "chi-square test": "Checks whether two categorical columns are independent. It needs enough rows in each cell of the table to be trustworthy.",
    "Kruskal-Wallis test": "Compares groups using ranks instead of averages, so it suits skewed data. A small p-value says at least one group differs.",
    "ANOVA": "Compares group averages. A small p-value says at least one group average differs from the others.",
    "R²": "The share of the variation in the target that the model explains, where 1 is everything and 0 is no better than predicting the average.",
    "RMSE": "A typical size of the model's error, in the same units as the target; large errors count extra.",
    "MAE": "The average size of the model's error in the units of the target; every error counts equally.",
    "MAPE": "The average error as a percentage of the true value. It breaks down when true values are near zero.",
    "accuracy": "The share of predictions that were right. It can look good on imbalanced data even when the model never finds the rare class.",
    "precision": "Of the cases the model flagged as positive, the share that really were.",
    "recall": "Of the cases that really were positive, the share the model found.",
    "F1": "A single score that balances precision and recall (1 is best).",
    "ROC-AUC": "How well the model ranks positives above negatives, from 0.5 (no better than chance) to 1 (perfect ranking).",
    "PR-AUC": "How well the model keeps precision high while finding positives; the most informative single score when positives are rare.",
    "log loss": "Penalises confident wrong probabilities; lower is better. It judges the probabilities, not only the labels.",
    "cross-validation": "Training and testing the model several times on different slices of the training data to see how stable its score is.",
    "overfitting": "When a model memorises its training data and does worse on new data. A large gap between training and validation scores is the warning sign.",
    "permutation importance": "How much the model's score drops when one column's values are shuffled. A big drop means the model leans on that column.",
    "balanced accuracy": "The average of the share found in each class, so a rare class counts as much as a common one.",
    "baseline": "A reference model that ignores every input (always the most common class, or always the average). A useful model has to beat it clearly.",
    "calibration": "Whether predicted probabilities match how often the outcome really happens. Well-calibrated scores can be read as rough chances; poorly calibrated ones are only rankings.",
    "held-out rows": "Rows set aside before any training. Because the model never saw them, their score is an honest preview of performance on new data.",
    "residual": "The actual value minus the model's prediction for one row. Residuals near zero and without a pattern mean the errors are small and unbiased.",
    "learning curve": "How the score changes as the model is given more training rows. It shows whether collecting more data is likely to help.",
    "partial dependence": "The average prediction as one column is moved across its range while the others keep their real values. It describes the model's behaviour, not a cause.",
    "hyper-parameter tuning": "Trying several settings of a model and keeping the best by cross-validation. The best of many tries is slightly optimistic, so the held-out score is the fair one.",
    "data leakage": "When an input contains the answer, or is only known after the outcome. A leaky model looks excellent in testing and fails in real use.",
    "confusion matrix": "A table of actual versus predicted classes: the diagonal holds the correct predictions and the other cells show which classes get mixed up.",
    "PR-AUC (average precision)": "Summarises precision across every recall level; the most informative score when the positive class is rare.",
}


def terms_for(*names: str) -> List[Dict[str, str]]:
    return [{"term": n, "meaning": TERMS[n]} for n in names if n in TERMS]


CAUSATION = "This describes a pattern in the data. It does not show that one thing causes another."
