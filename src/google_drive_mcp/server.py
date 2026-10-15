"""MCP server: list and read Google Drive files over stdio."""


from __future__ import annotations


import asyncio
import re
import webbrowser


from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


from google_drive_mcp import auth, drive
from google_drive_mcp.auth import AuthError
from google_drive_mcp.drive import DriveError


DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
FOLDER_URL_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
FILE_URL_RE = re.compile(r"/d/([A-Za-z0-9_-]+)")
OPEN_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]+)")


AUTHORIZE_WAIT_SECONDS = 100


INSTRUCTIONS = (
    "Read-only Google Drive tools. Use list_files to list a folder "
    "(including nested files), then read_file to extract text from a file ID. "
    "If a tool says Google is not authorized yet, call the authorize tool: it "
    "opens the user's browser for Google sign-in and returns once they approve. "
    "Tell the user to approve in the browser; if Google warns the app is "
    "unverified they should click Advanced, then the 'Go to ... (unsafe)' link at the bottom."
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




def _authorize_blocking() -> str:
    """Run (or resume) the browser sign-in; runs on a worker thread."""
    try:
        creds = auth.get_credentials(interactive=False)
    except auth.CredentialsFileError as exc:
        raise ToolError(f"Authorization failed: {exc}") from exc
    except AuthError:
        creds = None  # no usable token: fall through to the browser flow


    if creds is not None:
        try:
            return f"Already authorized as {auth.authorized_account_email(creds)}."
        except AuthError as exc:
            return f"A valid Google token exists, but the account check failed: {exc}"


    try:
        pending = auth.start_authorization()
    except AuthError as exc:
        raise ToolError(f"Authorization failed: {exc}") from exc


    pending.url_ready.wait(timeout=15)
    if pending.url and not pending.browser_opened:
        pending.browser_opened = True
        try:
            webbrowser.open(pending.url, new=1)
        except Exception:  # noqa: BLE001 - the URL is returned below as a fallback
            pass


    pending.done.wait(timeout=AUTHORIZE_WAIT_SECONDS)


    if pending.error is not None:
        raise ToolError(f"Authorization failed: {pending.error}")
    if pending.credentials is not None:
        try:
            who = auth.authorized_account_email(pending.credentials)
        except AuthError:
            who = "your Google account"
        return (
            f"Authorized {who} with read-only Drive access. "
            "You can now list and read files."
        )
    return (
        "Waiting for the user to finish signing in to Google in their browser. "
        "A browser tab should have opened; if not, open this URL:\n"
        f"{pending.url}\n\n"
        f"{auth.UNVERIFIED_APP_HINT}\n\n"
        "After approving, call authorize again (or just retry the request)."
    )




def create_server() -> MCPServer:
    """Register list_files and read_file on a new MCP server."""
    server = MCPServer(
        name="google-drive",
        title="Google Drive Reader",
        instructions=INSTRUCTIONS,
    )


    @server.tool(
        name="authorize",
        title="Authorize Google Drive",
        description=(
            "Sign in to Google (OAuth, read-only Drive scope). Opens the user's "
            "browser and waits for them to approve, then confirms which account "
            "was authorized. Safe to call again: it reports 'already authorized' "
            "when a valid token exists. Call this when another tool says Google "
            "is not authorized yet, or when the user asks to authorize/log in."
        ),
        structured_output=False,
    )
    async def authorize() -> str:
        return await asyncio.to_thread(_authorize_blocking)


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


