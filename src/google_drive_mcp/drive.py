"""Read-only Google Drive listing and file reading."""

from __future__ import annotations

import argparse
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, MediaIoBaseDownload
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from google_drive_mcp.auth import AuthError, _load_dotenv, get_credentials

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, mimeType, parents, size"
LIST_FIELDS = f"nextPageToken, files({FILE_FIELDS})"
PAGE_SIZE = 100
MAX_DEPTH = 50
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

GOOGLE_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

TEXT_MIMES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/csv",
    "application/rtf",
    "application/x-sh",
    "application/x-python",
    "application/typescript",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".py",
    ".yml",
    ".yaml",
    ".ini",
    ".log",
    ".rst",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".svg",
}


class DriveError(Exception):
    """Base error for Drive listing failures."""


class DriveAccessError(DriveError):
    """The file or folder is missing or not shared with the authorized account."""


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    path: str
    is_folder: bool
    parent_id: str | None = None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "id": self.id,
            "name": self.name,
            "mimeType": self.mime_type,
            "path": self.path,
            "isFolder": self.is_folder,
            "parentId": self.parent_id,
        }


@dataclass(frozen=True)
class FileContent:
    id: str
    name: str
    mime_type: str
    status: str
    text: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "name": self.name,
            "mimeType": self.mime_type,
            "status": self.status,
            "text": self.text,
            "message": self.message,
        }


def assessment_folder_id() -> str:
    _load_dotenv()
    folder_id = (os.environ.get("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    if not folder_id:
        raise DriveError(
            "GOOGLE_DRIVE_FOLDER_ID is not set. Copy .env.example to .env "
            "and put the Drive folder ID (the value after /folders/) there."
        )
    return folder_id


def drive_service(*, interactive: bool = False):
    creds = get_credentials(interactive=interactive)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _http_status(exc: HttpError) -> int:
    status = getattr(exc, "status_code", None)
    if status:
        return int(status)
    resp = getattr(exc, "resp", None)
    if resp is not None and getattr(resp, "status", None):
        return int(resp.status)
    return 0


def _raise_drive_http(exc: HttpError, folder_id: str) -> None:
    status = _http_status(exc)
    if status in (403, 404):
        raise DriveAccessError(
            f"Cannot access Drive file or folder {folder_id}. "
            "Google returns 404 when the item does not exist or is not "
            "shared with the authorized account. Open it in a browser "
            "while signed in as that account, or ask the owner to share it "
            "with Viewer access."
        ) from exc
    raise DriveError(f"Drive API request failed ({status or 'unknown'}): {exc}") from exc


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _execute(request: HttpRequest):
    return request.execute()


def _get_file(service, file_id: str) -> dict:
    try:
        return _execute(
            service.files().get(
                fileId=file_id,
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
        )
    except HttpError as exc:
        _raise_drive_http(exc, file_id)
        raise


def _item_from_file(file: dict, path: str, parent_id: str | None) -> DriveItem:
    mime = file.get("mimeType") or "application/octet-stream"
    return DriveItem(
        id=file["id"],
        name=file.get("name") or file["id"],
        mime_type=mime,
        path=path,
        is_folder=mime == FOLDER_MIME,
        parent_id=parent_id,
    )


def _list_children(service, folder_id: str) -> list[dict]:
    escaped = _escape_query_value(folder_id)
    query = f"'{escaped}' in parents and trashed = false"
    page_token: str | None = None
    files: list[dict] = []
    while True:
        try:
            response = _execute(
                service.files().list(
                    q=query,
                    fields=LIST_FIELDS,
                    pageSize=PAGE_SIZE,
                    pageToken=page_token,
                    orderBy="folder,name",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
        except HttpError as exc:
            _raise_drive_http(exc, folder_id)
            raise
        files.extend(response.get("files") or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def list_folder(
    folder_id: str | None = None,
    *,
    recursive: bool = True,
    service=None,
    interactive: bool = False,
) -> list[DriveItem]:
    """List files in a folder. Nested folders are walked when recursive=True."""
    target_id = folder_id or assessment_folder_id()
    client = service or drive_service(interactive=interactive)
    root = _get_file(client, target_id)
    if root.get("mimeType") != FOLDER_MIME:
        raise DriveError(
            f"{target_id} is not a folder (mimeType={root.get('mimeType')})."
        )

    parents = root.get("parents") or []
    root_parent = parents[0] if parents else None
    root_item = _item_from_file(root, root.get("name") or root["id"], root_parent)
    items = [root_item]
    visited = {root_item.id}

    def walk(current_id: str, current_path: str, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise DriveError(
                f"Folder nesting exceeded {MAX_DEPTH} levels under {current_path}."
            )
        children = _list_children(client, current_id)
        for child in children:
            child_id = child["id"]
            if child_id in visited:
                continue
            visited.add(child_id)
            name = child.get("name") or child_id
            path = f"{current_path}/{name}"
            item = _item_from_file(child, path, current_id)
            items.append(item)
            if recursive and item.is_folder:
                walk(item.id, item.path, depth + 1)

    walk(root_item.id, root_item.path, 1)
    return items


def _is_image(mime_type: str, name: str) -> bool:
    if mime_type.startswith("image/"):
        return True
    if mime_type in {
        "application/vnd.google-apps.photo",
        "application/vnd.google-apps.drawing",
    }:
        return True
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def _is_text(mime_type: str, name: str) -> bool:
    if mime_type.startswith("text/"):
        return True
    if mime_type in TEXT_MIMES:
        return True
    return Path(name).suffix.lower() in TEXT_EXTENSIONS


def _decode_text(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _download_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
    done = False
    try:
        while not done:
            _status, done = downloader.next_chunk()
    except HttpError as exc:
        _raise_drive_http(exc, file_id)
        raise
    return buffer.getvalue()


def _export_bytes(service, file_id: str, mime_type: str) -> bytes:
    request = service.files().export_media(fileId=file_id, mimeType=mime_type)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
    done = False
    try:
        while not done:
            _status, done = downloader.next_chunk()
    except HttpError as exc:
        _raise_drive_http(exc, file_id)
        raise
    return buffer.getvalue()


def _extract_pdf_text(data: bytes) -> str:
    if not data:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as exc:
        raise DriveError(f"Could not parse PDF: {exc}") from exc
    except Exception as exc:
        raise DriveError(f"Could not parse PDF: {exc}") from exc

    parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            extracted = page.extract_text() or ""
        except Exception as exc:
            raise DriveError(
                f"Could not extract text from PDF page {index}: {exc}"
            ) from exc
        parts.append(extracted)
    return "\n".join(parts).strip()


def _content(
    file: dict,
    *,
    status: str,
    text: str | None = None,
    message: str | None = None,
) -> FileContent:
    return FileContent(
        id=file["id"],
        name=file.get("name") or file["id"],
        mime_type=file.get("mimeType") or "application/octet-stream",
        status=status,
        text=text,
        message=message,
    )


def read_file(
    file_id: str,
    *,
    service=None,
    interactive: bool = False,
) -> FileContent:
    """Read a Drive file by ID into text, or return a clear unsupported/error status."""
    client = service or drive_service(interactive=interactive)
    file = _get_file(client, file_id)
    mime = file.get("mimeType") or "application/octet-stream"
    name = file.get("name") or file_id
    size = int(file["size"]) if str(file.get("size") or "").isdigit() else None

    if mime == FOLDER_MIME:
        return _content(
            file,
            status="error",
            message="This ID is a folder. Use list_folder to see its files.",
        )

    if _is_image(mime, name):
        return _content(
            file,
            status="unsupported",
            message=(
                f"Cannot extract text from image '{name}' ({mime}). "
                "This plugin is read-only text: list the file, but image bytes "
                "are not transcribed."
            ),
        )

    if size == 0:
        return _content(
            file,
            status="empty",
            text="",
            message=f"File '{name}' is empty.",
        )

    if size is not None and size > MAX_DOWNLOAD_BYTES:
        return _content(
            file,
            status="error",
            message=(
                f"File '{name}' is {size} bytes, which exceeds the "
                f"{MAX_DOWNLOAD_BYTES} byte download limit."
            ),
        )

    export_mime = GOOGLE_EXPORT_MIME.get(mime)
    try:
        if export_mime:
            data = _export_bytes(client, file_id, export_mime)
            text = _decode_text(data).strip()
        elif mime == "application/pdf" or name.lower().endswith(".pdf"):
            data = _download_bytes(client, file_id)
            text = _extract_pdf_text(data)
        elif _is_text(mime, name):
            data = _download_bytes(client, file_id)
            text = _decode_text(data).strip()
        elif mime.startswith("application/vnd.google-apps."):
            return _content(
                file,
                status="unsupported",
                message=(
                    f"Cannot extract text from Google file '{name}' ({mime}). "
                    "Only Docs, Sheets, and Slides are exported to text."
                ),
            )
        else:
            return _content(
                file,
                status="unsupported",
                message=(
                    f"Cannot extract text from '{name}' ({mime}). "
                    "Supported: plain text, Google Docs/Sheets/Slides, and PDF."
                ),
            )
    except DriveError as exc:
        return _content(file, status="error", message=str(exc))

    if not text:
        return _content(
            file,
            status="empty",
            text="",
            message=(
                f"No extractable text in '{name}'. "
                "The file may be empty or a scanned PDF without a text layer."
            ),
        )
    return _content(file, status="ok", text=text)


def format_listing(items: list[DriveItem]) -> str:
    if not items:
        return "No files found."
    root = items[0]
    lines = [
        f"Folder: {root.name}",
        f"ID: {root.id}",
        f"MIME: {root.mime_type}",
        "",
    ]
    descendants = items[1:]
    if not descendants:
        lines.append("(empty folder)")
        return "\n".join(lines)
    for item in descendants:
        kind = "folder" if item.is_folder else "file"
        lines.append(f"  [{kind}] {item.path}")
        lines.append(f"         id={item.id}  mime={item.mime_type}")
    lines.append("")
    lines.append(f"{len(descendants)} item(s) under this folder (including nested).")
    return "\n".join(lines)


def format_file_content(content: FileContent) -> str:
    lines = [
        f"File: {content.name}",
        f"ID: {content.id}",
        f"MIME: {content.mime_type}",
        f"Status: {content.status}",
        "",
    ]
    if content.message:
        lines.append(content.message)
        if content.text:
            lines.append("")
    if content.status == "ok" and content.text is not None:
        lines.append(content.text)
    return "\n".join(lines).rstrip() + "\n"


def _list_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="List files in a Google Drive folder (read-only)."
    )
    parser.add_argument(
        "folder_id",
        nargs="?",
        default=None,
        help="Folder ID (defaults to GOOGLE_DRIVE_FOLDER_ID).",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="List only the immediate children of the folder.",
    )
    args = parser.parse_args(argv)

    try:
        items = list_folder(
            args.folder_id,
            recursive=not args.no_recursive,
            interactive=True,
        )
    except (AuthError, DriveError) as exc:
        print(f"Listing failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(format_listing(items))


def _read_cli(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Read a Google Drive file by ID.")
    parser.add_argument("file_id", help="Drive file ID to read.")
    args = parser.parse_args(argv)

    try:
        content = read_file(args.file_id, interactive=True)
    except (AuthError, DriveError) as exc:
        print(f"Read failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(format_file_content(content), end="")
    if content.status not in {"ok", "empty", "unsupported"}:
        raise SystemExit(1)


def read_main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    _read_cli(argv)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "read":
        _read_cli(argv[1:])
        return
    _list_cli(argv)


if __name__ == "__main__":
    main()
