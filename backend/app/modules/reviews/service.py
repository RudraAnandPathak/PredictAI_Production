"""Reviews of a person's own work, shown to the person under the work and to the platform owner with the account that wrote them.

When an account is deleted the review stays, anonymised: the link to the person is removed, emails, phone numbers and the person's own name are blanked out of the comment,
the work's title is replaced by its kind (titles can identify a person), and the review gets a random label shared by all reviews of that deleted account."""
from __future__ import annotations

import re
import secrets
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from ...db import transaction
from ..identity.authorization import Principal, require
from ..identity.constants import Perm
from ..identity.tokens import new_id
from ..workspace.errors import invalid, not_found

MAX_COMMENT = 2000
PAGE = 50

# kind -> (label, table, title SQL). Every table has id and owner_user_id; a review is only allowed on work the person owns.
TARGETS: Dict[str, Tuple[str, str, str]] = {
    "notebook_output": ("Notebook output", "nb_outputs", "title"),
    "exam_prediction": ("Exam prediction", "exam_predictions", "'Exam prediction'"),
    "exam_paper": ("Exam paper", "exam_assessments", "title"),
    "report": ("Report", "rep_reports", "title"),
    "research_report": ("Research report", "ri_reports", "title"),
    "advanced_analysis": ("Advanced analysis", "adv_runs", "title"),
    "analysis": ("Analysis", "analysis_runs", "'Analysis: ' || kind"),
    "prediction": ("Prediction", "ml_predictions", "'Prediction'"),
}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
REMOVED = "[removed]"


def scrub(text: Optional[str], names: List[str]) -> Optional[str]:
    """Blank out emails, phone numbers and the given names (whole words, any case)."""
    if not text:
        return text
    out = PHONE.sub(REMOVED, EMAIL.sub(REMOVED, text))
    for n in sorted({n.strip() for n in names if n and len(n.strip()) >= 3}, key=len, reverse=True):
        out = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", REMOVED, out, flags=re.IGNORECASE)
    return out


def anonymize_for_user(conn: sqlite3.Connection, user_id: str, now: str) -> int:
    """Called during account deletion, while the user row still has the name and email. Returns how many reviews were anonymised."""
    u = conn.execute("SELECT email, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    names: List[str] = []
    if u is not None:
        local = (u["email"] or "").split("@")[0]
        names = [u["email"] or "", local, u["display_name"] or "", *(u["display_name"] or "").split()]
    label = "Deleted account #" + secrets.token_hex(3)
    rows = conn.execute("SELECT id, comment, target_kind FROM reviews WHERE author_user_id = ?", (user_id,)).fetchall()
    for r in rows:
        title = TARGETS.get(r["target_kind"], (r["target_kind"],))[0]
        conn.execute("UPDATE reviews SET author_user_id = NULL, anon_label = ?, comment = ?, target_title = ?, anonymized_at = ? WHERE id = ?",
                     (label, scrub(r["comment"], names), title, now, r["id"]))
    return len(rows)


class ReviewService:
    def __init__(self, ctx: Any) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    @staticmethod
    def kinds() -> Dict[str, Any]:
        return {"items": [{"kind": k, "label": v[0]} for k, v in TARGETS.items()]}

    def _target(self, principal: Principal, kind: Any, target_id: Any) -> Tuple[str, str, str]:
        if not isinstance(kind, str) or kind not in TARGETS:
            raise invalid("target_kind", "This kind of work cannot be reviewed.")
        if not isinstance(target_id, str) or not 1 <= len(target_id) <= 100:
            raise invalid("target_id", "Say which piece of work this is about.")
        _, table, title_sql = TARGETS[kind]
        row = self.conn.execute(f"SELECT {title_sql} AS title FROM {table} WHERE id = ? AND owner_user_id = ?", (target_id, principal.user_id)).fetchone()
        if row is None:
            raise not_found("work")
        return kind, target_id, (str(row["title"] or TARGETS[kind][0]))[:200]

    @staticmethod
    def _mine(r: sqlite3.Row) -> Dict[str, Any]:
        return {"id": r["id"], "target_kind": r["target_kind"], "target_id": r["target_id"], "rating": r["rating"], "comment": r["comment"], "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def get_mine(self, principal: Principal, kind: Any, target_id: Any) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        kind, target_id, _ = self._target(principal, kind, target_id)
        r = self.conn.execute("SELECT * FROM reviews WHERE author_user_id = ? AND target_kind = ? AND target_id = ?", (principal.user_id, kind, target_id)).fetchone()
        return {"review": self._mine(r) if r else None, "notice": "Reviews are read by the PredictAI owner with your account name. If you delete your account, your review stays without your name."}

    def put(self, principal: Principal, kind: Any, target_id: Any, rating: Any, comment: Any = None) -> Dict[str, Any]:
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        self.ctx.limiter.hit("review_write:user", principal.user_id)
        kind, target_id, title = self._target(principal, kind, target_id)
        if not isinstance(rating, int) or isinstance(rating, bool) or not 1 <= rating <= 5:
            raise invalid("rating", "Choose 1 to 5 stars.")
        if comment is not None and (not isinstance(comment, str) or len(comment) > MAX_COMMENT):
            raise invalid("comment", f"Write up to {MAX_COMMENT} characters.")
        comment = (comment or "").strip() or None
        now = self.ctx.clock.iso()
        with transaction(self.conn):
            r = self.conn.execute("SELECT id FROM reviews WHERE author_user_id = ? AND target_kind = ? AND target_id = ?", (principal.user_id, kind, target_id)).fetchone()
            if r:
                rid = r["id"]
                self.conn.execute("UPDATE reviews SET rating = ?, comment = ?, target_title = ?, updated_at = ? WHERE id = ?", (rating, comment, title, now, rid))
            else:
                rid = new_id()
                self.conn.execute("INSERT INTO reviews (id, target_kind, target_id, target_title, author_user_id, rating, comment, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (rid, kind, target_id, title, principal.user_id, rating, comment, now, now))
        self.ctx.audit.record("review.saved", "success", actor=principal.user_id, metadata={"kind": kind, "rating": rating})
        return self._mine(self.conn.execute("SELECT * FROM reviews WHERE id = ?", (rid,)).fetchone())

    def delete_mine(self, principal: Principal, kind: Any, target_id: Any) -> Dict[str, Any]:
        """A person may withdraw their own review while their account exists."""
        require(principal, Perm.PROJECTS_MANAGE_OWN)
        if not isinstance(kind, str) or kind not in TARGETS or not isinstance(target_id, str):
            raise invalid("target_kind", "This kind of work cannot be reviewed.")
        with transaction(self.conn):
            n = self.conn.execute("DELETE FROM reviews WHERE author_user_id = ? AND target_kind = ? AND target_id = ?", (principal.user_id, kind, target_id)).rowcount
        if not n:
            raise not_found("review")
        self.ctx.audit.record("review.deleted", "success", actor=principal.user_id, metadata={"kind": kind})
        return {"deleted": True}

    # ------------------------------------------------------------------ owner
    def owner_list(self, principal: Principal, kind: Any = None, rating: Any = None, page: Any = 1) -> Dict[str, Any]:
        require(principal, Perm.OWNER_USERS_READ)
        where, args = [], []
        if kind not in (None, ""):
            if kind not in TARGETS:
                raise invalid("kind", "Unknown kind of work.")
            where.append("r.target_kind = ?")
            args.append(kind)
        if rating not in (None, ""):
            try:
                rv = int(rating)
            except (TypeError, ValueError):
                rv = 0
            if not 1 <= rv <= 5:
                raise invalid("rating", "Choose 1 to 5.")
            where.append("r.rating = ?")
            args.append(rv)
        cond = ("WHERE " + " AND ".join(where)) if where else ""
        total = self.conn.execute(f"SELECT COUNT(*) FROM reviews r {cond}", args).fetchone()[0]
        page = max(1, int(page) if isinstance(page, int) or (isinstance(page, str) and page.isdigit()) else 1)
        rows = self.conn.execute(f"SELECT r.*, u.email, u.display_name FROM reviews r LEFT JOIN users u ON u.id = r.author_user_id {cond} ORDER BY r.created_at DESC, r.id LIMIT ? OFFSET ?",
                                 (*args, PAGE, (page - 1) * PAGE)).fetchall()
        items = [{"id": r["id"], "target_kind": r["target_kind"], "target_label": TARGETS.get(r["target_kind"], (r["target_kind"],))[0], "target_title": r["target_title"], "rating": r["rating"],
                  "comment": r["comment"], "created_at": r["created_at"], "updated_at": r["updated_at"],
                  "author": ({"deleted": True, "label": r["anon_label"]} if r["author_user_id"] is None else
                             {"deleted": False, "user_id": r["author_user_id"], "email": r["email"], "display_name": r["display_name"]})} for r in rows]
        summary = [{"kind": s["target_kind"], "label": TARGETS.get(s["target_kind"], (s["target_kind"],))[0], "count": s["n"], "average": round(s["avg"], 2), "low": s["low"]}
                   for s in self.conn.execute("SELECT target_kind, COUNT(*) AS n, AVG(rating) AS avg, SUM(rating <= 2) AS low FROM reviews GROUP BY target_kind ORDER BY n DESC")]
        return {"items": items, "page": page, "pages": max(1, -(-total // PAGE)), "total": total, "summary": summary}
