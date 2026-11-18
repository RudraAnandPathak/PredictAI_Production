"""Research Studio (R7): organise findings, connect them, outline a report, save generated analyses, explore evidence, and revisit earlier work.

Everything here lives in the Phase 8/10 workspace (`rw_workspaces`, `ri_sources`, `rw_notes`, `ri_findings`); the Studio adds links, groups, outlines and saved generator output, and never a second
workspace. All access is owner-checked, and every id a request mentions is verified to belong to the same person and workspace."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from ....db import transaction
from ...identity.authorization import Principal, require
from ...identity.constants import Perm
from ...identity.context import Context
from ...identity.tokens import new_id
from ...p7 import choice, jload
from ...workspace.errors import conflict, invalid, not_found
from ..core.common import EV_STUDIO
from ..reports import builder
from . import generators

RELATIONS = ("supports", "contradicts", "extends", "related")
KINDS = ("research_questions", "hypotheses", "literature_review")
MAX_NODES, MAX_DEPTH = 200, 3


def _text(v: Any, field: str, lo: int, hi: int) -> str:
    if not isinstance(v, str) or not (lo <= len(v.strip()) <= hi):
        raise invalid(field, f"Use {lo} to {hi} characters." if lo else f"Use at most {hi} characters.")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v.strip())


class StudioService:
    def __init__(self, ctx: Context) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    def _ws(self, principal: Principal, workspace_id: Any, writable: bool = False) -> sqlite3.Row:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        r = self.conn.execute("SELECT * FROM rw_workspaces WHERE id = ? AND owner_user_id = ?", (workspace_id if isinstance(workspace_id, str) else "", principal.user_id)).fetchone()
        if r is None:
            raise not_found("research workspace")
        if writable:
            self.ctx.limiter.hit("ri_write:user", principal.user_id)
            if r["status"] != "active":
                raise conflict("WORKSPACE_ARCHIVED", "This workspace is archived. Restore it to use the Studio.")
        return r

    def _finding(self, principal: Principal, ws: str, fid: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_findings WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (fid if isinstance(fid, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("finding")
        return r

    def _audit(self, principal: Principal, ws: str, action: str, **extra: Any) -> None:
        self.ctx.audit.record(EV_STUDIO, "success", actor=principal.user_id, metadata={"workspace_id": ws, "action": action, **extra})

    # ------------------------------------------------------------------ findings: edit comment
    def update_finding(self, principal: Principal, workspace_id: Any, finding_id: Any, comment: Any = None, kind: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        f = self._finding(principal, ws["id"], finding_id)
        new_comment = f["comment"] if comment is None else (_text(comment, "comment", 0, 2000) or None)
        new_kind = f["kind"] if kind is None else choice(kind, "kind", ("finding", "highlight", "evidence"))
        self.conn.execute("UPDATE ri_findings SET comment = ?, kind = ? WHERE id = ?", (new_comment, new_kind, f["id"]))
        return self._present_finding(self._finding(principal, ws["id"], f["id"]))

    def _present_finding(self, r: sqlite3.Row) -> Dict[str, Any]:
        s = self.conn.execute("SELECT title FROM ri_sources WHERE id = ?", (r["source_id"],)).fetchone()
        loc = self.conn.execute("SELECT locator FROM ri_chunks WHERE id = ?", (r["chunk_id"],)).fetchone() if r["chunk_id"] else None
        return {"id": r["id"], "source_id": r["source_id"], "source_title": s["title"] if s else None, "chunk_id": r["chunk_id"], "locator": loc["locator"] if loc else None,
                "kind": r["kind"], "text": r["text"], "comment": r["comment"], "created_at": r["created_at"]}

    # ------------------------------------------------------------------ links
    def link(self, principal: Principal, workspace_id: Any, from_id: Any, to_id: Any, relation: Any, note: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        rel = choice(relation, "relation", RELATIONS)
        a, b = self._finding(principal, ws["id"], from_id), self._finding(principal, ws["id"], to_id)
        if a["id"] == b["id"]:
            raise invalid("to_id", "A finding cannot be linked to itself.")
        if self.conn.execute("SELECT 1 FROM ri_finding_links WHERE from_id = ? AND to_id = ? AND relation = ?", (a["id"], b["id"], rel)).fetchone():
            raise conflict("LINK_EXISTS", "These findings are already connected this way.")
        lid = new_id()
        self.conn.execute("INSERT INTO ri_finding_links (id, workspace_id, owner_user_id, from_id, to_id, relation, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (lid, ws["id"], principal.user_id, a["id"], b["id"], rel, (_text(note, "note", 0, 1000) or None) if note not in (None, "") else None, self.ctx.clock.iso()))
        self._audit(principal, ws["id"], "link_created")
        return self._link(self.conn.execute("SELECT * FROM ri_finding_links WHERE id = ?", (lid,)).fetchone())

    @staticmethod
    def _link(r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "from_id": r["from_id"], "to_id": r["to_id"], "relation": r["relation"], "note": r["note"], "created_at": r["created_at"]}

    def links(self, principal: Principal, workspace_id: Any) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        return [self._link(r) for r in self.conn.execute("SELECT * FROM ri_finding_links WHERE workspace_id = ? AND owner_user_id = ? ORDER BY created_at, id", (ws["id"], principal.user_id)).fetchall()]

    def unlink(self, principal: Principal, workspace_id: Any, link_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self.conn.execute("SELECT id FROM ri_finding_links WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (link_id if isinstance(link_id, str) else "", ws["id"], principal.user_id)).fetchone()
        if r is None:
            raise not_found("link")
        self.conn.execute("DELETE FROM ri_finding_links WHERE id = ?", (r["id"],))
        return {"deleted": True, "id": r["id"]}

    # ------------------------------------------------------------------ groups
    def create_group(self, principal: Principal, workspace_id: Any, name: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        n = _text(name, "name", 1, 120)
        if self.conn.execute("SELECT 1 FROM ri_finding_groups WHERE workspace_id = ? AND owner_user_id = ? AND name = ?", (ws["id"], principal.user_id, n)).fetchone():
            raise conflict("GROUP_EXISTS", "You already have a group with this name.")
        gid = new_id()
        self.conn.execute("INSERT INTO ri_finding_groups (id, workspace_id, owner_user_id, name, created_at) VALUES (?,?,?,?,?)", (gid, ws["id"], principal.user_id, n, self.ctx.clock.iso()))
        return {"id": gid, "name": n, "finding_ids": []}

    def _group(self, principal: Principal, ws: str, gid: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_finding_groups WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (gid if isinstance(gid, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("group")
        return r

    def groups(self, principal: Principal, workspace_id: Any) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        out = []
        for g in self.conn.execute("SELECT * FROM ri_finding_groups WHERE workspace_id = ? AND owner_user_id = ? ORDER BY name", (ws["id"], principal.user_id)).fetchall():
            ids = [r["finding_id"] for r in self.conn.execute("SELECT finding_id FROM ri_finding_group_items WHERE group_id = ? ORDER BY position, finding_id", (g["id"],)).fetchall()]
            out.append({"id": g["id"], "name": g["name"], "finding_ids": ids})
        return out

    def rename_group(self, principal: Principal, workspace_id: Any, group_id: Any, name: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        g = self._group(principal, ws["id"], group_id)
        n = _text(name, "name", 1, 120)
        if self.conn.execute("SELECT 1 FROM ri_finding_groups WHERE workspace_id = ? AND owner_user_id = ? AND name = ? AND id <> ?", (ws["id"], principal.user_id, n, g["id"])).fetchone():
            raise conflict("GROUP_EXISTS", "You already have a group with this name.")
        self.conn.execute("UPDATE ri_finding_groups SET name = ? WHERE id = ?", (n, g["id"]))
        return next(x for x in self.groups(principal, ws["id"]) if x["id"] == g["id"])

    def set_group_items(self, principal: Principal, workspace_id: Any, group_id: Any, finding_ids: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        g = self._group(principal, ws["id"], group_id)
        if not isinstance(finding_ids, list) or len(finding_ids) > 500 or not all(isinstance(x, str) for x in finding_ids):
            raise invalid("finding_ids", "Send a list of finding ids (at most 500).")
        ids = list(dict.fromkeys(finding_ids))
        for fid in ids:
            self._finding(principal, ws["id"], fid)
        with transaction(self.conn):
            self.conn.execute("DELETE FROM ri_finding_group_items WHERE group_id = ?", (g["id"],))
            self.conn.executemany("INSERT INTO ri_finding_group_items (group_id, finding_id, position) VALUES (?,?,?)", [(g["id"], fid, i) for i, fid in enumerate(ids)])
        return next(x for x in self.groups(principal, ws["id"]) if x["id"] == g["id"])

    def delete_group(self, principal: Principal, workspace_id: Any, group_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        g = self._group(principal, ws["id"], group_id)
        with transaction(self.conn):
            self.conn.execute("DELETE FROM ri_finding_group_items WHERE group_id = ?", (g["id"],))
            self.conn.execute("DELETE FROM ri_finding_groups WHERE id = ?", (g["id"],))
        return {"deleted": True, "id": g["id"]}

    # ------------------------------------------------------------------ outlines
    def _outline_items(self, principal: Principal, ws: str, items: Any) -> List[Dict[str, Any]]:
        count = [0]

        def walk(nodes: Any, depth: int) -> List[Dict[str, Any]]:
            if not isinstance(nodes, list) or depth > MAX_DEPTH:
                raise invalid("items", f"An outline has at most {MAX_DEPTH} levels.")
            out = []
            for n in nodes:
                count[0] += 1
                if count[0] > MAX_NODES or not isinstance(n, dict):
                    raise invalid("items", f"An outline has at most {MAX_NODES} entries.")
                fids = n.get("finding_ids", [])
                if not isinstance(fids, list) or len(fids) > 50:
                    raise invalid("items", "Each entry links at most 50 findings.")
                for fid in fids:
                    self._finding(principal, ws, fid)
                out.append({"id": n["id"] if isinstance(n.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", n["id"]) else new_id()[:10], "heading": _text(n.get("heading"), "heading", 1, 200),
                            "notes": _text(n.get("notes") or "", "notes", 0, 4000), "finding_ids": list(dict.fromkeys(fids)), "children": walk(n.get("children", []), depth + 1)})
            return out
        return walk(items, 1)

    def create_outline(self, principal: Principal, workspace_id: Any, title: Any, items: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        t, its, oid, now = _text(title, "title", 1, 200), self._outline_items(principal, ws["id"], items or []), new_id(), self.ctx.clock.iso()
        self.conn.execute("INSERT INTO ri_outlines (id, workspace_id, owner_user_id, title, items, created_at, updated_at) VALUES (?,?,?,?,?,?,?)", (oid, ws["id"], principal.user_id, t, json.dumps(its), now, now))
        self._audit(principal, ws["id"], "outline_created")
        return self.outline(principal, ws["id"], oid)

    def _outline_row(self, principal: Principal, ws: str, oid: Any) -> sqlite3.Row:
        r = self.conn.execute("SELECT * FROM ri_outlines WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (oid if isinstance(oid, str) else "", ws, principal.user_id)).fetchone()
        if r is None:
            raise not_found("outline")
        return r

    def outline(self, principal: Principal, workspace_id: Any, outline_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        r = self._outline_row(principal, ws["id"], outline_id)
        return {"id": r["id"], "title": r["title"], "items": jload(r["items"], []), "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def outlines(self, principal: Principal, workspace_id: Any) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        return [{"id": r["id"], "title": r["title"], "entries": len(re.findall(r'"heading"', r["items"])), "updated_at": r["updated_at"]}
                for r in self.conn.execute("SELECT * FROM ri_outlines WHERE workspace_id = ? AND owner_user_id = ? ORDER BY updated_at DESC, rowid DESC", (ws["id"], principal.user_id)).fetchall()]

    def update_outline(self, principal: Principal, workspace_id: Any, outline_id: Any, title: Any = None, items: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._outline_row(principal, ws["id"], outline_id)
        t = r["title"] if title is None else _text(title, "title", 1, 200)
        its = r["items"] if items is None else json.dumps(self._outline_items(principal, ws["id"], items))
        self.conn.execute("UPDATE ri_outlines SET title = ?, items = ?, updated_at = ? WHERE id = ?", (t, its, self.ctx.clock.iso(), r["id"]))
        return self.outline(principal, ws["id"], r["id"])

    def delete_outline(self, principal: Principal, workspace_id: Any, outline_id: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        r = self._outline_row(principal, ws["id"], outline_id)
        self.conn.execute("DELETE FROM ri_outlines WHERE id = ?", (r["id"],))
        return {"deleted": True, "id": r["id"]}

    # ------------------------------------------------------------------ generators
    def generate(self, principal: Principal, workspace_id: Any, kind: Any, analysis_id: Any, source_ids: Any = None) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        k = choice(kind, "kind", KINDS)
        a = self.conn.execute("SELECT * FROM ri_analyses WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (analysis_id if isinstance(analysis_id, str) else "", ws["id"], principal.user_id)).fetchone()
        if a is None:
            raise not_found("analysis")
        if a["status"] != "succeeded":
            raise conflict("ANALYSIS_NOT_READY", "Wait for the analysis to finish first.")
        result = jload(a["result"], {})
        by_id = {s["source_id"]: s["label"] for s in result["sources"]}
        if source_ids is None:
            keep = None
        elif isinstance(source_ids, list) and source_ids and all(isinstance(x, str) and x in by_id for x in source_ids):
            keep = [by_id[x] for x in dict.fromkeys(source_ids)]
        else:
            raise invalid("source_ids", "Choose sources that belong to the analysis.")
        if k == "research_questions":
            content, title = {"items": generators.research_questions(result, keep), "note": generators.NOTE}, "Research questions"
        elif k == "hypotheses":
            content, title = {"items": generators.hypotheses(result, keep), "note": generators.NOTE}, "Hypotheses"
        else:
            content, title = builder.build("literature_review", result, keep), "Literature review draft"
        used = [s["source_id"] for s in result["sources"] if keep is None or s["label"] in keep]
        gid = new_id()
        self.conn.execute("INSERT INTO ri_generated (id, workspace_id, owner_user_id, analysis_id, kind, title, content, source_ids, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (gid, ws["id"], principal.user_id, a["id"], k, f"{title}: {a['title']}"[:200], json.dumps(content), json.dumps(used), self.ctx.clock.iso()))
        self._audit(principal, ws["id"], "generated", kind=k)
        return self.generated(principal, ws["id"], gid)

    def generated_list(self, principal: Principal, workspace_id: Any) -> List[Dict[str, Any]]:
        ws = self._ws(principal, workspace_id)
        return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "analysis_id": r["analysis_id"], "created_at": r["created_at"]}
                for r in self.conn.execute("SELECT * FROM ri_generated WHERE workspace_id = ? AND owner_user_id = ? ORDER BY created_at DESC, rowid DESC", (ws["id"], principal.user_id)).fetchall()]

    def generated(self, principal: Principal, workspace_id: Any, gid: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        r = self.conn.execute("SELECT * FROM ri_generated WHERE id = ? AND workspace_id = ? AND owner_user_id = ?", (gid if isinstance(gid, str) else "", ws["id"], principal.user_id)).fetchone()
        if r is None:
            raise not_found("saved output")
        return {"id": r["id"], "kind": r["kind"], "title": r["title"], "analysis_id": r["analysis_id"], "content": jload(r["content"], {}), "label": "Generated by PredictAI from the selected sources. Not validated.", "created_at": r["created_at"]}

    def delete_generated(self, principal: Principal, workspace_id: Any, gid: Any) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id, True)
        self.generated(principal, ws["id"], gid)
        self.conn.execute("DELETE FROM ri_generated WHERE id = ?", (gid,))
        return {"deleted": True, "id": gid}

    # ------------------------------------------------------------------ evidence explorer
    def evidence(self, principal: Principal, workspace_id: Any, *, source_id: Any = None, kind: Any = None, q: Any = None, limit: int = 200) -> Dict[str, Any]:
        ws = self._ws(principal, workspace_id)
        sql, args = "SELECT * FROM ri_findings WHERE workspace_id = ? AND owner_user_id = ?", [ws["id"], principal.user_id]
        if source_id:
            sql, args = sql + " AND source_id = ?", args + [source_id if isinstance(source_id, str) else ""]
        if kind:
            sql, args = sql + " AND kind = ?", args + [choice(kind, "kind", ("finding", "highlight", "evidence"))]
        if q:
            like = "%" + str(q)[:100].replace("%", "").replace("_", "") + "%"
            sql, args = sql + " AND (text LIKE ? OR COALESCE(comment,'') LIKE ?)", args + [like, like]
        rows = self.conn.execute(sql + " ORDER BY created_at DESC, id LIMIT ?", args + [max(1, min(int(limit), 500))]).fetchall()
        link_n = {r["fid"]: r["n"] for r in self.conn.execute("SELECT fid, COUNT(*) n FROM (SELECT from_id fid FROM ri_finding_links WHERE workspace_id = ? AND owner_user_id = ? UNION ALL SELECT to_id FROM ri_finding_links WHERE workspace_id = ? AND owner_user_id = ?) GROUP BY fid",
                                                           (ws["id"], principal.user_id, ws["id"], principal.user_id)).fetchall()}
        items = [{**self._present_finding(r), "links": link_n.get(r["id"], 0)} for r in rows]
        per = self.conn.execute("SELECT s.id, s.title, "
                                "(SELECT COUNT(*) FROM ri_findings f WHERE f.source_id = s.id AND f.kind = 'finding') AS findings, "
                                "(SELECT COUNT(*) FROM ri_findings f WHERE f.source_id = s.id AND f.kind = 'highlight') AS highlights, "
                                "(SELECT COUNT(*) FROM ri_findings f WHERE f.source_id = s.id AND f.kind = 'evidence') AS evidence, "
                                "(SELECT COUNT(*) FROM ri_citations c WHERE c.source_id = s.id) AS citations "
                                "FROM ri_sources s WHERE s.workspace_id = ? AND s.owner_user_id = ? ORDER BY s.title", (ws["id"], principal.user_id)).fetchall()
        return {"items": items, "sources": [dict(r) for r in per]}

    # ------------------------------------------------------------------ activity (revisit sessions) and home
    _ACT = ("SELECT 'analysis' AS kind, id, workspace_id, title, created_at AS at FROM ri_analyses WHERE owner_user_id = :u "
            "UNION ALL SELECT 'report', id, workspace_id, title, updated_at FROM ri_reports WHERE owner_user_id = :u "
            "UNION ALL SELECT 'generated', id, workspace_id, title, created_at FROM ri_generated WHERE owner_user_id = :u "
            "UNION ALL SELECT 'outline', id, workspace_id, title, updated_at FROM ri_outlines WHERE owner_user_id = :u "
            "UNION ALL SELECT 'finding', id, workspace_id, substr(text,1,100), created_at FROM ri_findings WHERE owner_user_id = :u "
            "UNION ALL SELECT 'note', id, workspace_id, substr(body,1,100), updated_at FROM rw_notes WHERE owner_user_id = :u "
            "UNION ALL SELECT 'source', id, workspace_id, title, created_at FROM ri_sources WHERE owner_user_id = :u "
            "UNION ALL SELECT 'conversation', id, workspace_id, title, updated_at FROM rw_conversations WHERE owner_user_id = :u")

    def activity(self, principal: Principal, workspace_id: Any = None, limit: int = 30) -> List[Dict[str, Any]]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        n = max(1, min(int(limit), 100))
        if workspace_id:
            ws = self._ws(principal, workspace_id)
            rows = self.conn.execute(f"SELECT * FROM ({self._ACT}) WHERE workspace_id = :w ORDER BY at DESC, id LIMIT :n", {"u": principal.user_id, "w": ws["id"], "n": n}).fetchall()
        else:
            rows = self.conn.execute(f"SELECT a.*, w.name AS workspace_name FROM ({self._ACT}) a JOIN rw_workspaces w ON w.id = a.workspace_id ORDER BY at DESC, a.id LIMIT :n", {"u": principal.user_id, "n": n}).fetchall()
        return [dict(r) for r in rows]

    def home(self, principal: Principal) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        u = principal.user_id
        ws = [dict(r) for r in self.conn.execute(
            "SELECT w.id, w.name, w.status, w.updated_at, (SELECT COUNT(*) FROM ri_sources s WHERE s.workspace_id = w.id) AS sources FROM rw_workspaces w WHERE w.owner_user_id = ? ORDER BY w.updated_at DESC LIMIT 6", (u,)).fetchall()]
        sources = [dict(r) for r in self.conn.execute("SELECT s.id, s.title, s.status, s.access, s.workspace_id, w.name AS workspace_name, s.created_at FROM ri_sources s JOIN rw_workspaces w ON w.id = s.workspace_id "
                                                      "WHERE s.owner_user_id = ? ORDER BY s.created_at DESC LIMIT 8", (u,)).fetchall()]
        findings = [dict(r) for r in self.conn.execute("SELECT f.id, f.kind, substr(f.text,1,200) AS text, f.workspace_id, w.name AS workspace_name, s.title AS source_title, f.created_at FROM ri_findings f JOIN rw_workspaces w ON w.id = f.workspace_id "
                                                       "JOIN ri_sources s ON s.id = f.source_id WHERE f.owner_user_id = ? ORDER BY f.created_at DESC LIMIT 8", (u,)).fetchall()]
        reports = [dict(r) for r in self.conn.execute("SELECT r.id, r.title, r.report_type, r.workspace_id, w.name AS workspace_name, r.updated_at FROM ri_reports r JOIN rw_workspaces w ON w.id = r.workspace_id WHERE r.owner_user_id = ? ORDER BY r.updated_at DESC LIMIT 5", (u,)).fetchall()]
        return {"workspaces": ws, "activity": self.activity(principal, None, 12), "sources": sources, "findings": findings, "reports": reports}
