"""Report generation, editing, versioning and export. Every report is built from evidence the platform already holds (saved research results, stored analysis runs, registered model versions,
exam analyses); creating a report never repeats the research or the calculation. Editing creates a new version and re-checks every statement against the evidence it cites."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from ...core.errors import AppError
from ..collab.access import AccessResolver
from ..identity.authorization import Principal
from ..p7 import E, Service7, choice, clean, conflict, invalid, jdump, jload, new_id, not_found, page, sha256_text
from ..storage import StorageError
from . import composer as C
from . import evidence as ev
from . import render
from .citations import STYLES
from .repository import ReportRepository
from .templates import BY_KEY, DEFAULT_SETTINGS, SECTION_KEYS, SYSTEM_TEMPLATES, validate_settings, validate_spec

SOURCE_KINDS = ("research", "dataset", "prediction", "exam", "scenario", "combined", "blank")
MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "md": "text/markdown; charset=utf-8", "csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "png": "image/png"}
EXPORT_NOTE = "A downloaded copy cannot be recalled. Removing someone's access stops future downloads, not copies already saved."


class ReportService(Service7):
    def __init__(self, ctx: Any, ws: Any, access: AccessResolver, research: Any, registry: Any, exam: Any = None, intelligence: Any = None) -> None:
        super().__init__(ctx, ws)
        self.repo = ReportRepository(ctx.conn)
        self.access, self.research, self.registry, self.exam, self.intel = access, research, registry, exam, intelligence
        self.storage = ws.storage

    # ------------------------------------------------------------------ templates
    def templates(self, principal: Principal) -> Dict[str, Any]:
        system = [{"key": t["key"], "name": t["name"], "report_type": t["report_type"], "version": t["version"], "about": t["about"], "system": True, "spec": t["spec"]} for t in SYSTEM_TEMPLATES]
        mine = [{"key": r["key"], "name": r["name"], "report_type": r["report_type"], "version": r["version"], "system": False, "spec": jload(r["spec"], {})} for r in self.repo.user_templates(principal.user_id)]
        return {"system": system, "mine": mine, "section_keys": SECTION_KEYS, "citation_styles": list(STYLES)}

    def create_template(self, principal: Principal, *, name: Any, spec: Any, base: Any = None, report_type: Any = None) -> Dict[str, Any]:
        nm = clean(name, "name", maximum=80)
        spec = validate_spec(spec)
        key = "u_" + "".join(ch if ch.isalnum() else "_" for ch in nm.lower())[:40]
        prior = self.repo.template(principal.user_id, key)
        version = (prior["version"] + 1) if prior else 1
        self.repo.insert_template(id=new_id(), owner_user_id=principal.user_id, key=key, name=nm, report_type=report_type or "custom", version=version, spec=jdump(spec), created_at=self.now())
        return {"key": key, "name": nm, "version": version, "spec": spec}

    def _template(self, principal: Principal, key: Any) -> Dict[str, Any]:
        if key in BY_KEY:
            t = BY_KEY[key]
            return {"key": t["key"], "version": t["version"], "report_type": t["report_type"], "spec": t["spec"]}
        row = self.repo.template(principal.user_id, key) if isinstance(key, str) else None
        if row is None:
            raise not_found("template")
        return {"key": row["key"], "version": row["version"], "report_type": row["report_type"], "spec": jload(row["spec"], {})}

    # ------------------------------------------------------------------ creation
    def create(self, principal: Principal, project_id: str, *, source_kind: Any, source: Any = None, template: Any = None, title: Any = None, settings: Any = None) -> Dict[str, Any]:
        self.limit("p7_report:user", principal)
        self.project(principal, project_id, writable=True)
        kind = choice(source_kind, "source_kind", SOURCE_KINDS)
        source = source if isinstance(source, dict) else {}
        default_key = {"research": "research_report", "dataset": "data_analysis", "prediction": "predictive_modelling", "exam": "exam_intelligence", "scenario": "scenario_analysis",
                       "combined": "research_report", "blank": "research_report"}[kind]
        tpl = self._template(principal, template or default_key)
        st = validate_settings(settings, tpl["spec"]["settings"])
        es = ev.EvidenceSet()
        pool: Dict[str, Dict[str, Any]] = {}
        refs: Dict[str, Any] = {}
        label = None
        if kind in ("research", "combined") and source.get("bundle_ids"):
            bundles = [self._load_bundle(principal, project_id, bid) for bid in self._ids(source, "bundle_ids")]
            pool = _merge(pool, C.research_pool(bundles, es))
            refs["bundle_ids"] = [b["id"] for b in bundles]
            label = bundles[0]["question"]
        if kind in ("dataset", "combined") and source.get("run_ids"):
            runs, assets = [], {}
            for rid in self._ids(source, "run_ids"):
                run = self.ws.analytics.get_run(principal, rid)
                a = self.ws.assets.get_owned(principal, run["asset_id"])
                if a["project_id"] != project_id:
                    raise conflict("PROJECT_MISMATCH", "All sources of a report must come from the same project.")
                runs.append(run)
                assets[run["asset_id"]] = a
            pool = _merge(pool, C.dataset_pool(runs, assets, es))
            refs["run_ids"] = [r["id"] for r in runs]
            label = label or f"Analysis of {next(iter(assets.values()))['name']}"
        if kind in ("prediction", "combined") and source.get("version_ids"):
            versions = []
            for vid in self._ids(source, "version_ids"):
                v = self.registry.get_version(principal, vid)
                m = self.registry.get(principal, v["model_id"])
                if m["project_id"] != project_id:
                    raise conflict("PROJECT_MISMATCH", "All sources of a report must come from the same project.")
                versions.append({**v, "name": m["name"]})
            pool = _merge(pool, C.model_pool(versions, es))
            refs["version_ids"] = [v["id"] for v in versions]
            label = label or f"{versions[0]['name']} model report"
        if kind in ("exam", "combined") and source.get("analysis_ids") and self.exam is not None:
            pool = _merge(pool, self.exam.report_pool(principal, project_id, self._ids(source, "analysis_ids"), es))
            refs["analysis_ids"] = self._ids(source, "analysis_ids")
            label = label or "Exam Intelligence report"
        if kind in ("scenario", "combined") and source.get("adv_run_ids") and self.intel is not None:
            pool = _merge(pool, self.intel.report_pool(principal, project_id, self._ids(source, "adv_run_ids"), es))
            refs["adv_run_ids"] = self._ids(source, "adv_run_ids")
            label = label or "Scenario analysis report"
        if kind != "blank" and not refs:
            raise invalid("source", "Choose at least one saved result to build the report from.")
        sections = C.assemble(tpl["spec"]["sections"], pool)
        content = {"title": clean(title, "title", maximum=200, required=False) or label or "Untitled report", "subtitle": None, "sections": sections}
        rid = new_id()
        now = self.now()
        with self.ctx.conn:
            self.repo.insert_report(id=rid, project_id=project_id, owner_user_id=principal.user_id, title=content["title"], report_type=tpl["report_type"], source_kind=kind, source_ref=jdump(refs),
                                    current_version=1, status="active", created_at=now, updated_at=now)
            for item in es.items:
                self.repo.insert_evidence(rid, item, new_id())
            self._save_version(rid, 1, content, st, tpl, principal, "Created from saved results", [])
        self.audit(E.REPORT_CREATED, principal, rid, kind=kind, sections=len(sections))
        return self.get(principal, rid)

    @staticmethod
    def _ids(source: Dict[str, Any], key: str) -> List[str]:
        ids = source.get(key)
        if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or not all(isinstance(i, str) and 0 < len(i) <= 64 for i in ids):
            raise invalid(f"source.{key}", "Choose one to ten saved results.")
        return list(dict.fromkeys(ids))

    def _load_bundle(self, principal: Principal, project_id: str, bundle_id: str) -> Dict[str, Any]:
        got = self.research.get_bundle(principal, bundle_id)
        if not got.get("integrity_ok", True):
            raise AppError("BUNDLE_DAMAGED", "This research result failed its integrity check, so a report cannot be built from it.", 409)
        b = got["bundle"]
        if b["project_id"] != project_id:
            raise conflict("PROJECT_MISMATCH", "All sources of a report must come from the same project.")
        return b

    def _save_version(self, rid: str, number: int, content: Dict[str, Any], settings: Dict[str, Any], tpl: Dict[str, Any], principal: Principal, note: str, stale: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        evmap = render.evidence_map(self.repo.evidence(rid))
        ready = render.readiness(content, evmap, stale)
        self.repo.insert_version(id=new_id(), report_id=rid, version=number, content=jdump(content), settings=jdump(settings), template_key=tpl.get("key"), template_version=tpl.get("version"),
                                 readiness=jdump({k: ready[k] for k in ("status", "reasons", "warnings", "incomplete_sections")}), content_sha256=sha256_text(jdump(content)), author_user_id=principal.user_id, note=note, created_at=self.now())
        return ready

    # ------------------------------------------------------------------ reading
    def list(self, principal: Principal, project_id: Optional[str] = None, *, limit: Any = None, offset: Any = None) -> Dict[str, Any]:
        lim, off = page(limit, offset)
        if project_id:
            self.project(principal, project_id)
        rows, total = self.repo.reports(principal.user_id, project_id, lim, off)
        items = [self._summary(r) for r in rows]
        return {"items": items, "total": total, "limit": lim, "offset": off, "shared_with_me": self.access.shared_with(principal, "report")}

    def _summary(self, r) -> Dict[str, Any]:
        v = self.repo.version(r["id"], r["current_version"])
        return {"id": r["id"], "project_id": r["project_id"], "title": r["title"], "report_type": r["report_type"], "source_kind": r["source_kind"], "version": r["current_version"],
                "readiness": (jload(v["readiness"], {}) or {}).get("status"), "updated_at": r["updated_at"], "created_at": r["created_at"]}

    def _row(self, principal: Principal, report_id: str, need: str):
        acc = self.access.resolve(principal, "report", report_id, need)
        return self.repo.report(report_id), acc

    def get(self, principal: Principal, report_id: str, version: Any = None, *, style: Any = None) -> Dict[str, Any]:
        r, acc = self._row(principal, report_id, "view")
        number = r["current_version"] if version is None else version
        if isinstance(number, bool) or not isinstance(number, int):
            raise invalid("version", "Use a version number.")
        v = self.repo.version(report_id, number)
        if v is None:
            raise not_found("report version")
        if style is not None:
            choice(style, "style", STYLES)
        evmap = render.evidence_map(self.repo.evidence(report_id))
        content = jload(v["content"], {})
        stale = self.stale(principal, report_id) if number == r["current_version"] else []
        ready = render.readiness(content, evmap, stale)
        author = self.user_email(v["author_user_id"])
        vv = {"version": v["version"], "content": content, "settings": jload(v["settings"], {}), "created_at": v["created_at"], "author_name": author}
        view = render.build_view(r, vv, evmap, ready, style=style)
        return {"id": r["id"], "project_id": r["project_id"], "title": content.get("title") or r["title"], "report_type": r["report_type"], "source_kind": r["source_kind"], "source": jload(r["source_ref"], {}),
                "version": v["version"], "current_version": r["current_version"], "template": {"key": v["template_key"], "version": v["template_version"]}, "content": content, "settings": vv["settings"],
                "evidence": evmap, "readiness": ready, "view": view, "your_access": acc.level, "content_sha256": v["content_sha256"], "editable": acc.allows("edit") and number == r["current_version"],
                "restriction_note": "This report was built from restricted project data and keeps the same access rules as that project."}

    def versions(self, principal: Principal, report_id: str) -> Dict[str, Any]:
        r, _ = self._row(principal, report_id, "view")
        exports = self.repo.exports(report_id)
        return {"current_version": r["current_version"], "items": [{"version": v["version"], "created_at": v["created_at"], "author": v["author_user_id"], "note": v["note"], "template": v["template_key"],
                                                                   "readiness": (jload(v["readiness"], {}) or {}).get("status"), "content_sha256": v["content_sha256"],
                                                                   "exports": [{"id": e["id"], "format": e["format"], "created_at": e["created_at"], "sha256": e["sha256"]} for e in exports if e["version"] == v["version"]]}
                                                                  for v in self.repo.versions(report_id)]}

    def diff(self, principal: Principal, report_id: str, a: int, b: int) -> Dict[str, Any]:
        self._row(principal, report_id, "view")
        va, vb = self.repo.version(report_id, a), self.repo.version(report_id, b)
        if va is None or vb is None:
            raise not_found("report version")
        flat = lambda row: {x["id"]: x for _, _, x in render.iter_blocks(jload(row["content"], {}))}  # noqa: E731
        fa, fb = flat(va), flat(vb)
        changed = [bid for bid in fb if bid in fa and jdump(fa[bid]) != jdump(fb[bid])]
        return {"from": a, "to": b, "added_blocks": [x for x in fb if x not in fa], "removed_blocks": [x for x in fa if x not in fb], "changed_blocks": changed,
                "settings_changed": jdump(jload(va["settings"])) != jdump(jload(vb["settings"]))}

    # ------------------------------------------------------------------ staleness
    def stale(self, principal: Principal, report_id: str) -> List[Dict[str, Any]]:
        """Sections that cite research which has since been replaced by a newer run or deleted. The report is never rewritten; the author decides."""
        r = self.repo.report(report_id)
        if r is None:
            return []
        v = self.repo.version(report_id, r["current_version"])
        content = jload(v["content"], {})
        bad: Dict[str, str] = {}
        for row in self.repo.evidence(report_id):
            if not row["bundle_ref"] or row["bundle_ref"] in bad:
                continue
            st = self.repo.bundle_state(row["bundle_ref"])
            if st is None:
                bad[row["bundle_ref"]] = "the research result this was built from was deleted"
            else:
                newer = self.repo.newer_bundle(st["session_id"], st["number"])
                if newer:
                    bad[row["bundle_ref"]] = f"a newer run (run {newer['number']}) of the same research exists"
        out = []
        if not bad:
            return out
        by_ref = {row["ref"]: row["bundle_ref"] for row in self.repo.evidence(report_id)}
        for s in content.get("sections", []):
            hit = {by_ref.get(ref) for _, _, b in [(s, None, b) for b in s.get("blocks", [])] + [(s, x, b) for x in s.get("subsections", []) for b in x.get("blocks", [])]
                   for ref in (([r for c in b.get("claims", []) for r in c.get("evidence", [])]) if b["type"] == "paragraph" else b.get("evidence", []))} & set(bad)
            if hit:
                out.append({"section_id": s["id"], "heading": s["heading"], "reason": "; ".join(sorted({bad[h] for h in hit}))})
        return out

    # ------------------------------------------------------------------ editing
    def edit(self, principal: Principal, report_id: str, base_version: Any, ops: Any, *, note: Any = None) -> Dict[str, Any]:
        self.limit("p7_report:user", principal)
        r, acc = self._row(principal, report_id, "edit")
        if base_version != r["current_version"]:
            raise conflict("VERSION_CONFLICT", f"The report is now at version {r['current_version']}; you edited version {base_version}. Reload it and apply your change again.")
        if not isinstance(ops, list) or not 1 <= len(ops) <= 50:
            raise invalid("ops", "Send one to fifty edit operations.")
        v = self.repo.version(report_id, r["current_version"])
        content = jload(v["content"], {})
        settings = jload(v["settings"], {})
        before = {bid: copy.deepcopy(b) for _, _, b in render.iter_blocks(content) for bid in [b["id"]]}
        evrows = self.repo.evidence(report_id)
        evmap = render.evidence_map(evrows)
        new_refs: List[Dict[str, Any]] = []
        title = None
        for op in ops:
            if not isinstance(op, dict):
                raise invalid("ops", "Each operation is an object.")
            title = self._apply(op, content, settings, evmap, new_refs, report_id) or title
        for item in new_refs:
            self.repo.insert_evidence(report_id, item, new_id())
        nxt = r["current_version"] + 1
        edit_warnings = self._edit_warnings(content, before, evmap)
        with self.ctx.conn:
            tpl = {"key": v["template_key"], "version": v["template_version"]}
            ready = self._save_version(report_id, nxt, content, settings, tpl, principal, clean(note, "note", maximum=300, required=False) or "Edited", [])
            self.repo.bump(report_id, nxt, content.get("title") if title else None, self.now())
        self.audit(E.REPORT_EDITED, principal, report_id, version=nxt, ops=len(ops))
        out = self.get(principal, report_id)
        out["edit_warnings"] = edit_warnings
        return out

    def _edit_warnings(self, content: Dict[str, Any], before: Dict[str, Any], evmap: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for sec, sub, b in render.iter_blocks(content):
            if b["type"] != "paragraph":
                continue
            old = before.get(b["id"])
            for i, c in enumerate(b["claims"]):
                changed = old is None or i >= len(old.get("claims", [])) or old["claims"][i]["text"] != c["text"] or old["claims"][i].get("evidence") != c.get("evidence")
                if not changed:
                    continue
                for w in render.claim_warnings(c["text"], c.get("kind", "fact"), c.get("evidence", []), evmap):
                    out.append({"block": b["id"], "claim": i, "section": sec["heading"], "message": w})
        return out

    def _find_block(self, content: Dict[str, Any], block_id: Any):
        for s in content["sections"]:
            for container in [s] + list(s.get("subsections", [])):
                for i, b in enumerate(container["blocks"]):
                    if b["id"] == block_id:
                        return container, i, b
        raise not_found("block")

    def _section(self, content: Dict[str, Any], sid: Any):
        for s in content["sections"]:
            if s["id"] == sid:
                return s
            for x in s.get("subsections", []):
                if x["id"] == sid:
                    return x
        raise not_found("section")

    def _next_block_id(self, content: Dict[str, Any], sid: str) -> str:
        used = {b["id"] for _, _, b in render.iter_blocks(content)}
        n = 1
        while f"{sid}-b{n}" in used:
            n += 1
        return f"{sid}-b{n}"

    def _refs_ok(self, refs: Any, evmap: Dict[str, Any], new_refs: List[Dict[str, Any]]) -> List[str]:
        if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
            raise invalid("evidence", "Use a list of evidence references such as [\"E1\"].")
        known = set(evmap) | {n["ref"] for n in new_refs}
        bad = [x for x in refs if x not in known]
        if bad:
            raise invalid("evidence", f"Unknown evidence reference: {bad[0]}. Add the reference first.")
        return list(dict.fromkeys(refs))

    def _apply(self, op: Dict[str, Any], content: Dict[str, Any], settings: Dict[str, Any], evmap: Dict[str, Any], new_refs: List[Dict[str, Any]], report_id: str) -> Optional[str]:
        kind = op.get("op")
        if kind == "set_title":
            content["title"] = clean(op.get("text"), "text", maximum=200)
            return content["title"]
        if kind == "set_subtitle":
            content["subtitle"] = clean(op.get("text"), "text", maximum=200, required=False)
        elif kind == "set_heading":
            self._section(content, op.get("section_id"))["heading"] = clean(op.get("text"), "text", maximum=160)
        elif kind == "add_section":
            n = len(content["sections"]) + 1
            used = {s["id"] for s in content["sections"]}
            while f"s{n}" in used:
                n += 1
            at = op.get("index", len(content["sections"]))
            if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= len(content["sections"]):
                raise invalid("index", "Use a position from 0 to the number of sections.")
            content["sections"].insert(at, {"id": f"s{n}", "key": "custom", "heading": clean(op.get("heading"), "heading", maximum=160), "status": "complete", "why": None, "blocks": [], "subsections": []})
        elif kind == "remove_section":
            sid = op.get("section_id")
            self._section(content, sid)
            content["sections"] = [s for s in content["sections"] if s["id"] != sid]
        elif kind == "move_section":
            sid, to = op.get("section_id"), op.get("index")
            idx = next((i for i, s in enumerate(content["sections"]) if s["id"] == sid), None)
            if idx is None:
                raise not_found("section")
            if isinstance(to, bool) or not isinstance(to, int) or not 0 <= to < len(content["sections"]):
                raise invalid("index", "Use a position within the report.")
            content["sections"].insert(to, content["sections"].pop(idx))
        elif kind == "add_claim":
            sec = self._section(content, op.get("section_id"))
            ck = choice(op.get("kind", "interpretation"), "kind", ("fact", "calculation", "prediction", "interpretation", "suggestion", "process", "note"))
            claim = C.claim(clean(op.get("text"), "text", maximum=1500), self._refs_ok(op.get("evidence", []), evmap, new_refs), ck)
            paras = [b for b in sec["blocks"] if b["type"] == "paragraph"]
            if paras:
                paras[-1]["claims"].append(claim)
            else:
                sec["blocks"].append({"id": self._next_block_id(content, sec["id"]), **C.para([claim])})
            sec["status"] = "complete" if sec.get("status") == "incomplete" and ck != "note" and (claim["evidence"] or ck in ("suggestion", "interpretation", "note")) else sec.get("status")
        elif kind == "edit_claim":
            _, _, b = self._find_block(content, op.get("block_id"))
            i = op.get("claim")
            if b["type"] != "paragraph" or isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(b["claims"]):
                raise invalid("claim", "Choose a statement in a paragraph.")
            if "text" in op:
                b["claims"][i]["text"] = clean(op["text"], "text", maximum=1500)
            if "evidence" in op:
                b["claims"][i]["evidence"] = self._refs_ok(op["evidence"], evmap, new_refs)
            if "kind" in op:
                b["claims"][i]["kind"] = choice(op["kind"], "kind", ("fact", "calculation", "prediction", "interpretation", "suggestion", "process", "note"))
        elif kind == "remove_claim":
            _, _, b = self._find_block(content, op.get("block_id"))
            i = op.get("claim")
            if b["type"] != "paragraph" or isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(b["claims"]):
                raise invalid("claim", "Choose a statement in a paragraph.")
            del b["claims"][i]
        elif kind == "remove_block":
            cont, i, _ = self._find_block(content, op.get("block_id"))
            del cont["blocks"][i]
        elif kind == "add_table":
            sec = self._section(content, op.get("section_id"))
            cols, rows = op.get("columns"), op.get("rows")
            if not isinstance(cols, list) or not 1 <= len(cols) <= 12 or not isinstance(rows, list) or len(rows) > 200 or not all(isinstance(r, list) and len(r) == len(cols) for r in rows):
                raise invalid("rows", "Give 1 to 12 columns and rows with the same number of cells.")
            sec["blocks"].append({"id": self._next_block_id(content, sec["id"]), **C.table(clean(op.get("caption"), "caption", maximum=200, required=False) or "", [str(c)[:80] for c in cols],
                                                                                           [[str(x)[:200] if not isinstance(x, (int, float)) else x for x in r] for r in rows], self._refs_ok(op.get("evidence", []), evmap, new_refs))})
        elif kind == "add_figure":
            sec = self._section(content, op.get("section_id"))
            labels, values = op.get("labels"), op.get("values")
            if not isinstance(labels, list) or not isinstance(values, list) or len(labels) != len(values) or not 1 <= len(labels) <= 40 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
                raise invalid("values", "Give the same number (1 to 40) of labels and numeric values.")
            sec["blocks"].append({"id": self._next_block_id(content, sec["id"]), **C.figure(clean(op.get("caption"), "caption", maximum=200, required=False) or "", clean(op.get("title"), "title", maximum=120, required=False) or "",
                                                                                              labels, values, self._refs_ok(op.get("evidence", []), evmap, new_refs), kind=choice(op.get("chart", "bar"), "chart", ("bar", "line")))})
        elif kind == "set_caption":
            _, _, b = self._find_block(content, op.get("block_id"))
            if b["type"] not in ("table", "figure"):
                raise invalid("block_id", "Only tables and figures have captions.")
            b["caption"] = clean(op.get("text"), "text", maximum=200, required=False) or ""
        elif kind == "add_note":
            sec = self._section(content, op.get("section_id"))
            sec["blocks"].append({"id": self._next_block_id(content, sec["id"]), **C.note(clean(op.get("text"), "text", maximum=1000))})
        elif kind == "add_reference":
            c = op.get("citation") if isinstance(op.get("citation"), dict) else {}
            n = self.repo.next_ref(report_id) + len(new_refs)
            meta = {"kind": "manual", "title": clean(c.get("title"), "citation.title", maximum=300), "authors": [clean(a, "citation.authors", maximum=120) for a in (c.get("authors") or [])][:10],
                    "date": clean(c.get("date"), "citation.date", maximum=30, required=False), "publisher": clean(c.get("publisher"), "citation.publisher", maximum=150, required=False),
                    "url": clean(c.get("url"), "citation.url", maximum=500, required=False), "doi": clean(c.get("doi"), "citation.doi", maximum=120, required=False)}
            new_refs.append({"ref": f"E{n}", "source_id": None, "source_type": "manual", "location": None, "citation": meta, "anchor": None, "passage": clean(op.get("passage"), "passage", maximum=1200, required=False),
                             "calculation_ref": None, "dataset_version": None, "model_version": None, "source_date": meta["date"], "retrieved_at": None, "verification": "user_entered", "uncertainty": None,
                             "limitation": "Entered by the author; PredictAI has not checked it.", "bundle_ref": None})
            evmap[f"E{n}"] = {**new_refs[-1], "citation": meta}
        elif kind == "set_settings":
            settings.update(validate_settings(op.get("settings"), settings))
        elif kind == "mark_section":
            sec = self._section(content, op.get("section_id"))
            sec["status"] = choice(op.get("status"), "status", ("complete", "incomplete"))
            sec["why"] = clean(op.get("why"), "why", maximum=300, required=False)
        else:
            raise invalid("op", f"Unknown edit '{kind}'.")
        return None

    def delete(self, principal: Principal, report_id: str) -> Dict[str, Any]:
        r, acc = self._row(principal, report_id, "edit")
        if not acc.is_owner:
            raise not_found("report")
        with self.ctx.conn:
            self.repo.mark_deleted(report_id, self.now())
            self.conn.execute("UPDATE ws_resources SET revoked_at = ?, revoked_by = ? WHERE resource_type = 'report' AND resource_id = ? AND revoked_at IS NULL", (self.now(), principal.user_id, report_id))
        return {"deleted": True, "note": "Earlier exports remain in storage for the audit trail but can no longer be downloaded."}

    # ------------------------------------------------------------------ export
    def export(self, principal: Principal, report_id: str, fmt: Any, *, version: Any = None, style: Any = None, origin: str = "manual", actor: Optional[Principal] = None) -> Dict[str, Any]:
        from . import exports as X
        self.limit("p7_export:user", principal)
        fmt = choice(fmt, "format", tuple(MIME))
        got = self.get(principal, report_id, version, style=style)
        try:
            data, ext = X.build(fmt, got["view"], got["content"])
        except X.ExportUnavailable as exc:
            raise AppError("EXPORT_UNAVAILABLE", str(exc), 501) from None
        digest = hashlib.sha256(data).hexdigest()
        eid = new_id()
        key = f"reports/{got['project_id']}/{report_id}/{eid}.{ext}"
        self.storage.put(key, [data], overwrite=False)
        with self.ctx.conn:
            self.repo.insert_export(id=eid, report_id=report_id, version=got["version"], format=fmt, storage_key=key, sha256=digest, bytes=len(data), readiness=jdump({"status": got["readiness"]["status"], "reasons": got["readiness"]["reasons"]}),
                                    actor_user_id=(actor or principal).user_id, origin=origin, created_at=self.now())
        self.audit(E.REPORT_EXPORTED, principal, report_id, format=fmt, version=got["version"], origin=origin)
        return {"export_id": eid, "format": fmt, "version": got["version"], "sha256": digest, "bytes": len(data), "readiness": got["readiness"]["status"], "note": EXPORT_NOTE,
                "filename": X.filename(got["view"]["title"], got["version"], ext)}

    def exports(self, principal: Principal, report_id: str) -> Dict[str, Any]:
        self._row(principal, report_id, "view")
        return {"items": [{"id": e["id"], "format": e["format"], "version": e["version"], "sha256": e["sha256"], "bytes": e["bytes"], "created_at": e["created_at"], "origin": e["origin"],
                           "readiness": jload(e["readiness"], {}).get("status")} for e in self.repo.exports(report_id)], "note": EXPORT_NOTE}

    def download(self, principal: Principal, export_id: str) -> tuple:
        from . import exports as X
        e = self.repo.export(export_id)
        if e is None:
            raise not_found("export")
        self._row(principal, e["report_id"], "view")                                  # access is checked again on every download: a revoked person gets nothing
        try:
            data = self.storage.read_bytes(e["storage_key"])
        except StorageError:
            raise not_found("export") from None
        if hashlib.sha256(data).hexdigest() != e["sha256"]:
            raise AppError("EXPORT_DAMAGED", "This export failed its integrity check.", 409)
        r = self.conn.execute("SELECT title FROM rep_reports WHERE id = ?", (e["report_id"],)).fetchone()
        return X.filename(r["title"], e["version"], e["format"]), MIME[e["format"]], data


def _merge(pool: Dict[str, Dict[str, Any]], more: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    for k, v in more.items():
        if k not in pool or (pool[k]["status"] == "incomplete" and not pool[k]["blocks"] and not pool[k]["subsections"]):
            pool[k] = v
        elif v["status"] != "incomplete" or v["blocks"] or v["subsections"]:
            pool[k]["blocks"] += v["blocks"]
            pool[k]["subsections"] += v["subsections"]
            pool[k]["status"] = "complete"
    return pool
