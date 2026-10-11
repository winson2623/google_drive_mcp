from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from google_drive_mcp import drive


def _folder(file_id: str, name: str, parent: str | None = None) -> dict:
    data = {
        "id": file_id,
        "name": name,
        "mimeType": drive.FOLDER_MIME,
    }
    if parent:
        data["parents"] = [parent]
    return data


def _file(file_id: str, name: str, mime: str, parent: str) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "parents": [parent],
    }


def _http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    resp.reason = "error"
    return HttpError(resp, b'{"error":{"message":"nope"}}')


def test_assessment_folder_id_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drive, "_load_dotenv", lambda: None)
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)
    with pytest.raises(drive.DriveError, match="GOOGLE_DRIVE_FOLDER_ID"):
        drive.assessment_folder_id()


def test_empty_folder() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _folder(
        "root", "Assessment"
    )
    service.files.return_value.list.return_value.execute.return_value = {"files": []}

    items = drive.list_folder("root", service=service)

    assert len(items) == 1
    assert items[0].name == "Assessment"
    assert items[0].is_folder
    assert "(empty folder)" in drive.format_listing(items)


def test_lists_files_and_nested_folders() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _folder(
        "root", "Assessment"
    )

    def list_execute() -> dict:
        request = service.files.return_value.list.call_args.kwargs
        query = request["q"]
        if "'root' in parents" in query:
            return {
                "files": [
                    _file("pdf1", "brief.pdf", "application/pdf", "root"),
                    _folder("sub", "nested", "root"),
                ]
            }
        if "'sub' in parents" in query:
            return {
                "files": [
                    _file("img1", "photo.png", "image/png", "sub"),
                ]
            }
        return {"files": []}

    service.files.return_value.list.return_value.execute.side_effect = list_execute

    items = drive.list_folder("root", service=service)
    by_id = {item.id: item for item in items}

    assert set(by_id) == {"root", "pdf1", "sub", "img1"}
    assert by_id["pdf1"].path == "Assessment/brief.pdf"
    assert by_id["pdf1"].mime_type == "application/pdf"
    assert by_id["sub"].is_folder
    assert by_id["img1"].path == "Assessment/nested/photo.png"
    assert by_id["img1"].parent_id == "sub"


def test_pagination() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _folder(
        "root", "Assessment"
    )
    service.files.return_value.list.return_value.execute.side_effect = [
        {
            "nextPageToken": "page-2",
            "files": [_file("a", "a.txt", "text/plain", "root")],
        },
        {
            "files": [_file("b", "b.txt", "text/plain", "root")],
        },
    ]

    items = drive.list_folder("root", service=service)

    assert [item.id for item in items] == ["root", "a", "b"]
    assert service.files.return_value.list.call_count == 2
    second_call = service.files.return_value.list.call_args_list[1]
    assert second_call.kwargs["pageToken"] == "page-2"


def test_not_a_folder() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _file(
        "file1", "notes.txt", "text/plain", "parent"
    )

    with pytest.raises(drive.DriveError, match="is not a folder"):
        drive.list_folder("file1", service=service)


def test_permission_error_404() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.side_effect = _http_error(404)

    with pytest.raises(drive.DriveAccessError, match="not shared"):
        drive.list_folder("missing", service=service)


def test_permission_error_403() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _folder(
        "root", "Assessment"
    )
    service.files.return_value.list.return_value.execute.side_effect = _http_error(403)

    with pytest.raises(drive.DriveAccessError, match="Cannot access Drive folder"):
        drive.list_folder("root", service=service)


def test_no_recursive_skips_nested_contents() -> None:
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = _folder(
        "root", "Assessment"
    )
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [_folder("sub", "nested", "root")]
    }

    items = drive.list_folder("root", recursive=False, service=service)

    assert [item.id for item in items] == ["root", "sub"]
    assert service.files.return_value.list.call_count == 1
