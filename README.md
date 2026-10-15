# google_drive_mcp


A small Claude Code integration to Google Drive. It's **read-only** and runs **entirely on your own machine**. No hosted backend, nothing to trust.


---


# Quick start


>You need: **Python 3.10+**, **git**, **Claude Code**, and a Google account that can open the test folder.


## 1. Install the plugin


**Windows (PowerShell):**


```powershell
python --version
claude plugin marketplace add winson2623/google_drive_mcp
claude plugin install google-drive-reader@google-drive-mcp
```


**macOS / Linux (bash):**


```bash
python3 --version
claude plugin marketplace add winson2623/google_drive_mcp
claude plugin install google-drive-reader@google-drive-mcp
find ~/.claude/plugins -path "*google-drive*" -name .mcp.json -exec sed -i.bak 's/"command": "python"/"command": "python3"/' {} +
```


> The last line (macOS / Linux only) switches the plugin from the `python` command to `python3`, which is what those systems usually have. Windows doesn't need it. On Debian/Ubuntu you may also need `sudo apt install python3-venv`.


No `pip install`, no venv to activate and no `.env` to create: the plugin sets itself up.


## 2. Start Claude Code


```
claude
```


> The first start installs the plugin's dependencies in the background (about 1-3 minutes, one time). Type `/mcp` and check that `google-drive` says **connected**. If Claude says it is still setting up, wait a moment and ask again.


## 3. Sign in to Google (one time)


**In Claude Code, type:**


> Authorize my Google Drive.


> Your browser opens. Sign in with the account that can see the folder. You'll see **"Google hasn't verified this app"**. That's expected. Click **Advanced → Go to … (unsafe)** and approve the read-only permission. Claude then confirms "Authorized ..." with your account.


## 4. Try it


> List the files in my Drive folder.


> Read the contents of `<file name>` and summarize it.




---


# How it works and why I built it this way


## What it does


Three MCP tools, over standard input/output:


- `authorize` signs you in to Google (see below).
- `list_files(folder_id?)` lists a folder and all its subfolders. If you don't pass a folder, it uses the one in `.mcp.json` (`GOOGLE_DRIVE_FOLDER_ID`).
- `read_file(file_id)` returns the text of a file, or a clear status message if it can't.


Both `list_files` and `read_file` accept a plain Drive ID or a full `drive.google.com` link.




## Decisions


### Local OAuth, no hosted backend


I used Google's `InstalledAppFlow.run_local_server()`. The plugin runs on the user's computer, so Google can redirect back to `http://127.0.0.1` (a random local port) and I don't need a deployed website to catch the callback. That means no server for me to secure, no one else holding your tokens, and fewer things that can break for this small feature.


The downside is that the OAuth client ID and secret live in the repo (`credentials.json`). For Desktop apps Google says these aren't really secret (they ship inside every copy of the app), and your Drive is still protected by your own permission screen. That is the trade off I've decided to make for this small feature.


### OAuth setup (my own Google Cloud client)


I created and run my own OAuth client, and the reviewer uses it as shipped in `credentials.json` (nothing to set up on your side). How it was set up, in [Google Cloud Console](https://console.cloud.google.com/):


1. Created a project (`google-drive-mcp`) and enabled the **Google Drive API** (APIs & Services → Library).
2. Configured the **OAuth consent screen** as *External* with only the scope `https://www.googleapis.com/auth/drive.readonly`, and set the publishing status to **In production** so any Google account can sign in. The app is not Google-verified, which is why the "Advanced → Go to … (unsafe)" screen appears.
3. Created **Credentials → OAuth client ID → Application type: Desktop app** and downloaded it as `credentials.json`.
4. **Redirect URI:** none to configure. Desktop clients allow Google to redirect to a loopback address (`http://127.0.0.1:<random port>/`), which is where the plugin listens during sign-in.
5. **Backend deployment:** none. The code-for-token exchange happens locally in the plugin (PKCE), so there is no server to deploy.


To use a different client instead, replace `credentials.json`, or set the `GDRIVE_CLIENT_ID` and `GDRIVE_CLIENT_SECRET` environment variables (they take precedence).




### Sign-in is a tool inside Claude Code, but it never prints to the server's output


The MCP server talks to Claude Code over stdio. If it printed a prompt or waited for input in the middle of that, it could hang or corrupt the connection. So the `authorize` tool runs the browser sign-in on a background thread, captures the sign-in URL instead of printing it (it is returned in the tool result as a fallback if no browser opens), and waits for you to approve. The other tools only reuse and refresh the saved token file. If there's no token, they return a clear message telling Claude to run `authorize`.


### Token storage


The token is saved to `token.json` in the plugin's own data folder (`~/.claude/plugins/data/google-drive-reader-...`), not in the repo. It is git-ignored and never hardcoded. The permissions are locked to your user and if token is expired, it refreshes automatically. If you deny consent then token is not saved and get error. The trade off is that token is a plain file protected by OS permissions (not the OS keychain), and uninstalling the plugin deletes it (signs you out). I decided it's acceptable for this small feature ran on local machine.




### Why a launcher script, not `uv`


I avoided piping an installer into a shell (`curl | sh`) and changing PowerShell's execution policy. `launch.py` uses only Python's standard library: on first start it creates a private virtual environment in the plugin's data folder and runs `pip install -r requirements.txt` into it (dependency versions are pinned), so nothing is installed system-wide. Because that first install takes longer than the ~30 seconds Claude Code allows an MCP server to connect, the launcher answers the connection handshake immediately, installs in the background, then starts the real server. No reconnect is needed. The trade off is that it needs Python 3.10+ on the PATH and internet access once.




### Reading: statuses instead of crashes


Every read returns one of `ok`, `empty`, `unsupported`, or `error`. I did this so Claude always gets something it can explain to the user, instead of a stack trace.




### Errors


Auth and Drive errors are turned into MCP `ToolError`s with plain messages. A 403 or 404 gets a specific explanation: Google returns 404 when a file exists but isn't shared with your account.


### Tests


There are unit tests for auth, listing, reading, and the MCP server, all with mocks so they run without internet or a Google account. They cover things like token refresh, refresh failure, denied consent, pagination, nested folders, 403/404, empty files, bad PDFs, and URL parsing. Run them with `pytest` (create a venv and `pip install -r requirements.txt pytest` first).




### Known limits


- No OCR, so images and scanned PDFs can't be read. This decision is to reduce the complexity of this small feature.
- No search and no write support. That was out of scope of this task and keeps the permissions small.
- Large folders are listed in full every time (no caching). To keep code and feature simple and always up to date.


---


