"""Deterministic question understanding: question text -> an interpretation (intent, columns, filters, a structured plan or a clarification).

No model is involved, so the same question on the same schema always yields the same plan and nothing leaves the server. Matching is
tolerant of typos (edit-distance matching), plurals and common aliases, but it never guesses between candidates: when a word could mean
two columns, or a needed column is missing, it asks. The user's question is never silently changed - every choice is listed in `steps`."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MAX_QUESTION = 600
STOP = set("the a an of in on for to is are was were be by with and or at as from that this what which who how do does did it its me my our your show tell give find get please can could would should there their than then also each per across over under".split())
ALIASES = [
    {"revenue", "sales", "turnover", "income", "earnings", "takings"},
    {"cost", "price", "spend", "spending", "expense", "expenses", "amount", "value", "charge"},
    {"age", "years"},
    {"customer", "client", "user", "buyer", "member"},
    {"region", "area", "territory", "zone", "location", "country", "state", "city"},
    {"category", "type", "segment", "group", "class", "kind", "tier"},
    {"churn", "churned", "attrition", "cancelled", "canceled", "left"},
    {"date", "time", "day", "signup", "joined", "created", "timestamp"},
    {"product", "item", "sku"},
]
KEYWORDS = {
    "missing": ["missing", "null", "empty", "blank", "nan", "incomplete"],
    "outlier": ["outlier", "outliers", "unusual", "anomaly", "anomalies", "abnormal", "strange", "extreme", "unexpected", "weird"],
    "trend": ["trend", "trends", "monthly", "weekly", "daily", "yearly", "quarterly", "annually", "timeline", "seasonal"],
    "relationship": ["relationship", "relation", "related", "correlation", "correlated", "associated", "association", "depend", "depends", "connection", "link", "linked", "affect", "affects", "influence", "influences", "impact"],
    "target": ["target", "drivers", "driver", "drives", "strongly"],
    "compare": ["compare", "comparison", "versus", "vs", "differ", "differs", "difference", "differences", "across"],
    "best": ["best", "top", "highest", "worst", "lowest", "performs", "performing", "largest", "smallest", "biggest", "leading", "least"],
    "aggregate": ["average", "mean", "median", "sum", "total", "maximum", "max", "minimum", "min", "count", "number", "many", "std", "deviation"],
    "distribution": ["distribution", "histogram", "spread", "shape"],
    "summary": ["summary", "summarise", "summarize", "overview", "describe", "profile"],
}
AGG_WORDS = {"average": "mean", "mean": "mean", "avg": "mean", "median": "median", "sum": "sum", "total": "sum", "maximum": "max", "max": "max", "highest": "max", "minimum": "min", "min": "min",
             "lowest": "min", "count": "count", "number": "count", "many": "count", "std": "std", "deviation": "std"}
FREQ_WORDS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year", "annually": "year"}
NUM_OPS = [(r">=|at least|no less than", "ge"), (r"<=|at most|no more than", "le"), (r">|over|above|greater than|more than|exceeding", "gt"), (r"<|under|below|less than|fewer than", "lt")]
CUES = {"where", "when", "for", "with", "in", "among", "only", "whose", "if", "and", "but", "excluding", "within"}


def norm_words(text: str) -> List[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _like(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def has_kw(words: Sequence[str], group: str) -> bool:
    for w in words:
        if w in KEYWORDS[group]:
            return True
        if len(w) >= 5 and get_close_matches(w, KEYWORDS[group], n=1, cutoff=0.84):
            return True
    return False


@dataclass
class Mention:
    column: str
    start: int
    end: int
    score: float
    via: str


@dataclass
class Interpretation:
    intent: str = "unknown"
    steps: List[str] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)
    filters: List[Dict[str, Any]] = field(default_factory=list)
    roles: Dict[str, Any] = field(default_factory=dict)
    clarification: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)
    focus: Dict[str, Any] = field(default_factory=dict)       # what the answer is about (used to pick headline facts)


def _ask(slot: str, prompt: str, options: Sequence[str]) -> Dict[str, Any]:
    return {"slot": slot, "prompt": prompt, "options": [{"label": o, "value": o} for o in options][:12]}


# ------------------------------------------------------------------ column mentions
def find_mentions(tokens: List[Tuple[str, int, int]], columns: Sequence[Mapping[str, Any]]) -> Tuple[List[Mention], List[Dict[str, Any]]]:
    """Returns (unambiguous mentions in text order, ambiguous groups [{phrase, candidates}])."""
    words = [t[0] for t in tokens]
    forms: Dict[str, List[Tuple[str, ...]]] = {c["name"]: [] for c in columns}
    for c in columns:
        base = [singular(w) for w in norm_words(c["name"])]
        forms[c["name"]] = [tuple(base)]
    found: List[Tuple[int, int, str, float, str]] = []     # start_tok, end_tok, column, score, via
    for n in (4, 3, 2, 1):
        for i in range(0, len(words) - n + 1):
            phrase = tuple(singular(w) for w in words[i:i + n])
            if n == 1 and phrase[0] in STOP:
                continue
            for name, fs in forms.items():
                for f in fs:
                    if phrase == f:
                        found.append((i, i + n, name, 1.0, "exact"))
                    elif len(f) == n and sum(map(len, f)) >= 4 and all(_like(a, b) >= 0.84 or a == b for a, b in zip(phrase, f)) and n <= 3 and phrase[0] not in STOP:
                        found.append((i, i + n, name, 0.9, "typo"))
    for i, w in enumerate(words):
        sw = singular(w)
        if w in STOP:
            continue
        for group in ALIASES:
            if sw in group or w in group:
                for c in columns:
                    cw = {singular(x) for x in norm_words(c["name"])}
                    if cw & (group - {sw}) and sw not in cw:
                        found.append((i, i + 1, c["name"], 0.8, "alias"))
    found.sort(key=lambda m: (-(m[1] - m[0]), -m[3], m[0]))
    taken: List[Tuple[int, int]] = []
    chosen: Dict[Tuple[int, int], List[Tuple[str, float, str]]] = {}
    for s, e, name, score, via in found:
        key = (s, e)
        if key in chosen:
            chosen[key].append((name, score, via))
            continue
        if any(not (e <= ts or s >= te) for ts, te in taken):
            continue
        taken.append((s, e))
        chosen[key] = [(name, score, via)]
    mentions, ambiguous = [], []
    for (s, e), cands in sorted(chosen.items()):
        best = max(sc for _, sc, _ in cands)
        top = sorted({n for n, sc, _ in cands if sc >= best - 0.02})
        if len(top) == 1:
            via = next(v for n, sc, v in cands if n == top[0])
            mentions.append(Mention(top[0], tokens[s][1], tokens[e - 1][2], best, via))
        else:
            ambiguous.append({"phrase": " ".join(words[s:e]), "candidates": top, "start": tokens[s][1], "end": tokens[e - 1][2]})
    return mentions, ambiguous


def _tokens(text: str) -> List[Tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in re.finditer(r"[A-Za-z0-9_]+", text)]


# ------------------------------------------------------------------ filters
def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_filters(text: str, mentions: List[Mention], columns: Mapping[str, Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Mention], List[str]]:
    """Explicit forms only: '<column> is <value>', '<column> over 30', 'between a and b', years/dates on a date column. Returns (filters, mentions used by filters, notes)."""
    filters: List[Dict[str, Any]] = []
    used: List[Mention] = []
    notes: List[str] = []
    for m in mentions:
        tail = text[m.end:]
        head_words = re.findall(r"[A-Za-z]+", text[:m.start].lower())[-3:]
        cued = bool(set(head_words) & CUES)
        role = columns[m.column]["role"]
        mt = re.match(r"\s*(?:is not|isn't|!=|<>)\s*(\"[^\"]+\"|'[^']+'|[\w\-\.]+)", tail, re.I)
        if mt and cued:
            filters.append({"column": m.column, "op": "ne", "value": _coerce(mt.group(1).strip("\"'"), columns[m.column])})
            used.append(m)
            continue
        me = re.match(r"\s*(?:is|are|=|==|equals|:)\s*(\"[^\"]+\"|'[^']+'|[\w\-\.]+)", tail, re.I)
        if me and cued and role != "numeric" or (me and cued and role == "numeric" and re.match(r"-?\d", me.group(1))):
            filters.append({"column": m.column, "op": "eq", "value": _coerce(me.group(1).strip("\"'"), columns[m.column])})
            used.append(m)
            continue
        if cued and role == "categorical":                     # 'for region North': a bare value right after a cued categorical column
            mv = re.match(r"\s+(\"[^\"]+\"|'[^']+'|[A-Za-z0-9][\w\-\.]*)", tail)
            if mv:
                word = mv.group(1).strip("\"'")
                nxt = {x.lower() for x in re.findall(r"[A-Za-z]+", word)}
                if not (nxt & STOP) and not any(o.start == m.end + mv.start(1) for o in mentions if o is not m) and word.lower() not in ("is", "by", "per"):
                    filters.append({"column": m.column, "op": "eq", "value": _coerce(word, columns[m.column])})
                    used.append(m)
                    notes.append(f"I read '{m.column} {word}' as: only rows where {m.column} is {word}.")
                    continue
        mb = re.match(r"\s*(?:is\s+)?between\s+(-?[\d,\.]+|\d{4}-\d{2}-\d{2})\s+and\s+(-?[\d,\.]+|\d{4}-\d{2}-\d{2})", tail, re.I)
        if mb and (cued or True):
            lo, hi = mb.group(1), mb.group(2)
            filters.append({"column": m.column, "op": "between", "value": [_coerce(lo, columns[m.column]), _coerce(hi, columns[m.column])]})
            used.append(m)
            continue
        for pat, op in NUM_OPS:
            mo = re.match(rf"\s*(?:is\s+|are\s+)?(?:{pat})\s*(-?[\d,\.]+|\d{{4}}-\d{{2}}-\d{{2}})", tail, re.I)
            if mo and cued:
                filters.append({"column": m.column, "op": op, "value": _coerce(mo.group(1), columns[m.column])})
                used.append(m)
                break
    for m in mentions:
        if columns[m.column]["role"] == "datetime" and m not in used:
            my = re.search(r"\b(?:in|during|for|of)\s+(\d{4})\b", text[m.end:m.end + 30]) or re.search(r"\b(?:in|during|for|of)\s+(\d{4})\b", text[max(0, m.start - 30):m.start])
            if my:
                y = my.group(1)
                filters.append({"column": m.column, "op": "between", "value": [f"{y}-01-01", f"{y}-12-31 23:59:59"]})
                used.append(m)
    return filters, used, notes


def _coerce(v: str, col: Mapping[str, Any]) -> Any:
    if col["role"] == "numeric":
        n = _num(v)
        return n if n is not None else v
    return v


# ------------------------------------------------------------------ main entry
def interpret(question: str, columns: Sequence[Mapping[str, Any]], overrides: Optional[Mapping[str, Any]] = None) -> Interpretation:
    out = Interpretation()
    text = " ".join(str(question or "").split())[:MAX_QUESTION]
    if len(text) < 3:
        out.clarification = _ask("question", "What would you like to know about this dataset?", [])
        return out
    overrides = dict(overrides or {})
    by_name = {c["name"]: c for c in columns}
    tokens = _tokens(text)
    words = [t[0] for t in tokens]
    mentions, ambiguous = find_mentions(tokens, columns)
    for slot, val in list(overrides.items()):          # an answer to an earlier clarification replaces the ambiguous phrase
        if slot.startswith("phrase:") and val in by_name:
            phrase = slot[7:]
            ambiguous = [a for a in ambiguous if a["phrase"] != phrase]
            for a in find_mentions(tokens, columns)[1]:
                if a["phrase"] == phrase:
                    mentions.append(Mention(val, a["start"], a["end"], 1.0, "chosen"))
    mentions.sort(key=lambda m: m.start)
    filters, used, notes = parse_filters(text, mentions, by_name)
    out.filters, out.notes = filters, notes
    rest = [m for m in mentions if m not in used]
    year = re.search(r"\b(?:in|during|for|of|year)\s+((?:19|20)\d{2})\b", text)
    if year and not any(by_name[f["column"]]["role"] == "datetime" for f in filters):
        dcols = [c["name"] for c in columns if c["role"] == "datetime"]
        chosen = overrides.get("date") if overrides.get("date") in by_name else (dcols[0] if len(dcols) == 1 else None)
        if chosen is None:
            out.clarification = _ask("date", f"Which date column should {year.group(1)} apply to?" if dcols else f"I cannot apply {year.group(1)}: this dataset has no date column.", dcols)
            return out
        filters.append({"column": chosen, "op": "between", "value": [f"{year.group(1)}-01-01", f"{year.group(1)}-12-31 23:59:59"]})
        out.notes.append(f"Applied the year {year.group(1)} to the date column '{chosen}'.")
    for m in rest:
        if m.via in ("alias", "typo"):
            out.notes.append(f"Read \"{text[m.start:m.end]}\" as the column '{m.column}'" + (" (matched despite a spelling difference)." if m.via == "typo" else " (a common alias)."))
    out.steps.append(f"Read the question: \"{text}\"")
    if used:
        out.steps.append("Filters understood: " + "; ".join(_describe(f) for f in filters))
    if ambiguous and not (has_kw(words, "missing") and not rest):
        a = ambiguous[0]
        out.clarification = _ask(f"phrase:{a['phrase']}", f"\"{a['phrase']}\" could mean more than one column. Which do you mean?", a["candidates"])
        out.steps.append(f"Ambiguous term \"{a['phrase']}\"; asked which column is meant instead of guessing.")
        return out

    def pick(role: str, slot: str, mentioned: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        ov = overrides.get(slot)
        if ov in by_name:
            return ov, None
        cands = [n for n in (mentioned if mentioned is not None else [m.column for m in rest]) if by_name[n]["role"] == role]
        cands = list(dict.fromkeys(cands))
        if len(cands) == 1:
            return cands[0], None
        pool = cands or [c["name"] for c in columns if c["role"] == role and c["name"] not in {f["column"] for f in filters}]
        if not pool:
            return None, _ask(slot, f"This question needs a {role} column, but the dataset has none.", [])
        if role == "datetime" and len(pool) == 1:
            out.notes.append(f"Used the only date column, '{pool[0]}'.")
            return pool[0], None
        word = {"numeric": "number column (measure)", "categorical": "category column", "datetime": "date column"}[role]
        hint = "" if cands else " I could not match a column to the words in your question."
        return None, _ask(slot, f"Which {word} do you mean?{hint}", pool[:12])

    num_m = [m.column for m in rest if by_name[m.column]["role"] == "numeric"]
    cat_m = [m.column for m in rest if by_name[m.column]["role"] == "categorical"]
    date_m = [m.column for m in rest if by_name[m.column]["role"] == "datetime"]
    all_num = [c["name"] for c in columns if c["role"] == "numeric"]
    mentioned_any = [m.column for m in rest]
    agg_word = next((AGG_WORDS[w] for w in words if w in AGG_WORDS), None)
    freq = next((FREQ_WORDS[w] for w in words if w in FREQ_WORDS), "auto")

    def done(intent: str, ops: List[Dict[str, Any]], roles: Dict[str, Any], focus: Dict[str, Any]) -> Interpretation:
        out.intent, out.operations, out.roles, out.focus = intent, ops, roles, focus
        out.steps.append(f"Interpreted as: {intent.replace('_', ' ')}.")
        if roles:
            out.steps.append("Columns: " + ", ".join(f"{k} = '{v}'" for k, v in roles.items() if isinstance(v, str)))
        out.steps.append("Built a structured plan: " + ", ".join(o["op"] for o in ops) + ". Calculations are run by the analysis engine, not by a language model.")
        return out

    if has_kw(words, "missing"):
        return done("missing_values", [{"op": "missing_values", "params": {}}], {}, {"kind": "missing"})
    if has_kw(words, "outlier"):
        col = num_m[0] if len(set(num_m)) == 1 else None
        ops = [{"op": "data_quality", "params": {}}, {"op": "column_stats", "params": {"columns": [col] if col else all_num[:12]}}] if all_num else [{"op": "data_quality", "params": {}}]
        return done("outliers", ops, {"measure": col} if col else {}, {"kind": "outliers", "column": col})
    if has_kw(words, "trend"):
        date, ask = pick("datetime", "date", date_m or None)
        if ask:
            out.clarification = ask
            return out
        measure = overrides.get("measure") if overrides.get("measure") in by_name else (num_m[0] if len(set(num_m)) == 1 else None)
        if len(set(num_m)) > 1 and not measure:
            out.clarification = _ask("measure", "Which number should the trend show?", list(dict.fromkeys(num_m)))
            return out
        params: Dict[str, Any] = {"date": date, "freq": freq}
        if measure:
            params.update({"measure": measure, "agg": {"sum": "sum", "median": "median", "max": "max", "min": "min", "count": "count"}.get(agg_word or "", "mean")})
            if not agg_word:
                out.notes.append("Showing the mean per period; say 'total' for the sum.")
        else:
            out.notes.append("No measure named, so the trend shows how many rows fall in each period.")
        return done("trend", [{"op": "trend", "params": params}], {"date": date, **({"measure": measure} if measure else {})}, {"kind": "trend", "measure": measure})
    if has_kw(words, "target") or (has_kw(words, "relationship") and len(set(mentioned_any)) == 1 and any(w in words for w in ("most", "strongly", "drives", "driver", "drivers", "matter", "matters"))):
        target = overrides.get("target") if overrides.get("target") in by_name else (mentioned_any[0] if len(set(mentioned_any)) == 1 else None)
        if target is None:
            out.clarification = _ask("target", "Which column is the target?", [c["name"] for c in columns][:12])
            return out
        return done("target_association", [{"op": "discover", "params": {"target": target, "top_k": 10}}], {"target": target}, {"kind": "target", "target": target})
    if has_kw(words, "relationship"):
        pair = list(dict.fromkeys(mentioned_any))
        a, b = overrides.get("column_a"), overrides.get("column_b")
        if a in by_name and b in by_name:
            pair = [a, b]
        if len(pair) < 2:
            out.clarification = _ask("column_b" if pair else "column_a", "Which two columns should I relate?" if not pair else f"What should I relate '{pair[0]}' to?", [c["name"] for c in columns if c["name"] not in pair][:12])
            if pair:
                out.clarification["keep"] = {"column_a": pair[0]}
            return out
        if len(pair) > 2:
            out.clarification = _ask("column_b", "That names more than two columns. Which two should I relate?", pair)
            return out
        return done("relationship", _relationship_ops(pair[0], pair[1], by_name), {"first": pair[0], "second": pair[1]}, {"kind": "relationship", "pair": pair})
    if has_kw(words, "best") and (cat_m or has_kw(words, "best")) and not (agg_word == "count" and not num_m):
        dim, ask = pick("categorical", "dimension", cat_m or None)
        if ask:
            out.clarification = ask
            return out
        measure, ask = pick("numeric", "measure", num_m or None)
        if ask:
            out.clarification = ask
            return out
        asc = any(w in words for w in ("worst", "lowest", "smallest", "least"))
        agg = "sum" if agg_word == "sum" or any(w in words for w in ("revenue", "sales", "total")) else (agg_word if agg_word in ("mean", "median", "max", "min") else "mean")
        out.notes.append(f"'Best' is judged by the {agg} of '{measure}' per '{dim}'. Ask for the total or the average explicitly if you meant the other.")
        return done("best_category", [{"op": "aggregate", "params": {"agg": agg, "measure": measure, "group_by": [dim], "sort": "value_asc" if asc else "value_desc"}}],
                    {"dimension": dim, "measure": measure}, {"kind": "best", "agg": agg, "measure": measure, "dimension": dim, "ascending": asc})
    if has_kw(words, "compare") and cat_m and (num_m or overrides.get("measure")):
        dim = cat_m[0] if len(set(cat_m)) == 1 else overrides.get("dimension")
        if dim not in by_name:
            out.clarification = _ask("dimension", "Compare across which category?", list(dict.fromkeys(cat_m)))
            return out
        measure = overrides.get("measure") if overrides.get("measure") in by_name else (num_m[0] if len(set(num_m)) == 1 else None)
        if measure is None:
            out.clarification = _ask("measure", "Which number should be compared?", list(dict.fromkeys(num_m)))
            return out
        return done("compare_groups", [{"op": "group_compare", "params": {"measure": measure, "group": dim}},
                                        {"op": "aggregate", "params": {"agg": "mean", "measure": measure, "group_by": [dim]}}],
                    {"measure": measure, "dimension": dim}, {"kind": "compare", "measure": measure, "dimension": dim})
    if has_kw(words, "compare") and not (cat_m and num_m):
        measure, ask = pick("numeric", "measure", num_m or None)
        dim, ask2 = (None, None) if ask else pick("categorical", "dimension", cat_m or None)
        if ask or ask2:
            out.clarification = ask or ask2
            return out
        return done("compare_groups", [{"op": "group_compare", "params": {"measure": measure, "group": dim}}, {"op": "aggregate", "params": {"agg": "mean", "measure": measure, "group_by": [dim]}}],
                    {"measure": measure, "dimension": dim}, {"kind": "compare", "measure": measure, "dimension": dim})
    if has_kw(words, "distribution"):
        col = overrides.get("column") if overrides.get("column") in by_name else (mentioned_any[0] if len(set(mentioned_any)) == 1 else None)
        if col is None:
            out.clarification = _ask("column", "The distribution of which column?", [c["name"] for c in columns][:12])
            return out
        ops = [{"op": "column_stats", "params": {"columns": [col]}}] if by_name[col]["role"] == "numeric" else [{"op": "frequency", "params": {"column": col}}]
        if by_name[col]["role"] == "numeric":
            ops.append({"op": "chart", "params": {"type": "histogram", "x": col}})
        return done("distribution", ops, {"column": col}, {"kind": "distribution", "column": col})
    if has_kw(words, "aggregate") or agg_word:
        agg = agg_word or "mean"
        if agg == "count" and not num_m:
            by = cat_m[:1]
            return done("count", [{"op": "aggregate", "params": {"agg": "count", **({"group_by": by} if by else {})}}], {"dimension": by[0]} if by else {}, {"kind": "aggregate", "agg": "count", "measure": None, "dimension": by[0] if by else None})
        measure, ask = pick("numeric", "measure", num_m or None)
        if ask:
            out.clarification = ask
            return out
        dim = (cat_m[0] if cat_m else None)
        params = {"agg": agg, "measure": measure, **({"group_by": [dim]} if dim else {})}
        return done("aggregate", [{"op": "aggregate", "params": params}], {"measure": measure, **({"dimension": dim} if dim else {})}, {"kind": "aggregate", "agg": agg, "measure": measure, "dimension": dim})
    if has_kw(words, "summary") or any(p in text.lower() for p in ("how many rows", "how big", "number of rows", "how many records")):
        return done("dataset_summary", [{"op": "dataset_summary", "params": {}}], {}, {"kind": "summary"})
    out.clarification = _ask("question", "I could not tell what to calculate from that wording. Try one of these, naming your columns:", [
        "What is the average <column>?", "Which <category> has the highest <column>?", "Is there a relationship between <column> and <column>?", "Which columns have the most missing values?",
        "Compare <column> across <category>", "Show monthly trends of <column>", "Are there unusual observations?", "Which variables are most strongly associated with <target>?"])
    return out


def _relationship_ops(a: str, b: str, by_name: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    ra, rb = by_name[a]["role"], by_name[b]["role"]
    if ra == "numeric" and rb == "numeric":
        return [{"op": "correlation", "params": {"columns": [a, b]}}, {"op": "hypothesis", "params": {"kind": "correlation", "outcome": a, "second": b}}]
    if ra == "numeric" and rb == "categorical":
        return [{"op": "group_compare", "params": {"measure": a, "group": b}}, {"op": "hypothesis", "params": {"kind": "compare_groups", "outcome": a, "group": b}}]
    if ra == "categorical" and rb == "numeric":
        return _relationship_ops(b, a, by_name)
    return [{"op": "contingency", "params": {"row": a, "column": b}}, {"op": "hypothesis", "params": {"kind": "association", "outcome": a, "group": b}}]


def _describe(f: Mapping[str, Any]) -> str:
    sym = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "between": "between"}.get(f["op"], f["op"])
    return f"{f['column']} {sym} {f.get('value')}"
