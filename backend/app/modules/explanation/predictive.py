"""Explainers for predictive modelling: dataset preparation, problem type, model ranking, evaluation, importance, overfitting, learning curve, tuning, predictions
(single and batch), partial dependence and model comparison.

Each reads the structured result the modelling code produced and writes plain English beside it. Every figure is cited through `Say` (so it must exist in the
result) and the finished text is scanned for any figure that did not come from `Say`. Nothing here changes a result, and nothing claims more than the result supports:
importance and partial dependence describe the model, never a cause; probabilities are described as scores unless the calibration check supports more."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .core import Draft, Say, Unverifiable, explainer, terms_for

PROBLEM_WORDS = {"binary_classification": "binary classification (two possible outcomes)", "multiclass_classification": "multiclass classification (several possible outcomes)",
                 "regression": "regression (predicting a number)"}
CLASSIFICATION = ("binary_classification", "multiclass_classification")
METRIC_TERMS = {"roc_auc": "ROC-AUC", "average_precision": "PR-AUC", "f1": "F1", "f1_macro": "F1", "accuracy": "accuracy", "balanced_accuracy": "balanced accuracy",
                "neg_log_loss": "log loss", "neg_root_mean_squared_error": "RMSE", "neg_mean_absolute_error": "MAE", "r2": "R²"}
LEARNING_WORDS = {"converging": "The validation score has levelled off, so more rows of the same kind are unlikely to change much.",
                  "plateau": "The validation score has stopped improving as rows were added, so a more informative set of inputs (not more rows) is the likelier route to a better model.",
                  "more_data_may_help": "The validation score was still rising at the largest size tried, so more training rows could help.",
                  "memorising": "The model fits its training rows far better than new rows, even with more data, which is the pattern of memorising rather than learning."}
CALIBRATION_WORDS = {"good": "The predicted probabilities matched how often the outcome happened closely enough to be read as rough chances.",
                     "fair": "The predicted probabilities were only roughly in line with how often the outcome happened, so read them as approximate scores.",
                     "poor": "The predicted probabilities did not match how often the outcome happened, so read them as rankings of likelihood and not as real chances.",
                     "unknown": "Whether the probabilities can be read as real chances could not be checked."}


def _l(say: Say, name: Any) -> str:
    return f"'{say.lit(name)}'"


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return one if n == 1 else (many or one + "s")


def _names(say: Say, names: List[Any], limit: int = 4) -> str:
    shown = [_l(say, n) for n in names[:limit]]
    extra = len(names) - limit
    if extra > 0:
        return ", ".join(shown) + f" and {say.items(extra, 'further columns')} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def _metric_terms(metric: Optional[str]) -> List[str]:
    t = METRIC_TERMS.get(metric or "")
    return [t] if t else []


def _score(say: Say, value: Any, digits: int = 3, what: str = "score") -> str:
    return say.num(value, digits, what)


def _flip(say: Say, r: Mapping[str, Any], value: Any, what: str = "score") -> str:
    """Scores are ranked so that higher is better, which stores error metrics (RMSE, MAE) with their sign flipped; people read them as ordinary positive errors."""
    if r["best"]["lower_is_better"]:
        return say.derived(-float(value), from_values=[value], what=what)
    return say.num(value, 3, what)


def _best_candidate(r: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    bid = (r.get("best") or {}).get("id")
    return next((c for c in r.get("candidates", []) if c.get("id") == bid), None)


# ------------------------------------------------------------------------------------------------ before training
@explainer("ml:assessment")
def _assessment(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    suit, prob_, ds, target = r["suitability"], r["problem"], r["dataset"], r["target"]
    if not prob_.get("valid", True) or prob_.get("chosen") is None:
        reason = (prob_.get("errors") or ["The prediction objective is not valid for this column."])[0]
        return Draft(f"This prediction objective cannot be used: {say.quote(reason, cap=False)}", [], cautions=["Choose a different target column or problem type."])
    verdict = suit["verdict"]
    head = {"ready": "The dataset looks suitable for this prediction.", "caution": "The dataset can be modelled, with the cautions below.", "not_suitable": "The dataset cannot be modelled as it stands."}[verdict]
    summary = f"{head} The aim is to predict {_l(say, target['name'])} from {say.count(r['preprocessing']['used'])} input {_plural(r['preprocessing']['used'], 'column')}, using {say.count(ds['labelled_rows'])} rows that have a known answer."
    pts = [say.quote(prob_["why"], cap=True)]
    pre = r["preprocessing"]
    if pre["dropped"]:
        pts.append(f"{say.count(pre['dropped'])} {_plural(pre['dropped'], 'column is', 'columns are')} left out as inputs (identifiers, constant columns, columns with too much missing data, or suspected leakage); the reason for each is listed in the table.")
    if r.get("leakage"):
        cols = [x["column"] for x in r["leakage"] if x["action"] == "exclude"]
        warn = [x["column"] for x in r["leakage"] if x["action"] != "exclude"]
        if cols:
            pts.append(f"{_names(say, cols)} {_plural(len(cols), 'looks', 'look')} like {_plural(len(cols), 'it contains', 'they contain')} the answer, so {_plural(len(cols), 'it is', 'they are')} excluded unless you override this. A model that sees the answer scores brilliantly in testing and fails on new cases.")
        if warn:
            pts.append(f"{_names(say, warn)} {_plural(len(warn), 'is', 'are')} very closely tied to the target; check that {_plural(len(warn), 'it is', 'they are')} known before the outcome in real use.")
    if prob_["chosen"] in CLASSIFICATION and r.get("imbalance"):
        pts.append(say.quote(r["imbalance"]["message"]))
    pts.append(say.quote(r["metrics"]["reasons"][0], cap=True))
    miss = [c for c in r["columns"] if c.get("missing", 0) > 0 and c["name"] != target["name"] and c["decision"]["action"] == "use"]
    if miss:
        pts.append(f"{say.items(len(miss))} input {_plural(len(miss), 'column has', 'columns have')} missing values; they are filled in using only the training rows (no information from the test rows).")
    pts.append(f"{say.items(len(r['algorithms']['selected']))} {_plural(len(r['algorithms']['selected']), 'algorithm is', 'algorithms are')} suitable and will be compared.")
    cautions = [say.quote(c) for c in suit["blockers"]] + [say.quote(c) for c in suit["cautions"]]
    return Draft(summary, pts, terms_for("data leakage", "cross-validation", *_metric_terms(r["metrics"]["primary"])), cautions)


# ------------------------------------------------------------------------------------------------ trained model report
@explainer("ml:problem")
def _problem(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    p, d, v = r["problem"], r["data"], r["validation"]
    summary = f"The model predicts {_l(say, p['target']['name'])} as {PROBLEM_WORDS[p['type']]}."
    pts = [say.quote(p["why"], cap=True)]
    if p["chosen_by"] == "you":
        pts.append("You chose this problem type yourself.")
    pts.append(f"{say.count(d['rows_used'])} rows were used: {say.count(d['train_rows'])} to train and compare models, and {say.count(d['test_rows'])} held back and used only for the final check.")
    if d["rows_without_target"]:
        pts.append(f"{say.count(d['rows_without_target'])} rows had no known answer and could not be used.")
    if d["duplicates_removed"]:
        pts.append(f"{say.count(d['duplicates_removed'])} duplicate rows were removed so that copies of one row cannot appear on both sides of the split.")
    if d.get("rare_class_rows_dropped"):
        pts.append(f"{say.count(d['rare_class_rows_dropped'])} rows belonging to classes with too few examples were set aside.")
    if d.get("sampled"):
        pts.append("The table was larger than the limit, so a recorded random sample was used.")
    table = d.get("class_table")
    if table:
        top = ", ".join(f"{_l(say, c['label'])} ({say.share(c['share'], 0)})" for c in table[:4])
        pts.append(f"Share of rows by outcome: {top}{', ...' if len(table) > 4 else ''}.")
    cv = v["cross_validation"]
    pts.append(f"Models were compared using {say.count(cv['folds'])}-fold cross-validation on the training rows" + (" (time-ordered)" if v["holdout"].get("time_ordered") else "") + "; the held-out rows were never used to choose a model.")
    return Draft(summary, pts, terms_for("cross-validation", "held-out rows"))


@explainer("ml:features")
def _features(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    f = r["features"]
    used, dropped = f["used"], f["dropped"]
    summary = f"The model uses {say.items(len(used))} input {_plural(len(used), 'column')}, which become {say.count(f['encoded_columns'])} numeric features after preparation."
    pts = []
    if dropped:
        pts.append("Left out: " + "; ".join(f"{_l(say, d['name'])} ({say.quote(d['reason'], cap=False).rstrip('.')})" for d in dropped[:4]) + ("; and others." if len(dropped) > 4 else "."))
    if f["leakage"]:
        pts.append(f"{say.items(len(f['leakage']))} {_plural(len(f['leakage']), 'column was', 'columns were')} flagged for possible data leakage.")
    pts.append(say.quote(f["principle"]))
    return Draft(summary, pts, terms_for("data leakage"))


@explainer("ml:ranking")
def _ranking(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    best, rank, base = r["best"], r["ranking"], r.get("baseline") or {}
    primary = best["primary_label"]
    low = best["lower_is_better"]
    n = len(rank)
    summary = (f"{say.items(n)} {_plural(n, 'model was', 'models were')} compared by {say.lit(primary)} ({'lower' if low else 'higher'} is better). "
               f"{say.lit(best['label'])} came first, scoring {_score(say, best['cv_primary_shown'])} in cross-validation" +
               (f" and {_score(say, best['test_primary_shown'])} on the held-out rows." if best.get("test_primary_shown") is not None else "."))
    pts = []
    if base.get("cv_primary_shown") is not None:
        gain, spread = best.get("beats_baseline_by") or 0, best.get("cv_std") or 0
        pts.append(f"For reference, a model that ignores every input scores {_score(say, base['cv_primary_shown'])}. " +
                   ("The best model beats it clearly." if gain > max(spread, 0) else
                    "The best model beats it by less than the usual variation between folds, so the gain may not be real." if gain > 0 else
                    "The best model does not beat it, so the inputs carry little usable signal."))
    if best.get("cv_std") is not None:
        pts.append(f"The cross-validation score varied by about {_score(say, best['cv_std'])} between folds, so differences smaller than that between models are not meaningful.")
    close = best.get("close_alternatives") or []
    if close:
        simple = next((x for x in rank if x["id"] == best.get("simpler_alternative")), None)
        pts.append(f"{say.items(len(close))} other {_plural(len(close), 'model')} scored within normal variation of the best" +
                   (f"; {say.lit(simple['label'])} is simpler and may be preferable if you value speed or ease of explanation." if simple else "."))
    if best["tuned"]:
        pts.append("The winner is a tuned version. Tuning picks the best of several tries, so its cross-validation score is slightly optimistic; the held-out score is the fair one.")
    others = [x for x in rank if x["id"] != best["id"]][:3]
    if others:
        pts.append("Next: " + "; ".join(f"{say.lit(x['label'])} ({_score(say, x['cv_primary_shown'])})" for x in others) + ".")
    return Draft(summary, pts, terms_for("cross-validation", "baseline", "hyper-parameter tuning", *_metric_terms(best["primary_metric"])) if best["tuned"] else terms_for("cross-validation", "baseline", *_metric_terms(best["primary_metric"])),
                 [say.quote(w) for w in r.get("warnings", [])[:2]])


@explainer("ml:baseline")
def _baseline(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    base, best = r["baseline"], r["best"]
    if base.get("cv_primary_shown") is None:
        return None
    summary = f"A model that ignores all inputs scores {_score(say, base['cv_primary_shown'])} on {say.lit(best['primary_label'])}; the chosen model scores {_score(say, best['cv_primary_shown'])}."
    pts = [say.quote(base["description"])]
    if "majority_class_share" in (base.get("test") or {}):
        pts.append(f"On the held-out rows, always guessing the most common class is right {say.share(base['test']['majority_class_share'], 0)} of the time; accuracy only means something above that.")
    return Draft(summary, pts, terms_for("baseline"))


@explainer("ml:evaluation")
def _evaluation(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    ev = r["evaluation"]
    m = ev["metrics"]
    basis = "held-out rows the model never saw" if ev["basis"] == "holdout" else "cross-validation predictions (no rows were held out)"
    classification = r["problem"]["type"] in CLASSIFICATION
    pts: List[str] = []
    cautions: List[str] = []
    if classification:
        summary = f"On {say.count(ev['n'])} {basis}, the model was right for {say.share(m['accuracy'], 0)} of cases (accuracy {say.num(m['accuracy'])})."
        bal = r["data"].get("class_balance") or {}
        if bal.get("accuracy_warning"):
            cautions.append("Accuracy can look high when one class is much rarer than the other, so the measures below say more.")
        pts.append(f"Balanced accuracy, which gives every class equal weight, is {say.num(m['balanced_accuracy'])}.")
        if r["problem"]["type"] == "binary_classification":
            pos = r["problem"]["positive_label"]
            pts.append(f"For {_l(say, pos)}: precision {say.num(m['precision'])} (of cases flagged, the share that were) and recall {say.num(m['recall'])} (of real cases, the share found); F1 {say.num(m['f1'])} balances the two.")
            if m.get("roc_auc") is not None:
                pts.append(f"ROC-AUC is {say.num(m['roc_auc'])}, which measures how well the model ranks real cases above non-cases.")
            if m.get("average_precision") is not None:
                pts.append(f"PR-AUC is {say.num(m['average_precision'])}, which tracks how precise the model stays while it finds more of the real cases.")
        else:
            pts.append(f"Macro F1, which averages the per-class F1 scores, is {say.num(m['f1_macro'])}.")
            if m.get("roc_auc") is not None:
                pts.append(f"ROC-AUC (one class against the rest) is {say.num(m['roc_auc'])}.")
        cm = ev["confusion_matrix"]
        labels, counts = cm["labels"], cm["counts"]
        worst = None
        for i, row in enumerate(counts):
            for j, cnt in enumerate(row):
                if i != j and cnt > 0 and (worst is None or cnt > worst[2]):
                    worst = (i, j, cnt)
        if worst:
            pts.append(f"The most common mistake: real {_l(say, labels[worst[0]])} cases predicted as {_l(say, labels[worst[1]])} ({say.count(worst[2])} {_plural(worst[2], 'row')}).")
        else:
            pts.append("No held-out row was misclassified.")
        cal = ev.get("calibration") or {}
        if cal.get("verdict") in CALIBRATION_WORDS and r["problem"]["type"] == "binary_classification":
            pts.append(CALIBRATION_WORDS[cal["verdict"]])
        if m.get("log_loss") is not None:
            pts.append(f"Log loss is {say.num(m['log_loss'])} (lower is better); it also penalises confident wrong probabilities.")
        terms = terms_for("accuracy", "balanced accuracy", "precision", "recall", "F1", "ROC-AUC", "PR-AUC", "confusion matrix", "calibration", "log loss", "held-out rows")
    else:
        summary = f"On {say.count(ev['n'])} {basis}, predictions were off by {say.num(m['mae'])} on average (MAE), measured in the units of the target."
        pts.append(f"RMSE is {say.num(m['rmse'])}; it is larger than MAE when a few predictions are far off.")
        pts.append(f"R² is {say.num(m['r2'])}, meaning the model accounts for about {say.share(m['r2'], 0)} of the variation in the target." if m["r2"] >= 0 else
                   f"R² is {say.num(m['r2'])}, which is below zero: on these rows the model does worse than simply predicting the average.")
        if m.get("adjusted_r2") is not None:
            pts.append(f"Adjusted R² ({say.num(m['adjusted_r2'])}) corrects R² for the number of inputs.")
        te = ev.get("typical_error")
        if te:
            pts.append(f"About {say.const(80, 'coverage level of the typical-error range')}% of held-out predictions were within {say.num(te['within_80'])} of the true value, and {say.const(95, 'coverage level of the wide range')}% within {say.num(te['within_95'])}. This is a typical error, not a guarantee for any one prediction.")
        if m.get("mape") is not None:
            pts.append(f"On average the error is about {say.share(m['mape'], 1)} of the true value (MAPE).")
        if abs(m.get("bias", 0)) > 0:
            pts.append(f"The average residual is {say.num(m['bias'])}; a value near zero means the model does not systematically over- or under-predict.")
        terms = terms_for("MAE", "RMSE", "R²", "MAPE", "residual", "held-out rows")
    if ev["basis"] != "holdout":
        cautions.append("No rows were held out, so these figures come from cross-validation and are less independent than a held-out check.")
    if ev["n"] < 100:
        cautions.append(f"Only {say.count(ev['n'])} rows were available for this check, so every figure here is uncertain.")
    return Draft(summary, pts, terms, cautions)


@explainer("ml:importance")
def _importance(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    imp = r["importance"]
    perm, native = imp.get("permutation") or {}, imp.get("native") or {}
    pts: List[str] = []
    terms: List[str] = []
    if perm.get("available") and perm.get("features"):
        feats = perm["features"][:4]
        top = feats[0]
        summary = f"The model relies most on {_l(say, top['feature'])}, which accounts for about {say.share(top['share'], 0)} of the total importance."
        pts.append("Shuffling a column's values and re-scoring the model shows how much it depends on that column. The largest effects: " +
                   "; ".join(f"{_l(say, f['feature'])} ({say.num(f['importance'])}, {say.share(f['share'], 0)} of the total)" for f in feats) + ".")
        weak = [f["feature"] for f in perm["features"] if not f.get("reliable")]
        if weak:
            pts.append(f"For {_names(say, weak)} the effect is within normal run-to-run variation, so the model may not really use {_plural(len(weak), 'it', 'them')}.")
        terms.append("permutation importance")
    elif native.get("available") and native.get("features"):
        feats = native["features"][:4]
        summary = f"Inside the model, {_l(say, feats[0]['feature'])} carries the most weight ({say.share(feats[0]['share'], 0)} of the total)."
        pts.append("Weights by column: " + "; ".join(f"{_l(say, f['feature'])} ({say.share(f['share'], 0)})" for f in feats) + ".")
    else:
        return None
    if native.get("available") and native.get("features") and perm.get("available"):
        n0 = native["features"][0]
        if n0["feature"] == perm["features"][0]["feature"]:
            pts.append(f"The model's own internal weights agree: {_l(say, n0['feature'])} is also its heaviest input ({say.share(n0['share'], 0)}). Two different views pointing the same way make this a sturdier finding.")
        else:
            pts.append(f"The model's own internal weights point to {_l(say, n0['feature'])} ({say.share(n0['share'], 0)}) as the heaviest input, which differs from the shuffling test. The two views measure different things (weights are not the same as dependence), so treat the ranking as uncertain.")
    pts.append(say.quote(imp["shap"]["reason"]) if not imp["shap"].get("available") else "SHAP values are available for this model.")
    return Draft(summary, pts, terms_for(*terms), [say.quote(imp["note"])])


@explainer("ml:overfitting")
def _overfitting(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    c = _best_candidate(r)
    if c is None or "overfitting" not in c:
        return None
    o, g = c["overfitting"], c.get("generalisation") or {}
    level = {"low": "no sign of overfitting", "moderate": "some sign of overfitting", "high": "strong signs of overfitting"}.get(o["level"], "an unclear overfitting picture")
    summary = f"The chosen model shows {level}: {say.quote(o['message'], cap=False)}"
    pts = [f"Measured as {say.lit(o['metric'])}, the gap between the training rows and unseen rows is {say.num(o['gap'])}."]
    if g.get("reading"):
        diff = say.derived(abs(g["difference"]), from_values=[g["difference"]], what="difference")
        pts.append(f"The cross-validation score ({_flip(say, r, g['cv_primary'])}) and the held-out score ({_flip(say, r, g['test_primary'])}) differ by {diff}. {say.quote(g['reading'])}")
    return Draft(summary, pts, terms_for("overfitting", "cross-validation"))


@explainer("ml:learning_curve")
def _learning_curve(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    lc = r.get("learning_curve") or {}
    if not lc.get("available") or not lc.get("points"):
        return None
    pts_ = lc["points"]
    first, last = pts_[0], pts_[-1]
    summary = LEARNING_WORDS.get(lc["verdict"], "The learning curve shows how the score changes with more training rows.")
    pts = [f"With {say.count(first['train_rows'])} training rows the validation score was {_flip(say, r, first['validation_score'])}; with {say.count(last['train_rows'])} it was {_flip(say, r, last['validation_score'])}.",
           f"At the largest size the training score was {_flip(say, r, last['train_score'])}, a gap of {say.derived(abs(lc['final_gap']), from_values=[lc['final_gap']], what='gap')} from the validation score."]
    return Draft(summary, pts, terms_for("learning curve", "overfitting"))


@explainer("ml:hpo")
def _hpo(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    h = r.get("hpo")
    if not h or not h.get("algorithms"):
        return None
    method = {"grid": "grid search (every combination)", "random": "randomized search (a random sample of combinations)"}.get(h["method"], "a parameter search")
    summary = f"Tuning used {method} on {say.items(len(h['algorithms']))} {_plural(len(h['algorithms']), 'algorithm')}."
    pts = []
    for a in h["algorithms"]:
        line = f"{say.lit(a['label'])}: {say.count(a['trials_run'])} of {say.count(a['trials_planned'])} parameter sets were scored"
        if a.get("best_score") is not None:
            line += f"; the best reached {say.num(a['best_score'])} against {say.num(a['baseline_score'])} with default settings"
        if a.get("stopped_early"):
            line += " (stopped early to stay within the time limit)"
        pts.append(line + ".")
    return Draft(summary, pts, terms_for("hyper-parameter tuning", "cross-validation"), [say.quote(h["caution"])])


# ------------------------------------------------------------------------------------------------ predictions
@explainer("ml:prediction")
def _prediction(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    issues = r.get("input_issues") or []
    pts: List[str] = []
    cautions: List[str] = []
    if r["problem"] in CLASSIFICATION:
        pred = r["prediction"]
        summary = f"The model predicts {_l(say, pred['label'])} for {_l(say, r['target'])}, with a score of {say.share(pred['probability'], 0)} for that outcome."
        pts.append(say.quote(r["probability_reading"]))
        others = [p for p in r["probabilities"] if p["class"] != pred["label"]][:3]
        if others:
            pts.append("Scores for the other outcomes: " + "; ".join(f"{_l(say, p['class'])} {say.share(p['probability'], 0)}" for p in others) + ".")
        terms = terms_for("calibration")
    else:
        pred = r["prediction"]
        te = r.get("typical_error") or {}
        summary = f"The model predicts {say.num(pred['value'])} for {_l(say, r['target'])}."
        if te:
            pts.append(f"On held-out rows, about {say.const(80, 'coverage level of the typical-error range')}% of predictions were within {say.num(te['within_80'])} of the true value, which here would mean roughly {say.num(te['range_80'][0])} to {say.num(te['range_80'][1])}. That is a typical error, not a guarantee for this case.")
        terms = terms_for("MAE", "held-out rows")
    local = r.get("local")
    if local and local.get("effects"):
        top = [e for e in local["effects"] if e["effect"] != 0][:3]
        if top:
            pts.append("Inputs that moved this prediction most (versus typical values): " +
                       "; ".join(f"{_l(say, e['feature'])} = {say.lit(e['value'])} {e['direction']} it by {say.derived(abs(e['effect']), from_values=[e['effect']], what='effect')}" for e in top) +
                       ". This is how the model behaves, not proof of what drives the outcome (not SHAP).")
    if issues:
        cautions.append(f"{say.items(len(issues))} {_plural(len(issues), 'input issue')} to check: {say.quote(issues[0]['message'], cap=False)}")
    if any(i["kind"] == "range" for i in issues):
        cautions.append("A value outside the range the model was trained on makes the prediction less reliable.")
    return Draft(summary, pts, terms, cautions)


@explainer("ml:batch")
def _batch(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    summary = f"Predictions were made for {say.count(r['rows'])} rows of {_l(say, r['source']['name'])}."
    pts: List[str] = []
    if r["problem"] in CLASSIFICATION:
        counts = r["prediction_counts"][:4]
        pts.append("Predicted outcomes: " + "; ".join(f"{_l(say, c['class'])} {say.count(c['rows'])} rows ({say.share(c['share'], 1)})" for c in counts) + ".")
        pts.append(say.quote(r["probability_reading"]))
    else:
        s = r["prediction_summary"]
        pts.append(f"Predicted {_l(say, r['target'])} ranges from {say.num(s['min'])} to {say.num(s['max'])}, with a middle value (median) of {say.num(s['median'])}.")
        te = r.get("typical_error") or {}
        if te:
            pts.append(f"On held-out rows about {say.const(80, 'coverage level of the typical-error range')}% of predictions were within {say.num(te['within_80'])} of the truth.")
    cautions: List[str] = []
    if r["rows_with_warnings"]:
        cols = list(r["issues_by_column"].keys())
        cautions.append(f"{say.count(r['rows_with_warnings'])} {_plural(r['rows_with_warnings'], 'row has', 'rows have')} values that are missing, new, or outside the training range (columns: {_names(say, cols)}). Those rows are flagged in the download and their predictions are less reliable.")
    if r.get("possible_drift"):
        cautions.append("Some columns look different from the training data, so the model may be less accurate on this dataset.")
    return Draft(summary, pts, terms_for("calibration") if r["problem"] in CLASSIFICATION else terms_for("held-out rows"), cautions)


@explainer("ml:pdp")
def _pdp(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    if not r.get("available") or not r.get("series"):
        return None
    name = next(iter(r["series"]))
    ys = r["series"][name]
    grid = r["grid"]
    pts: List[str] = []
    if r["kind"] == "numeric":
        a, b = ys[0], ys[-1]
        lo, hi = grid[0], grid[-1]
        direction = "rises" if b > a else "falls" if b < a else "stays flat"
        summary = f"As {_l(say, r['feature'])} goes from {say.num(lo)} to {say.num(hi)}, the average prediction {direction}, from {say.num(a)} to {say.num(b)}."
        pts.append("The line is the average prediction when this column is set to each value and every other column keeps its real values.")
    else:
        pairs = sorted(zip(grid, ys), key=lambda p: p[1])
        lo, hi = pairs[0], pairs[-1]
        summary = f"The model predicts the highest average for {_l(say, hi[0])} ({say.num(hi[1])}) and the lowest for {_l(say, lo[0])} ({say.num(lo[1])})."
        pts.append("Each bar is the average prediction when the column is set to that category and every other column keeps its real values.")
    if r["kind"] == "numeric" and len(ys) > 2:
        mid = ys[len(ys) // 2]
        if (mid - ys[0]) * (ys[-1] - mid) < 0:
            pts.append("The relationship is not a straight line: the direction changes along the range.")
    focus = f" for {_l(say, name)}" if name != "prediction" else ""
    pts.append(f"Based on {say.count(r['rows_used'])} reference rows{focus}. The shaded band shows how much predictions vary between rows.")
    return Draft(summary, pts, terms_for("partial dependence"), ["This describes how the model behaves. It does not show what would happen if the real-world value changed."])


@explainer("ml:model_compare")
def _compare(say: Say, r: Mapping[str, Any]) -> Optional[Draft]:
    items = r["models"]
    if len(items) < 2:
        return None
    summary = f"{say.items(len(items))} models for {_l(say, r['target'])} are compared."
    pts: List[str] = []
    if r["same_primary_metric"]:
        low = items[0]["lower_is_better"]
        ordered = sorted(items, key=lambda i: (i["test_score"] if i["test_score"] is not None else float("inf") if low else float("-inf")), reverse=not low)
        best, worst = ordered[0], ordered[-1]
        if best["test_score"] is not None and worst["test_score"] is not None:
            pts.append(f"On {say.lit(best['primary_label'])} ({'lower' if low else 'higher'} is better), {_l(say, best['name'])} leads with {say.num(best['test_score'])} on held-out rows; {_l(say, worst['name'])} has {say.num(worst['test_score'])}.")
    for n in r.get("notes", []):
        pts.append(say.quote(n))
    return Draft(summary, pts, terms_for("held-out rows", "cross-validation"), ["A small difference between models is often just chance in which rows landed in the test set. Prefer the simpler model when scores are close."])
