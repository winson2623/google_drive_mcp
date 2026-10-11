# google_drive_mcp

A Claude Code integration that lets you ask natural-language questions about files in a Google Drive folder. **Read-only**, runs **entirely on your machine**: no hosted backend, no server to trust.

## Prerequisites

- **Python 3.10+** 
- **Claude Code** 
- **git**
- A Google account that can open the folder (owner, or shared as Viewer)


### 1. Clone and install
**macOS / Linux:**
git clone https://github.com/winson2623/google_drive_mcp
cd google_drive_mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env


**Windows Powershell:**

git clone https://github.com/winson2623/google_drive_mcp
cd google_drive_mcp
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env

Your prompt should now start with (.venv).
Downloaded a ZIP instead of cloning? Open the terminal in the folder that contains pyproject.toml and run everything from py -m venv .venv onward.


### 2. Authorize Google (one time)

**In the same terminal:**

python -m google_drive_mcp.auth

Your browser opens for Google sign-in:

Sign in with the account that can see the folder.
You will see "Google hasn't verified this app". This is expected (see Unverified app screen). Click Advanced → Go to google_drive_mcp (unsafe).
Approve the read-only Drive scope and return to the terminal.

The token is saved to token.json in the project folder.

### 3. Start Claude Code

**In the same terminal** (virtual environment still active), from the project folder:

claude


Claude Code asks whether to trust the project's `google-drive` MCP server (from `.mcp.json`). Choose **Yes**. Then run `/mcp` and confirm `google-drive` shows as **connected**.

### 4. Try it

> List the files in my Drive folder.

> Read the contents of `<file name>` and summarize it.

