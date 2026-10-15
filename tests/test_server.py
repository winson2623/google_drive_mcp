import asyncio
from unittest.mock import MagicMock


import pytest
from mcp.server.mcpserver.exceptions import ToolError


from google_drive_mcp import drive
from google_drive_mcp.auth import AuthError
from google_drive_mcp.server import create_server, parse_drive_id




def _text(result) -> str:
    return "".join(getattr(block, "text", "") or "" for block in result.content)




def test_parse_drive_id_from_url() -> None:
    folder_url = "https://drive.google.com/drive/folders/1saJB6XNlDo-lTGvVD9ILcA4Ra4P28MQy"
    file_url = "https://drive.google.com/file/d/1I8VBwJXxMtArAECKpNp1efVE5DnxQbHK/view"


    assert parse_drive_id(folder_url, field="folder_id") == "1saJB6XNlDo-lTGvVD9ILcA4Ra4P28MQy"
    assert parse_drive_id(file_url, field="file_id") == "1I8VBwJXxMtArAECKpNp1efVE5DnxQbHK"
    assert parse_drive_id("root", field="folder_id") == "root"




def test_parse_drive_id_rejects_empty_and_names() -> None:
    with pytest.raises(ToolError, match="required"):
        parse_drive_id("  ", field="folder_id")
    with pytest.raises(ToolError, match="not a valid Drive ID"):
        parse_drive_id("notes.pdf", field="file_id")




def test_lists_registered_tools() -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}


    assert names == {"authorize", "list_files", "read_file"}
    by_name = {tool.name: tool for tool in tools}
    assert "folder_id" in by_name["list_files"].description
    assert "file_id" in by_name["read_file"].description
    assert "folder_id" in (by_name["list_files"].input_schema.get("properties") or {})
    assert "file_id" in (by_name["read_file"].input_schema.get("properties") or {})
    required = by_name["read_file"].input_schema.get("required") or []
    assert "file_id" in required




def test_list_files_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        drive.DriveItem(
            id="root",
            name="Assessment",
            mime_type=drive.FOLDER_MIME,
            path="Assessment",
            is_folder=True,
        ),
        drive.DriveItem(
            id="pdf1",
            name="Winston Chen.pdf",
            mime_type="application/pdf",
            path="Assessment/Winston Chen.pdf",
            is_folder=False,
            parent_id="root",
        ),
    ]
    listed = MagicMock(return_value=items)
    monkeypatch.setattr(drive, "list_folder", listed)


    result = asyncio.run(
        create_server().call_tool("list_files", {"folder_id": "1saJB6XNlDo-lTGvVD9ILcA4Ra4P28MQy"})
    )


    listed.assert_called_once()
    assert listed.call_args.args[0] == "1saJB6XNlDo-lTGvVD9ILcA4Ra4P28MQy"
    assert listed.call_args.kwargs["interactive"] is False
    assert "Winston Chen.pdf" in _text(result)
    assert result.is_error is not True




def test_list_files_defaults_to_env_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = MagicMock(
        return_value=[
            drive.DriveItem(
                id="env",
                name="FromEnv",
                mime_type=drive.FOLDER_MIME,
                path="FromEnv",
                is_folder=True,
            )
        ]
    )
    monkeypatch.setattr(drive, "list_folder", listed)


    result = asyncio.run(create_server().call_tool("list_files", {}))


    listed.assert_called_once()
    assert listed.call_args.args[0] is None
    assert "FromEnv" in _text(result)




def test_read_file_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    content = drive.FileContent(
        id="pdf1",
        name="Winston Chen.pdf",
        mime_type="application/pdf",
        status="ok",
        text="Resume text",
    )
    reader = MagicMock(return_value=content)
    monkeypatch.setattr(drive, "read_file", reader)


    result = asyncio.run(
        create_server().call_tool(
            "read_file",
            {"file_id": "https://drive.google.com/file/d/1I8VBwJXxMtArAECKpNp1efVE5DnxQbHK/view"},
        )
    )


    reader.assert_called_once()
    assert reader.call_args.args[0] == "1I8VBwJXxMtArAECKpNp1efVE5DnxQbHK"
    assert "Resume text" in _text(result)




def test_read_file_rejects_invalid_id() -> None:
    with pytest.raises(ToolError, match="not a valid Drive ID"):
        asyncio.run(create_server().call_tool("read_file", {"file_id": "catmeme.jpg"}))




def test_auth_error_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "list_folder",
        MagicMock(side_effect=AuthError("No valid Google credentials.")),
    )


    with pytest.raises(ToolError, match="No valid Google credentials"):
        asyncio.run(create_server().call_tool("list_files", {"folder_id": "1saJB6XNlDo-lTGvVD9ILcA4Ra4P28MQy"}))




# --- authorize tool -------------------------------------------------------




def _fake_pending(**attrs) -> MagicMock:
    pending = MagicMock()
    pending.url = "https://accounts.google.com/o/oauth2/auth?client_id=x"
    pending.browser_opened = False
    pending.error = None
    pending.credentials = None
    pending.url_ready.wait.return_value = True
    pending.done.wait.return_value = False
    for key, value in attrs.items():
        setattr(pending, key, value)
    return pending




def test_authorize_reports_already_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    from google_drive_mcp import auth


    monkeypatch.setattr(auth, "get_credentials", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(auth, "authorized_account_email", lambda _c: "me@example.com")
    start = MagicMock()
    monkeypatch.setattr(auth, "start_authorization", start)


    result = asyncio.run(create_server().call_tool("authorize", {}))


    assert "Already authorized as me@example.com" in _text(result)
    start.assert_not_called()




def test_authorize_opens_browser_and_returns_url_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google_drive_mcp import auth, server


    monkeypatch.setattr(auth, "get_credentials", MagicMock(side_effect=AuthError("none")))
    pending = _fake_pending()
    monkeypatch.setattr(auth, "start_authorization", lambda: pending)
    opened = MagicMock()
    monkeypatch.setattr(server.webbrowser, "open", opened)


    first = _text(asyncio.run(create_server().call_tool("authorize", {})))
    second = _text(asyncio.run(create_server().call_tool("authorize", {})))


    assert pending.url in first and "authorize again" in first
    assert pending.url in second
    opened.assert_called_once()  # a second call must not open a second tab




def test_authorize_success_confirms_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from google_drive_mcp import auth, server


    monkeypatch.setattr(auth, "get_credentials", MagicMock(side_effect=AuthError("none")))
    creds = MagicMock()
    pending = _fake_pending(credentials=creds)
    pending.done.wait.return_value = True
    monkeypatch.setattr(auth, "start_authorization", lambda: pending)
    monkeypatch.setattr(auth, "authorized_account_email", lambda _c: "me@example.com")
    monkeypatch.setattr(server.webbrowser, "open", MagicMock())


    result = asyncio.run(create_server().call_tool("authorize", {}))


    assert "Authorized me@example.com" in _text(result)




def test_authorize_denied_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from google_drive_mcp import auth, server


    monkeypatch.setattr(auth, "get_credentials", MagicMock(side_effect=AuthError("none")))
    pending = _fake_pending(error=auth.AuthorizationDeniedError("authorization was denied"))
    pending.done.wait.return_value = True
    monkeypatch.setattr(auth, "start_authorization", lambda: pending)
    monkeypatch.setattr(server.webbrowser, "open", MagicMock())


    with pytest.raises(ToolError, match="authorization was denied"):
        asyncio.run(create_server().call_tool("authorize", {}))




def test_missing_client_file_is_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from google_drive_mcp import auth


    monkeypatch.setattr(
        auth, "get_credentials", MagicMock(side_effect=auth.CredentialsFileError("OAuth client file not found"))
    )
    with pytest.raises(ToolError, match="OAuth client file not found"):
        asyncio.run(create_server().call_tool("authorize", {}))


