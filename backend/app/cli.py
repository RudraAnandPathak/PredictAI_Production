"""Server-side administration: ``python -m app.cli <command>`` (run from the backend directory).

These commands act directly on the database, so they are only available to whoever can run
code on the server. Passwords are read without echo (or from stdin) and are never printed.

  migrate          apply database migrations
  provision-owner  create the first owner (or a backup owner with --allow-additional)
  recover-owner    break-glass: set a new password for an existing owner and end all its sessions
  list-owners      show who currently holds the owner role
  purge            delete data older than its retention period
  oauth-status     show whether Google/GitHub sign-in is configured and why not (names only, never secrets)
"""
from __future__ import annotations

import argparse
import getpass
import sys
from typing import Callable, List, Optional

from .core.clock import Clock
from .core.config import Settings, get_settings, oauth_diagnostics
from .core.errors import AppError
from .db import Database, migrate, open_database, pending_versions  # noqa: F401
from .modules.identity.container import build_services, run_housekeeping
from .modules.identity.context import RequestMeta
from .modules.identity.email import build_email_sender
from .modules.identity.passwords import PasswordHasher
from .modules.storage import LocalStorage
from .modules.workspace.container import build_workspace
from .modules.identity.bootstrap import bootstrap_owner_from_env
from .modules.identity.provisioning import provision_owner, recover_owner_access


def _read_password(args: argparse.Namespace, *, prompt: Callable[[str], str], stdin) -> str:
    if args.password_stdin:
        return stdin.readline().rstrip("\r\n")
    first = prompt("Password: ")
    if first != prompt("Repeat password: "):
        raise SystemExit("The passwords did not match.")
    return first


def _import_to_turso(args, settings: Settings, out: Callable[[str], None]) -> int:
    from pathlib import Path

    from .ops.turso_import import run_import, summary_lines

    if settings.database_backend != "turso":
        out("import-to-turso copies INTO Turso: set PREDICTAI_DATABASE_BACKEND=turso, TURSO_DATABASE_URL and TURSO_AUTH_TOKEN for the target first.")
        return 2
    if not Path(args.source).is_file():
        out(f"The source database {args.source} does not exist.")
        return 2
    report = run_import(args.source, open_database(settings), execute=args.execute, allow_existing=args.allow_existing, report_dir=args.report_dir)
    for line in summary_lines(report):
        out(line)
    if not args.execute and report["result"] == "ok":
        out("This was a dry run; nothing was written. Run again with --execute to copy.")
    return 0 if report["result"] == "ok" else 1


def main(argv: Optional[List[str]] = None, *, settings: Optional[Settings] = None, prompt: Callable[[str], str] = getpass.getpass,
         stdin=None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("list-owners")
    sub.add_parser("purge")
    sub.add_parser("oauth-status")
    sub.add_parser("bootstrap-owner", help="create the owner from PREDICTAI_OWNER_NAME / _EMAIL / _PASSWORD / _ACTIVATION_CODE (does nothing if an owner exists)")
    for name in ("provision-owner", "recover-owner"):
        p = sub.add_parser(name)
        p.add_argument("--email", required=True)
        p.add_argument("--password-stdin", action="store_true", help="read the password from the first line of stdin")
        if name == "provision-owner":
            p.add_argument("--display-name")
            p.add_argument("--allow-additional", action="store_true", help="add a backup owner although an owner already exists")
    imp = sub.add_parser("import-to-turso", help="copy a local SQLite database into the Turso database in TURSO_DATABASE_URL (dry run unless --execute)")
    imp.add_argument("--source", required=True, help="path to the local SQLite file (opened read-only, never changed)")
    imp.add_argument("--execute", action="store_true", help="actually copy; without it nothing is written")
    imp.add_argument("--allow-existing", action="store_true", help="merge into a target that already has user accounts (existing rows are never overwritten)")
    imp.add_argument("--report-dir", default="data/import-reports", help="where the JSON and Markdown reports go")
    args = parser.parse_args(argv)

    if args.command == "oauth-status":
        for item in oauth_diagnostics():
            out(f"{item['provider']}: " + ("configured" if item["configured"] else "NOT configured; missing " + ", ".join(item["missing"])))  # type: ignore[arg-type]
            for hint in item["hints"]:  # type: ignore[union-attr]
                out(f"  - {hint}")
        return 0

    settings = settings or get_settings()
    if args.command == "import-to-turso":
        return _import_to_turso(args, settings, out)
    db = open_database(settings)
    with db.session() as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            out(f"Applied migrations: {applied}" if applied else "Database is up to date.")
            return 0
        if pending_versions(conn):
            out("The database needs migrating first: python -m app.cli migrate")
            return 1
        services = build_services(settings=settings, conn=conn, clock=Clock(), hasher=PasswordHasher(*settings.password_hash_params),
                                  sender=build_email_sender(settings), meta=RequestMeta(request_id="cli"))
        try:
            if args.command == "bootstrap-owner":
                result = bootstrap_owner_from_env(services.ctx)
                out(f"Owner: {result['owner']}; activation code: {result['activation_code']}" + (f" ({result['reason']})" if result.get("reason") else ""))
            elif args.command == "provision-owner":
                password = None
                existing = services.ctx.repo.user_by_email(args.email.strip().lower())
                if existing is None:
                    password = _read_password(args, prompt=prompt, stdin=stdin or sys.stdin)
                result = provision_owner(services.ctx, email=args.email, password=password, display_name=args.display_name,
                                         allow_additional=args.allow_additional)
                out(("Created owner " if result["created"] else "Promoted existing account to owner ") + result["user_id"])
            elif args.command == "recover-owner":
                user_id = recover_owner_access(services.ctx, email=args.email, new_password=_read_password(args, prompt=prompt, stdin=stdin or sys.stdin))
                out(f"Password reset for owner {user_id}. All of their sessions were ended.")
            elif args.command == "list-owners":
                for owner_id in services.ctx.repo.active_owner_ids():
                    row = services.ctx.repo.user_by_id(owner_id)
                    out(f"{owner_id}  {row['email']}")
            elif args.command == "purge":
                out("Removed: " + ", ".join(f"{name}={count}" for name, count in run_housekeeping(services).items()))
                data = build_workspace(services.ctx, LocalStorage(settings.storage_dir)).governance.run_retention()
                out("Workspace: " + ", ".join(f"{name}={count}" for name, count in data.items()))
        except AppError as error:
            out(f"Error: {error.message}" + ("".join(f"\n  - {d.get('message', d)}" for d in (error.details or [])) if error.details else ""))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
