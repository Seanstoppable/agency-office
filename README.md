# Agency Office 🏢

A local web dashboard for browsing, searching, and managing [GitHub Copilot CLI](https://githubnext.com/projects/copilot-cli/) sessions across all your repositories.

Works with both **vanilla Copilot CLI** and the [Agency](https://github.com/Seanstoppable/agency) wrapper.

![Dashboard](https://img.shields.io/badge/port-8420-blue) ![Python](https://img.shields.io/badge/python-3.12+-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Why?

Copilot CLI sessions accumulate quickly — across repos, branches, and terminals. There's no built-in way to:

- See all your sessions in one place
- Find that session from last week where you fixed the auth bug
- Know which terminal is waiting for your input vs. actively working
- Clean up stale sessions and zombie lock files

Agency Office fills that gap.

## Features

### 📊 Dashboard
- Sessions grouped by repository (alphabetically sorted)
- **Activity state detection** — reads `events.jsonl` to show:
  - ⚙️ **Working** — actively running tools/generating
  - ⏳ **Waiting for input** — finished a turn, needs your prompt
  - 💤 **Idle** — stale session (>1 hour since last activity)
- Stats bar with Working / Waiting / Idle breakdown
- Resume sessions directly from the UI
- Per-repo "Clear All" (preserves active sessions)

### 🔍 Search
- Full-text search across session summaries, conversation turns, and checkpoints
- Powered by SQLite FTS5

### 📋 Session Detail
- Full conversation history (user messages + assistant responses)
- Checkpoints with overview, technical details, and next steps
- Files touched and git refs (commits, PRs)

### 🌳 Worktrees
- Lists all git worktrees with their branches
- One-click cleanup of worktrees with merged PRs

### 🧹 Cleanup
- Preview stale sessions (no turns, old, shutdowns with stale locks)
- Bulk purge from the session store
- Clean up orphaned lock files

### 🏢 The Office *(whimsical)*
- Repos are **rooms** in an office floor plan
- Sessions are **workers** at desks with persistent emoji avatars
- Worker activities derived from real session data (summary, branch, turn count)
- The Lobby for uncategorized sessions, pinned at the top
- Proportional room sizing with masonry layout

### 📚 The Library *(whimsical)*
- Sessions are **books** on wooden shelves
- Book thickness = turn count, spine color = deterministic per session
- Genre classification from session summary keywords
- Condition labels: "Never opened" → "Spine cracked"
- Hover tooltips with full metadata

## Architecture

```
~/.copilot/
├── session-store.db          ← SQLite DB (read-only, sessions/turns/checkpoints)
└── session-state/
    └── {session-uuid}/
        ├── workspace.yaml    ← cwd, branch, summary
        ├── events.jsonl      ← event stream (activity detection)
        └── inuse.{pid}.lock  ← active session indicator
```

Single-file FastAPI app (`app.py`, ~1200 lines) with Jinja2 templates and htmx for interactivity. No build step, no JavaScript framework, no external database.

## Quick Start

```bash
# Clone
git clone https://github.com/Seanstoppable/agency-office.git
cd agency-office

# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python app.py
# → http://127.0.0.1:8420
```

Or use the restart script (kills existing, starts fresh, health checks):

```bash
./restart.sh
# ✓ Dashboard running at http://127.0.0.1:8420/
```

## Requirements

- Python 3.12+
- Copilot CLI (the standard `~/.copilot/` directory structure)
- Dependencies: `fastapi`, `uvicorn`, `jinja2`, `watchfiles`, `aiofiles`

## How It Works

**Session discovery**: Reads `~/.copilot/session-store.db` (SQLite, WAL mode, read-only) for sessions, turns, checkpoints, file refs, and git refs.

**Active detection**: Scans for `inuse.{pid}.lock` files and verifies the PID is alive.

**Activity state**: Reads the last event from `events.jsonl` to determine if a session is working, waiting for input, or stale. Uses a 64KB tail read to handle large event files efficiently.

**Repository fallback**: When no git remote is configured (empty `repository` field), derives the repo name from the working directory path.

## Compatibility

| Feature | Copilot CLI | Agency |
|---------|:-----------:|:------:|
| Session store DB | ✅ | ✅ |
| workspace.yaml | ✅ | ✅ |
| events.jsonl | ✅ | ✅ |
| Lock files | ✅ | ✅ |
| Activity detection | ✅ | ✅ |
| Session resume | — | ✅ |

The dashboard reads standard Copilot CLI infrastructure. The only Agency-specific feature is session resume (launching `copilot --resume`).

## License

MIT
