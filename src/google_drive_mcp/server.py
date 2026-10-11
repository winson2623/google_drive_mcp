"""MCP server: list and read Google Drive files over stdio."""

from __future__ import annotations

import re

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from google_drive_mcp import drive
from google_drive_mcp.auth import AuthError
from google_drive_mcp.drive import DriveError

DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
FOLDER_URL_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
FILE_URL_RE = re.compile(r"/d/([A-Za-z0-9_-]+)")
OPEN_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")

INSTRUCTIONS = (
    "Read-only Google Drive tools. Use list_files to list a folder "
    "(including nested files), then read_file to extract text from a file ID. "
    "Authorize first with: python -m google_drive_mcp.auth"
)


def parse_drive_id(value: str | None, *, field: str) -> str:
    """Accept a Drive file/folder ID or a drive.google.com URL."""
    raw = (value or "").strip()
    if not raw:
        raise ToolError(
            f"{field} is required. Pass a Google Drive ID or a drive.google.com URL."
        )

    for pattern in (FOLDER_URL_RE, FILE_URL_RE, OPEN_ID_RE):
        match = pattern.search(raw)
        if match:
            raw = match.group(1)
            break

    if raw.lower() == "root":
        return "root"
    if not DRIVE_ID_RE.fullmatch(raw):
        raise ToolError(
            f"{field} is not a valid Drive ID. Use the ID from the Drive URL "
            "(after /folders/ or /d/), not a file name."
        )
    return raw


def _call_drive(action: str, fn):
    try:
        return fn()
    except (AuthError, DriveError) as exc:
        raise ToolError(f"{action} failed: {exc}") from exc


def create_server() -> MCPServer:
    """Register list_files and read_file on a new MCP server."""
    server = MCPServer(
        name="google-drive",
        title="Google Drive Reader",
        instructions=INSTRUCTIONS,
    )

    @server.tool(
        name="list_files",
        title="List Drive files",
        description=(
            "List files and subfolders in a Google Drive folder (read-only). "
            "Pass folder_id as the Drive folder ID or a folders/ URL. "
            "If folder_id is omitted, uses GOOGLE_DRIVE_FOLDER_ID from the environment. "
            "Returns names, IDs, MIME types, and relative paths, including nested folders."
        ),
        structured_output=False,
    )
    def list_files(folder_id: str | None = None) -> str:
        target = (
            parse_drive_id(folder_id, field="folder_id")
            if (folder_id or "").strip()
            else None
        )
        items = _call_drive(
            "Listing",
            lambda: drive.list_folder(target, recursive=True, interactive=False),
        )
        return drive.format_listing(items)

    @server.tool(
        name="read_file",
        title="Read Drive file",
        description=(
            "Read a Google Drive file by ID and return extracted text. "
            "Pass file_id as the Drive file ID or a file URL. "
            "Supports plain text, Google Docs/Sheets/Slides (exported to text/CSV), "
            "and PDFs (text layer via pypdf). Images and other binary types return a "
            "clear unsupported message instead of crashing."
        ),
        structured_output=False,
    )
    def read_file(file_id: str) -> str:
        target = parse_drive_id(file_id, field="file_id")
        content = _call_drive(
            "Read",
            lambda: drive.read_file(target, interactive=False),
        )
        return drive.format_file_content(content)

    return server


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
