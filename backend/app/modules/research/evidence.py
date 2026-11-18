"""From candidates to evidence, findings, conflicts and conclusions. Entirely extractive and deterministic: every statement shown is a sentence that exists, character for
character, in a source, with the location it came from. Nothing is paraphrased here, so nothing can be invented here.

Vocabulary used throughout (and shown to the person):
  stated       a source says it as plain fact (PredictAI has NOT verified it)
  uncertain    the source hedges it ("may", "reportedly", "estimated")
  opinion      the source gives a view or recommendation
  prediction   a forecast, or the output of a trained model: never a fact
  computed     a number PredictAI calculated from the person's own data"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from urllib.parse import quote

from ..retrieval.lexical import BM25
from ..textkit import numbers_in, sentence_spans, stem, tokenize
from ..webaccess.structure import section_at
from . import relevance as rel
from .safety import screen
from .understanding import Frame, mentions

HEDGE = re.compile(r"\b(may|might|could|possibly|perhaps|reportedly|allegedly|apparently|appears? to|suggests?|likely|unlikely|probably|estimated|approximately|roughly|claims?|claimed|according to|said to|rumou?rs?|unconfirmed)\b", re.I)
OPINION = re.compile(r"\b(i think|we think|in my opinion|in our opinion|should|ought to|the best|the worst|believes?|believed|argues?|argued|feels?|recommend(?:s|ed)?)\b", re.I)
FORECAST = re.compile(r"\b(will|would|forecasts?|projected|projections?|expected to|predict(?:s|ed)?|outlook|by 20\d\d|is set to|are set to)\b", re.I)
NEGATION = re.compile(r"\b(no|not|never|none|neither|nor|without|cannot|can't|isn't|aren't|wasn't|weren't|doesn't|don't|didn't|won't|denied|denies|false|untrue)\b", re.I)
UP = re.compile(r"\b(rose|rise[sn]?|increase[sd]?|grew|grow(?:s|n)?|higher|up|gain(?:ed|s)?|surged?|climb(?:ed|s)?)\b", re.I)
DOWN = re.compile(r"\b(fell|fall(?:s|en)?|decrease[sd]?|declin(?:e|ed|es)|lower|down|drop(?:ped|s)?|dropped|shrank|shrink(?:s)?|slump(?:ed|s)?)\b", re.I)
BOILER = re.compile(r"(cookie|subscribe|sign up|newsletter|privacy policy|terms of (use|service)|all rights reserved|click here|advertisement|javascript)", re.I)
MIN_WORDS, MAX_CHARS = 6, 420
DATASET_CAP = 10                                                                   # calculated findings kept per dataset subtopic


def sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def statement_kind(text: str, origin: str) -> str:
    if origin == "model_prediction":
        return "prediction"
    if origin == "calculated":
        return "computed"
    if FORECAST.search(text):
        return "prediction"
    if OPINION.search(text):
        return "opinion"
    if HEDGE.search(text):
        return "uncertain"
    return "stated"


_LINK = frozenset("a an and as at by for from in into of on or the to with under over between during after before than that which who whom whose but nor yet so if its his her their".split())
TERMINAL = re.compile(r"[.!?][\"'”’)\]]*$")


def _strip_heading_lines(text: str, a: int, b: int) -> int:
    """A sentence taken from extracted document text can start with the heading line above it ("The Old Regime\\nFrance before 1789 ..."). Returns the offset where the sentence itself starts."""
    while True:
        chunk = text[a:b]
        nl = chunk.find("\n")
        if nl <= 0:
            return a
        first = chunk[:nl].strip()
        if first and len(first.split()) <= 9 and "," not in first and not TERMINAL.search(first) and not first.endswith((";", ":")) and first[:1].isupper() and first.split()[-1].lower() not in _LINK:
            a += nl + 1
            continue
        return a


def _sentences_of(cand: Dict[str, Any]) -> List[Tuple[str, int, int]]:
    text = cand["text"]
    if cand["kind"] in ("dataset", "analysis", "model", "evidence"):
        return [(text, 0, len(text))] if text.strip() else []
    out = []
    for a, b in sentence_spans(text):
        if cand["kind"] == "document":
            a = _strip_heading_lines(text, a, b)
        out.append((text[a:b], a, b))
    return out


DEPENDENT = re.compile(r"^(?:in\s+)?(?:this|the\s+following)\s+(?:sub)?(?:section|chapter)\b|^(?:\w+\s+){0,2}(?:days?|weeks?|months?|years?)\s+(?:later|earlier|before|after)\b|^(?:meanwhile|thereafter|afterwards|consequently|likewise)\b", re.I)


def _title_like(sentence: str, title: str) -> bool:
    """A heading or page title is not a statement: it has no closing punctuation, or it is the page's own title."""
    s = " ".join(sentence.split())
    return not TERMINAL.search(s) or bool(DEPENDENT.match(s)) or (bool(title) and s.casefold().rstrip(".") == " ".join(title.split()).casefold().rstrip("."))


def extract_evidence(candidates: Sequence[Dict[str, Any]], plan: Dict[str, Any], *, min_match: int = 2, exclude: Optional[set] = None) -> Dict[str, Any]:
    """Returns {sources, evidence, removed (flagged sentences), key_to_id, used}. `candidates` already carry their source record and citation.
    `min_match` is how many of a subtopic's query words a sentence must contain; `exclude` holds (source key, offset) pairs already used, so a second pass never repeats a passage."""
    exclude = exclude or set()
    frame = rel.frame_from_plan(plan) if plan.get("understanding") else None
    sources: List[Dict[str, Any]] = []
    key_to_id: Dict[str, str] = {}
    pool: List[Dict[str, Any]] = []
    fixed: List[Dict[str, Any]] = []                                                  # calculated findings already assigned to a dataset subtopic: never competed for by text matching
    removed_all: List[Dict[str, Any]] = []
    for cand in candidates:
        key = cand["source"]["key"]
        if key not in key_to_id:
            key_to_id[key] = f"S{len(key_to_id) + 1}"
            sources.append({**{k: v for k, v in cand["source"].items() if k != "text"}, "id": key_to_id[key], "injection_removed": 0})
        src = next(s for s in sources if s["id"] == key_to_id[key])
        raw = _sentences_of(cand)
        if cand.get("subtopic"):
            for text_, a, b in raw:
                fixed.append({"text": text_, "start": a, "end": b, "cand": cand, "source": src["id"], "key": key, "tokens": tokenize(text_)})
            continue
        kept, removed = screen([s for s, _, _ in raw])
        removed_texts = {r["text"] for r in removed}
        src["injection_removed"] += len(removed)
        removed_all += [{**r, "source": src["id"]} for r in removed]
        clean_raw = [(s_, a_, b_) for s_, a_, b_ in raw if s_[:300] not in removed_texts]            # neighbours shown as context come only from sentences that passed the safety screen
        pos_of = {a_: n_ for n_, (s_, a_, b_) in enumerate(clean_raw)}
        for s, a, b in raw:
            if s[:300] in removed_texts or (key, a) in exclude:
                continue
            words = len(s.split())
            if cand["kind"] in ("document", "web", "news") and frame is not None and _title_like(s, cand["source"].get("title") or ""):
                continue
            if cand["kind"] in ("document", "web", "news") and (words < MIN_WORDS or len(s) > MAX_CHARS or BOILER.search(s) or sum(ch.isalpha() for ch in s) < 0.5 * len(s)):
                continue
            sec_words: set = set()
            if cand["kind"] in ("web", "news") and cand["source"].get("sections"):
                sec = section_at(cand["source"]["sections"], a)
                if sec is not None:
                    sec_words = rel._stems(" ".join(sec.get("path") or [sec.get("heading") or ""]))
            pool.append({"text": s, "start": a, "end": b, "cand": cand, "source": src["id"], "key": key, "tokens": tokenize(s), "section_words": sec_words, "first": (clean_raw[0][1] == a) if clean_raw else False,
                         "neighbors": ((clean_raw[pos_of[a] - 1][0] if pos_of.get(a, 0) > 0 else ""), (clean_raw[pos_of[a] + 1][0] if pos_of.get(a, len(clean_raw)) + 1 < len(clean_raw) else ""))})
    evidence: List[Dict[str, Any]] = []
    used: List[Tuple[str, int]] = []
    subtopics = plan["subtopics"]
    # Calculated findings go straight to the dataset subtopic that asked for them, in the order the analysis produced them.
    for st in subtopics:
        if st.get("scope") != "dataset":
            continue
        for p in [f for f in fixed if f["cand"]["subtopic"] == st["id"]][:DATASET_CAP]:
            evidence.append(_evidence(len(evidence) + 1, st["id"], p, 1.0))
    if not pool:
        return {"sources": sources, "evidence": evidence, "removed": removed_all, "key_to_id": key_to_id, "used": used}
    bm25 = BM25([p["tokens"] for p in pool])
    question_terms = tokenize(plan["question"])
    taken: Dict[str, List[set]] = {}
    per_source: Dict[Tuple[str, str], int] = {}
    n_pass = plan["local"]["passages_per_subtopic"]
    general = [k for k, st in enumerate(subtopics) if st.get("scope") != "dataset"]
    if not general:
        return {"sources": sources, "evidence": evidence, "removed": removed_all, "key_to_id": key_to_id, "used": used}
    queries = [tokenize(st["query"]) or question_terms for st in subtopics]
    topic_sets = [set(tokenize(" ".join(st.get("topic_terms") or []))) for st in subtopics]
    matrix = [bm25.scores(q + question_terms[:3]) for q in queries]
    home = {}                                                                         # each sentence belongs to the subtopic it fits best, so one sentence is never counted twice
    vocabs = [rel.subtopic_vocab(st) if frame is not None else set() for st in subtopics]
    ent_keys = [st.get("entity") for st in subtopics]
    for i in range(len(pool)):
        cand = pool[i]["cand"]
        if frame is not None and cand["origin"] not in ("calculated", "model_prediction") and cand["kind"] in ("document", "web", "news"):
            stems = rel._stems(pool[i]["text"]) | pool[i].get("section_words", set())
            ok = [k for k in general if _entity_fits(frame, ent_keys[k], pool[i], cand)]
            if not ok:
                continue
            best = max(ok, key=lambda k: (len(stems & vocabs[k]) if vocabs[k] else 0, matrix[k][i], -k))
            if vocabs[best] and not len(stems & vocabs[best]) and not matrix[best][i] > 0:
                continue
        else:
            best = max(general, key=lambda k: (matrix[k][i], -k))
        if matrix[best][i] > 0 or frame is not None:
            home[i] = best
    for k, st in enumerate(subtopics):
        if st.get("scope") == "dataset":
            continue
        q = queries[k]
        def fit(i: int, k: int = k) -> float:
            """How well a sentence fits this part: the part's own topic words it (or its section heading) contains, with the keyword score as tie-breaker."""
            if frame is None or not vocabs[k]:
                return matrix[k][i]
            hits = len(rel._stems(pool[i]["text"]) & vocabs[k]) + 2 * len(pool[i].get("section_words", set()) & vocabs[k])
            lead = 6 if (pool[i].get("first") and pool[i]["cand"]["kind"] == "web" and cand_provider(pool[i]) == "wikipedia") else 0                      # the first sentence of an article says what it is about
            return hits + lead + matrix[k][i] / (1.0 + matrix[k][i])
        order = sorted((i for i, h in home.items() if h == k), key=lambda i: (-fit(i), i))
        chosen = 0
        for i in order:
            if chosen >= n_pass:
                break
            p = pool[i]
            cand = p["cand"]
            matched = len(set(q) & set(p["tokens"]))
            if frame is not None and cand["origin"] not in ("calculated", "model_prediction") and cand["kind"] in ("document", "web", "news"):
                identity = (cand["source"].get("identity") or {}).get("level") or "n/a"
                if not rel.sentence_ok(frame, vocabs[k] | rel.frame_vocab(plan, frame) if not vocabs[k] else vocabs[k], p["text"], source_identity=identity, section_words=p.get("section_words"), min_hits=1 if plan.get("follow_up") else rel.SENTENCE_MIN_VOCAB):
                    continue
            elif matched < min(min_match, len(set(q))) and cand["origin"] not in ("calculated", "model_prediction"):
                continue
            topic = topic_sets[k]
            if topic and cand["kind"] == "web" and cand["source"].get("provider") not in ("wikipedia", "arxiv"):
                # a passage from a general web page must mention the question's own topic words, not only the facet words ("definition", "statistics") that every page contains
                if len(topic & set(p["tokens"])) < (1 if len(topic) <= 3 else 2):
                    continue
            if per_source.get((st["id"], p["source"]), 0) >= (3 if (cand["source"].get("sections") and len(cand["source"].get("sections_used") or []) >= 3) else 2):
                continue                                                                  # a paper read in full may give a part three passages; any other source two
            tset = set(p["tokens"])
            # Repeats from the SAME source (overlapping chunks, repeated paragraphs) are dropped. The same sentence in a DIFFERENT source is kept: that is corroboration.
            if any(src == p["source"] and len(tset & o) / max(1, len(tset | o)) >= 0.85 for src, o in taken.get(st["id"], [])):
                continue
            taken.setdefault(st["id"], []).append((p["source"], tset))
            per_source[(st["id"], p["source"])] = per_source.get((st["id"], p["source"]), 0) + 1
            evidence.append(_evidence(len(evidence) + 1, st["id"], p, float(matrix[k][i])))
            used.append((p["key"], p["start"]))
            chosen += 1
    return {"sources": sources, "evidence": evidence, "removed": removed_all, "key_to_id": key_to_id, "used": used}


def _surrounding(cand: Dict[str, Any], p: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The sentence before and the sentence after the quoted one, from the same passage and only if it passed the safety screen, so a reader sees what the quote sits in. Display only: never part
    of the cited text."""
    before, after = p.get("neighbors") or ("", "")
    out = {k: " ".join(v.split())[:300] for k, v in (("before", before), ("after", after)) if v and _plain(v) and not DEPENDENT_FRAGMENT(v)}
    return out or None


def DEPENDENT_FRAGMENT(sentence: str) -> bool:
    return not sentence.strip()[:1].isupper()


def _plain(sentence: str) -> bool:
    return bool(TERMINAL.search(" ".join(sentence.split())))


def cand_provider(p: Dict[str, Any]) -> str:
    return str(p["cand"]["source"].get("provider") or "")


def _entity_fits(frame: Frame, key: Optional[str], p: Dict[str, Any], cand: Dict[str, Any]) -> bool:
    """Which part of a comparison may a sentence go to?
      a part about ONE compared entity: a sentence from a source about that entity, or a sentence from any other source that names that entity and not the other;
      the shared 'differences' part (key '*'): a sentence that names two or more of the compared entities.
    A sentence that names both is never filed under one of them: that is how two biographies get merged."""
    if not key:
        return True
    compared = frame.identities
    named = [e for e in compared if mentions(p["text"], e)]
    if key == "*":
        return len(named) >= 2
    ent = next((e for e in compared if e.key == key), None)
    if ent is None:
        return True
    src_ent = (cand["source"].get("identity") or {}).get("entity") or ""
    if src_ent == key:
        return True
    if src_ent and src_ent in {e.key for e in compared}:
        return False                                                                      # the source is about the other one
    return ent in named and len(named) == 1


def _evidence(n: int, subtopic: str, p: Dict[str, Any], score: float) -> Dict[str, Any]:
    cand, text = p["cand"], p["text"]
    base = dict(cand.get("citation") or {})
    if cand["kind"] == "document":
        citation = {**base, "start": base["start"] + p["start"], "end": base["start"] + p["end"], "quote_sha256": sha16(text)}
        location = {"page_start": base.get("page_start"), "page_end": base.get("page_end"), "section": base.get("section"), "chunk_id": base.get("chunk_id"), "page_estimated": base.get("page_estimated")}
    elif cand["kind"] in ("web", "news"):
        citation = {"kind": cand["kind"], "url": base.get("url"), "content_hash": base.get("content_hash"), "start": p["start"], "end": p["end"], "quote_sha256": sha16(text)}
        location = {"url": base.get("url")}
        sec = section_at(cand["source"].get("sections") or [], p["start"])
        if sec is not None:
            location.update({"section": sec.get("heading") or "Introduction (lead)", "section_path": sec.get("path") or [], "anchor": sec.get("anchor"), "page_start": sec.get("page_start"),
                             "page_end": sec.get("page_end"), "deep_url": deep_link(cand["source"], sec)})
            location = {k: v for k, v in location.items() if v not in (None, [], "")}
    else:
        citation = {**base, "quote_sha256": sha16(text)}
        location = {k: base.get(k) for k in ("kind", "model_id", "insight_id", "bundle_id", "run_id", "op", "claim") if base.get(k)}
    context = _surrounding(cand, p) if cand["kind"] in ("document", "web", "news") else None
    out = {"id": f"E{n}", "source": p["source"], "subtopic": subtopic, "quote": text, "location": location, "citation": citation, "origin": cand["origin"], "kind": statement_kind(text, cand["origin"]),
           "flags": {"hedged": bool(HEDGE.search(text)), "negated": bool(NEGATION.search(text)), "direction": "up" if UP.search(text) and not DOWN.search(text) else "down" if DOWN.search(text) and not UP.search(text) else None},
           "numbers": sorted(numbers_in(text))[:12], "score": round(score, 4), "tokens": sorted(set(tokenize(text)))}
    if context:
        out["context"] = context
    if cand.get("caveats"):
        out["caveats"] = list(cand["caveats"])
    if cand.get("facts"):
        out["facts"] = cand["facts"]                                                                       # the structured numbers behind a calculated sentence (used by the report and the checks)
    return out


def deep_link(source: Dict[str, Any], sec: Dict[str, Any]) -> Optional[str]:
    """A link that opens the original at the cited section: a Wikipedia section anchor on the exact revision read, an arXiv HTML anchor, or the PDF page. None when there is no way to say."""
    meta = source.get("meta") or {}
    anchor = sec.get("anchor")
    if source.get("provider") == "wikipedia":
        base = meta.get("permalink") or source.get("url")
        return f"{base}#{quote(str(anchor).replace(' ', '_'), safe='_-.~()%')}" if base and anchor else base
    if source.get("provider") == "arxiv":
        if meta.get("retrieved_via") == "arxiv-html" and meta.get("html_url"):
            return f"{meta['html_url']}#{quote(str(anchor), safe='_-.~')}" if anchor else meta["html_url"]
        if meta.get("pdf_url"):
            return f"{meta['pdf_url']}#page={sec['page_start']}" if sec.get("page_start") else meta["pdf_url"]
    return source.get("url")


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def same_claim(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two passages from different sources say the same thing: very similar words AND no difference in the numbers, the negation or the direction."""
    if a["source"] == b["source"] or _jaccard(set(a["tokens"]), set(b["tokens"])) < 0.5:
        return False
    if a.get("origin") == "calculated" or b.get("origin") == "calculated":
        return False                                                                # two datasets that give similar sentences are two calculations, not two independent sources agreeing
    if a["numbers"] and b["numbers"] and not set(a["numbers"]) & set(b["numbers"]):
        return False
    if a["flags"]["negated"] != b["flags"]["negated"]:
        return False
    return not (a["flags"]["direction"] and b["flags"]["direction"] and a["flags"]["direction"] != b["flags"]["direction"])


NEAR_IDENTICAL = 0.85                                                                  # the same threshold that drops repeats inside one source


def _near_identical(members: List[Dict[str, Any]]) -> bool:
    """True when every pair of passages from different sources uses almost the same words. Such agreement may be one source copying another rather than two independent reports."""
    pairs = [(a, b) for i, a in enumerate(members) for b in members[i + 1:] if a["source"] != b["source"]]
    return bool(pairs) and all(_jaccard(set(a["tokens"]), set(b["tokens"])) >= NEAR_IDENTICAL for a, b in pairs)


def build_findings(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group evidence that says the same thing (see same_claim) into one finding, per subtopic. Passages that differ in a number are NOT merged: that difference is reported as a conflict."""
    by_id = {e["id"]: e for e in evidence}
    findings: List[Dict[str, Any]] = []
    for st in dict.fromkeys(e["subtopic"] for e in evidence):
        group: List[Dict[str, Any]] = []
        for e in sorted((x for x in evidence if x["subtopic"] == st), key=lambda x: (-x["score"], x["id"])):
            home = next((f for f in group if any(same_claim(e, by_id[m]) for m in f["evidence"])), None)
            if home is not None:
                home["evidence"].append(e["id"])
            else:
                group.append({"id": "", "subtopic": st, "evidence": [e["id"]]})
        findings += group
    for i, f in enumerate(findings, start=1):
        f["id"] = f"F{i}"
        members = [by_id[m] for m in f["evidence"]]
        rep = max(members, key=lambda m: (m["origin"] == "user_provided", m["score"]))
        f.update(statement=rep["quote"], representative=rep["id"], sources=sorted({m["source"] for m in members}, key=lambda s: int(s[1:])), kind=rep["kind"], origin=rep["origin"],
                 numbers=rep["numbers"], agreement="corroborated" if len({m["source"] for m in members}) >= 2 else "single_source",
                 near_identical=_near_identical(members))
    return findings


def evidence_by_id(evidence: Sequence[Dict[str, Any]], eid: str) -> Dict[str, Any]:
    return next(e for e in evidence if e["id"] == eid)


def detect_conflicts(evidence: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Possible disagreements between different sources inside one subtopic. They are possibilities: two sentences can look alike and describe different things."""
    conflicts: List[Dict[str, Any]] = []
    fin_of = {eid: f["id"] for f in findings for eid in f["evidence"]}
    seen = set()
    for st in dict.fromkeys(e["subtopic"] for e in evidence):
        items = [e for e in evidence if e["subtopic"] == st and e["kind"] in ("stated", "uncertain", "computed")]
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a["source"] == b["source"] or fin_of.get(a["id"]) == fin_of.get(b["id"]):
                    continue
                if a.get("origin") == "calculated" and b.get("origin") == "calculated":
                    continue                                                        # two datasets have their own numbers: a different result is not a disagreement between sources
                words_a = {t for t in a["tokens"] if not t[:1].isdigit()}
                words_b = {t for t in b["tokens"] if not t[:1].isdigit()}
                shared = words_a & words_b
                sim = _jaccard(words_a, words_b)
                kind = None
                if a["numbers"] and b["numbers"] and not set(a["numbers"]) & set(b["numbers"]) and len(shared) >= 4 and sim >= 0.5:
                    kind, why = "numbers_differ", "These passages seem to describe the same thing but give different numbers."
                elif a["flags"]["negated"] != b["flags"]["negated"] and len(shared) >= 5 and sim >= 0.45:
                    kind, why = "opposite_claims", "One passage denies or negates what the other says."
                elif a["flags"]["direction"] and b["flags"]["direction"] and a["flags"]["direction"] != b["flags"]["direction"] and len(shared) >= 4 and sim >= 0.45:
                    kind, why = "opposite_direction", "One passage describes a rise and the other a fall."
                if kind and (a["id"], b["id"]) not in seen:
                    seen.add((a["id"], b["id"]))
                    conflicts.append({"id": f"X{len(conflicts) + 1}", "kind": kind, "subtopic": st, "status": "unresolved", "explanation": why,
                                      "hint": "Check the dates, units, places and definitions each source uses before deciding they truly disagree.",
                                      "statements": [{"evidence": e["id"], "source": e["source"], "text": e["quote"]} for e in (a, b)]})
                    if len(conflicts) >= 12:
                        return conflicts
    return conflicts


def conclude(plan: Dict[str, Any], findings: List[Dict[str, Any]], conflicts: List[Dict[str, Any]], evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    contested = {eid for c in conflicts for s in c["statements"] for eid in [s["evidence"]]}
    conclusions: List[Dict[str, Any]] = []
    gaps: List[Dict[str, str]] = []
    per = 4 if plan["level"] == "in_depth" else 3
    by_ev = {e["id"]: e for e in evidence}
    for st in plan["subtopics"]:
        fs = [f for f in findings if f["subtopic"] == st["id"]]
        is_dataset = st.get("scope") == "dataset"
        if not fs:
            gaps.append({"subtopic": st["id"], "title": st["title"], "note": "Nothing could be calculated for this part from the selected data." if is_dataset else "Nothing relevant was found for this part."})
            continue
        if is_dataset:
            fs.sort(key=lambda f: int(f["evidence"][0][1:]))                                    # the order the analysis produced them: most important first
        else:
            fs.sort(key=lambda f: (f["agreement"] != "corroborated", f["kind"] in ("uncertain", "opinion"), -len(f["evidence"]), f["id"]))
        for f in fs[: DATASET_CAP if is_dataset else per]:
            is_contested = any(e in contested for e in f["evidence"])
            calculated = f["origin"] == "calculated"
            confidence = ("calculated" if calculated else "prediction" if f["kind"] == "prediction" else "contested" if is_contested else "uncertain_wording" if f["kind"] in ("uncertain", "opinion")
                          else "well_supported" if f["agreement"] == "corroborated" else "single_source")
            caveats = []
            if calculated:
                caveats.extend(by_ev[f["representative"]].get("caveats") or [])
            if f["kind"] == "prediction":
                caveats.append("This is a prediction or forecast, not an observed fact.")
            if f["kind"] == "opinion":
                caveats.append("This is a view or recommendation held by the source.")
            if f["kind"] == "uncertain":
                caveats.append("The source hedges this statement.")
            if f["agreement"] == "single_source" and not calculated:
                caveats.append("Only one source in this research says this.")
            elif f.get("near_identical"):
                caveats.append("The sources use almost the same words, so one may have copied the other; this may not be independent agreement.")
            if is_contested:
                caveats.append("Another source may disagree; see the conflicts.")
            conclusions.append({"id": f"C{len(conclusions) + 1}", "subtopic": st["id"], "text": f["statement"], "finding": f["id"], "evidence": f["evidence"], "sources": f["sources"], "kind": f["kind"],
                                "confidence": confidence, "caveats": caveats, "authored_by": "extracted"})
    return {"conclusions": conclusions, "gaps": gaps}


def apply_reliability(sources: List[Dict[str, Any]], findings: List[Dict[str, Any]], now: Any, max_age: Optional[int]) -> None:
    from .sources import freshness, reliability
    corro: Dict[str, set] = {s["id"]: set() for s in sources}
    for f in findings:
        if len(f["sources"]) >= 2:
            for s in f["sources"]:
                corro[s] |= set(f["sources"]) - {s}
    for s in sources:
        s["freshness"] = freshness(s.get("published"), now, max_age) if s["kind"] in ("web", "news") else {"published": None, "age_days": None, "label": "not applicable", "outdated": None, "note": "Your own material."}
        s["reliability"] = reliability(s, len(corro[s["id"]]))
