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

    assert names == {"list_files", "read_file"}
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
