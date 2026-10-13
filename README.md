# google_drive_mcp

A small Claude Code integration to Google Drive. It's **read-only** and runs **entirely on your own machine**. No hosted backend, nothing to trust.

---

# Quick start

>You need: **Python 3.10+**, **git**, **Claude Code**, and a Google account that can open the test folder.

## 1. Clone and install

**macOS / Linux (bash):**

git clone https://github.com/winson2623/google_drive_mcp
cd google_drive_mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env

**Windows (PowerShell):**

git clone https://github.com/winson2623/google_drive_mcp
cd google_drive_mcp
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env

> Your prompt should now start with `(.venv)`.

## 2. Sign in to Google (one time)

**In the same terminal:**

python -m google_drive_mcp.auth


> Your browser opens. Sign in with the account that can see the folder. You'll see **"Google hasn't verified this app"**. That's expected. Click **Advanced → Go to google_drive_mcp (unsafe)** and approve the read-only permission. Then go back to the terminal. You should see "Authorized Google account: ...".

## 3. Start Claude Code

**Same terminal (venv still active):**

claude

>Claude Code will ask if you trust the `google-drive` MCP server from `.mcp.json`. Choose **Yes**. Then type `/mcp` and check that `google-drive` says **connected**.

## 4. Try it

> List the files in my Drive folder.

> Read the contents of `<file name>` and summarize it.


---

# How it works and why I built it this way

## What it does

Two MCP tools, over standard input/output:

- `list_files(folder_id?)` lists a folder and all its subfolders. If you don't pass a folder, it uses the one in `.env`.
- `read_file(file_id)` returns the text of a file, or a clear status message if it can't.

Both accept a plain Drive ID or a full `drive.google.com` link.


## Decisions

### Local OAuth, no hosted backend

I used Google's `InstalledAppFlow.run_local_server()`. The plugin runs on the user's computer, so Google can redirect back to `http://localhost` and I don't need a deployed website to catch the callback. That means no server for me to secure, no one else holding your tokens, and fewer things that can break for this small feature.

The downside is that the OAuth client ID and secret live in the repo. For Desktop apps Google says these aren't really secret (they ship inside every copy of the app), and your Drive is still protected by your own permission screen. That is the trade off I've decided to make for this small feature.

### Sign-in is a separate command, not inside the server

The MCP server talks to Claude Code over stdio. If it tried to open a browser or wait for input in the middle of that, it could hang or corrupt the connection. So I split it up. You run `python -m google_drive_mcp.auth` once, and the server only reuses and refreshes the saved token file. If there's no token, the tool returns a clear message telling you what to run.


### Token storage

The token is saved to `token.json` in the project folder. It is git-ignored and never hardcoded. The permissions are locked to your user and if token is expired, it refreshes automatically. If you deny consent then token is not saved and get error. The trade off is that token is in same file as code, so deleting the folder signs you out, I decided it's acceptable for this small feature ran on local machine.


### Reading: statuses instead of crashes

Every read returns one of `ok`, `empty`, `unsupported`, or `error`. I did this so Claude always gets something it can explain to the user, instead of a stack trace.


### Errors

Auth and Drive errors are turned into MCP `ToolError`s with plain messages. A 403 or 404 gets a specific explanation: Google returns 404 when a file exists but isn't shared with your account.

### Tests

There are unit tests for auth, listing, reading, and the MCP server, all with mocks so they run without internet or a Google account. They cover things like token refresh, refresh failure, denied consent, pagination, nested folders, 403/404, empty files, bad PDFs, and URL parsing. Run them with `pytest`.


### Known limits

- No OCR, so images and scanned PDFs can't be read. This decision is to reduce the complexity of this small feature.
- No search and no write support. That was out of scope of this task and keeps the permissions small.
- Large folders are listed in full every time (no caching). To keep code and feature simple and always up to date.

---




