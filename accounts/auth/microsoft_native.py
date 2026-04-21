"""Native Microsoft OAuth helpers for desktop mail accounts."""

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


MICROSOFT_GRAPH_NATIVE_SCOPES = (
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
    "offline_access",
    "openid",
    "profile",
    "email",
)

_MS_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_MS_GRAPH_PROFILE_URL = "https://graph.microsoft.com/v1.0/me"
_MS_OAUTH_TIMEOUT_SECS = 240


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
        raise OAuthTokenAcquisitionError(
            detail or f"Microsoft OAuth request failed with {exc.code}",
            stage="http",
            retryable=exc.code in (429, 500, 502, 503, 504),
            source="microsoft",
            original=exc,
        ) from exc
    try:
        return json.loads(raw or b"{}")
    except Exception as exc:
        raise OAuthTokenAcquisitionError(
            "Microsoft OAuth returned invalid JSON",
            stage="http",
            retryable=True,
            source="microsoft",
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


def _decode_id_token_email(id_token):
    # Personal MSA accounts sometimes leave /me.mail null; the id_token's email
    # claim is the reliable source for those.
    id_token = str(id_token or "").strip()
    if not id_token:
        return ""
    parts = id_token.split(".")
    if len(parts) < 2:
        return ""
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded or b"{}")
    except Exception:
        return ""
    for key in ("email", "preferred_username", "upn"):
        value = str(claims.get(key) or "").strip()
        if value and "@" in value:
            return value
    return ""


def _graph_profile(access_token, *, id_token="", timeout=15):
    req = urllib.request.Request(
        _MS_GRAPH_PROFILE_URL,
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
            f"Microsoft profile lookup failed with {exc.code}",
            stage="profile",
            retryable=exc.code in (429, 500, 502, 503, 504),
            source="microsoft",
            original=exc,
        ) from exc
    for key in ("mail", "userPrincipalName"):
        value = str(data.get(key) or "").strip()
        if value and "@" in value:
            return value
    fallback = _decode_id_token_email(id_token)
    if fallback:
        return fallback
    raise OAuthTokenAcquisitionError(
        "Microsoft sign-in did not return a mailbox identity",
        stage="profile",
        retryable=False,
        source="microsoft",
    )


def _token_bundle_from_response(payload, *, preserve_refresh_token=""):
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise OAuthTokenAcquisitionError(
            "Microsoft sign-in did not return an access token",
            stage="token exchange",
            retryable=False,
            source="microsoft",
        )
    expires_in = int(payload.get("expires_in") or 0)
    now = time.time()
    refresh_token = str(
        payload.get("refresh_token") or preserve_refresh_token or ""
    ).strip()
    return {
        "provider": "microsoft",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
        "expires_at": now + max(0, expires_in),
        "obtained_at": now,
        "id_token": str(payload.get("id_token") or "").strip(),
    }


def exchange_ms_auth_code(
    client_id, code, code_verifier, redirect_uri, *, scopes=None, timeout=15
):
    scopes = tuple(
        str(scope or "").strip()
        for scope in (scopes or MICROSOFT_GRAPH_NATIVE_SCOPES)
        if str(scope or "").strip()
    )
    payload_data = {
        "client_id": str(client_id or "").strip(),
        "code": str(code or "").strip(),
        "code_verifier": str(code_verifier or "").strip(),
        "grant_type": "authorization_code",
        "redirect_uri": str(redirect_uri or "").strip(),
        "scope": " ".join(scopes),
    }
    payload = _json_request(
        _MS_TOKEN_URL,
        method="POST",
        data=payload_data,
        timeout=timeout,
    )
    return _token_bundle_from_response(payload)


def refresh_ms_access_token(client_id, refresh_token, *, scopes=None, timeout=15):
    scopes = tuple(
        str(scope or "").strip()
        for scope in (scopes or MICROSOFT_GRAPH_NATIVE_SCOPES)
        if str(scope or "").strip()
    )
    payload_data = {
        "client_id": str(client_id or "").strip(),
        "refresh_token": str(refresh_token or "").strip(),
        "grant_type": "refresh_token",
        "scope": " ".join(scopes),
    }
    payload = _json_request(
        _MS_TOKEN_URL,
        method="POST",
        data=payload_data,
        timeout=timeout,
    )
    return _token_bundle_from_response(payload, preserve_refresh_token=refresh_token)


def run_ms_native_oauth_authorization(
    client_id,
    *,
    login_hint="",
    scopes=None,
    timeout_seconds=_MS_OAUTH_TIMEOUT_SECS,
    progress_callback=None,
):
    client_id = str(client_id or "").strip()
    if not client_id:
        raise OAuthTokenAcquisitionError(
            "Microsoft OAuth client ID is required",
            stage="client setup",
            retryable=False,
            source="microsoft",
        )
    scopes = tuple(
        str(scope or "").strip()
        for scope in (scopes or MICROSOFT_GRAPH_NATIVE_SCOPES)
        if str(scope or "").strip()
    )
    if not scopes:
        raise OAuthTokenAcquisitionError(
            "At least one Microsoft OAuth scope is required",
            stage="client setup",
            retryable=False,
            source="microsoft",
        )

    verifier = _pkce_code_verifier()
    challenge = _pkce_code_challenge(verifier)
    state = secrets.token_urlsafe(24)
    result = {}
    ready = threading.Event()

    # Azure public-client apps typically register `http://localhost` as the
    # redirect URI; Microsoft ignores the port for loopback but requires the
    # path to match exactly, so we send a redirect_uri with no path.
    callback_paths = ("/", "")

    class _OAuthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            is_callback = parsed.path in callback_paths
            code = str((query.get("code") or [""])[0] or "").strip()
            state_value = str((query.get("state") or [""])[0] or "").strip()
            error = str((query.get("error") or [""])[0] or "").strip()
            error_description = str(
                (query.get("error_description") or [""])[0] or ""
            ).strip()
            if is_callback and (code or error) and not ready.is_set():
                result["path"] = parsed.path
                result["code"] = code
                result["state"] = state_value
                result["error"] = error
                result["error_description"] = error_description
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
    redirect_uri = f"http://localhost:{server.server_address[1]}"
    server_thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
    )
    server_thread.start()
    try:
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(scopes),
            "prompt": "select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        login_hint = str(login_hint or "").strip()
        if login_hint:
            query["login_hint"] = login_hint
        auth_url = _MS_AUTH_URL + "?" + urllib.parse.urlencode(query)
        if not _open_browser(auth_url):
            raise OAuthTokenAcquisitionError(
                "Could not open the browser for Microsoft sign-in",
                stage="authorization",
                retryable=True,
                source="microsoft",
            )
        _report_progress(
            progress_callback, "Waiting for Microsoft approval in your browser."
        )
        if not ready.wait(timeout_seconds):
            raise OAuthTokenAcquisitionError(
                "Microsoft sign-in timed out before approval completed",
                stage="authorization",
                retryable=True,
                source="microsoft",
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
            "Microsoft sign-in returned an invalid state token",
            stage="authorization",
            retryable=False,
            source="microsoft",
        )
    if result.get("error"):
        description = (
            result.get("error_description")
            or result.get("error")
            or "authorization failed"
        )
        raise OAuthTokenAcquisitionError(
            f"Microsoft sign-in was denied: {description}",
            stage="authorization",
            retryable=False,
            source="microsoft",
        )
    code = str(result.get("code") or "").strip()
    if not code:
        raise OAuthTokenAcquisitionError(
            "Microsoft sign-in did not return an authorization code",
            stage="authorization",
            retryable=False,
            source="microsoft",
        )
    _report_progress(progress_callback, "Exchanging Microsoft authorization...")
    bundle = exchange_ms_auth_code(
        client_id,
        code,
        verifier,
        redirect_uri,
        scopes=scopes,
    )
    _report_progress(progress_callback, "Fetching your Outlook address...")
    bundle["identity"] = _graph_profile(
        bundle["access_token"], id_token=bundle.get("id_token", "")
    )
    bundle["client_id"] = client_id
    bundle["scopes"] = list(scopes)
    return bundle
