"""Native Google OAuth helpers for desktop mail accounts."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from .oauth_common import OAuthTokenAcquisitionError
except ImportError:
    from accounts.auth.oauth_common import OAuthTokenAcquisitionError


GOOGLE_GMAIL_NATIVE_SCOPES = ("https://www.googleapis.com/auth/gmail.modify",)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GOOGLE_GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
_GOOGLE_OAUTH_TIMEOUT_SECS = 240


def _urlsafe_b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_code_verifier():
    return _urlsafe_b64(secrets.token_bytes(48))


def _pkce_code_challenge(verifier):
    return _urlsafe_b64(hashlib.sha256(verifier.encode("ascii")).digest())


def _json_request(url, *, method="GET", data=None, headers=None, timeout=15):
    payload = None
    request_headers = dict(headers or {})
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            payload = json.loads(body or "{}")
            detail = str(
                payload.get("error_description") or payload.get("error") or ""
            ).strip()
        except Exception:
            detail = str(exc).strip()
        if "client_secret is missing" in detail.lower():
            detail = (
                "Google OAuth client secret is missing. Add google_oauth_client_secret "
                "to Hermod settings or use a Google Desktop app OAuth client."
            )
        raise OAuthTokenAcquisitionError(
            detail or f"Google OAuth request failed with {exc.code}",
            stage="http",
            retryable=exc.code in (429, 500, 502, 503, 504),
            source="google",
            original=exc,
        ) from exc
    try:
        return json.loads(raw or b"{}")
    except Exception as exc:
        raise OAuthTokenAcquisitionError(
            "Google OAuth returned invalid JSON",
            stage="http",
            retryable=True,
            source="google",
            original=exc,
        ) from exc


def _open_browser(url):
    if webbrowser.open(url, new=1, autoraise=True):
        return True
    try:
        subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        return False


def _report_progress(progress_callback, message):
    if not callable(progress_callback):
        return
    try:
        progress_callback(str(message or "").strip())
    except Exception:
        return


def _gmail_profile(access_token, timeout=15):
    req = urllib.request.Request(
        _GOOGLE_GMAIL_PROFILE_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise OAuthTokenAcquisitionError(
            f"Google profile lookup failed with {exc.code}",
            stage="profile",
            retryable=exc.code in (429, 500, 502, 503, 504),
            source="google",
            original=exc,
        ) from exc
    identity = str(data.get("emailAddress") or "").strip()
    if not identity:
        raise OAuthTokenAcquisitionError(
            "Google sign-in did not return a mailbox identity",
            stage="profile",
            retryable=False,
            source="google",
        )
    return identity


def _token_bundle_from_response(payload, *, preserve_refresh_token=""):
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise OAuthTokenAcquisitionError(
            "Google sign-in did not return an access token",
            stage="token exchange",
            retryable=False,
            source="google",
        )
    expires_in = int(payload.get("expires_in") or 0)
    now = time.time()
    refresh_token = str(
        payload.get("refresh_token") or preserve_refresh_token or ""
    ).strip()
    return {
        "provider": "google",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
        "expires_at": now + max(0, expires_in),
        "obtained_at": now,
    }


def exchange_google_auth_code(
    client_id, code, code_verifier, redirect_uri, *, client_secret="", timeout=15
):
    payload_data = {
        "client_id": str(client_id or "").strip(),
        "code": str(code or "").strip(),
        "code_verifier": str(code_verifier or "").strip(),
        "grant_type": "authorization_code",
        "redirect_uri": str(redirect_uri or "").strip(),
    }
    client_secret = str(client_secret or "").strip()
    if client_secret:
        payload_data["client_secret"] = client_secret
    payload = _json_request(
        _GOOGLE_TOKEN_URL,
        method="POST",
        data=payload_data,
        timeout=timeout,
    )
    return _token_bundle_from_response(payload)


def refresh_google_access_token(
    client_id, refresh_token, *, client_secret="", timeout=15
):
    payload_data = {
        "client_id": str(client_id or "").strip(),
        "refresh_token": str(refresh_token or "").strip(),
        "grant_type": "refresh_token",
    }
    client_secret = str(client_secret or "").strip()
    if client_secret:
        payload_data["client_secret"] = client_secret
    payload = _json_request(
        _GOOGLE_TOKEN_URL,
        method="POST",
        data=payload_data,
        timeout=timeout,
    )
    return _token_bundle_from_response(payload, preserve_refresh_token=refresh_token)


def revoke_google_token(token, *, timeout=15):
    token = str(token or "").strip()
    if not token:
        return False
    data = urllib.parse.urlencode({"token": token}).encode("utf-8")
    req = urllib.request.Request(
        _GOOGLE_REVOKE_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def run_google_native_oauth_authorization(
    client_id,
    *,
    client_secret="",
    login_hint="",
    scopes=None,
    timeout_seconds=_GOOGLE_OAUTH_TIMEOUT_SECS,
    progress_callback=None,
):
    client_id = str(client_id or "").strip()
    client_secret = str(client_secret or "").strip()
    if not client_id:
        raise OAuthTokenAcquisitionError(
            "Google OAuth client ID is required",
            stage="client setup",
            retryable=False,
            source="google",
        )
    scopes = tuple(
        str(scope or "").strip()
        for scope in (scopes or GOOGLE_GMAIL_NATIVE_SCOPES)
        if str(scope or "").strip()
    )
    if not scopes:
        raise OAuthTokenAcquisitionError(
            "At least one Google OAuth scope is required",
            stage="client setup",
            retryable=False,
            source="google",
        )

    verifier = _pkce_code_verifier()
    challenge = _pkce_code_challenge(verifier)
    state = secrets.token_urlsafe(24)
    result = {}
    ready = threading.Event()

    class _OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            result["path"] = parsed.path
            result["code"] = str((query.get("code") or [""])[0] or "").strip()
            result["state"] = str((query.get("state") or [""])[0] or "").strip()
            result["error"] = str((query.get("error") or [""])[0] or "").strip()
            result["error_description"] = str(
                (query.get("error_description") or [""])[0] or ""
            ).strip()
            ready.set()
            body = (
                '<html><body style="font-family: sans-serif;">'
                "<h2>Hermod sign-in complete</h2>"
                "<p>You can close this browser window.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _OAuthHandler)
    server.daemon_threads = True
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/oauth2/callback"
    server_thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
    )
    server_thread.start()
    try:
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        login_hint = str(login_hint or "").strip()
        if login_hint:
            query["login_hint"] = login_hint
        auth_url = _GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(query)
        if not _open_browser(auth_url):
            raise OAuthTokenAcquisitionError(
                "Could not open the browser for Google sign-in",
                stage="authorization",
                retryable=True,
                source="google",
            )
        _report_progress(
            progress_callback, "Waiting for Google approval in your browser."
        )
        if not ready.wait(timeout_seconds):
            raise OAuthTokenAcquisitionError(
                "Google sign-in timed out before approval completed",
                stage="authorization",
                retryable=True,
                source="google",
            )
        _report_progress(
            progress_callback, "Browser approval received. Finishing sign-in..."
        )
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        server_thread.join(timeout=1)

    if result.get("state") != state:
        raise OAuthTokenAcquisitionError(
            "Google sign-in returned an invalid state token",
            stage="authorization",
            retryable=False,
            source="google",
        )
    if result.get("error"):
        description = (
            result.get("error_description")
            or result.get("error")
            or "authorization failed"
        )
        raise OAuthTokenAcquisitionError(
            f"Google sign-in was denied: {description}",
            stage="authorization",
            retryable=False,
            source="google",
        )
    code = str(result.get("code") or "").strip()
    if not code:
        raise OAuthTokenAcquisitionError(
            "Google sign-in did not return an authorization code",
            stage="authorization",
            retryable=False,
            source="google",
        )
    _report_progress(progress_callback, "Exchanging Google authorization...")
    bundle = exchange_google_auth_code(
        client_id,
        code,
        verifier,
        redirect_uri,
        client_secret=client_secret,
    )
    _report_progress(progress_callback, "Fetching your Gmail address...")
    bundle["identity"] = _gmail_profile(bundle["access_token"])
    bundle["client_id"] = client_id
    if client_secret:
        bundle["client_secret"] = client_secret
    bundle["scopes"] = list(scopes)
    return bundle
