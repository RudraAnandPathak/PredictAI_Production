"""Turns stored report content plus its evidence into one flat view that every exporter and the preview share: numbered references, in-text markers in the chosen citation style,
captions, the readiness line, and the warnings. Exporters never read the database."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..p7 import jload
from .citations import format_citation, in_text

WORD = re.compile(r"[A-Za-z][A-Za-z\-']{3,}")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
STOP = frozenset("this that with from have were been which their there about would could should into than then them they these those when what where while also such only more most some other over under".split())


def evidence_map(rows: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in rows:
        out[r["ref"]] = {"ref": r["ref"], "source_type": r["source_type"], "location": r["location"], "citation": jload(r["citation"], {}), "anchor": r["anchor"], "passage": r["passage"],
                         "calculation_ref": r["calculation_ref"], "dataset_version": r["dataset_version"], "model_version": r["model_version"], "source_date": r["source_date"], "retrieved_at": r["retrieved_at"],
                         "verification": r["verification"], "uncertainty": r["uncertainty"], "limitation": r["limitation"], "bundle_ref": r["bundle_ref"], "source_id": r["source_id"]}
    return out


def claim_warnings(text: str, kind: str, evidence_refs: Sequence[str], ev: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """Is this statement still backed by what it cites? A check on wording, not on meaning, and it says so."""
    out: List[str] = []
    if kind in ("fact", "calculation", "prediction") and not evidence_refs:
        out.append("This statement is presented as fact but cites no evidence.")
        return out
    passages = " ".join((ev.get(r) or {}).get("passage") or "" for r in evidence_refs)
    if not passages or kind == "process":
        return out
    words = {w.lower() for w in WORD.findall(text)} - STOP
    pw = {w.lower() for w in WORD.findall(passages)}
    if words and len(words & pw) / len(words) < 0.3:
        out.append("Few of this statement's words appear in the evidence it cites. Check that the evidence still supports it.")
    norm = lambda s: s.replace(",", "").rstrip("%")  # noqa: E731
    pnums = {norm(n) for n in NUMBER.findall(passages)}
    missing = [n for n in NUMBER.findall(text) if norm(n) not in pnums and len(norm(n)) > 1 and not re.fullmatch(r"(19|20)\d\d", norm(n))]
    if missing:
        out.append("It contains a number not found in the cited evidence: " + ", ".join(missing[:3]) + ".")
    return out


def iter_blocks(content: Mapping[str, Any]):
    for s in content.get("sections", []):
        for b in s.get("blocks", []):
            yield s, None, b
        for sub in s.get("subsections", []):
            for b in sub.get("blocks", []):
                yield s, sub, b


def cited_refs(content: Mapping[str, Any]) -> List[str]:
    seen: List[str] = []
    for _, _, b in iter_blocks(content):
        refs = [r for c in b.get("claims", []) for r in c.get("evidence", [])] if b["type"] == "paragraph" else b.get("evidence", [])
        for r in refs:
            if r not in seen:
                seen.append(r)
    return seen


def readiness(content: Mapping[str, Any], ev: Mapping[str, Mapping[str, Any]], stale: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    reasons: List[str] = []
    warnings: List[Dict[str, Any]] = []
    incomplete = [s["heading"] for s in content.get("sections", []) if s.get("status") == "incomplete"]
    incomplete += [f"{s['heading']} › {x['heading']}" for s in content.get("sections", []) for x in s.get("subsections", []) if x.get("status") == "incomplete"]
    for sec, sub, b in iter_blocks(content):
        if b["type"] != "paragraph":
            continue
        for i, c in enumerate(b["claims"]):
            for w in claim_warnings(c["text"], c.get("kind", "fact"), c.get("evidence", []), ev):
                warnings.append({"block": b["id"], "claim": i, "section": sec["heading"], "message": w})
    unverified = [r for r in cited_refs(content) if (ev.get(r) or {}).get("verification") in ("unverified", "checks_incomplete")]
    if incomplete:
        reasons.append(f"{len(incomplete)} section(s) have no supporting evidence yet: " + "; ".join(incomplete[:5]) + ("…" if len(incomplete) > 5 else ""))
    if warnings:
        reasons.append(f"{len(warnings)} statement(s) need checking against their evidence.")
    if stale:
        reasons.append(f"{len(stale)} section(s) rest on research that has since changed or been removed.")
    if unverified:
        reasons.append(f"{len(unverified)} cited item(s) were not verified: " + ", ".join(unverified[:5]) + ".")
    status = "incomplete" if incomplete else "needs_review" if (warnings or stale or unverified) else "ready"
    return {"status": status, "reasons": reasons, "warnings": warnings, "incomplete_sections": incomplete, "stale": list(stale)}


def build_view(report: Mapping[str, Any], version: Mapping[str, Any], ev: Mapping[str, Mapping[str, Any]], ready: Mapping[str, Any], *, style: Optional[str] = None) -> Dict[str, Any]:
    content = version["content"]
    settings = version["settings"]
    style = style or settings.get("citation_style", "apa")
    order = cited_refs(content)
    number = {r: i + 1 for i, r in enumerate(order)}
    marker = lambda refs: " ".join(in_text(style, number[r], (ev.get(r) or {}).get("citation", {})) for r in refs if r in number)  # noqa: E731
    sections = []
    for s in content.get("sections", []):
        def conv(blocks):
            out = []
            for b in blocks:
                if b["type"] == "paragraph":
                    out.append({"id": b["id"], "type": "paragraph", "claims": [{"text": c["text"], "marker": marker(c.get("evidence", [])), "kind": c.get("kind"), "caveats": c.get("caveats", []), "evidence": c.get("evidence", [])} for c in b["claims"]]})
                elif b["type"] in ("table", "figure"):
                    out.append({**b, "marker": marker(b.get("evidence", []))})
                else:
                    out.append(dict(b))
            return out
        sections.append({"id": s["id"], "heading": s["heading"], "status": s.get("status"), "why": s.get("why"), "blocks": conv(s.get("blocks", [])),
                         "subsections": [{"id": x["id"], "heading": x["heading"], "status": x.get("status"), "why": x.get("why"), "blocks": conv(x.get("blocks", []))} for x in s.get("subsections", [])]})
    refs = []
    for r in order:
        e = ev.get(r)
        if e is None:
            continue
        f = format_citation(e["citation"], style)
        refs.append({"number": number[r], "ref": r, "text": f["text"], "missing": f["missing"], "source_type": e["source_type"], "location": e["location"], "verification": e["verification"],
                     "calculation_ref": e["calculation_ref"], "dataset_version": e["dataset_version"], "model_version": e["model_version"], "limitation": e["limitation"], "uncertainty": e["uncertainty"]})
    return {"title": content.get("title") or report["title"], "subtitle": content.get("subtitle"), "report_id": report["id"], "version": version["version"], "report_type": report["report_type"],
            "created_at": report["created_at"], "version_created_at": version["created_at"], "author": version.get("author_name"), "citation_style": style, "settings": settings,
            "readiness": ready, "sections": sections, "references": refs, "register": [{"ref": r, **ev[r]} for r in order if r in ev]}
