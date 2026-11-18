"""Research reports (R8): create from a finished analysis, edit, regenerate, version, export.

Rules that protect the reader (all enforced here, all tested):
 * A report is built only from a SUCCEEDED analysis of the same workspace, optionally restricted to a subset of its sources.
 * Quoted evidence cannot be edited (only removed): a quotation is either exact source text or it is not there.
 * Text the person edits or adds is relabelled `author`, so tool analysis and the person's own words stay distinguishable.
 * Every edit or regeneration first stores the previous version; regeneration keeps what the person wrote.
 * Citation labels must refer to sources of the report; references come only from stored metadata.
 * Deleting a source redacts its quotations from every report (the report itself and the author's text stay) instead of leaving its text behind."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from ....db import transaction
from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...identity.errors import unavailable
from ...identity.tokens import new_id
from ...p7 import choice, jload
from ...workspace.errors import conflict, invalid, not_found
from ..core.common import EV_REPORT
from . import builder, export as exporter

MAX_BLOCKS_PER_SECTION, MAX_SECTIONS, MAX_TEXT, MAX_VERSIONS = 200, 40, 20000, 20


def _clean(s: Any, field: str, maximum: int, required: bool = False) -> str:
    if s is None:
        s = ""
    if not isinstance(s, str) or len(s) > maximum or (required and not s.strip()):
        raise invalid(field, f"Use 1 to {maximum} characters." if required else f"Use at most {maximum} characters.")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)


class ReportService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    def _ws(self, principal: Principal, workspace_id: Any, writable: bool = False) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("research workspace")
        if writable:
            self.ctx.limiter.hit("ri_report:user", principal.user_id)
            if r["status"] != "active":
                raise conflict("WORKSPACE_ARCHIVED", "This workspace is archived. Restore it to work on reports.")
        return r

    def _row(self, principal: Principal, ws: str, rid: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_reports WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (rid if isinstance(rid, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("report")
        return r

    @staticmethod
    def _summary(r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "workspace_id": r["workspace_id"], "analysis_id": r["analysis_id"], "report_type": r["report_type"], "type_label": builder.TYPES[r["report_type"]][0], "title": r["title"],
                "version": r["version"], "edited": bool(r["edited"]), "source_count": len(jload(r["sources"], [])), "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def _full(self, r: sqlite3.Row) -> Dict[str, Any]:
        return {**self._summary(r), "notice": r["notice"], "sources": jload(r["sources"], []), "sections": jload(r["sections"], [])}

    def types(self) -> List[Dict[str, Any]]:
        return [{"key": k, "label": v[0], "sections": [builder.HEADINGS[s] for s in v[1]]} for k, v in builder.TYPES.items()]

    # ------------------------------------------------------------------ create / read
    def create(self, principal: Principal, workspace_id: str, *, analysis_id: Any, report_type: Any, title: Any = None, source_ids: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        rtype = choice(report_type, "report_type", tuple(builder.TYPES))
        a = self.conn.execute("SELECT * FROM ri_analyses WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (analysis_id if isinstance(analysis_id, str) else "", ws["id"], principal.user_id)).fetchone()
        if a is None:
            raise not_found("analysis")
        if a["status"] != "succeeded":
            raise conflict("ANALYSIS_NOT_READY", "Wait for the analysis to finish before building a report from it.")
        result = jload(a["result"], {})
        keep = self._keep(result, source_ids)
        try:
            built = builder.build(rtype, result, keep)
        except ValueError:
            raise invalid("source_ids", "Choose at least one source of the analysis.")
        title = _clean(title, "title", 200).strip() or f"{builder.TYPES[rtype][0]}: {a['title']}"[:200]
        rid, now = new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_reports (id, workspace_id, owner_user_id, analysis_id, report_type, title, notice, sources, sections, version, edited, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, ws["id"], principal.user_id, a["id"], rtype, title, built["notice"], json.dumps(built["sources"]), json.dumps(built["sections"]), 1, 0, now, now))
        self.ctx.audit.record(EV_REPORT, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "report_id": rid, "action": "created", "type": rtype, "sources": len(built["sources"])})
        return self._full(self._row(principal, ws["id"], rid))

    @staticmethod
    def _keep(result: Dict[str, Any], source_ids: Any) -> Optional[List[str]]:
        if source_ids is None:
            return None
        if not isinstance(source_ids, list) or not all(isinstance(x, str) for x in source_ids):
            raise invalid("source_ids", "Choose sources from the analysis.")
        by_id = {s["source_id"]: s["label"] for s in result["sources"]}
        bad = [x for x in source_ids if x not in by_id]
        if bad:
            raise invalid("source_ids", "Every chosen source must be part of the analysis.")
        return [by_id[x] for x in dict.fromkeys(source_ids)]

    def list(self, principal: Principal, workspace_id: str) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        return [self._summary(r) for r in self.conn.execute("SELECT * FROM ri_reports WHERE workspace_id = ? AND owner_user_id = ? ORDER BY updated_at DESC, rowid DESC", (ws["id"], principal.user_id)).fetchall()]

    def list_all(self, principal: Principal, limit: int = 100) -> List[Dict[str, Any]]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        rows = self.conn.execute("SELECT r.*, w.name AS ws_name FROM ri_reports r JOIN rw_workspaces w ON w.id = r.workspace_id WHERE r.owner_user_id = ? ORDER BY r.updated_at DESC, r.rowid DESC LIMIT ?",
                                 (principal.user_id, max(1, min(int(limit), 300)))).fetchall()
        return [{**self._summary(r), "workspace_name": r["ws_name"]} for r in rows]

    def get(self, principal: Principal, workspace_id: str, report_id: str) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        return self._full(self._row(principal, ws["id"], report_id))

    def versions(self, principal: Principal, workspace_id: str, report_id: str) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        r = self._row(principal, ws["id"], report_id)
        rows = self.conn.execute("SELECT id, version, reason, title, created_at FROM ri_report_versions WHERE report_id = ? AND owner_user_id = ? ORDER BY id DESC", (r["id"], principal.user_id)).fetchall()
        return [{"id": x["id"], "version": x["version"], "reason": x["reason"], "title": x["title"], "created_at": x["created_at"]} for x in rows]

    def _snapshot(self, r: sqlite3.Row, reason: str) -> None:
        self.conn.execute("INSERT INTO ri_report_versions (report_id, owner_user_id, version, reason, title, sections, created_at) VALUES (?,?,?,?,?,?,?)",
                          (r["id"], r["owner_user_id"], r["version"], reason, r["title"], r["sections"], self.ctx.clock.iso()))
        self.conn.execute("DELETE FROM ri_report_versions WHERE report_id = ? AND id NOT IN (SELECT id FROM ri_report_versions WHERE report_id = ? ORDER BY id DESC LIMIT ?)", (r["id"], r["id"], MAX_VERSIONS))

    # ------------------------------------------------------------------ edit
    def update(self, principal: Principal, workspace_id: str, report_id: str, *, title: Any = None, sections: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._row(principal, ws["id"], report_id)
        labels = {s["label"] for s in jload(r["sources"], [])}
        new_title = r["title"] if title is None else _clean(title, "title", 200, True).strip()
        old = jload(r["sections"], [])
        new = old if sections is None else self._validate(old, sections, labels)
        if new == old and new_title == r["title"]:
            return self._full(r)
        with transaction(self.conn):
            self._snapshot(r, "edit")
            self.conn.execute("UPDATE ri_reports SET title = ?, sections = ?, version = version + 1, edited = 1, updated_at = ? WHERE id = ?", (new_title, json.dumps(new), self.ctx.clock.iso(), r["id"]))
        self.ctx.audit.record(EV_REPORT, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "report_id": r["id"], "action": "edited"})
        return self._full(self._row(principal, ws["id"], r["id"]))

    def _validate(self, old: List[Dict[str, Any]], incoming: Any, labels: set) -> List[Dict[str, Any]]:
        if not isinstance(incoming, list) or len(incoming) > MAX_SECTIONS:
            raise invalid("sections", f"Send the sections as a list of at most {MAX_SECTIONS}.")
        old_s = {s["id"]: s for s in old}
        out: List[Dict[str, Any]] = []
        used = set()
        for s in incoming:
            if not isinstance(s, dict) or not isinstance(s.get("blocks"), list) or len(s["blocks"]) > MAX_BLOCKS_PER_SECTION:
                raise invalid("sections", "Each section needs a list of blocks (at most %d)." % MAX_BLOCKS_PER_SECTION)
            sid = s.get("id") if isinstance(s.get("id"), str) else ""
            prev = old_s.get(sid)
            if prev is None:
                sid = "author-" + new_id()[:8]
            elif sid in used:
                raise invalid("sections", "A section appears twice.")
            used.add(sid)
            sec = {"id": sid, "heading": _clean(s.get("heading"), "heading", 200, True).strip(), "kind": prev["kind"] if prev else "author", "blocks": self._blocks(prev, s["blocks"], labels, sid)}
            out.append(sec)
        return out

    def _blocks(self, prev: Optional[Dict[str, Any]], blocks: List[Any], labels: set, sid: str) -> List[Dict[str, Any]]:
        old_b = {b["id"]: b for b in (prev["blocks"] if prev else [])}
        out, seen = [], set()
        for b in blocks:
            if not isinstance(b, dict):
                raise invalid("sections", "Each block must be an object.")
            bid = b.get("id") if isinstance(b.get("id"), str) else ""
            o = old_b.get(bid)
            cite = b.get("cite", [])
            if not isinstance(cite, list) or any((not isinstance(c, str)) or c not in labels for c in cite):
                raise invalid("sections", "A citation refers to a source that is not part of this report.")
            if o is None:                                                                        # a block the person added
                if b.get("type") not in ("text", "bullets"):
                    raise invalid("sections", "You can add text or bullet blocks. Quotations come only from the sources.")
                nb = {"id": "a-" + new_id()[:10], "type": b["type"], "origin": "author", "cite": cite}
            else:
                if bid in seen:
                    raise invalid("sections", "A block appears twice.")
                nb = copy.deepcopy(o)
            seen.add(nb["id"])
            if nb["type"] in ("quote", "table"):
                if o is None or any(b.get(k) != o.get(k) for k in (("text", "ref") if nb["type"] == "quote" else ("columns", "rows"))):
                    raise invalid("sections", "Quoted evidence and computed tables cannot be edited. Remove the block, or write your own text next to it.")
                nb["cite"] = o.get("cite", [])
            elif nb["type"] == "text":
                text = _clean(b.get("text"), "text", MAX_TEXT)
                if o is not None and text != o.get("text"):
                    nb["edited_from"], nb["origin"] = o.get("origin"), "author"
                nb["text"], nb["cite"] = text, cite
            elif nb["type"] == "bullets":
                items = b.get("items")
                if not isinstance(items, list) or len(items) > 200:
                    raise invalid("sections", "Bullets need a list of at most 200 items.")
                items = [_clean(i, "items", 1000) for i in items]
                if o is not None and items != o.get("items"):
                    nb["edited_from"], nb["origin"] = o.get("origin"), "author"
                nb["items"] = items
            if o is not None and o.get("hint"):
                nb["hint"] = o["hint"]
            out.append(nb)
        return out

    # ------------------------------------------------------------------ regenerate / restore / delete / export
    def regenerate(self, principal: Principal, workspace_id: str, report_id: str, *, source_ids: Any = None, keep_author_text: bool = True) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._row(principal, ws["id"], report_id)
        a = self.conn.execute("SELECT * FROM ri_analyses WHERE id = ? AND workspace_id = ? AND owner_user_id = ? AND status = 'succeeded'", (r["analysis_id"] or "", ws["id"], principal.user_id)).fetchone()
        if a is None:
            raise conflict("ANALYSIS_GONE", "The analysis this report was built from no longer exists (a source it used may have been deleted). Run a new analysis and create a new report.")
        result = jload(a["result"], {})
        current = {s["label"] for s in jload(r["sources"], [])}
        keep = self._keep(result, source_ids) if source_ids is not None else sorted(current)
        try:
            built = builder.build(r["report_type"], result, keep)
        except ValueError:
            raise invalid("source_ids", "Choose at least one source of the analysis.")
        sections = builder.regenerate_merge(jload(r["sections"], []), built["sections"]) if keep_author_text else built["sections"]
        with transaction(self.conn):
            self._snapshot(r, "regenerate")
            self.conn.execute("UPDATE ri_reports SET sources = ?, sections = ?, notice = ?, version = version + 1, updated_at = ? WHERE id = ?", (json.dumps(built["sources"]), json.dumps(sections), built["notice"], self.ctx.clock.iso(), r["id"]))
        self.ctx.audit.record(EV_REPORT, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "report_id": r["id"], "action": "regenerated"})
        return self._full(self._row(principal, ws["id"], r["id"]))

    def restore(self, principal: Principal, workspace_id: str, report_id: str, version_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._row(principal, ws["id"], report_id)
        v = self.conn.execute("SELECT * FROM ri_report_versions WHERE id = ? AND report_id = ? AND owner_user_id = ?", (version_id if isinstance(version_id, int) and not isinstance(version_id, bool) else -1, r["id"], principal.user_id)).fetchone()
        if v is None:
            raise not_found("version")
        with transaction(self.conn):
            self._snapshot(r, "restore")
            self.conn.execute("UPDATE ri_reports SET title = ?, sections = ?, version = version + 1, edited = 1, updated_at = ? WHERE id = ?", (v["title"], v["sections"], self.ctx.clock.iso(), r["id"]))
        return self._full(self._row(principal, ws["id"], r["id"]))

    def delete(self, principal: Principal, workspace_id: str, report_id: str) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._row(principal, ws["id"], report_id)
        with transaction(self.conn):
            self.conn.execute("DELETE FROM ri_report_versions WHERE report_id = ?", (r["id"],))
            self.conn.execute("DELETE FROM ri_reports WHERE id = ?", (r["id"],))
        self.ctx.audit.record(EV_REPORT, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "report_id": r["id"], "action": "deleted"})
        return {"deleted": True, "id": r["id"]}

    def export(self, principal: Principal, workspace_id: str, report_id: str, fmt: Any):
        ws = self._ws(principal, workspace_id)
        self.ctx.limiter.hit("ri_export:user", principal.user_id)
        fmt = choice(fmt, "format", tuple(exporter.FORMATS))
        rep = self._full(self._row(principal, ws["id"], report_id))
        try:
            data, ctype, name = exporter.export(rep, fmt)
        except ImportError as e:
            pkg = {"docx": "python-docx", "pdf": "reportlab", "pptx": "python-pptx"}.get(fmt, "a required package")
            raise unavailable(f"Exporting to {fmt.upper()} needs {pkg}, which is not installed on this server. Ask the platform owner to install backend/requirements-reports.txt, or export another format.") from e
        self.ctx.audit.record(EV_REPORT, "success", actor=principal.user_id, metadata={"workspace_id": ws["id"], "report_id": rep["id"], "action": "exported", "format": fmt})
        return data, ctype, name


def redact_source(conn: sqlite3.Connection, workspace_id: str, owner_id: str, source_id: str) -> int:
    """Called when a source is deleted: remove its quotations and references from reports of the workspace (the person's own text stays). Returns the number of reports changed."""
    changed = 0
    for r in conn.execute("SELECT * FROM ri_reports WHERE workspace_id = ? AND owner_user_id = ?", (workspace_id, owner_id)).fetchall():
        sources = jload(r["sources"], [])
        label = next((s["label"] for s in sources if s["source_id"] == source_id), None)
        if label is None:
            continue
        sections = jload(r["sections"], [])
        for s in sections:
            kept = []
            for b in s["blocks"]:
                if b["type"] == "quote" and (b.get("ref") or {}).get("source_id") == source_id:
                    continue
                if b["type"] == "bullets" and s["id"] == "references":
                    b["items"] = [i for i in b["items"] if not i.startswith(f"[{label}] ")]
                if label in b.get("cite", []):
                    b["cite"] = [c for c in b["cite"] if c != label]
                    if b["type"] == "text" and b.get("origin") != "author":
                        b["text"] = (b.get("text") or "") + " (A source mentioned here was deleted.)"
                kept.append(b)
            s["blocks"] = kept
        conn.execute("UPDATE ri_reports SET sources = ?, sections = ? WHERE id = ?", (json.dumps([s for s in sources if s["source_id"] != source_id]), json.dumps(sections), r["id"]))
        conn.execute("DELETE FROM ri_report_versions WHERE report_id = ?", (r["id"],))             # older versions still hold the deleted source's text
        changed += 1
    return changed
