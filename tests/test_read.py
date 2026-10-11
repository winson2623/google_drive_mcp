from unittest.mock import MagicMock

import pytest

from google_drive_mcp import drive


def _meta(
    file_id: str,
    name: str,
    mime: str,
    *,
    size: str | None = None,
) -> dict:
    data = {"id": file_id, "name": name, "mimeType": mime}
    if size is not None:
        data["size"] = size
    return data


def test_read_text_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta("t1", "notes.txt", "text/plain", size="5"),
    )
    monkeypatch.setattr(drive, "_download_bytes", lambda _service, _file_id: b"hello")

    result = drive.read_file("t1", service=MagicMock())

    assert result.status == "ok"
    assert result.text == "hello"


def test_export_google_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta(
            "d1", "Spec", "application/vnd.google-apps.document"
        ),
    )
    monkeypatch.setattr(drive, "_export_bytes", lambda _service, _file_id, mime: b"Doc body")

    result = drive.read_file("d1", service=MagicMock())

    assert result.status == "ok"
    assert result.text == "Doc body"


def test_extract_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta(
            "p1", "Winston Chen.pdf", "application/pdf", size="20"
        ),
    )
    monkeypatch.setattr(drive, "_download_bytes", lambda _service, _file_id: b"%PDF-fake")
    monkeypatch.setattr(drive, "_extract_pdf_text", lambda _data: "Resume text")

    result = drive.read_file("p1", service=MagicMock())

    assert result.status == "ok"
    assert result.text == "Resume text"


def test_image_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta(
            "i1", "catmeme.jpg", "image/jpeg", size="99"
        ),
    )
    download = MagicMock()
    monkeypatch.setattr(drive, "_download_bytes", download)

    result = drive.read_file("i1", service=MagicMock())

    assert result.status == "unsupported"
    assert "Cannot extract text from image" in (result.message or "")
    download.assert_not_called()


def test_empty_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta("e1", "blank.txt", "text/plain", size="0"),
    )

    result = drive.read_file("e1", service=MagicMock())

    assert result.status == "empty"
    assert result.text == ""


def test_pdf_without_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta("p2", "scan.pdf", "application/pdf", size="10"),
    )
    monkeypatch.setattr(drive, "_download_bytes", lambda _service, _file_id: b"%PDF")
    monkeypatch.setattr(drive, "_extract_pdf_text", lambda _data: "")

    result = drive.read_file("p2", service=MagicMock())

    assert result.status == "empty"
    assert "No extractable text" in (result.message or "")


def test_pdf_extraction_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta("p3", "bad.pdf", "application/pdf", size="10"),
    )
    monkeypatch.setattr(drive, "_download_bytes", lambda _service, _file_id: b"not-pdf")
    monkeypatch.setattr(
        drive,
        "_extract_pdf_text",
        lambda _data: (_ for _ in ()).throw(drive.DriveError("Could not parse PDF: boom")),
    )

    result = drive.read_file("p3", service=MagicMock())

    assert result.status == "error"
    assert "Could not parse PDF" in (result.message or "")


def test_folder_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        drive,
        "_get_file",
        lambda _service, _file_id: _meta("f1", "nested", drive.FOLDER_MIME),
    )

    result = drive.read_file("f1", service=MagicMock())

    assert result.status == "error"
    assert "folder" in (result.message or "").lower()


def test_access_error_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_service, file_id: str) -> dict:
        raise drive.DriveAccessError(f"Cannot access Drive file or folder {file_id}.")

    monkeypatch.setattr(drive, "_get_file", fail)

    with pytest.raises(drive.DriveAccessError):
        drive.read_file("missing", service=MagicMock())
