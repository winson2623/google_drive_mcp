"""Google OAuth for the local installed-app flow.

Uses InstalledAppFlow.run_local_server() so a Desktop OAuth client can:
open a browser, receive the localhost callback, exchange the code, and
persist a refreshable token file with user-only permissions.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError, OAuth2Error

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
UNVERIFIED_APP_HINT = (
    "If Google shows \"Google hasn't verified this app\", that is expected. "
    "Click Advanced, then Go to google-drive-mcp (unsafe), and approve "
    "drive.readonly. This is not a plugin bug."
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AuthError(Exception):
    """Base error for OAuth and token-store failures."""


class CredentialsFileError(AuthError):
    """The Desktop OAuth client JSON is missing or invalid."""


class AuthorizationDeniedError(AuthError):
    """The user closed the consent screen or denied Drive access."""


class TokenRefreshError(AuthError):
    """Stored credentials exist but cannot be refreshed and interactive login is off."""


def _load_dotenv(path: Path | None = None) -> None:
    candidates = (
        [path] if path else [PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.example"]
    )
    for env_path in candidates:
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def credentials_path() -> Path:
    _load_dotenv()
    configured = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def client_config_from_env() -> dict | None:
    """Desktop OAuth client from GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET.

    When both are set, they override credentials.json so a reviewer can use
    their own Google Cloud client if this project is quota-limited or flagged.
    """
    _load_dotenv()
    client_id = (os.environ.get("GDRIVE_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GDRIVE_CLIENT_SECRET") or "").strip()
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }
    if client_id or client_secret:
        raise CredentialsFileError(
            "Set both GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET, or neither. "
            "These env vars let you use your own Desktop OAuth client instead "
            "of credentials.json."
        )
    return None


def resolve_oauth_client(
    client_secrets: Path | None = None,
) -> tuple[str, Path | dict]:
    """Return ('env'|'file', client config or credentials.json path)."""
    if client_secrets is not None:
        return "file", client_secrets
    env_config = client_config_from_env()
    if env_config is not None:
        return "env", env_config
    return "file", credentials_path()


def token_path() -> Path:
    _load_dotenv()
    configured = os.environ.get("GOOGLE_TOKEN_FILE", "token.json")
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _restrict_private_file(path: Path) -> None:
    """Limit the token file to the current user (best-effort on Windows)."""
    if os.name == "nt":
        username = os.environ.get("USERNAME") or os.environ.get("USER")
        if not username:
            return
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{username}:F",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _write_token(path: Path, creds: Credentials) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    _restrict_private_file(tmp)
    tmp.replace(path)
    _restrict_private_file(path)


def _load_stored_credentials(stored_token: Path) -> Credentials | None:
    if not stored_token.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(str(stored_token), SCOPES)
    except (ValueError, OSError) as exc:
        raise AuthError(
            f"Token file is unreadable or invalid: {stored_token}\n"
            "Delete it and run authorization again."
        ) from exc


def _refresh_credentials(creds: Credentials) -> Credentials:
    creds.refresh(Request())
    return creds


def _run_installed_app_flow(client: Path | dict) -> Credentials:
    try:
        if isinstance(client, Path):
            flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
        else:
            flow = InstalledAppFlow.from_client_config(client, SCOPES)
    except (ValueError, OSError) as exc:
        hint = (
            f"OAuth client file is invalid: {client}\n"
            if isinstance(client, Path)
            else "GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET are invalid.\n"
        )
        raise CredentialsFileError(
            hint
            + "Use a Desktop app client from Google Cloud Console "
            "(credentials.json, or GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET)."
        ) from exc

    try:
        return flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
            authorization_prompt_message=(
                "Open this URL in a browser to authorize Google Drive access:\n"
                "{url}\n\n"
                f"{UNVERIFIED_APP_HINT}\n"
            ),
            success_message=(
                "Google Drive access granted. You can close this tab and "
                "return to the terminal."
            ),
        )
    except AccessDeniedError as exc:
        raise AuthorizationDeniedError(
            "Google authorization was denied. Grant the Drive read-only scope "
            "to list and read files in the assessment folder."
        ) from exc
    except OAuth2Error as exc:
        message = str(exc)
        if "access_denied" in message.lower():
            raise AuthorizationDeniedError(
                "Google authorization was denied. Grant the Drive read-only scope "
                "to list and read files in the assessment folder."
            ) from exc
        raise AuthError(f"Google authorization failed: {exc}") from exc


def get_credentials(
    *,
    client_secrets: Path | None = None,
    token_file: Path | None = None,
    interactive: bool = True,
) -> Credentials:
    """Return user credentials, opening a local browser login when needed.

    Stored tokens are reused when valid. Expired tokens are refreshed with
    the refresh token. If refresh fails or no token exists, InstalledAppFlow
    starts a localhost callback server and opens the system browser.
    """
    _source, client = resolve_oauth_client(client_secrets)
    stored_token = token_file or token_path()

    if isinstance(client, Path) and not client.is_file():
        raise CredentialsFileError(
            f"OAuth client file not found: {client}\n"
            "Download the Desktop client JSON from Google Cloud Console "
            "and save it as credentials.json, or set GDRIVE_CLIENT_ID and "
            "GDRIVE_CLIENT_SECRET to use your own client."
        )

    creds = _load_stored_credentials(stored_token)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds = _refresh_credentials(creds)
            _write_token(stored_token, creds)
            return creds
        except RefreshError as exc:
            if stored_token.is_file():
                stored_token.unlink()
            creds = None
            if not interactive:
                raise TokenRefreshError(
                    "Stored Google credentials expired and could not be refreshed "
                    f"({exc}). Tokens issued while the OAuth app is in Testing "
                    "expire after 7 days; after Audience is In production, delete "
                    "token.json and run: python -m google_drive_mcp.auth\n"
                    "You can also set GDRIVE_CLIENT_ID and GDRIVE_CLIENT_SECRET "
                    "to authorize with your own Desktop client."
                ) from exc

    if not interactive:
        raise AuthError(
            "No valid Google credentials. Run: python -m google_drive_mcp.auth"
        )

    creds = _run_installed_app_flow(client)
    if not creds or not creds.valid:
        raise AuthError("Google authorization finished without usable credentials.")
    _write_token(stored_token, creds)
    return creds


def authorized_account_email(creds: Credentials | None = None) -> str:
    """Return the Google account email that authorized Drive access."""
    credentials = creds or get_credentials()
    try:
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        about = service.about().get(fields="user(emailAddress,displayName)").execute()
    except HttpError as exc:
        raise AuthError(
            "Could not confirm the authorized account with the Drive API. "
            f"Details: {exc}"
        ) from exc
    user = about.get("user") or {}
    email = user.get("emailAddress")
    if not email:
        raise AuthError("Drive API did not return an authorized account email.")
    return email


def main() -> None:
    try:
        creds = get_credentials(interactive=True)
        email = authorized_account_email(creds)
    except AuthError as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Authorized Google account: {email}")
    source, client = resolve_oauth_client()
    if source == "env":
        print("OAuth client: GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET")
    else:
        print(f"OAuth client file: {client}")
    print(f"Token file: {token_path()}")
    print(f"Scope: {SCOPES[0]}")
    print(UNVERIFIED_APP_HINT)
    if creds.refresh_token:
        print("Refresh token: present (offline access)")
    else:
        print(
            "Refresh token: missing. Delete token.json and authorize again "
            "so Google issues a refresh token.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
