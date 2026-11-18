"""Notebooks are ordinary project files (.ipynb, nbformat 4). This service creates, validates and exports them; running them is a job (engine.py), and the JupyterLab server is a session (sessions.py).

Exports: ipynb, py and html are produced here. PDF needs nbconvert plus a TeX installation and is reported as unavailable instead of being faked. HTML escapes every cell and output; no notebook script is ever
executed or embedded."""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List

from .common import DevService, contains_secret_pattern, dev_error, invalid, not_found, safe_path
from .files import FileService

MAX_CELLS = 2000
EXPORTS = {"ipynb": "application/x-ipynb+json", "py": "text/x-python", "html": "text/html", "pdf": None}


def blank(kernel: str = "python3") -> Dict[str, Any]:
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": kernel, "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}}, "cells": []}


def src(cell: Dict[str, Any]) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else str(s)


def validate(doc: Any) -> List[str]:
    """Problems found, as plain sentences. Empty list = a notebook the headless runner and Jupyter can both open."""
    if not isinstance(doc, dict):
        return ["The file is not a notebook (expected a JSON object)."]
    out = []
    if doc.get("nbformat") != 4:
        out.append("Only notebook format 4 is supported.")
    cells = doc.get("cells")
    if not isinstance(cells, list):
        return out + ["The notebook has no 'cells' list."]
    if len(cells) > MAX_CELLS:
        out.append(f"More than {MAX_CELLS} cells.")
    for i, c in enumerate(cells[:MAX_CELLS]):
        if not isinstance(c, dict) or c.get("cell_type") not in ("code", "markdown", "raw"):
            out.append(f"Cell {i + 1} has an unknown type.")
        elif not isinstance(c.get("source", ""), (str, list)):
            out.append(f"Cell {i + 1} has an unreadable source.")
    return out


def to_py(doc: Dict[str, Any]) -> str:
    parts = ["# Exported from a PredictAI notebook. Markdown cells are comments; magics and shell lines are commented out.\n"]
    for i, c in enumerate(doc["cells"], 1):
        text = src(c)
        if c["cell_type"] == "code":
            body = "\n".join(("# " + l) if l.lstrip().startswith(("%", "!")) else l for l in text.splitlines())
            parts.append(f"# %% [cell {i}]\n{body}\n")
        else:
            parts.append(f"# %% [{c['cell_type']} {i}]\n" + "\n".join("# " + l for l in text.splitlines()) + "\n")
    return "\n".join(parts)


def to_html(doc: Dict[str, Any], title: str) -> str:
    rows = []
    for c in doc["cells"]:
        t = html.escape(src(c))
        if c["cell_type"] == "code":
            outs = []
            for o in c.get("outputs", []) if isinstance(c.get("outputs"), list) else []:
                if not isinstance(o, dict):
                    continue
                if o.get("output_type") == "stream":
                    outs.append(html.escape("".join(o["text"]) if isinstance(o.get("text"), list) else str(o.get("text", ""))))
                elif o.get("output_type") == "error":
                    outs.append(html.escape(f"{o.get('ename', 'Error')}: {o.get('evalue', '')}"))
                elif isinstance(o.get("data"), dict) and "text/plain" in o["data"]:
                    d = o["data"]["text/plain"]
                    outs.append(html.escape("".join(d) if isinstance(d, list) else str(d)))
            rows.append(f"<pre class='code'>{t}</pre>" + "".join(f"<pre class='out'>{o}</pre>" for o in outs))
        else:
            rows.append(f"<div class='md'><pre>{t}</pre></div>")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{html.escape(title)}</title><style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem}}pre{{white-space:pre-wrap;padding:.6rem;border-radius:6px}}"
            ".code{background:#f4f4f5}.out{background:#fff;border-left:3px solid #94a3b8}.md pre{font-family:inherit}</style></head><body>" + "".join(rows) + "</body></html>")


class NotebookService(DevService):
    def _files(self) -> FileService:
        return FileService(self.ctx, self.ws)

    def create(self, principal, project_id: Any, path: Any, title: Any = None) -> Dict[str, Any]:
        self.gate(principal)                          # permission first: an ordinary user must not learn anything from input validation
        rel = safe_path(path)
        if not rel.endswith(".ipynb"):
            raise invalid("path", "Notebook files end in .ipynb.")
        doc = blank()
        if isinstance(title, str) and title.strip():
            doc["cells"].append({"cell_type": "markdown", "metadata": {}, "source": "# " + title.strip()[:120]})
        doc["cells"].append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": ""})
        return self._files().write(principal, project_id, rel, json.dumps(doc, indent=1), None)

    def _load(self, principal, project_id: Any, path: Any):
        self.gate(principal)
        rel = safe_path(path)
        if not rel.endswith(".ipynb"):
            raise invalid("path", "Choose an .ipynb notebook.")
        data = self._files().read(principal, project_id, rel)
        try:
            doc = json.loads(data["content"])
        except (ValueError, TypeError, KeyError):
            raise dev_error("NOTEBOOK_INVALID", "This file is not valid notebook JSON.", 422)
        return rel, data, doc

    def info(self, principal, project_id: Any, path: Any) -> Dict[str, Any]:
        rel, data, doc = self._load(principal, project_id, path)
        problems = validate(doc)
        cells = doc.get("cells", []) if not problems else doc["cells"]
        secret_hits = sum(1 for c in cells if isinstance(c, dict) and contains_secret_pattern(src(c)))
        return {"path": rel, "version": data["version"], "valid": not problems, "problems": problems, "cells": len(cells) if isinstance(cells, list) else 0,
                "code_cells": sum(1 for c in cells if isinstance(c, dict) and c.get("cell_type") == "code") if isinstance(cells, list) else 0,
                "has_outputs": any(isinstance(c, dict) and c.get("outputs") for c in cells) if isinstance(cells, list) else False, "possible_secrets_in_cells": secret_hits,
                "exports": self.export_formats()}

    def export_formats(self) -> Dict[str, Any]:
        return {"ipynb": {"available": True}, "py": {"available": True}, "html": {"available": True},
                "pdf": {"available": False, "reason": "PDF export needs nbconvert and a TeX installation, which are not part of this build. Export HTML and print it to PDF from your browser."}}

    def export(self, principal, project_id: Any, path: Any, fmt: Any, strip_outputs: Any = False) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if fmt not in EXPORTS:
            raise invalid("format", "Choose ipynb, py or html.")
        if fmt == "pdf":
            raise dev_error("EXPORT_UNAVAILABLE", self.export_formats()["pdf"]["reason"], 501)
        rel, data, doc = self._load(principal, project_id, path)
        problems = validate(doc)
        if problems:
            raise dev_error("NOTEBOOK_INVALID", problems[0], 422)
        if strip_outputs is True or fmt == "ipynb" and strip_outputs:
            for c in doc["cells"]:
                if c.get("cell_type") == "code":
                    c["outputs"], c["execution_count"] = [], None
        text = json.dumps(doc, indent=1) if fmt == "ipynb" else to_py(doc) if fmt == "py" else to_html(doc, rel)
        hits = [i + 1 for i, c in enumerate(doc["cells"]) if contains_secret_pattern(src(c))]
        self.event(project["id"], principal, "notebook.exported", "file", rel, format=fmt, cells=len(doc["cells"]))
        return {"path": rel, "format": fmt, "content_type": EXPORTS[fmt], "filename": rel.rsplit("/", 1)[-1].rsplit(".", 1)[0] + "." + fmt, "content": text,
                "warnings": ([f"Cells {hits[:10]} look like they contain a credential. Check before sharing."] if hits else [])}
