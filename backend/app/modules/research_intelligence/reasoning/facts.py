"""Facts of one source, for comparing sources with each other.

Everything here is extractive: a fact is an exact sentence of the source with the passage it came from, so a later quote can always be checked against the stored text. Nothing is
generated. Sentence classification uses cue phrases (transparent, testable, and wrong sometimes: the analysis says so). Passages the ingestion flagged as containing instructions aimed
at an AI are never used."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ..ingestion.intelligence import CLAIM_CUES, STAT, tokens

EXTRACTOR_VERSION = 1
MAX_FINDINGS, MAX_METHODS, MAX_LIMITS = 12, 6, 6

RESULT_CUES = re.compile(r"\b(significant(ly)?|associated with|correlat\w*|increas\w*|decreas\w*|reduc\w*|improv\w*|outperform\w*|higher|lower|greater|fewer|more likely|less likely|"
                         r"no (significant )?(effect|difference|association|evidence)|did not|failed to|worse|declin\w*|enhanc\w*|lead(s|ing)? to|result(s|ed)? in)\b", re.I)
RESULT_SECTION = re.compile(r"result|finding|conclusion|discussion|abstract|summary", re.I)
METHOD_CUES = re.compile(r"\b(we (used|conducted|collected|recruited|trained|surveyed|interviewed|measured|analy[sz]ed|randomi[sz]ed|sampled|evaluated|compared|fitted|simulated)|participants|respondents|"
                         r"were (recruited|randomly|assigned|surveyed|interviewed|sampled)|questionnaire|dataset|data set|cross-validation|regression|sample of|sampling|inclusion criteria|"
                         r"semi-structured|trial|cohort|experiment(al)?|simulation|benchmark)\b", re.I)
LIMIT_CUES = re.compile(r"\b(limitations?|limited by|small sample|future (work|research|studies|investigations?)|further (research|work|studies|investigation)|caveats?|"
                        r"(not|cannot be|may not be) generali[sz]\w*|confound\w*|selection bias|self-reported|short follow-up|single (site|center|centre|institution)|warrants? further)\b", re.I)
UP = re.compile(r"\b(increas\w*|higher|improv\w*|greater|more likely|gain\w*|rise[sn]?|rose|enhanc\w*|outperform\w*|positive(ly)?|boost\w*|larger|stronger|faster)\b", re.I)
DOWN = re.compile(r"\b(decreas\w*|lower|reduc\w*|declin\w*|worse|worsen\w*|fewer|less likely|drop\w*|negative(ly)?|smaller|weaker|slower|diminish\w*)\b", re.I)
NULL = re.compile(r"\b(no (statistically )?(significant )?(effect|difference|association|relationship|change|evidence|impact)|not (statistically )?significant(ly)?|did not (differ|change|improve|reduce|increase|affect)|"
                  r"failed to (improve|reduce|increase|show|find|detect)|no benefit|unrelated)\b", re.I)
SAMPLE = [re.compile(r"\bn\s?=\s?(\d[\d,]*)", re.I),
          re.compile(r"\b(\d[\d,]*)\s+(?:participants|patients|subjects|respondents|residents|students|adults|children|individuals|users|people|households|samples|cases|trees|sites|studies|trials|papers|articles|records)\b", re.I)]

# (key, label, rank, pattern). Rank 1 is the strongest design for a causal or effect question. Order matters: the first matching, most specific design wins.
DESIGNS: List[Tuple[str, str, int, "re.Pattern[str]"]] = [
    ("meta_analysis", "Systematic review or meta-analysis", 1, re.compile(r"\b(meta-?analy\w*|systematic review|pooled analysis)\b", re.I)),
    ("rct", "Randomised controlled trial", 2, re.compile(r"\b(randomi[sz]ed (controlled )?(clinical )?trial|\bRCT\b|randomly assigned|randomi[sz]ed experiment)\b", re.I)),
    ("cohort", "Cohort or longitudinal study", 3, re.compile(r"\b(cohort|longitudinal|prospective(ly)?|panel (study|data)|follow-up study)\b", re.I)),
    ("quasi", "Quasi-experimental or controlled experiment", 3, re.compile(r"\b(quasi-?experiment\w*|natural experiment|difference-in-differences|controlled experiment|laboratory experiment|field experiment|a/b test)\b", re.I)),
    ("case_control", "Case-control or cross-sectional study", 4, re.compile(r"\b(case-control|cross-sectional|observational study|retrospective)\b", re.I)),
    ("benchmark", "Benchmark or computational evaluation", 4, re.compile(r"\b(benchmark\w*|simulation study|we simulat\w*|empirical evaluation|ablation|state-of-the-art)\b", re.I)),
    ("survey", "Survey", 4, re.compile(r"\b(survey(ed)?|questionnaire|respondents|likert)\b", re.I)),
    ("qualitative", "Qualitative or case study", 5, re.compile(r"\b(qualitative|interviews?|focus groups?|case study|ethnograph\w*|thematic analysis|grounded theory)\b", re.I)),
    ("review", "Narrative review", 5, re.compile(r"\b((literature|narrative|scoping) review|this review|we review)\b", re.I)),
    ("theory", "Theoretical or opinion", 6, re.compile(r"\b(theoretical (framework|analysis|model)|we (argue|propose a framework)|position paper|commentary|editorial|perspective)\b", re.I)),
]
GRADES = {1: "strong", 2: "strong", 3: "moderate", 4: "limited", 5: "limited", 6: "weak"}


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", text.replace("\n", " ")) if 30 <= len(s.strip()) <= 400]


def polarity(sentence: str) -> Optional[str]:
    """'up', 'down', 'null' or None. Null (no effect) wins over a direction word in the same sentence; mixed up and down is left undecided rather than guessed."""
    if NULL.search(sentence):
        return "null"
    up, down = bool(UP.search(sentence)), bool(DOWN.search(sentence))
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    return None


def numbers(sentence: str) -> List[float]:
    out: List[float] = []
    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s?(%|percent|per cent)", sentence, re.I):
        out.append(float(m.group(1)))
    return out[:4]


def sample_size(text: str) -> Optional[int]:
    best = None
    for rx in SAMPLE:
        for m in rx.finditer(text):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 2 <= n <= 50_000_000 and (best is None or n > best):
                best = n
    return best


def design_of(text: str) -> Dict[str, Any]:
    for key, label, rank, rx in DESIGNS:
        m = rx.search(text)
        if m:
            return {"key": key, "label": label, "rank": rank, "grade": GRADES[rank], "evidence": m.group(0)[:60]}
    return {"key": "unknown", "label": "Design not stated", "rank": 9, "grade": "ungraded", "evidence": None}


def cache_key(row: sqlite3.Row) -> str:
    return hashlib.sha256(f"{EXTRACTOR_VERSION}:{row['version']}:{row['content_sha256'] or ''}:{row['chunk_count']}:{row['access']}".encode()).hexdigest()[:32]


def extract(conn: sqlite3.Connection, src: sqlite3.Row) -> Dict[str, Any]:
    """Build the facts of one source from its stored chunks (and abstract)."""
    chunks = conn.execute("SELECT id, ordinal, text, section, locator, flagged FROM ri_chunks WHERE source_id = ? AND owner_user_id = ? AND flagged = 0 ORDER BY ordinal", (src["id"], src["owner_user_id"])).fetchall()
    findings: List[Dict[str, Any]] = []
    methods: List[Dict[str, Any]] = []
    limits: List[Dict[str, Any]] = []
    seen = set()
    for c in chunks:
        if c["section"] and re.search(r"reference|bibliograph|acknowledg", c["section"], re.I):
            continue
        in_results = bool(c["section"] and RESULT_SECTION.search(c["section"]))
        for s in _sentences(c["text"]):
            key = re.sub(r"\W+", " ", s.lower())[:90]
            if key in seen:
                continue
            seen.add(key)
            base = {"text": s, "chunk_id": c["id"], "locator": c["locator"], "section": c["section"]}
            if LIMIT_CUES.search(s) and len(limits) < MAX_LIMITS:
                limits.append(base)
            elif METHOD_CUES.search(s) and not CLAIM_CUES.search(s) and len(methods) < MAX_METHODS:
                methods.append(base)
            elif (CLAIM_CUES.search(s) or (RESULT_CUES.search(s) and (in_results or STAT.search(s)))) and len(findings) < MAX_FINDINGS:
                findings.append({**base, "polarity": polarity(s), "numbers": numbers(s)})
    basis = " ".join([src["title"] or "", src["abstract"] or "", " ".join(m["text"] for m in methods), " ".join(c["text"] for c in chunks[:3])])
    return {"findings": findings, "methods": methods, "limitations": limits, "design": design_of(basis), "sample_size": sample_size(basis),
            "keywords": json.loads(src["keywords"] or "[]")[:12], "extractor": EXTRACTOR_VERSION}


def load(conn: sqlite3.Connection, src: sqlite3.Row, now: str) -> Dict[str, Any]:
    """Cached facts for a source; recomputed when the source changed."""
    key = cache_key(src)
    row = conn.execute("SELECT cache_key, facts FROM ri_source_facts WHERE source_id = ? AND owner_user_id = ?", (src["id"], src["owner_user_id"])).fetchone()
    if row and row["cache_key"] == key:
        return json.loads(row["facts"])
    facts = extract(conn, src)
    conn.execute("INSERT INTO ri_source_facts (source_id, workspace_id, owner_user_id, cache_key, facts, created_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(source_id) DO UPDATE SET cache_key = excluded.cache_key, facts = excluded.facts, created_at = excluded.created_at",
                 (src["id"], src["workspace_id"], src["owner_user_id"], key, json.dumps(facts), now))
    return facts


_SUFFIX = re.compile(r"(ing|ed|ly|es|s)$")


def stem(word: str) -> str:
    return _SUFFIX.sub("", word) if len(word) > 5 else word


def topic_terms(text: str) -> frozenset:
    return frozenset(stem(t) for t in tokens(text) if len(t) > 3)
