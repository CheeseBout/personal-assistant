"""Google Auth — shared OAuth foundation for all Google connectors (Gmail first).

Local-first, single-user: we use the OAuth *installed-app* (Desktop) flow. The
user signs in (and handles 2FA) once in their browser; the resulting token is
cached on disk at ``settings.GOOGLE_TOKEN_PATH`` and refreshed automatically.

Security:
- The token is stored locally only and is NEVER returned through the API, logged,
  or exposed to the LLM. Handlers receive a built service object, not credentials.
- Scopes are limited to what Gmail needs (see settings.GOOGLE_SCOPES).
"""

import json
import os
import threading
from pathlib import Path
from typing import Optional, List

from ..core.config import settings
from ..core.logging_config import logger


def _token_path() -> Path:
    p = Path(settings.GOOGLE_TOKEN_PATH)
    if not p.is_absolute():
        p = (Path(__file__).parent.parent.parent / settings.GOOGLE_TOKEN_PATH).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class GoogleAuth:
    """Singleton holding cached Google OAuth credentials."""

    _instance: Optional["GoogleAuth"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._creds = None  # google.oauth2.credentials.Credentials
        self._service_cache = {}  # (api_name, version) -> service client

    @classmethod
    def get_instance(cls) -> "GoogleAuth":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = GoogleAuth()
        return cls._instance

    # --- credential lifecycle --------------------------------------------------------

    def _client_config(self) -> dict:
        """Build the installed-app client config from settings (no JSON file needed)."""
        return {
            "installed": {
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    def get_credentials(self):
        """Return valid credentials, refreshing if needed, or None if not connected."""
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        if self._creds is None:
            path = _token_path()
            if path.exists():
                try:
                    self._creds = Credentials.from_authorized_user_file(
                        str(path), settings.GOOGLE_SCOPES
                    )
                except Exception as e:
                    logger.error(f"Failed to load Google token: {e}")
                    return None

        creds = self._creds
        if creds is None:
            return None
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._service_cache.clear()  # rebuild clients with refreshed creds
                self._save(creds)
                return creds
            except Exception as e:
                logger.error(f"Google token refresh failed: {e}")
                return None
        return None

    def _save(self, creds):
        try:
            path = _token_path()
            path.write_text(creds.to_json(), encoding="utf-8")
            # Harden permissions: the token grants full Gmail/Drive access.
            # No-op on Windows; enforces owner-only read/write on POSIX.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception as e:
            logger.error(f"Failed to persist Google token: {e}")

    def start_auth_flow(self) -> dict:
        """Run the interactive installed-app flow (opens a browser). Blocking.

        Returns {"connected": True, "email": ...} on success or {"error": ...}.
        """
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            return {"error": "GOOGLE_CLIENT_ID/SECRET chưa được cấu hình trong .env"}
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_config(
                self._client_config(), settings.GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=0, prompt="consent")
            self._creds = creds
            self._service_cache.clear()
            self._save(creds)
            return {"connected": True, "email": self._email(creds)}
        except Exception as e:
            logger.error(f"Google auth flow failed: {e}")
            return {"error": f"Đăng nhập Google thất bại: {e}"}

    def revoke(self) -> dict:
        """Disconnect: revoke the token at Google, then forget the local cache.

        Revoking at Google's endpoint invalidates the refresh token server-side,
        so a leaked token.json copy can no longer be refreshed. If the network
        call fails we still clear local state (best effort).
        """
        creds = self._creds
        token_to_revoke = None
        if creds is not None:
            token_to_revoke = getattr(creds, "refresh_token", None) or getattr(creds, "token", None)
        if token_to_revoke:
            try:
                import requests
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token_to_revoke},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Google token revoke call failed (clearing locally anyway): {e}")
        self._creds = None
        self._service_cache.clear()
        try:
            path = _token_path()
            if path.exists():
                os.remove(str(path))
        except Exception as e:
            logger.error(f"Failed to remove Google token: {e}")
            return {"error": str(e)}
        return {"connected": False}

    def status(self) -> dict:
        """Connection status WITHOUT exposing any token material."""
        creds = self.get_credentials()
        if creds is None:
            return {"connected": False, "email": None}
        return {"connected": True, "email": self._email(creds)}

    def _email(self, creds) -> Optional[str]:
        try:
            from googleapiclient.discovery import build
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            profile = svc.users().getProfile(userId="me").execute()
            return profile.get("emailAddress")
        except Exception:
            return None

    def build_service(self, api_name: str, version: str):
        """Return a Google API service client, or raise RuntimeError if not connected.

        Clients are cached per (api_name, version); the cache is cleared on
        refresh/connect/revoke so a stale client never outlives its credentials.
        """
        creds = self.get_credentials()
        if creds is None:
            raise RuntimeError("not_connected")
        key = (api_name, version)
        cached = self._service_cache.get(key)
        if cached is not None:
            return cached
        from googleapiclient.discovery import build
        svc = build(api_name, version, credentials=creds, cache_discovery=False)
        self._service_cache[key] = svc
        return svc
