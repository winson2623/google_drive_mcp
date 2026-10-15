from pathlib import Path
from unittest.mock import MagicMock


import pytest
from google.auth.exceptions import RefreshError
from oauthlib.oauth2.rfc6749.errors import AccessDeniedError


from google_drive_mcp import auth




def _client_json(path: Path) -> Path:
    path.write_text(
        '{"installed":{"client_id":"id","client_secret":"secret",'
        '"redirect_uris":["http://localhost"]}}',
        encoding="utf-8",
    )
    return path




def test_missing_credentials_file_raises(tmp_path: Path) -> None:
    secrets = tmp_path / "credentials.json"
    token = tmp_path / "token.json"


    with pytest.raises(auth.CredentialsFileError, match="OAuth client file not found"):
        auth.get_credentials(client_secrets=secrets, token_file=token)




def test_reuses_valid_stored_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")


    creds = MagicMock()
    creds.valid = True
    creds.expired = False
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda *a, **k: creds)
    run_flow = MagicMock()
    monkeypatch.setattr(auth, "_run_installed_app_flow", run_flow)


    result = auth.get_credentials(client_secrets=secrets, token_file=token)


    assert result is creds
    run_flow.assert_not_called()




def test_refreshes_expired_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")


    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "refresh-token"
    creds.to_json.return_value = '{"token":"new"}'


    def refresh(_request: object) -> None:
        creds.valid = True
        creds.expired = False


    creds.refresh.side_effect = refresh
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda *a, **k: creds)
    monkeypatch.setattr(auth, "_restrict_private_file", lambda _path: None)
    run_flow = MagicMock()
    monkeypatch.setattr(auth, "_run_installed_app_flow", run_flow)


    result = auth.get_credentials(client_secrets=secrets, token_file=token)


    assert result is creds
    creds.refresh.assert_called_once()
    run_flow.assert_not_called()
    assert token.read_text(encoding="utf-8") == '{"token":"new"}'




def test_refresh_failure_starts_browser_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"
    token.write_text("stale", encoding="utf-8")


    stale = MagicMock()
    stale.valid = False
    stale.expired = True
    stale.refresh_token = "refresh-token"
    stale.refresh.side_effect = RefreshError("invalid_grant")
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda *a, **k: stale)


    fresh = MagicMock()
    fresh.valid = True
    fresh.to_json.return_value = '{"token":"fresh"}'
    monkeypatch.setattr(auth, "_run_installed_app_flow", lambda _secrets: fresh)
    monkeypatch.setattr(auth, "_restrict_private_file", lambda _path: None)


    result = auth.get_credentials(client_secrets=secrets, token_file=token)


    assert result is fresh
    assert token.read_text(encoding="utf-8") == '{"token":"fresh"}'




def test_refresh_failure_without_interactive_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"
    token.write_text("stale", encoding="utf-8")


    stale = MagicMock()
    stale.valid = False
    stale.expired = True
    stale.refresh_token = "refresh-token"
    stale.refresh.side_effect = RefreshError("invalid_grant")
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda *a, **k: stale)


    with pytest.raises(auth.TokenRefreshError, match="could not be refreshed"):
        auth.get_credentials(
            client_secrets=secrets, token_file=token, interactive=False
        )
    assert not token.exists()




def test_authorization_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"


    flow = MagicMock()
    flow.run_local_server.side_effect = AccessDeniedError()
    monkeypatch.setattr(
        auth.InstalledAppFlow, "from_client_secrets_file", lambda *a, **k: flow
    )


    with pytest.raises(auth.AuthorizationDeniedError, match="authorization was denied"):
        auth.get_credentials(client_secrets=secrets, token_file=token)
    assert not token.exists()




def test_interactive_flow_writes_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    token = tmp_path / "token.json"


    fresh = MagicMock()
    fresh.valid = True
    fresh.to_json.return_value = '{"token":"abc"}'
    monkeypatch.setattr(auth, "_run_installed_app_flow", lambda _secrets: fresh)
    monkeypatch.setattr(auth, "_restrict_private_file", lambda _path: None)


    result = auth.get_credentials(client_secrets=secrets, token_file=token)


    assert result is fresh
    assert token.read_text(encoding="utf-8") == '{"token":"abc"}'




def test_env_client_without_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token.json"
    monkeypatch.setattr(auth, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(auth, "credentials_path", lambda: tmp_path / "missing.json")
    monkeypatch.setenv("GDRIVE_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "env-client-secret")


    captured: dict[str, object] = {}


    def fake_flow(client: object) -> MagicMock:
        captured["client"] = client
        fresh = MagicMock()
        fresh.valid = True
        fresh.to_json.return_value = '{"token":"env"}'
        return fresh


    monkeypatch.setattr(auth, "_run_installed_app_flow", fake_flow)
    monkeypatch.setattr(auth, "_restrict_private_file", lambda _path: None)


    result = auth.get_credentials(token_file=token)


    assert result.valid is True
    assert token.read_text(encoding="utf-8") == '{"token":"env"}'
    config = captured["client"]
    assert isinstance(config, dict)
    assert config["installed"]["client_id"] == "env-client-id"




def test_partial_env_client_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(auth, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GDRIVE_CLIENT_ID", "only-id")
    monkeypatch.delenv("GDRIVE_CLIENT_SECRET", raising=False)
    with pytest.raises(auth.CredentialsFileError, match="both GDRIVE_CLIENT_ID"):
        auth.get_credentials(token_file=tmp_path / "token.json")




def test_env_client_overrides_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("GDRIVE_CLIENT_ID", "override-id")
    monkeypatch.setenv("GDRIVE_CLIENT_SECRET", "override-secret")


    source, client = auth.resolve_oauth_client()
    assert source == "env"
    assert isinstance(client, dict)
    assert client["installed"]["client_id"] == "override-id"




def test_capturing_flow_records_url_without_printing(capsys: pytest.CaptureFixture[str]) -> None:
    """stdout is the MCP channel, so the consent URL must be captured, never printed."""
    import threading


    config = {
        "installed": {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "secret",
            "auth_uri": auth.AUTH_URI,
            "token_uri": auth.TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = auth._build_flow(config, auth._CapturingFlow)
    flow.url_ready = threading.Event()
    flow.redirect_uri = "http://127.0.0.1:5555/"


    url, _state = flow.authorization_url(access_type="offline", prompt="consent")


    assert flow.auth_url == url and flow.url_ready.is_set()
    assert "code_challenge=" in url and "drive.readonly" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A5555%2F" in url
    assert capsys.readouterr().out == ""




def test_translate_flow_error_messages() -> None:
    assert isinstance(auth._translate_flow_error(AccessDeniedError()), auth.AuthorizationDeniedError)
    timeout = auth._translate_flow_error(auth.WSGITimeoutError())
    assert "Timed out" in str(timeout) and "authorize" in str(timeout)




def test_not_authorized_message_points_to_authorize_tool(tmp_path: Path) -> None:
    secrets = _client_json(tmp_path / "credentials.json")
    with pytest.raises(auth.AuthError, match="authorize"):
        auth.get_credentials(
            client_secrets=secrets, token_file=tmp_path / "token.json", interactive=False
        )


