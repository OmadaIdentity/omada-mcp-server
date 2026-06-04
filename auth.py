"""
auth.py — OAuth 2.0 Authorization Code + PKCE authentication for Omada MCP Server.

Handles token acquisition, storage, and refresh transparently.
No bearer_token parameter needed in tool functions — this module manages it all.
"""

import asyncio
import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from typing import Optional

import httpx

try:
    import win32crypt
    _DPAPI_AVAILABLE = True
except ImportError:
    _DPAPI_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (loaded from environment)
# ---------------------------------------------------------------------------
TENANT_ID: Optional[str] = os.getenv("TENANT_ID")
CLIENT_ID: Optional[str] = os.getenv("CLIENT_ID")
REDIRECT_PORT: int = int(os.getenv("REDIRECT_PORT", "8765"))

# OAUTH2_SCOPE can be set explicitly, or auto-derived from OMADA_BASE_URL.
# Omada Azure AD app registrations always use the base URL as identifier URI,
# so the scope is always {base_url}/user_impersonation.
_base_url = os.getenv("OMADA_BASE_URL", "").rstrip("/")
OAUTH2_SCOPE: str = os.getenv(
    "OAUTH2_SCOPE",
    f"{_base_url}/user_impersonation offline_access" if _base_url else "",
)

_TOKEN_ENDPOINT = (
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    if TENANT_ID
    else None
)
_AUTH_ENDPOINT = (
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
    if TENANT_ID
    else None
)

# ---------------------------------------------------------------------------
# Token store (in-memory + DPAPI-encrypted file cache)
# ---------------------------------------------------------------------------
_TOKEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token_cache.bin")

_token_store: dict = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": None,
    "user_identity": None,
}

# Async lock to prevent concurrent auth flows without blocking the event loop
_auth_lock = asyncio.Lock()


def _load_token_cache() -> None:
    """Load persisted tokens from an encrypted file on startup.

    On Windows with pywin32: the file is encrypted with DPAPI (Windows Data Protection
    API), which ties the encryption to the current Windows user session. No key is
    stored anywhere — Windows derives it from the user's login credentials.

    Fallback (non-Windows or pywin32 unavailable): plain JSON, with a warning.
    """
    try:
        if not os.path.exists(_TOKEN_CACHE_FILE):
            return
        with open(_TOKEN_CACHE_FILE, "rb") as f:
            raw_bytes = f.read()
        if _DPAPI_AVAILABLE:
            _, decrypted = win32crypt.CryptUnprotectData(raw_bytes, None, None, None, 0)
            data = json.loads(decrypted.decode("utf-8"))
            logger.debug("Token cache loaded (DPAPI-encrypted)")
        else:
            data = json.loads(raw_bytes.decode("utf-8"))
            logger.warning("Token cache loaded as plain text — install pywin32 for encryption")
        _token_store["access_token"] = data.get("access_token")
        _token_store["refresh_token"] = data.get("refresh_token")
        _token_store["user_identity"] = data.get("user_identity")
        expires_at_str = data.get("expires_at")
        if expires_at_str:
            _token_store["expires_at"] = datetime.fromisoformat(expires_at_str)
    except Exception as e:
        logger.warning(f"Could not load token cache: {e}")


def _save_token_cache() -> None:
    """Persist tokens to an encrypted file.

    On Windows with pywin32: uses DPAPI to encrypt the data before writing.
    The encrypted blob is only decryptable by the same Windows user on the same machine.
    No key material is stored — Windows handles key derivation transparently.

    Fallback: plain JSON (with a warning logged).
    """
    try:
        data = {
            "access_token": _token_store.get("access_token"),
            "refresh_token": _token_store.get("refresh_token"),
            "user_identity": _token_store.get("user_identity"),
            "expires_at": (
                _token_store["expires_at"].isoformat()
                if _token_store.get("expires_at")
                else None
            ),
        }
        raw_bytes = json.dumps(data).encode("utf-8")
        if _DPAPI_AVAILABLE:
            encrypted = win32crypt.CryptProtectData(raw_bytes, "omada-mcp-token", None, None, None, 0)
            with open(_TOKEN_CACHE_FILE, "wb") as f:
                f.write(encrypted)
        else:
            logger.warning("pywin32 not available — saving token cache as plain text")
            with open(_TOKEN_CACHE_FILE, "wb") as f:
                f.write(raw_bytes)
    except Exception as e:
        logger.warning(f"Could not save token cache: {e}")


# Load any previously cached token at import time
_load_token_cache()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_token_valid() -> bool:
    """Return True if access token exists and has more than 5 minutes left."""
    if not _token_store["access_token"] or not _token_store["expires_at"]:
        return False
    return datetime.now() < _token_store["expires_at"] - timedelta(minutes=5)


def _extract_user_from_token(token: str) -> str:
    """Decode JWT payload and extract user identity for cache keying.

    Checks Azure AD v2.0 claims in order of preference:
      preferred_username → email → sub (GUID fallback)
    Falls back to a hash of the token if decoding fails.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return hashlib.sha256(token.encode()).hexdigest()[:16]

        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding

        claims = json.loads(
            base64.b64decode(payload.replace("-", "+").replace("_", "/"))
        )

        # v2.0 token claims only — upn/unique_name are v1.0 and won't appear here
        return (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("sub")
            or hashlib.sha256(token.encode()).hexdigest()[:16]
        )
    except Exception:
        return hashlib.sha256(token.encode()).hexdigest()[:16]


def _store_token_response(data: dict) -> None:
    """Populate token store from an Azure token response dict."""
    access_token = data["access_token"]
    _token_store["access_token"] = access_token
    _token_store["refresh_token"] = data.get("refresh_token", _token_store.get("refresh_token"))
    _token_store["expires_at"] = datetime.now() + timedelta(
        seconds=data.get("expires_in", 3600)
    )
    _token_store["user_identity"] = _extract_user_from_token(access_token)
    logger.info(f"Token stored for user: {_token_store['user_identity']}")
    _save_token_cache()


async def _try_refresh() -> bool:
    """Silently refresh access token using refresh_token. Returns True on success."""
    if not _token_store["refresh_token"]:
        return False
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _TOKEN_ENDPOINT,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": _token_store["refresh_token"],
                    "scope": OAUTH2_SCOPE,
                },
                timeout=15.0,
            )
            if response.status_code == 200:
                _store_token_response(response.json())
                logger.info("Token refreshed silently")
                return True
            else:
                logger.warning(f"Refresh failed: {response.status_code} {response.text}")
    except Exception as e:
        logger.warning(f"Refresh token error: {e}")
    return False


async def _launch_browser_auth() -> None:
    """
    Launch Auth Code + PKCE flow:
      1. Start local HTTP server on REDIRECT_PORT
      2. Open browser to Azure AD login
      3. Receive authorization code via redirect
      4. Exchange code for tokens
    """
    if not TENANT_ID or not CLIENT_ID:
        raise Exception(
            "TENANT_ID and CLIENT_ID environment variables are required for authentication. "
            "Please configure them in your .env file."
        )

    # Generate PKCE pair
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    auth_code_holder: dict = {"code": None, "error": None}
    server_ready = threading.Event()
    server_done = threading.Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                auth_code_holder["code"] = params["code"][0]
            elif "error" in params:
                desc = params.get("error_description", ["Unknown error"])[0]
                auth_code_holder["error"] = desc
            # Send a clean success page
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:2em'>"
                b"<h2>&#10003; Authentication complete</h2>"
                b"<p>You can close this window and return to your MCP client.</p>"
                b"</body></html>"
            )
            server_done.set()

        def log_message(self, format, *args):
            pass  # Suppress HTTP access logs

    def _run_server():
        srv = http.server.HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
        server_ready.set()
        srv.handle_request()  # Handle exactly one callback
        srv.server_close()

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()

    if not server_ready.wait(timeout=5):
        raise Exception("Local callback server failed to start")

    # Build authorization URL
    auth_params = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": f"http://localhost:{REDIRECT_PORT}",
            "scope": OAUTH2_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
        }
    )
    auth_url = f"{_AUTH_ENDPOINT}?{auth_params}"

    logger.info(f"Opening browser for authentication: {auth_url}")
    webbrowser.open(auth_url)

    # Wait for callback (5 minute timeout)
    if not server_done.wait(timeout=300):
        raise Exception("Authentication timed out (5 minutes). Please try again.")

    if auth_code_holder["error"]:
        raise Exception(f"Authentication failed: {auth_code_holder['error']}")
    if not auth_code_holder["code"]:
        raise Exception("No authorization code received. Please try again.")

    # Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        response = await client.post(
            _TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": auth_code_holder["code"],
                "redirect_uri": f"http://localhost:{REDIRECT_PORT}",
                "code_verifier": code_verifier,
                "scope": OAUTH2_SCOPE,
            },
            timeout=15.0,
        )
        if response.status_code != 200:
            raise Exception(
                f"Token exchange failed: {response.status_code} {response.text}"
            )
        _store_token_response(response.json())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ensure_authenticated() -> str:
    """
    Return a valid access token.

    Priority:
      1. Cached token still valid → return immediately (no lock needed)
      2. Acquire async lock, then re-check:
         a. Another coroutine may have refreshed while we waited
         b. Refresh token available → silent refresh
         c. No tokens → launch browser Auth Code + PKCE flow

    The lock is asyncio-based so it never blocks the event loop.
    Both refresh and browser auth run inside the lock to prevent a race
    condition where two concurrent coroutines burn the single-use refresh token.

    Raises:
        Exception if authentication fails
    """
    # Fast path — no lock needed if token is already valid
    if _is_token_valid():
        return _token_store["access_token"]

    # Slow path — serialise refresh + browser auth to prevent duplicate flows
    async with _auth_lock:
        # Re-check: another coroutine may have authenticated while we waited
        if _is_token_valid():
            return _token_store["access_token"]

        if _token_store["refresh_token"]:
            if await _try_refresh():
                return _token_store["access_token"]

        # No valid token and no refresh token — launch interactive browser flow
        await _launch_browser_auth()

    return _token_store["access_token"]


def get_user_identity() -> str:
    """Return the authenticated user's identity (email/UPN) for cache keying."""
    return _token_store.get("user_identity") or "anonymous"


def clear_tokens() -> None:
    """Clear stored tokens (force re-authentication on next call)."""
    _token_store.update(
        {
            "access_token": None,
            "refresh_token": None,
            "expires_at": None,
            "user_identity": None,
        }
    )
    try:
        if os.path.exists(_TOKEN_CACHE_FILE):
            os.remove(_TOKEN_CACHE_FILE)
    except Exception:
        pass
    logger.info("Tokens cleared")
