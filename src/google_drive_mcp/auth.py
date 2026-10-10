"""Google OAuth for the local installed-app flow."""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
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


def token_path() -> Path:
    _load_dotenv()
    configured = os.environ.get("GOOGLE_TOKEN_FILE", "token.json")
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_credentials(
    *,
    client_secrets: Path | None = None,
    token_file: Path | None = None,
) -> Credentials:
    """Return user credentials, opening a local browser login when needed."""
    secrets_file = client_secrets or credentials_path()
    stored_token = token_file or token_path()

    if not secrets_file.is_file():
        raise FileNotFoundError(
            f"OAuth client file not found: {secrets_file}\n"
            "Download the Desktop client JSON from Google Cloud Console "
            "and save it as credentials.json in the project root."
        )

    creds: Credentials | None = None
    if stored_token.is_file():
        creds = Credentials.from_authorized_user_file(str(stored_token), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
        creds = flow.run_local_server(port=0)

    stored_token.parent.mkdir(parents=True, exist_ok=True)
    stored_token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def authorized_account_email(creds: Credentials | None = None) -> str:
    """Return the Google account email that authorized Drive access."""
    credentials = creds or get_credentials()
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    about = service.about().get(fields="user(emailAddress,displayName)").execute()
    user = about.get("user") or {}
    email = user.get("emailAddress")
    if not email:
        raise RuntimeError("Drive API did not return an authorized account email.")
    return email


def main() -> None:
    creds = get_credentials()
    email = authorized_account_email(creds)
    print(f"Authorized Google account: {email}")
    print(f"OAuth client file: {credentials_path()}")
    print(f"Token file: {token_path()}")
    print("Scope: https://www.googleapis.com/auth/drive.readonly")


if __name__ == "__main__":
    main()
