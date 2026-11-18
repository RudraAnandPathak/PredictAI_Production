"""External accounts (Google, GitHub): linking, sign-in, registration and step-up re-authentication.

One service, one flow (authorization code + PKCE + single-use `state`), four intents:
  link      the signed-in user connects a provider (state bound to that user)
  reauth    the signed-in user proves control of a connected provider for a sensitive action (state bound to that user)
  signin    an anonymous visitor signs in with an already-connected provider
  register  an anonymous visitor creates an account from a provider-verified email
Anonymous states are bound to the browser that started them by a nonce cookie (hash stored in the state's
payload), so an attacker cannot make a victim's browser finish the attacker's flow (login CSRF).
Identity is read from the provider's own userinfo endpoint over TLS (no id_token is consumed).
Rules that never bend: an account is created only from an email the provider reports as verified; an
existing PredictAI account is never merged or signed into by matching email alone (the owner must sign in
and connect the provider themselves); a provider is only usable when its client id and secret are configured.
Nothing here has been exercised against the real providers (no credentials were available); it is tested
with a fake HTTP client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional, Protocol, Tuple

from ...core.clock import to_iso
from ...core.config import Settings
from ...core.errors import AppError
from ...db import transaction
from .authentication import AuthService, IssuedSession, LoginResult, confirm_password
from .authorization import Principal, build_principal, require
from .constants import Event, Perm, Purpose, Role, Status
from .context import Context
from .errors import conflict, invalid_token, not_found
from .tokens import hash_token, new_id, new_token
from .validation import clean_display_name, normalize_email

STATE_TTL = timedelta(minutes=10)


class OAuthError(Exception):
    """A provider call failed (network, bad response). Never shown to users verbatim."""


class HttpClient(Protocol):
    def post_form(self, url: str, data: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]: ...
    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]: ...


class UrllibHttpClient:
    """Minimal HTTPS JSON client: https only, short timeout, capped response size."""

    def __init__(self, timeout: float = 10.0, max_bytes: int = 1_000_000) -> None:
        self.timeout, self.max_bytes = timeout, max_bytes

    def _call(self, request: urllib.request.Request) -> Tuple[int, Any]:
        if not request.full_url.lower().startswith("https://"):
            raise OAuthError("Only https URLs are allowed.")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 (https enforced above)
                status, raw = response.status, response.read(self.max_bytes + 1)
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read(self.max_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise OAuthError("Network error contacting the provider.") from error
        if len(raw) > self.max_bytes:
            raise OAuthError("Provider response too large.")
        try:
            return status, json.loads(raw.decode("utf-8"))
        except ValueError as error:
            raise OAuthError("Provider returned invalid JSON.") from error

    def post_form(self, url: str, data: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]:
        body = urllib.parse.urlencode(data).encode("ascii")
        request = urllib.request.Request(url, data=body, method="POST", headers={"Accept": "application/json", **(headers or {})})
        return self._call(request)

    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]:
        return self._call(urllib.request.Request(url, method="GET", headers={"Accept": "application/json", **(headers or {})}))


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: Tuple[str, ...]
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    emails_url: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def providers_from_settings(settings: Settings) -> Dict[str, Provider]:
    return {
        "google": Provider("google", "Google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                           "https://openidconnect.googleapis.com/v1/userinfo", ("openid", "email"),
                           settings.oauth_google_client_id, settings.oauth_google_client_secret),
        "github": Provider("github", "GitHub", "https://github.com/login/oauth/authorize", "https://github.com/login/oauth/access_token",
                           "https://api.github.com/user", ("read:user", "user:email"),
                           settings.oauth_github_client_id, settings.oauth_github_client_secret, "https://api.github.com/user/emails"),
    }


def _failed(message: str = "The connection could not be completed.") -> AppError:
    return AppError("OAUTH_FAILED", message, 400)


@dataclass(frozen=True)
class Profile:
    subject: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str]


@dataclass(frozen=True)
class SignInResult:
    login: LoginResult
    created: bool


def _failed(message: str = "The connection could not be completed.") -> AppError:
    return AppError("OAUTH_FAILED", message, 400)


SIGNIN_INTENTS = ("signin", "register")


class OAuthService:
    def __init__(self, ctx: Context, providers: Dict[str, Provider], http: HttpClient) -> None:
        self.ctx, self.providers, self.http = ctx, providers, http
        self.auth = AuthService(ctx)

    def _provider(self, provider_id: str) -> Provider:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise not_found()
        if not provider.configured:
            raise AppError("PROVIDER_NOT_CONFIGURED", "This sign-in provider is not configured on this server.", 503)
        return provider

    def redirect_uri(self, provider_id: str) -> str:
        s = self.ctx.settings
        return f"{s.oauth_redirect_base_url}{s.api_prefix}/auth/oauth/{provider_id}/callback"

    def available(self) -> List[Dict[str, Any]]:
        """Public: which providers can be offered on the sign-in and registration pages (no secrets, no user data)."""
        return [{"id": p.id, "label": p.label, "configured": p.configured} for p in self.providers.values()]

    def list_providers(self, principal: Principal) -> List[Dict[str, Any]]:
        require(principal, Perm.OAUTH_MANAGE_OWN)
        linked = {i["provider"]: i for i in self.ctx.repo.identities_for_user(principal.user_id) if i["provider"] != "password"}
        return [{"id": p.id, "label": p.label, "configured": p.configured, "linked": p.id in linked,
                 "linked_at": linked[p.id]["created_at"] if p.id in linked else None} for p in self.providers.values()]

    # ------------------------------------------------------------------ starting a flow
    def _start(self, provider: Provider, *, user_id: Optional[str], intent: str, nonce: Optional[str] = None) -> str:
        ctx = self.ctx
        state, verifier = new_token(32), new_token(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        now = ctx.clock.now()
        payload = {"provider": provider.id, "verifier": verifier, "intent": intent}
        if nonce is not None:
            payload["nonce"] = hash_token(nonce)
        ctx.repo.insert_token(id=new_id(), purpose=Purpose.OAUTH_STATE, token_hash=hash_token(state), user_id=user_id,
                              now=to_iso(now), expires_at=to_iso(now + STATE_TTL), payload=json.dumps(payload))
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": provider.client_id, "redirect_uri": self.redirect_uri(provider.id),
            "scope": " ".join(provider.scopes), "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        return f"{provider.authorize_url}?{query}"

    def start_link(self, principal: Principal, provider_id: str) -> str:
        require(principal, Perm.OAUTH_MANAGE_OWN)
        provider = self._provider(provider_id)
        self.ctx.limiter.hit("oauth:user", principal.user_id)
        return self._start(provider, user_id=principal.user_id, intent="link")

    def start_reauth(self, principal: Principal, provider_id: str) -> str:
        """Prove control of an already-connected provider (for accounts that have no password)."""
        require(principal, Perm.OAUTH_MANAGE_OWN)
        provider = self._provider(provider_id)
        self.ctx.limiter.hit("oauth:user", principal.user_id)
        if not any(i["provider"] == provider.id for i in self.ctx.repo.identities_for_user(principal.user_id)):
            raise conflict("OAUTH_NOT_LINKED", "Connect this provider to your account first.")
        return self._start(provider, user_id=principal.user_id, intent="reauth")

    def start_signin(self, provider_id: str, intent: str) -> Tuple[str, str]:
        """Anonymous start. Returns (authorization URL, browser-binding nonce); the caller sets the nonce as a cookie."""
        if intent not in ("login", "register"):
            raise AppError("VALIDATION_ERROR", "Choose to sign in or to register.", 422)
        provider = self._provider(provider_id)
        self.ctx.limiter.hit("oauth_login:ip", self.ctx.meta.ip or "unknown")
        nonce = new_token(24)
        return self._start(provider, user_id=None, intent="signin" if intent == "login" else "register", nonce=nonce), nonce

    def peek_intent(self, state: Any) -> Optional[str]:
        """Which kind of flow a returning `state` belongs to, without consuming it (decides how the callback is handled)."""
        if not isinstance(state, str) or not state:
            return None
        row = self.ctx.repo.token_by_hash(Purpose.OAUTH_STATE, hash_token(state))
        try:
            return json.loads(row["payload"]).get("intent") if row is not None and row["payload"] else None
        except ValueError:
            return None

    # ------------------------------------------------------------------ talking to the provider
    def _consume_state(self, provider: Provider, state: Any, *, intent: Tuple[str, ...], user_id: Optional[str], nonce: Optional[str] = None) -> Dict[str, Any]:
        ctx = self.ctx
        row = ctx.repo.token_by_hash(Purpose.OAUTH_STATE, hash_token(state)) if isinstance(state, str) and state else None
        now = ctx.clock.iso()
        payload = json.loads(row["payload"]) if row is not None and row["payload"] else {}
        bad = (row is None or row["consumed_at"] is not None or row["expires_at"] <= now or row["user_id"] != user_id
               or payload.get("provider") != provider.id or payload.get("intent") not in intent)
        if not bad and user_id is None:  # anonymous flows must come back in the browser that started them
            bad = not (isinstance(nonce, str) and nonce and hmac.compare_digest(payload.get("nonce", ""), hash_token(nonce)))
        if bad or not ctx.repo.consume_token(row["id"], now):
            raise invalid_token()  # unknown, reused, expired, wrong kind, issued to someone else, or another browser
        return payload

    def _exchange(self, provider: Provider, code: Any, payload: Dict[str, Any]) -> Profile:
        if not isinstance(code, str) or not code:
            raise _failed("The provider did not return an authorization code.")
        status, token = self.http.post_form(provider.token_url, {
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri(provider.id),
            "client_id": provider.client_id, "client_secret": provider.client_secret, "code_verifier": payload["verifier"],
        })
        access_token = token.get("access_token") if isinstance(token, dict) else None
        if status != 200 or not isinstance(access_token, str):
            raise _failed()
        return self._profile(provider, access_token)

    def _profile(self, provider: Provider, access_token: str) -> Profile:
        headers = {"Authorization": f"Bearer {access_token}"}
        status, info = self.http.get_json(provider.userinfo_url, headers)
        if status != 200 or not isinstance(info, dict):
            raise OAuthError("userinfo failed")
        email: Optional[str] = None
        if provider.id == "google":
            subject, verified = info.get("sub"), info.get("email_verified") is True
            email = info.get("email") if isinstance(info.get("email"), str) else None
            name = info.get("name")
        else:
            subject, verified = info.get("id"), False
            name = info.get("name") or info.get("login")
            if provider.emails_url:
                status, emails = self.http.get_json(provider.emails_url, headers)
                if status == 200 and isinstance(emails, list):
                    primary = next((e for e in emails if isinstance(e, dict) and e.get("primary") and isinstance(e.get("email"), str)), None)
                    if primary is not None:
                        email, verified = primary["email"], primary.get("verified") is True
        if subject is None or str(subject) == "":
            raise OAuthError("no subject")
        return Profile(str(subject), email, verified, name if isinstance(name, str) else None)

    # ------------------------------------------------------------------ linking
    def complete_link(self, principal: Principal, provider_id: str, code: Any, state: Any) -> None:
        ctx = self.ctx
        require(principal, Perm.OAUTH_MANAGE_OWN)
        ctx.limiter.hit("oauth:user", principal.user_id)
        failure: Optional[str] = None
        try:
            provider = self._provider(provider_id)
            payload = self._consume_state(provider, state, intent=("link",), user_id=principal.user_id)
            profile = self._exchange(provider, code, payload)
            existing = ctx.repo.identity(provider.id, profile.subject)
            if existing is not None and existing["user_id"] != principal.user_id:
                raise conflict("OAUTH_ALREADY_LINKED", "That account is already connected to a different PredictAI account.")
            if existing is None:
                with transaction(ctx.conn):
                    ctx.repo.add_identity(id=new_id(), user_id=principal.user_id, provider=provider.id, subject=profile.subject,
                                          password_hash=None, verified=profile.email_verified, now=ctx.clock.iso())
                    ctx.audit.record(Event.OAUTH_LINKED, actor=principal.user_id, target=principal.user_id, metadata={"provider": provider.id})
                ctx.mailer.security_notice(principal.email, "oauth_linked", provider.label)
        except OAuthError:
            failure = "provider_error"
        except AppError as error:
            failure = error.code.lower()
            ctx.audit.record(Event.OAUTH_FAILED, "failure", actor=principal.user_id, reason=failure, metadata={"provider": provider_id})
            raise
        if failure:
            ctx.audit.record(Event.OAUTH_FAILED, "failure", actor=principal.user_id, reason=failure, metadata={"provider": provider_id})
            raise _failed()

    # ------------------------------------------------------------------ step-up for accounts without a password
    def complete_reauth(self, principal: Principal, provider_id: str, code: Any, state: Any) -> IssuedSession:
        ctx = self.ctx
        require(principal, Perm.OAUTH_MANAGE_OWN)
        ctx.limiter.hit("oauth:user", principal.user_id)
        reason: Optional[str] = None
        try:
            provider = self._provider(provider_id)
            payload = self._consume_state(provider, state, intent=("reauth",), user_id=principal.user_id)
            profile = self._exchange(provider, code, payload)
            identity = ctx.repo.identity(provider.id, profile.subject)
            if identity is None or identity["user_id"] != principal.user_id:
                raise conflict("OAUTH_WRONG_ACCOUNT", "That is not the account connected to PredictAI. Use the one you connected earlier.")
            with transaction(ctx.conn):
                issued = self.auth._rotate(principal, reason="rotated", revoke_others=False, reauth_now=True)
                ctx.audit.record(Event.REAUTH, actor=principal.user_id, metadata={"method": provider.id})
            return issued
        except OAuthError:
            reason = "provider_error"
        except AppError as error:
            reason = error.code.lower()
            ctx.audit.record(Event.OAUTH_FAILED, "failure", actor=principal.user_id, reason=reason, metadata={"provider": provider_id, "intent": "reauth"})
            raise
        ctx.audit.record(Event.OAUTH_FAILED, "failure", actor=principal.user_id, reason=reason, metadata={"provider": provider_id, "intent": "reauth"})
        raise _failed()

    # ------------------------------------------------------------------ sign-in and registration
    def complete_signin(self, provider_id: str, code: Any, state: Any, nonce: Optional[str]) -> SignInResult:
        ctx = self.ctx
        ctx.limiter.hit("oauth_login:ip", ctx.meta.ip or "unknown")
        reason: Optional[str] = None
        try:
            provider = self._provider(provider_id)
            payload = self._consume_state(provider, state, intent=SIGNIN_INTENTS, user_id=None, nonce=nonce)
            profile = self._exchange(provider, code, payload)
            return self._sign_in_or_register(provider, payload["intent"], profile)
        except OAuthError:
            reason = "provider_error"
        except sqlite3.IntegrityError:
            reason = "race"  # two requests created the same account at once; the person can simply try again
        except AppError as error:
            ctx.audit.record(Event.OAUTH_LOGIN_FAILED, "failure", reason=error.code.lower(), metadata={"provider": provider_id})
            raise
        ctx.audit.record(Event.OAUTH_LOGIN_FAILED, "failure", reason=reason, metadata={"provider": provider_id})
        raise _failed()

    def _sign_in_or_register(self, provider: Provider, intent: str, profile: Profile) -> SignInResult:
        ctx = self.ctx
        identity = ctx.repo.identity(provider.id, profile.subject)
        if identity is not None:
            user = ctx.repo.user_by_id(identity["user_id"])
            if user is None or user["status"] != Status.ACTIVE:
                raise AppError("ACCOUNT_NOT_ACTIVE", "This account cannot sign in right now.", 403)
            with transaction(ctx.conn):
                ctx.repo.touch_identity(identity["id"], ctx.clock.iso())
                issued = self.auth.issue_session(user["id"], persistent=True)
                ctx.audit.record(Event.OAUTH_LOGIN, actor=user["id"], target=user["id"], metadata={"provider": provider.id})
            return SignInResult(LoginResult(issued, build_principal(ctx.repo, user, ctx.repo.session_by_id(issued.session_id))), False)
        if intent == "signin":
            raise AppError("OAUTH_NO_ACCOUNT", f"No PredictAI account is connected to this {provider.label} account. Register first, "
                           f"or sign in with your password and connect {provider.label} from your Account page.", 409)
        normalized = normalize_email(profile.email) if profile.email else None
        if normalized is None or not profile.email_verified:
            raise AppError("OAUTH_EMAIL_UNVERIFIED", f"{provider.label} did not confirm an email address for this account, so we cannot create a PredictAI account from it. "
                           "Verify your email with the provider, or register with an email and password.", 422)
        existing = ctx.repo.user_by_email(normalized)
        if existing is not None:
            ctx.audit.record(Event.OAUTH_LOGIN_FAILED, "denied", target=existing["id"], reason="email_exists", metadata={"provider": provider.id})
            ctx.mailer.security_notice(existing["email"], "oauth_signup_blocked", provider.label)
            raise AppError("OAUTH_EMAIL_EXISTS", "A PredictAI account with this email already exists. Sign in with your password, then connect "
                           f"{provider.label} from your Account page. Accounts are never merged automatically.", 409)
        try:
            name = clean_display_name(profile.name)
        except ValueError:
            name = None
        now, user_id = ctx.clock.iso(), new_id()
        with transaction(ctx.conn):
            ctx.repo.create_user(id=user_id, email=profile.email.strip(), email_normalized=normalized, display_name=name,
                                 status=Status.ACTIVE, verified_at=now, now=now)
            ctx.repo.add_identity(id=new_id(), user_id=user_id, provider=provider.id, subject=profile.subject, password_hash=None, verified=True, now=now)
            ctx.repo.grant_role(id=new_id(), user_id=user_id, role=Role.USER, granted_by=None, now=now)
            ctx.audit.record(Event.OAUTH_REGISTERED, actor=user_id, target=user_id, metadata={"provider": provider.id})
            issued = self.auth.issue_session(user_id, persistent=True)
        user = ctx.repo.user_by_id(user_id)
        return SignInResult(LoginResult(issued, build_principal(ctx.repo, user, ctx.repo.session_by_id(issued.session_id))), True)

    # ------------------------------------------------------------------ unlink
    def unlink(self, principal: Principal, provider_id: str, password: Any) -> None:
        ctx = self.ctx
        require(principal, Perm.OAUTH_MANAGE_OWN)
        if provider_id not in self.providers:
            raise not_found()
        confirm_password(ctx, principal, password)  # password accounts: the password; password-less accounts: a recent provider sign-in
        identities = ctx.repo.identities_for_user(principal.user_id)
        target = next((i for i in identities if i["provider"] == provider_id), None)
        if target is None:
            raise not_found()
        if not any(i is not target and (i["provider"] != "password" or i["password_hash"]) for i in identities):
            raise conflict("LAST_SIGN_IN_METHOD", "Add a password or connect another provider before disconnecting your only sign-in method.")
        with transaction(ctx.conn):
            ctx.repo.delete_identity(target["id"])
            ctx.audit.record(Event.OAUTH_UNLINKED, actor=principal.user_id, target=principal.user_id, metadata={"provider": provider_id})
        ctx.mailer.security_notice(principal.email, "oauth_unlinked", self.providers[provider_id].label)
