"""Application configuration, loaded from environment variables.

Real environment variables always win over values in the repository-root ``.env``
file. Settings are validated once at start-up and every problem is reported
together, so a misconfigured deployment fails fast with a useful message.
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from dotenv import dotenv_values

ENVIRONMENTS = ("development", "test", "staging", "production")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_FORMATS = ("text", "json")
DEFAULT_DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

# backend/app/core/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[3]
DOTENV_PATH = REPO_ROOT / ".env"
DATA_DIR = REPO_ROOT / "data"  # git-ignored: local database, dev secret, dev email outbox

EMAIL_BACKENDS = ("file", "smtp", "disabled")
SMTP_SECURITY = ("starttls", "ssl", "none")
PASSWORD_HASH_STANDARD = (2**15, 8, 3)  # scrypt N, r, p (OWASP-aligned, ~32 MiB)
PASSWORD_HASH_TEST = (2**10, 8, 1)  # only ever used when PREDICTAI_ENV=test
_PLACEHOLDER_SECRET = re.compile(r"change.?me|replace.?me|example|secret.?key|password|default", re.I)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when configuration is invalid. The message lists every problem found."""


@dataclass(frozen=True)
class Settings:
    environment: str
    host: str
    port: int
    cors_allowed_origins: Tuple[str, ...]
    log_level: str
    log_format: str
    docs_enabled: bool
    service_name: str = "predictai-api"
    api_prefix: str = "/api/v1"
    # ---- Phase 2: identity and security ----
    database_path: str = str(DATA_DIR / "predictai.db")
    # ---- Phase 11 (Turso): which database the app uses. "sqlite" (default, a local file) or "turso" (a remote libSQL database). ----
    database_backend: str = "sqlite"
    turso_database_url: str = ""
    turso_auth_token: str = field(default="", repr=False)   # never logged, never returned by the API
    turso_mode: str = "remote"                              # "remote": every query goes to Turso; "replica": reads from a local copy that syncs with Turso
    turso_replica_path: str = ""                            # local copy for replica mode (a cache only: the data lives in Turso)
    turso_sync_interval: int = 0                            # replica mode: seconds between automatic syncs (0 = sync after each write transaction)
    auto_migrate: bool = True
    secret_key: bytes = field(default=b"", repr=False)
    frontend_base_url: str = "http://localhost:5173"
    session_cookie_name: str = "pai_session"
    cookie_secure: bool = False
    session_idle_minutes: int = 1440
    session_absolute_hours: int = 168
    owner_session_idle_minutes: int = 60
    owner_session_absolute_hours: int = 12
    step_up_minutes: int = 10
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    audit_retention_days: int = 365
    require_email_verification: bool = False
    notify_security_events: bool = True
    email_backend: str = "file"
    email_outbox_dir: str = str(DATA_DIR / "outbox")
    email_from: str = "PredictAI <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    # Phase 8: optional owner bootstrap from the environment (never exposed to the browser; secrets are kept out of repr/logs).
    owner_bootstrap_name: str = ""
    owner_bootstrap_email: str = ""
    owner_bootstrap_password: str = field(default="", repr=False)
    owner_bootstrap_code: str = field(default="", repr=False)
    # Phase 8: research workspace foundation limits (Phase 10 will use them). Special accounts are capped only by the safeguard values.
    standard_max_research_workspaces: int = 5
    standard_max_research_sources: int = 20
    safeguard_max_research_workspaces: int = 200
    safeguard_max_research_sources: int = 500
    # Phase 10 (Research Intelligence). Independent of the Phase 6 research engine; see docs/phase10.
    ri_root: str = ""                          # original files and extracted artefacts (default: <storage_dir>/ri)
    ri_max_upload_mb_standard: int = 10
    ri_max_upload_mb_special: int = 50
    ri_pdf_max_pages: int = 400
    ri_chunk_words: int = 220
    ri_chunk_overlap_words: int = 40
    ri_max_chunks_per_source: int = 3000
    ri_ai_requests_per_day_standard: int = 30
    ri_ai_requests_per_day_special: int = 300
    ri_imports_per_day_standard: int = 40      # source additions (uploads, URLs, DOIs) per account per day
    ri_imports_per_day_special: int = 400
    ri_vector_backend: str = "sqlite"          # sqlite | qdrant
    ri_qdrant_url: str = ""                    # e.g. http://127.0.0.1:6333 ; the API key (if any) is read from PREDICTAI_RI_QDRANT_API_KEY at call time
    ri_qdrant_collection: str = "predictai_research"
    ri_malware_scan: str = "auto"              # auto (scan when clamscan exists) | off | required (refuse imports when no scanner)
    ri_allow_private_urls: bool = False        # tests / a trusted LAN only; never honoured when hosted
    ri_url_max_mb: int = 25
    ri_url_timeout_s: int = 20
    ri_worker_lease_s: int = 300
    ri_job_max_attempts: int = 3
    ri_extract_timeout_s: int = 90
    ri_extract_memory_mb: int = 1536
    ri_crossref_mailto: str = ""               # optional contact address for Crossref's and OpenAlex's polite pools
    ri_discovery_providers: str = "crossref,openalex,arxiv,semanticscholar,searxng"   # providers a search uses by default (API keys come from the environment at call time, never from here)
    ri_discovery_per_provider: int = 25
    ri_discovery_timeout_s: int = 15
    ri_discovery_history_max: int = 100
    ri_analyses_per_day_standard: int = 10
    ri_analyses_per_day_special: int = 100
    ri_analysis_max_sources: int = 100
    # Phase 9 (Development Workspace). Execution is OFF unless the operator turns it on; see docs/phase9.
    dev_execution: str = "off"                 # off | local_trusted | docker
    dev_root: str = ""                         # working trees, environments and job directories (default: <storage_dir>/dev)
    dev_jupyter_command: str = ""              # e.g. "jupyter-lab" ; empty = auto-detect on PATH
    dev_ide_command: str = ""                  # e.g. "code-server" ; empty = auto-detect on PATH
    dev_git_allow_local: bool = False          # allow file:// and local-path remotes (development and tests only)
    dev_git_hosts: str = "github.com,gitlab.com"
    dev_pip_index_urls: str = "https://pypi.org/simple"
    dev_pipelines_enabled: bool = False
    dev_deploy_enabled: bool = False
    dev_job_wall_seconds: int = 300
    dev_job_cpu_seconds: int = 300
    dev_job_memory_mb: int = 512
    dev_job_output_kb: int = 1024
    dev_job_max_procs: int = 32
    dev_job_storage_mb: int = 200
    dev_user_concurrency: int = 1
    dev_project_concurrency: int = 1
    dev_queue_depth: int = 20
    dev_project_storage_mb: int = 200
    dev_user_cpu_seconds_per_day: int = 3600
    dev_idle_minutes: int = 30
    dev_max_sessions: int = 2
    smtp_security: str = "starttls"
    trusted_proxy_count: int = 0
    max_owners: int = 3
    oauth_redirect_base_url: str = ""
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = field(default="", repr=False)
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = field(default="", repr=False)
    password_hash_params: Tuple[int, int, int] = PASSWORD_HASH_STANDARD
    # ---- Phase 3: projects, data workspace, governance ----
    storage_dir: str = str(DATA_DIR / "storage")
    analytics_isolation: str = "subprocess"
    ml_isolation: str = "subprocess"
    ml_workers: int = 2
    # ---- Phase 6: research, web access, world intelligence, provider routing (everything outbound is OFF unless switched on here AND by the person) ----
    research_web_enabled: bool = False
    research_workers: int = 2
    research_allow_private_network: bool = False
    research_contact: str = ""
    research_cache_hours: int = 24
    research_cache_max_mb: int = 100
    research_fetch_timeout_s: int = 15
    searxng_url: str = ""
    ai_allow_paid: bool = False
    ai_provider: str = "none"
    ai_base_url: str = "http://127.0.0.1:11434"
    ai_model: str = ""
    ai_allow_remote_host: bool = False
    ai_external_default: bool = True
    ai_local_first_tasks: tuple = ("classify", "extract", "embed")
    ai_timeout_s: int = 60
    upload_max_bytes: int = 50 * 1024 * 1024
    upload_chunk_max_bytes: int = 4 * 1024 * 1024
    upload_session_ttl_hours: int = 24
    standard_max_documents: int = 3
    standard_max_pages: int = 100
    parse_max_rows: int = 500_000
    parse_max_columns: int = 1000
    ocr_enabled: bool = True
    ocr_languages: str = "eng"
    ocr_max_pages: int = 300
    preview_ttl_minutes: int = 60

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_hosted(self) -> bool:
        return self.environment in ("staging", "production")

    @property
    def allowed_origins(self) -> Tuple[str, ...]:
        """Origins trusted for credentialed requests: CORS origins plus the frontend origin."""
        merged = list(self.cors_allowed_origins)
        if self.frontend_base_url not in merged:
            merged.append(self.frontend_base_url)
        return tuple(merged)


def _parse_bool(name: str, raw: str, problems: List[str]) -> Optional[bool]:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    problems.append(f"{name} must be true or false (got {raw!r}).")
    return None


def _parse_port(name: str, raw: str, problems: List[str]) -> int:
    try:
        port = int(raw.strip())
    except ValueError:
        problems.append(f"{name} must be an integer (got {raw!r}).")
        return 8000
    if not 1 <= port <= 65535:
        problems.append(f"{name} must be between 1 and 65535 (got {port}).")
        return 8000
    return port


def _parse_origins(name: str, raw: str, problems: List[str]) -> Tuple[str, ...]:
    origins: List[str] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        if item == "*":
            problems.append(f"{name} must list explicit origins; '*' is not allowed.")
            continue
        parts = urlsplit(item)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.netloc)
            and f"{parts.scheme}://{parts.netloc}" == item
        )
        if not valid:
            problems.append(
                f"{name} entry {item!r} must look like https://host[:port] "
                "(scheme and host only, no path or trailing slash)."
            )
            continue
        if item not in origins:
            origins.append(item)
    return tuple(origins)


def _parse_int(env: Mapping[str, str], name: str, default: int, low: int, high: int, problems: List[str]) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        problems.append(f"{name} must be an integer (got {raw!r}).")
        return default
    if not low <= value <= high:
        problems.append(f"{name} must be between {low} and {high} (got {value}).")
        return default
    return value



def _ri_choice(env: Mapping[str, str], name: str, allowed: tuple, default: str, problems: List[str]) -> str:
    value = (env.get(name) or default).strip().lower()
    if value not in allowed:
        problems.append(f"{name} must be one of {', '.join(allowed)}; using {default}.")
        return default
    return value


def _ri_backend(env: Mapping[str, str], problems: List[str]) -> str:
    return _ri_choice(env, "PREDICTAI_RI_VECTOR_BACKEND", ("sqlite", "qdrant"), "sqlite", problems)


def _ri_providers(env: Mapping[str, str], problems: List[str]) -> str:
    default = "crossref,openalex,arxiv,semanticscholar,searxng"
    raw = (env.get("PREDICTAI_RI_DISCOVERY_PROVIDERS") or default).strip().lower()
    names = [n.strip() for n in raw.split(",") if n.strip()]
    bad = [n for n in names if n not in ("crossref", "openalex", "arxiv", "semanticscholar", "searxng")]
    if bad or not names:
        problems.append(f"PREDICTAI_RI_DISCOVERY_PROVIDERS may list crossref, openalex, arxiv, semanticscholar, searxng (got {raw!r}); using the default.")
        return default
    return ",".join(dict.fromkeys(names))


def _flag(env: Mapping[str, str], name: str, default: bool, problems: List[str]) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    parsed = _parse_bool(name, raw, problems)
    return default if parsed is None else parsed


def _origin(env: Mapping[str, str], name: str, default: str, problems: List[str]) -> str:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    parsed = _parse_origins(name, raw.strip(), problems)
    return parsed[0] if parsed else default


def _dev_secret_from_file() -> str:
    """A random, per-machine development secret kept in the git-ignored data directory."""
    path = DATA_DIR / "dev-secret-key"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def _resolve_secret(env: Mapping[str, str], environment: str, hosted: bool, persist_dev: bool, problems: List[str]) -> bytes:
    raw = (env.get("PREDICTAI_SECRET_KEY") or "").strip()
    if raw:
        if len(raw) < 32:
            problems.append("PREDICTAI_SECRET_KEY must be at least 32 characters (try: python -c \"import secrets; print(secrets.token_urlsafe(48))\").")
        elif _PLACEHOLDER_SECRET.search(raw):
            problems.append("PREDICTAI_SECRET_KEY looks like a placeholder; generate a random value.")
        return raw.encode("utf-8")
    if hosted:
        problems.append(f"PREDICTAI_SECRET_KEY must be set when PREDICTAI_ENV={environment}.")
        return b""
    if environment == "development" and persist_dev:
        return _dev_secret_from_file().encode("utf-8")
    return secrets.token_urlsafe(48).encode("utf-8")  # test and explicit-env loads: ephemeral


_TURSO_SCHEMES = ("libsql://", "https://")
_LOCAL_DEV_SCHEMES = ("http://127.0.0.1", "http://localhost", "ws://127.0.0.1", "ws://localhost")


def _database_choice(env: Mapping[str, str], environment: str, problems: List[str]) -> Tuple[str, str, str, str, str, int]:
    """Phase 11: PREDICTAI_DATABASE_BACKEND=sqlite|turso and the Turso settings. Problems never include the token's value."""
    backend = (env.get("PREDICTAI_DATABASE_BACKEND") or "sqlite").strip().lower()
    url = (env.get("TURSO_DATABASE_URL") or "").strip()
    token = (env.get("TURSO_AUTH_TOKEN") or "").strip()
    mode = (env.get("PREDICTAI_TURSO_MODE") or "remote").strip().lower()
    replica = (env.get("PREDICTAI_TURSO_REPLICA_PATH") or "").strip()
    interval_raw = (env.get("PREDICTAI_TURSO_SYNC_INTERVAL") or "0").strip()
    if backend not in ("sqlite", "turso"):
        problems.append(f"PREDICTAI_DATABASE_BACKEND must be sqlite or turso (got {backend!r}).")
        return "sqlite", "", "", "remote", "", 0
    try:
        interval = int(interval_raw)
    except ValueError:
        problems.append(f"PREDICTAI_TURSO_SYNC_INTERVAL must be a whole number of seconds (got {interval_raw!r}).")
        interval = 0
    if not 0 <= interval <= 3600:
        problems.append("PREDICTAI_TURSO_SYNC_INTERVAL must be between 0 and 3600.")
        interval = 0
    if backend == "sqlite":
        if environment == "production" and (env.get("RENDER") or "").strip().lower() == "true":
            problems.append("PREDICTAI_DATABASE_BACKEND=sqlite on Render keeps the database on a temporary disk that is wiped on every deploy and restart. "
                            "Set PREDICTAI_DATABASE_BACKEND=turso with TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
        return "sqlite", "", "", "remote", "", 0
    if mode not in ("remote", "replica"):
        problems.append(f"PREDICTAI_TURSO_MODE must be remote or replica (got {mode!r}).")
        mode = "remote"
    local_dev = url.startswith(_LOCAL_DEV_SCHEMES)
    if not url:
        problems.append("TURSO_DATABASE_URL must be set when PREDICTAI_DATABASE_BACKEND=turso (it looks like libsql://<database>-<org>.turso.io).")
    elif not (url.startswith(_TURSO_SCHEMES) or local_dev):
        problems.append("TURSO_DATABASE_URL must start with libsql:// or https:// (an encrypted connection).")
    elif local_dev and environment != "development":
        problems.append("TURSO_DATABASE_URL points at an unencrypted local server; that is only allowed when PREDICTAI_ENV=development.")
    if "authToken=" in url or "@" in url.split("://", 1)[-1].split("/", 1)[0]:
        problems.append("TURSO_DATABASE_URL must not contain credentials; put the token in TURSO_AUTH_TOKEN.")
    if not token and not local_dev:
        problems.append("TURSO_AUTH_TOKEN must be set when PREDICTAI_DATABASE_BACKEND=turso (create one with: turso db tokens create <database>).")
    elif token and (any(ch.isspace() for ch in token) or len(token) < 20):
        problems.append("TURSO_AUTH_TOKEN does not look like a Turso token (it should be one long string without spaces).")
    return "turso", url, token, mode, replica, interval


def _read_environment() -> Mapping[str, str]:
    """Merge the repository-root .env file under the real environment."""
    merged = {k: v for k, v in dotenv_values(DOTENV_PATH).items() if v is not None}
    merged.update(os.environ)
    return merged


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Build validated settings. Pass ``env`` to bypass the process environment (tests)."""
    persist_dev_secret = env is None
    if env is None:
        env = _read_environment()
    problems: List[str] = []

    environment = env.get("PREDICTAI_ENV", "development").strip().lower()
    if environment not in ENVIRONMENTS:
        problems.append(f"PREDICTAI_ENV must be one of {', '.join(ENVIRONMENTS)} (got {environment!r}).")
        environment = "development"
    hosted = environment in ("staging", "production")

    host = env.get("PREDICTAI_API_HOST", "127.0.0.1").strip()
    if not host:
        problems.append("PREDICTAI_API_HOST must not be empty.")
        host = "127.0.0.1"
    port = _parse_port("PREDICTAI_API_PORT", env.get("PREDICTAI_API_PORT", "8000"), problems)

    raw_origins = env.get("PREDICTAI_CORS_ALLOWED_ORIGINS")
    if raw_origins is None:
        origins: Tuple[str, ...] = () if hosted else DEFAULT_DEV_ORIGINS
        if hosted:
            problems.append(
                "PREDICTAI_CORS_ALLOWED_ORIGINS must be set explicitly when "
                f"PREDICTAI_ENV={environment} (an empty value means same-origin only)."
            )
    else:
        origins = _parse_origins("PREDICTAI_CORS_ALLOWED_ORIGINS", raw_origins, problems)
    if environment == "production":
        for origin in origins:
            if origin.startswith("http://"):
                problems.append(f"PREDICTAI_CORS_ALLOWED_ORIGINS entry {origin!r} must use https in production.")

    log_level = env.get("PREDICTAI_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in LOG_LEVELS:
        problems.append(f"PREDICTAI_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)} (got {log_level!r}).")
        log_level = "INFO"
    log_format = env.get("PREDICTAI_LOG_FORMAT", "json" if hosted else "text").strip().lower()
    if log_format not in LOG_FORMATS:
        problems.append(f"PREDICTAI_LOG_FORMAT must be one of {', '.join(LOG_FORMATS)} (got {log_format!r}).")
        log_format = "text"

    docs_enabled = not hosted
    raw_docs = env.get("PREDICTAI_API_DOCS_ENABLED")
    if raw_docs is not None:
        parsed = _parse_bool("PREDICTAI_API_DOCS_ENABLED", raw_docs, problems)
        if parsed is not None:
            docs_enabled = parsed


    # ---- Phase 2: identity and security ----
    secret_key = _resolve_secret(env, environment, hosted, persist_dev_secret, problems)
    database_path = (env.get("PREDICTAI_DATABASE_PATH") or "").strip() or str(DATA_DIR / "predictai.db")
    database_backend, turso_url, turso_token, turso_mode, turso_replica_path, turso_sync_interval = _database_choice(env, environment, problems)
    auto_migrate = _flag(env, "PREDICTAI_AUTO_MIGRATE", not hosted, problems)

    frontend_base_url = _origin(env, "PREDICTAI_FRONTEND_BASE_URL", "http://localhost:5173", problems)
    if hosted and not (env.get("PREDICTAI_FRONTEND_BASE_URL") or "").strip():
        problems.append(f"PREDICTAI_FRONTEND_BASE_URL must be set when PREDICTAI_ENV={environment}.")
    elif environment == "production" and frontend_base_url.startswith("http://"):
        problems.append("PREDICTAI_FRONTEND_BASE_URL must use https in production.")

    cookie_secure = _flag(env, "PREDICTAI_COOKIE_SECURE", hosted, problems)
    if hosted and not cookie_secure:
        problems.append("PREDICTAI_COOKIE_SECURE must be true in staging and production.")
    cookie_name = (env.get("PREDICTAI_SESSION_COOKIE_NAME") or ("__Host-pai_session" if hosted else "pai_session")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,40}", cookie_name):
        problems.append("PREDICTAI_SESSION_COOKIE_NAME may only contain letters, digits, '_' and '-' (3-40 characters).")
    elif cookie_name.startswith("__Host-") and not cookie_secure:
        problems.append("A __Host- cookie name requires PREDICTAI_COOKIE_SECURE=true.")

    session_idle = _parse_int(env, "PREDICTAI_SESSION_IDLE_MINUTES", 1440, 5, 43200, problems)
    session_abs = _parse_int(env, "PREDICTAI_SESSION_ABSOLUTE_HOURS", 168, 1, 720, problems)
    owner_idle = _parse_int(env, "PREDICTAI_OWNER_SESSION_IDLE_MINUTES", 60, 5, 43200, problems)
    owner_abs = _parse_int(env, "PREDICTAI_OWNER_SESSION_ABSOLUTE_HOURS", 12, 1, 720, problems)
    step_up = _parse_int(env, "PREDICTAI_STEP_UP_MINUTES", 10, 1, 60, problems)
    max_failures = _parse_int(env, "PREDICTAI_LOGIN_MAX_FAILURES", 5, 3, 20, problems)
    lockout = _parse_int(env, "PREDICTAI_LOGIN_LOCKOUT_MINUTES", 15, 1, 1440, problems)
    retention = _parse_int(env, "PREDICTAI_AUDIT_RETENTION_DAYS", 365, 30, 3650, problems)
    max_owners = _parse_int(env, "PREDICTAI_MAX_OWNERS", 3, 1, 10, problems)
    proxies = _parse_int(env, "PREDICTAI_TRUSTED_PROXY_COUNT", 0, 0, 5, problems)

    require_verification = _flag(env, "PREDICTAI_REQUIRE_EMAIL_VERIFICATION", hosted, problems)
    notify = _flag(env, "PREDICTAI_NOTIFY_SECURITY_EVENTS", True, problems)

    smtp_host = (env.get("PREDICTAI_SMTP_HOST") or "").strip()
    smtp_port = _parse_int(env, "PREDICTAI_SMTP_PORT", 587, 1, 65535, problems)
    smtp_security = (env.get("PREDICTAI_SMTP_SECURITY") or "starttls").strip().lower()
    if smtp_security not in SMTP_SECURITY:
        problems.append(f"PREDICTAI_SMTP_SECURITY must be one of {', '.join(SMTP_SECURITY)} (got {smtp_security!r}).")
        smtp_security = "starttls"
    default_backend = "smtp" if smtp_host else ("disabled" if hosted else "file")
    email_backend = (env.get("PREDICTAI_EMAIL_BACKEND") or default_backend).strip().lower()
    if email_backend not in EMAIL_BACKENDS:
        problems.append(f"PREDICTAI_EMAIL_BACKEND must be one of {', '.join(EMAIL_BACKENDS)} (got {email_backend!r}).")
        email_backend = "disabled"
    email_from = (env.get("PREDICTAI_EMAIL_FROM") or "PredictAI <no-reply@localhost>").strip()
    if email_backend == "smtp":
        if not smtp_host:
            problems.append("PREDICTAI_SMTP_HOST must be set when PREDICTAI_EMAIL_BACKEND=smtp.")
        if hosted and not (env.get("PREDICTAI_EMAIL_FROM") or "").strip():
            problems.append("PREDICTAI_EMAIL_FROM must be set when sending real email.")
        if hosted and smtp_security == "none":
            problems.append("PREDICTAI_SMTP_SECURITY=none is not allowed in staging or production.")
    if hosted and email_backend == "file":
        problems.append("The file email backend writes tokens to disk and is for development only.")
    if hosted and require_verification and email_backend != "smtp":
        problems.append(
            "Email verification needs a working email backend: configure PREDICTAI_SMTP_HOST, "
            "or set PREDICTAI_REQUIRE_EMAIL_VERIFICATION=false explicitly."
        )

    google_id = (env.get("PREDICTAI_OAUTH_GOOGLE_CLIENT_ID") or "").strip()
    google_secret = (env.get("PREDICTAI_OAUTH_GOOGLE_CLIENT_SECRET") or "").strip()
    github_id = (env.get("PREDICTAI_OAUTH_GITHUB_CLIENT_ID") or "").strip()
    github_secret = (env.get("PREDICTAI_OAUTH_GITHUB_CLIENT_SECRET") or "").strip()
    for label, cid, csecret in (("GOOGLE", google_id, google_secret), ("GITHUB", github_id, github_secret)):
        if bool(cid) != bool(csecret):
            problems.append(f"PREDICTAI_OAUTH_{label}_CLIENT_ID and _CLIENT_SECRET must be set together.")
    oauth_base = _origin(env, "PREDICTAI_OAUTH_REDIRECT_BASE_URL", frontend_base_url, problems)
    if (google_id or github_id) and environment == "production" and oauth_base.startswith("http://"):
        problems.append("PREDICTAI_OAUTH_REDIRECT_BASE_URL must use https in production.")

    dev_execution = (env.get("PREDICTAI_DEV_EXECUTION") or "off").strip().lower()
    if dev_execution not in ("off", "local_trusted", "docker"):
        problems.append("PREDICTAI_DEV_EXECUTION must be off, local_trusted or docker.")
        dev_execution = "off"
    if hosted and dev_execution == "local_trusted":
        problems.append("PREDICTAI_DEV_EXECUTION=local_trusted is not allowed when hosted: it is not a multi-user sandbox. Use docker or off.")
        dev_execution = "off"
    storage_dir = (env.get("PREDICTAI_STORAGE_DIR") or "").strip() or str(DATA_DIR / "storage")
    analytics_isolation = (env.get("PREDICTAI_ANALYTICS_ISOLATION") or "subprocess").strip().lower()
    if analytics_isolation not in ("subprocess", "inprocess"):
        problems.append("PREDICTAI_ANALYTICS_ISOLATION must be 'subprocess' or 'inprocess'.")
        analytics_isolation = "subprocess"
    if hosted and analytics_isolation != "subprocess":
        problems.append("PREDICTAI_ANALYTICS_ISOLATION=inprocess is for tests and development only; use subprocess when hosted.")
    ml_isolation = (env.get("PREDICTAI_ML_ISOLATION") or "subprocess").strip().lower()
    if ml_isolation not in ("subprocess", "inprocess"):
        problems.append("PREDICTAI_ML_ISOLATION must be 'subprocess' or 'inprocess'.")
        ml_isolation = "subprocess"
    if hosted and ml_isolation != "subprocess":
        problems.append("PREDICTAI_ML_ISOLATION=inprocess is for tests and development only; use subprocess when hosted.")
    ml_workers = _parse_int(env, "PREDICTAI_ML_WORKERS", 2, 1, 8, problems)
    research_web = _flag(env, "PREDICTAI_RESEARCH_WEB_ENABLED", False, problems)
    research_workers = _parse_int(env, "PREDICTAI_RESEARCH_WORKERS", 2, 1, 8, problems)
    research_private = _flag(env, "PREDICTAI_RESEARCH_ALLOW_PRIVATE_NETWORK", False, problems)
    if research_private and hosted:
        problems.append("PREDICTAI_RESEARCH_ALLOW_PRIVATE_NETWORK=true is for local development only; it lets research fetches reach private addresses, so it is refused when hosted.")
        research_private = False
    research_contact = (env.get("PREDICTAI_RESEARCH_CONTACT") or "").strip()[:120]
    if any(ch in research_contact for ch in "\r\n\t"):
        problems.append("PREDICTAI_RESEARCH_CONTACT must be a single line (an email address or a URL).")
        research_contact = ""
    research_cache_hours = _parse_int(env, "PREDICTAI_RESEARCH_CACHE_HOURS", 24, 1, 720, problems)
    research_cache_mb = _parse_int(env, "PREDICTAI_RESEARCH_CACHE_MAX_MB", 100, 1, 5000, problems)
    research_fetch_timeout = _parse_int(env, "PREDICTAI_RESEARCH_FETCH_TIMEOUT_SECONDS", 15, 3, 60, problems)
    searxng_url = (env.get("PREDICTAI_SEARXNG_URL") or "").strip().rstrip("/")
    if searxng_url and (urlsplit(searxng_url).scheme not in ("http", "https") or not urlsplit(searxng_url).hostname or urlsplit(searxng_url).username):
        problems.append("PREDICTAI_SEARXNG_URL must be an http(s) URL without credentials, for example http://127.0.0.1:8080.")
        searxng_url = ""
    ai_allow_paid = _flag(env, "PREDICTAI_AI_ALLOW_PAID", False, problems)
    ai_provider = (env.get("PREDICTAI_AI_PROVIDER") or "none").strip().lower()
    if ai_provider not in ("none", "ollama"):
        problems.append("PREDICTAI_AI_PROVIDER must be 'none' or 'ollama'.")
        ai_provider = "none"
    ai_base_url = (env.get("PREDICTAI_AI_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
    ai_allow_remote = _flag(env, "PREDICTAI_AI_ALLOW_REMOTE_HOST", False, problems)
    ai_external_default = _flag(env, "PREDICTAI_AI_EXTERNAL_DEFAULT", True, problems)
    ai_local_first = tuple(t for t in (x.strip().lower() for x in (env.get("PREDICTAI_AI_LOCAL_FIRST_TASKS") or "classify,extract,embed").split(",")) if t)
    ai_model = (env.get("PREDICTAI_AI_MODEL") or "").strip()
    if ai_provider == "ollama":
        host = (urlsplit(ai_base_url).hostname or "").lower()
        if urlsplit(ai_base_url).scheme not in ("http", "https") or not host:
            problems.append("PREDICTAI_AI_BASE_URL must be an http(s) URL.")
        elif host not in ("127.0.0.1", "localhost", "::1") and not ai_allow_remote:
            problems.append("PREDICTAI_AI_BASE_URL points to another machine. Result summaries would leave this server; set PREDICTAI_AI_ALLOW_REMOTE_HOST=true only if you accept that.")
        if not ai_model:
            problems.append("PREDICTAI_AI_MODEL must name the local model (for example one you pulled with Ollama) when PREDICTAI_AI_PROVIDER=ollama.")
    ai_timeout = _parse_int(env, "PREDICTAI_AI_TIMEOUT_SECONDS", 60, 5, 600, problems)
    upload_max_mb = _parse_int(env, "PREDICTAI_UPLOAD_MAX_MB", 50, 1, 2048, problems)
    chunk_max_mb = _parse_int(env, "PREDICTAI_UPLOAD_CHUNK_MAX_MB", 4, 1, 32, problems)
    upload_ttl = _parse_int(env, "PREDICTAI_UPLOAD_SESSION_TTL_HOURS", 24, 1, 168, problems)
    std_docs = _parse_int(env, "PREDICTAI_STANDARD_MAX_DOCUMENTS", 3, 0, 1000, problems)
    std_pages = _parse_int(env, "PREDICTAI_STANDARD_MAX_PAGES", 100, 1, 5000, problems)
    max_rows = _parse_int(env, "PREDICTAI_PARSE_MAX_ROWS", 500_000, 100, 10_000_000, problems)
    max_cols = _parse_int(env, "PREDICTAI_PARSE_MAX_COLUMNS", 1000, 1, 20_000, problems)
    ocr_enabled = _flag(env, "PREDICTAI_OCR_ENABLED", True, problems)
    ocr_languages = (env.get("PREDICTAI_OCR_LANGUAGES") or "eng").strip()
    if not re.fullmatch(r"[a-z_]{3,12}(\+[a-z_]{3,12})*", ocr_languages):
        problems.append("PREDICTAI_OCR_LANGUAGES must look like 'eng' or 'eng+hin'.")
        ocr_languages = "eng"
    ocr_max_pages = _parse_int(env, "PREDICTAI_OCR_MAX_PAGES", 300, 1, 5000, problems)
    preview_ttl = _parse_int(env, "PREDICTAI_PREVIEW_TTL_MINUTES", 60, 5, 1440, problems)
    if chunk_max_mb > upload_max_mb:
        problems.append("PREDICTAI_UPLOAD_CHUNK_MAX_MB cannot exceed PREDICTAI_UPLOAD_MAX_MB.")

    if problems:
        raise ConfigError("Invalid configuration:\n- " + "\n- ".join(problems))

    built = Settings(
        environment=environment,
        host=host,
        port=port,
        cors_allowed_origins=origins,
        log_level=log_level,
        log_format=log_format,
        docs_enabled=docs_enabled,
        database_path=database_path,
        database_backend=database_backend,
        turso_database_url=turso_url,
        turso_auth_token=turso_token,
        turso_mode=turso_mode,
        turso_replica_path=turso_replica_path,
        turso_sync_interval=turso_sync_interval,
        auto_migrate=auto_migrate,
        secret_key=secret_key,
        frontend_base_url=frontend_base_url,
        session_cookie_name=cookie_name,
        cookie_secure=cookie_secure,
        session_idle_minutes=session_idle,
        session_absolute_hours=session_abs,
        owner_session_idle_minutes=owner_idle,
        owner_session_absolute_hours=owner_abs,
        step_up_minutes=step_up,
        login_max_failures=max_failures,
        login_lockout_minutes=lockout,
        audit_retention_days=retention,
        require_email_verification=require_verification,
        notify_security_events=notify,
        email_backend=email_backend,
        email_outbox_dir=(env.get("PREDICTAI_EMAIL_OUTBOX_DIR") or "").strip() or str(DATA_DIR / "outbox"),
        email_from=email_from,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_username=(env.get("PREDICTAI_SMTP_USERNAME") or "").strip(),
        smtp_password=env.get("PREDICTAI_SMTP_PASSWORD") or "",
        owner_bootstrap_name=(env.get("PREDICTAI_OWNER_NAME") or env.get("OWNER_NAME") or "").strip(),
        owner_bootstrap_email=(env.get("PREDICTAI_OWNER_EMAIL") or env.get("OWNER_EMAIL") or "").strip(),
        owner_bootstrap_password=env.get("PREDICTAI_OWNER_PASSWORD") or env.get("OWNER_PASSWORD") or "",
        owner_bootstrap_code=(env.get("PREDICTAI_OWNER_ACTIVATION_CODE") or env.get("OWNER_ACTIVATION_CODE") or "").strip(),
        standard_max_research_workspaces=_parse_int(env, "PREDICTAI_STANDARD_MAX_RESEARCH_WORKSPACES", 5, 1, 500, problems),
        standard_max_research_sources=_parse_int(env, "PREDICTAI_STANDARD_MAX_RESEARCH_SOURCES", 20, 1, 500, problems),
        safeguard_max_research_workspaces=_parse_int(env, "PREDICTAI_SAFEGUARD_MAX_RESEARCH_WORKSPACES", 200, 1, 5000, problems),
        safeguard_max_research_sources=_parse_int(env, "PREDICTAI_SAFEGUARD_MAX_RESEARCH_SOURCES", 500, 1, 5000, problems),
        ri_root=(env.get("PREDICTAI_RI_ROOT") or "").strip(),
        ri_max_upload_mb_standard=_parse_int(env, "PREDICTAI_RI_MAX_UPLOAD_MB_STANDARD", 10, 1, 500, problems),
        ri_max_upload_mb_special=_parse_int(env, "PREDICTAI_RI_MAX_UPLOAD_MB_SPECIAL", 50, 1, 2000, problems),
        ri_pdf_max_pages=_parse_int(env, "PREDICTAI_RI_PDF_MAX_PAGES", 400, 1, 5000, problems),
        ri_chunk_words=_parse_int(env, "PREDICTAI_RI_CHUNK_WORDS", 220, 60, 800, problems),
        ri_chunk_overlap_words=_parse_int(env, "PREDICTAI_RI_CHUNK_OVERLAP_WORDS", 40, 0, 200, problems),
        ri_max_chunks_per_source=_parse_int(env, "PREDICTAI_RI_MAX_CHUNKS_PER_SOURCE", 3000, 10, 50000, problems),
        ri_ai_requests_per_day_standard=_parse_int(env, "PREDICTAI_RI_AI_REQUESTS_PER_DAY_STANDARD", 30, 0, 100000, problems),
        ri_ai_requests_per_day_special=_parse_int(env, "PREDICTAI_RI_AI_REQUESTS_PER_DAY_SPECIAL", 300, 0, 100000, problems),
        ri_imports_per_day_standard=_parse_int(env, "PREDICTAI_RI_IMPORTS_PER_DAY_STANDARD", 40, 0, 100000, problems),
        ri_imports_per_day_special=_parse_int(env, "PREDICTAI_RI_IMPORTS_PER_DAY_SPECIAL", 400, 0, 100000, problems),
        ri_vector_backend=_ri_backend(env, problems),
        ri_qdrant_url=(env.get("PREDICTAI_RI_QDRANT_URL") or "").strip().rstrip("/"),
        ri_qdrant_collection=(env.get("PREDICTAI_RI_QDRANT_COLLECTION") or "predictai_research").strip(),
        ri_malware_scan=_ri_choice(env, "PREDICTAI_RI_MALWARE_SCAN", ("auto", "off", "required"), "auto", problems),
        ri_allow_private_urls=_flag(env, "PREDICTAI_RI_ALLOW_PRIVATE_URLS", False, problems) and not hosted,
        ri_url_max_mb=_parse_int(env, "PREDICTAI_RI_URL_MAX_MB", 25, 1, 500, problems),
        ri_url_timeout_s=_parse_int(env, "PREDICTAI_RI_URL_TIMEOUT_S", 20, 2, 120, problems),
        ri_worker_lease_s=_parse_int(env, "PREDICTAI_RI_WORKER_LEASE_S", 300, 30, 3600, problems),
        ri_job_max_attempts=_parse_int(env, "PREDICTAI_RI_JOB_MAX_ATTEMPTS", 3, 1, 10, problems),
        ri_extract_timeout_s=_parse_int(env, "PREDICTAI_RI_EXTRACT_TIMEOUT_S", 90, 5, 900, problems),
        ri_extract_memory_mb=_parse_int(env, "PREDICTAI_RI_EXTRACT_MEMORY_MB", 1536, 256, 16384, problems),
        ri_crossref_mailto=(env.get("PREDICTAI_RI_CROSSREF_MAILTO") or "").strip(),
        ri_discovery_providers=_ri_providers(env, problems),
        ri_discovery_per_provider=_parse_int(env, "PREDICTAI_RI_DISCOVERY_PER_PROVIDER", 25, 5, 100, problems),
        ri_discovery_timeout_s=_parse_int(env, "PREDICTAI_RI_DISCOVERY_TIMEOUT_S", 15, 3, 60, problems),
        ri_discovery_history_max=_parse_int(env, "PREDICTAI_RI_DISCOVERY_HISTORY_MAX", 100, 10, 1000, problems),
        ri_analyses_per_day_standard=_parse_int(env, "PREDICTAI_RI_ANALYSES_PER_DAY_STANDARD", 10, 0, 10000, problems),
        ri_analyses_per_day_special=_parse_int(env, "PREDICTAI_RI_ANALYSES_PER_DAY_SPECIAL", 100, 0, 10000, problems),
        ri_analysis_max_sources=_parse_int(env, "PREDICTAI_RI_ANALYSIS_MAX_SOURCES", 100, 2, 300, problems),
        dev_execution=dev_execution,
        dev_root=(env.get("PREDICTAI_DEV_ROOT") or "").strip(),
        dev_jupyter_command=(env.get("PREDICTAI_DEV_JUPYTER_COMMAND") or "").strip(),
        dev_ide_command=(env.get("PREDICTAI_DEV_IDE_COMMAND") or "").strip(),
        dev_git_allow_local=_flag(env, "PREDICTAI_DEV_GIT_ALLOW_LOCAL", False, problems) and not hosted,
        dev_git_hosts=(env.get("PREDICTAI_DEV_GIT_HOSTS") or "github.com,gitlab.com").strip().lower(),
        dev_pip_index_urls=(env.get("PREDICTAI_DEV_PIP_INDEX_URLS") or "https://pypi.org/simple").strip(),
        dev_pipelines_enabled=_flag(env, "PREDICTAI_DEV_PIPELINES_ENABLED", False, problems),
        dev_deploy_enabled=False,                # deployment is never switchable from the environment in this release
        dev_job_wall_seconds=_parse_int(env, "PREDICTAI_DEV_JOB_WALL_SECONDS", 300, 5, 3600, problems),
        dev_job_cpu_seconds=_parse_int(env, "PREDICTAI_DEV_JOB_CPU_SECONDS", 300, 5, 3600, problems),
        dev_job_memory_mb=_parse_int(env, "PREDICTAI_DEV_JOB_MEMORY_MB", 512, 64, 16384, problems),
        dev_job_output_kb=_parse_int(env, "PREDICTAI_DEV_JOB_OUTPUT_KB", 1024, 16, 65536, problems),
        dev_job_max_procs=_parse_int(env, "PREDICTAI_DEV_JOB_MAX_PROCS", 32, 1, 512, problems),
        dev_job_storage_mb=_parse_int(env, "PREDICTAI_DEV_JOB_STORAGE_MB", 200, 1, 10240, problems),
        dev_user_concurrency=_parse_int(env, "PREDICTAI_DEV_USER_CONCURRENCY", 1, 1, 16, problems),
        dev_project_concurrency=_parse_int(env, "PREDICTAI_DEV_PROJECT_CONCURRENCY", 1, 1, 16, problems),
        dev_queue_depth=_parse_int(env, "PREDICTAI_DEV_QUEUE_DEPTH", 20, 1, 500, problems),
        dev_project_storage_mb=_parse_int(env, "PREDICTAI_DEV_PROJECT_STORAGE_MB", 200, 1, 10240, problems),
        dev_user_cpu_seconds_per_day=_parse_int(env, "PREDICTAI_DEV_USER_CPU_SECONDS_PER_DAY", 3600, 10, 86400, problems),
        dev_idle_minutes=_parse_int(env, "PREDICTAI_DEV_IDLE_MINUTES", 30, 1, 1440, problems),
        dev_max_sessions=_parse_int(env, "PREDICTAI_DEV_MAX_SESSIONS", 2, 1, 16, problems),
        smtp_security=smtp_security,
        trusted_proxy_count=proxies,
        max_owners=max_owners,
        oauth_redirect_base_url=oauth_base,
        oauth_google_client_id=google_id,
        oauth_google_client_secret=google_secret,
        oauth_github_client_id=github_id,
        oauth_github_client_secret=github_secret,
        password_hash_params=PASSWORD_HASH_TEST if environment == "test" else PASSWORD_HASH_STANDARD,
        storage_dir=storage_dir,
        analytics_isolation=analytics_isolation,
        ml_isolation=ml_isolation,
        ml_workers=ml_workers,
        research_web_enabled=research_web,
        research_workers=research_workers,
        research_allow_private_network=research_private,
        research_contact=research_contact,
        research_cache_hours=research_cache_hours,
        research_cache_max_mb=research_cache_mb,
        research_fetch_timeout_s=research_fetch_timeout,
        searxng_url=searxng_url,
        ai_allow_paid=ai_allow_paid,
        ai_provider=ai_provider,
        ai_base_url=ai_base_url,
        ai_model=ai_model,
        ai_allow_remote_host=ai_allow_remote,
        ai_external_default=ai_external_default,
        ai_local_first_tasks=ai_local_first,
        ai_timeout_s=ai_timeout,
        upload_max_bytes=upload_max_mb * 1024 * 1024,
        upload_chunk_max_bytes=chunk_max_mb * 1024 * 1024,
        upload_session_ttl_hours=upload_ttl,
        standard_max_documents=std_docs,
        standard_max_pages=std_pages,
        parse_max_rows=max_rows,
        parse_max_columns=max_cols,
        ocr_enabled=ocr_enabled,
        ocr_languages=ocr_languages,
        ocr_max_pages=ocr_max_pages,
        preview_ttl_minutes=preview_ttl,
    )
    if problems:                                   # values parsed while building Settings (Phase 8-10 fields) must fail loudly too, not fall back silently
        raise ConfigError("Invalid configuration:\n- " + "\n- ".join(problems))
    return built


OAUTH_PROVIDERS = ("GOOGLE", "GITHUB")


def oauth_diagnostics(env: Optional[Mapping[str, str]] = None, dotenv_path: Optional[Path] = None) -> List[Dict[str, object]]:
    """Explain, per provider, whether sign-in with Google/GitHub is configured and, if not, why.

    Reports variable *names* and file locations only, never values. Typical causes it points out: the variables are
    missing or misspelt (``GOOGLE_CLIENT_ID`` instead of ``PREDICTAI_OAUTH_GOOGLE_CLIENT_ID``), the ``.env`` is not at
    the repository root, or the server was not restarted after editing it (settings are read once at start-up)."""
    env = _read_environment() if env is None else env
    path = DOTENV_PATH if dotenv_path is None else dotenv_path
    misplaced = [str(p) for p in (REPO_ROOT / "backend" / ".env", REPO_ROOT / "frontend" / ".env") if p.exists() and p != path]
    report: List[Dict[str, object]] = []
    for label in OAUTH_PROVIDERS:
        wanted = [f"PREDICTAI_OAUTH_{label}_CLIENT_ID", f"PREDICTAI_OAUTH_{label}_CLIENT_SECRET"]
        missing = [name for name in wanted if not (env.get(name) or "").strip()]
        near = sorted(k for k in env if label in k.upper() and any(w in k.upper() for w in ("CLIENT", "OAUTH", "SECRET")) and k not in wanted)
        hints: List[str] = []
        if missing:
            if near:
                hints.append("Found similarly named variables (" + ", ".join(near) + "); the server only reads " + " and ".join(wanted) + ".")
            if not path.exists():
                hints.append(f"No .env file at {path}; it must be in the repository root.")
            elif misplaced:
                hints.append("Another .env exists at " + ", ".join(misplaced) + "; only the repository-root file is read.")
            hints.append("Settings are read once at start-up: restart the API after editing .env.")
        report.append({"provider": label.lower(), "configured": not missing, "missing": missing, "hints": hints})
    return report


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return load_settings()
