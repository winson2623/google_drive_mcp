from pathlib import Path

import pytest

from google_drive_mcp import auth


def test_missing_credentials_file_raises(tmp_path: Path) -> None:
    secrets = tmp_path / "credentials.json"
    token = tmp_path / "token.json"

    with pytest.raises(FileNotFoundError, match="OAuth client file not found"):
        auth.get_credentials(client_secrets=secrets, token_file=token)
