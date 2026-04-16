"""
Agency Session Dashboard — Local control plane for Copilot CLI sessions.

Reads session-store.db (read-only) and session-state/ directories to provide
a web-based view of all sessions across repos, with search, live status,
and session interaction capabilities.
"""

import asyncio
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

# --- Configuration ---

COPILOT_DIR = Path.home() / ".copilot"
SESSION_STORE_DB = COPILOT_DIR / "session-store.db"
SESSION_STATE_DIR = COPILOT_DIR / "session-state"

app = FastAPI(title="Agency Session Dashboard")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# --- Database helpers ---

@contextmanager
def get_db():
    """Open session-store.db in read-only mode."""
    conn = sqlite3.connect(f"file:{SESSION_STORE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- Session state helpers ---

def get_active_sessions() -> dict[str, int]:
    """Return {session_id: pid} for sessions with a live lock file."""
    active = {}
    for lock_file in SESSION_STATE_DIR.glob("*/inuse.*.lock"):
        session_id = lock_file.parent.name
        pid_str = lock_file.stem.split(".")[-1]  # inuse.{pid}
        try:
            pid = int(pid_str)
            os.kill(pid, 0)  # check if alive
            active[session_id] = pid
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    return active


def get_workspace_yaml(session_id: str) -> dict:
    """Read workspace.yaml for a session."""
    yaml_path = SESSION_STATE_DIR / session_id / "workspace.yaml"
    if not yaml_path.exists():
        return {}
    result = {}
    try:
        with open(yaml_path) as f:
            for line in f:
                if ":" in line:
                    key, _, val = line.partition(":")
                    result[key.strip()] = val.strip()
    except Exception:
        pass
    return result


def get_events_summary(session_id: str, max_events: int = 50) -> list[dict]:
    """Read recent events from events.jsonl."""
    events_path = SESSION_STATE_DIR / session_id / "events.jsonl"
    if not events_path.exists():
        return []
    events = []
    try:
        with open(events_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return events[-max_events:]


# --- Jinja2 filters ---

def time_ago(dt_str: str | None) -> str:
    """Convert ISO datetime string to human-readable 'time ago'."""
    if not dt_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            mins = seconds // 60
            return f"{mins}m ago"
        elif seconds < 86400:
            hrs = seconds // 3600
            return f"{hrs}h ago"
        else:
            days = seconds // 86400
            return f"{days}d ago"
    except Exception:
        return dt_str


def short_repo(repo: str | None) -> str:
    """Extract short repo name from full ADO path."""
    if not repo:
        return "—"
    parts = repo.split("/")
    return parts[-1] if parts else repo


def truncate(text: str | None, length: int = 80) -> str:
    if not text:
        return ""
    return text[:length] + "…" if len(text) > length else text


templates.env.filters["time_ago"] = time_ago
templates.env.filters["short_repo"] = short_repo
templates.env.filters["truncate"] = truncate


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard — active sessions + recent sessions grouped by repo."""
    active_pids = get_active_sessions()

    with get_db() as db:
        # All sessions ordered by recent activity
        sessions = db.execute("""
            SELECT s.id, s.cwd, s.repository, s.branch, s.summary,
                   s.created_at, s.updated_at,
                   COUNT(DISTINCT t.turn_index) as turn_count
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            LIMIT 200
        """).fetchall()

    # Enrich with live status and workspace data
    enriched = []
    for s in sessions:
        row = dict(s)
        row["is_active"] = s["id"] in active_pids
        row["pid"] = active_pids.get(s["id"])
        # Fill in missing metadata from workspace.yaml
        if not row.get("cwd") or not row.get("repository"):
            ws = get_workspace_yaml(s["id"])
            row["cwd"] = row.get("cwd") or ws.get("cwd", "")
            row["repository"] = row.get("repository") or ws.get("repository", "")
            row["branch"] = row.get("branch") or ws.get("branch", "")
        enriched.append(row)

    # Split active vs recent
    active = [s for s in enriched if s["is_active"]]
    recent = [s for s in enriched if not s["is_active"]]

    # Group recent by repo
    repos: dict[str, list] = {}
    for s in recent:
        repo = s.get("repository") or "No Repository"
        repos.setdefault(repo, []).append(s)

    return templates.TemplateResponse(
        request, "dashboard.html",
        context={
            "active_sessions": active,
            "repos": repos,
            "total_sessions": len(enriched),
        },
    )


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def session_detail(request: Request, session_id: str):
    """Session detail — summary, checkpoints, conversation, files."""
    active_pids = get_active_sessions()

    with get_db() as db:
        session = db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return HTMLResponse("<h1>Session not found</h1>", status_code=404)

        turns = db.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,)
        ).fetchall()

        checkpoints = db.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY checkpoint_number",
            (session_id,)
        ).fetchall()

        files = db.execute(
            "SELECT * FROM session_files WHERE session_id = ? ORDER BY first_seen_at",
            (session_id,)
        ).fetchall()

        refs = db.execute(
            "SELECT * FROM session_refs WHERE session_id = ? ORDER BY created_at",
            (session_id,)
        ).fetchall()

    ws = get_workspace_yaml(session_id)
    session_dict = dict(session)
    session_dict["is_active"] = session_id in active_pids
    session_dict["cwd"] = session_dict.get("cwd") or ws.get("cwd", "")
    session_dict["repository"] = session_dict.get("repository") or ws.get("repository", "")
    session_dict["branch"] = session_dict.get("branch") or ws.get("branch", "")
    session_dict["host_type"] = ws.get("host_type", "")

    return templates.TemplateResponse(
        request, "session_detail.html",
        context={
            "session": session_dict,
            "turns": [dict(t) for t in turns],
            "checkpoints": [dict(c) for c in checkpoints],
            "files": [dict(f) for f in files],
            "refs": [dict(r) for r in refs],
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = Query(default="")):
    """Full-text search across session content."""
    results = []
    if q and len(q) >= 2:
        with get_db() as db:
            results = db.execute("""
                SELECT si.content, si.session_id, si.source_type, si.source_id,
                       s.summary, s.repository, s.branch, s.updated_at
                FROM search_index si
                JOIN sessions s ON s.id = si.session_id
                WHERE search_index MATCH ?
                ORDER BY rank
                LIMIT 50
            """, (q,)).fetchall()
            results = [dict(r) for r in results]

    return templates.TemplateResponse(
        request, "search.html",
        context={
            "query": q,
            "results": results,
        },
    )


@app.get("/api/sessions", response_class=JSONResponse)
async def api_sessions():
    """API endpoint for session list (for htmx polling)."""
    active_pids = get_active_sessions()

    with get_db() as db:
        sessions = db.execute("""
            SELECT id, cwd, repository, branch, summary, updated_at
            FROM sessions ORDER BY updated_at DESC LIMIT 50
        """).fetchall()

    data = []
    for s in sessions:
        row = dict(s)
        row["is_active"] = s["id"] in active_pids
        if not row.get("repository"):
            ws = get_workspace_yaml(s["id"])
            row["repository"] = ws.get("repository", "")
            row["branch"] = ws.get("branch", "")
        data.append(row)

    return data


@app.post("/api/launch", response_class=JSONResponse)
async def launch_session(request: Request):
    """Launch a new agency session in iTerm2."""
    body = await request.json()
    cwd = body.get("cwd", os.path.expanduser("~"))
    prompt = body.get("prompt", "")

    cmd = f"cd {cwd} && agency copilot"
    if prompt:
        cmd += f' -p "{prompt}"'

    # Launch in iTerm2 via AppleScript
    ascript = f'''
    tell application "iTerm"
        activate
        tell current window
            create tab with default profile
            tell current session
                write text "{cmd}"
            end tell
        end tell
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", ascript], check=True, capture_output=True)
        return {"status": "ok", "message": f"Launched in iTerm2: {cwd}"}
    except subprocess.CalledProcessError as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


@app.post("/api/resume/{session_id}", response_class=JSONResponse)
async def resume_session(session_id: str):
    """Resume a session in iTerm2."""
    ws = get_workspace_yaml(session_id)
    cwd = ws.get("cwd", os.path.expanduser("~"))

    cmd = f"cd {cwd} && agency copilot --resume {session_id}"

    ascript = f'''
    tell application "iTerm"
        activate
        tell current window
            create tab with default profile
            tell current session
                write text "{cmd}"
            end tell
        end tell
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", ascript], check=True, capture_output=True)
        return {"status": "ok", "message": f"Resumed session {session_id[:8]}…"}
    except subprocess.CalledProcessError as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )


# --- Worktree & Git helpers ---

def get_worktrees() -> list[dict]:
    """Scan for git worktrees across known repos."""
    worktrees = []
    # Find all worktrees by scanning parent directories of known session cwds
    seen_roots = set()
    for session_dir in SESSION_STATE_DIR.iterdir():
        ws = get_workspace_yaml(session_dir.name)
        git_root = ws.get("git_root", "")
        if not git_root:
            continue
        # The main repo root could have worktrees
        parent = str(Path(git_root).parent)
        if parent in seen_roots:
            continue
        seen_roots.add(parent)

    # Also check common work directories
    work_dir = Path.home() / "code" / "work"
    if work_dir.exists():
        # Find all git dirs that are worktrees
        try:
            result = subprocess.run(
                ["find", str(work_dir), "-maxdepth", "2", "-name", ".git", "-type", "f"],
                capture_output=True, text=True, timeout=5
            )
            for git_file in result.stdout.strip().split("\n"):
                if not git_file:
                    continue
                wt_dir = str(Path(git_file).parent)
                try:
                    with open(git_file) as f:
                        content = f.read().strip()
                    if content.startswith("gitdir:"):
                        main_git = content.split("gitdir:")[1].strip()
                        # This is a worktree
                        wt_info = {"path": wt_dir, "is_worktree": True}
                        # Get branch
                        br = subprocess.run(
                            ["git", "-C", wt_dir, "branch", "--show-current"],
                            capture_output=True, text=True, timeout=3
                        )
                        wt_info["branch"] = br.stdout.strip()
                        # Check dirty
                        st = subprocess.run(
                            ["git", "-C", wt_dir, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=3
                        )
                        wt_info["dirty"] = bool(st.stdout.strip())
                        wt_info["name"] = Path(wt_dir).name
                        worktrees.append(wt_info)
                except Exception:
                    pass
        except Exception:
            pass

    return worktrees


def check_branch_merged(repo_path: str, branch: str) -> bool | None:
    """Check if a branch has been merged into main/master."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "branch", "--merged", "origin/main"],
            capture_output=True, text=True, timeout=5
        )
        merged_branches = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n")]
        return branch in merged_branches
    except Exception:
        return None


@app.get("/worktrees", response_class=HTMLResponse)
async def worktrees_page(request: Request):
    """Show all git worktrees with their status."""
    wts = get_worktrees()
    # Enrich with merge status
    for wt in wts:
        wt["merged"] = check_branch_merged(wt["path"], wt["branch"])

    return templates.TemplateResponse(
        request, "worktrees.html",
        context={"worktrees": wts},
    )


@app.post("/api/cleanup-worktree", response_class=JSONResponse)
async def cleanup_worktree(request: Request):
    """Remove a worktree and its branch."""
    body = await request.json()
    path = body.get("path", "")
    branch = body.get("branch", "")

    if not path or not Path(path).exists():
        return JSONResponse({"status": "error", "message": "Path not found"}, status_code=400)

    try:
        # Find the main repo (parent of .git/worktrees)
        git_file = Path(path) / ".git"
        if git_file.is_file():
            with open(git_file) as f:
                content = f.read().strip()
            if "gitdir:" in content:
                # Extract main repo path from gitdir reference
                gitdir = content.split("gitdir:")[1].strip()
                # gitdir points to .git/worktrees/<name>, main repo is 3 levels up
                main_repo = str(Path(gitdir).parent.parent.parent)
        else:
            main_repo = path

        # Remove worktree
        subprocess.run(
            ["git", "-C", main_repo, "worktree", "remove", "--force", path],
            check=True, capture_output=True, text=True, timeout=10
        )
        # Delete branch
        if branch:
            subprocess.run(
                ["git", "-C", main_repo, "branch", "-D", branch],
                capture_output=True, text=True, timeout=5
            )
        return {"status": "ok", "message": f"Removed worktree {Path(path).name} and branch {branch}"}
    except subprocess.CalledProcessError as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed: {e.stderr}"},
            status_code=500
        )


# --- Headless prompt execution ---

# Track running headless jobs
_headless_jobs: dict[str, dict] = {}


@app.post("/api/headless", response_class=JSONResponse)
async def headless_prompt(request: Request):
    """Run a headless agency copilot prompt. Returns a job ID to poll for results."""
    body = await request.json()
    cwd = body.get("cwd", os.path.expanduser("~"))
    prompt = body.get("prompt", "")
    session_id = body.get("session_id")  # optional: resume existing session

    if not prompt:
        return JSONResponse({"status": "error", "message": "Prompt required"}, status_code=400)

    job_id = f"job-{datetime.now().strftime('%H%M%S')}-{os.getpid()}"

    cmd = ["agency", "copilot", "-p", prompt, "--yolo"]
    if session_id:
        cmd.extend(["--resume", session_id])

    _headless_jobs[job_id] = {
        "status": "running",
        "prompt": prompt,
        "cwd": cwd,
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "output": "",
    }

    async def run_job():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await proc.communicate()
            _headless_jobs[job_id]["output"] = stdout.decode(errors="replace")
            _headless_jobs[job_id]["status"] = "done" if proc.returncode == 0 else "error"
            _headless_jobs[job_id]["returncode"] = proc.returncode
        except Exception as e:
            _headless_jobs[job_id]["status"] = "error"
            _headless_jobs[job_id]["output"] = str(e)

    asyncio.create_task(run_job())
    return {"status": "ok", "job_id": job_id}


@app.get("/api/headless/{job_id}", response_class=JSONResponse)
async def headless_status(job_id: str):
    """Check status of a headless job."""
    job = _headless_jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "error", "message": "Job not found"}, status_code=404)
    return job


# --- Session cleanup ---

def get_cleanup_candidates(
    no_summary: bool = True,
    low_turns: bool = True,
    older_than_days: int = 30,
) -> list[dict]:
    """Find sessions that are candidates for cleanup."""
    active_pids = get_active_sessions()
    now = datetime.now(timezone.utc)

    with get_db() as db:
        sessions = db.execute("""
            SELECT s.id, s.cwd, s.repository, s.branch, s.summary,
                   s.created_at, s.updated_at,
                   COUNT(DISTINCT t.turn_index) as turn_count
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at ASC
        """).fetchall()

    candidates = []
    for s in sessions:
        sid = s["id"]
        # Skip active sessions
        if sid in active_pids:
            continue

        row = dict(s)
        reasons = []

        # Check reasons
        if no_summary and not s["summary"]:
            reasons.append("no_summary")
        if low_turns and (s["turn_count"] or 0) <= 1:
            reasons.append("low_turns")

        age_days = None
        if s["updated_at"]:
            try:
                updated = datetime.fromisoformat(s["updated_at"].replace("Z", "+00:00"))
                age_days = (now - updated).days
                if older_than_days and age_days >= older_than_days:
                    reasons.append("old")
            except Exception:
                pass

        if not reasons:
            continue

        # Get folder size
        session_dir = SESSION_STATE_DIR / sid
        folder_size = 0
        folder_exists = session_dir.exists()
        if folder_exists:
            try:
                for f in session_dir.rglob("*"):
                    if f.is_file():
                        folder_size += f.stat().st_size
            except Exception:
                pass

        row["reasons"] = reasons
        row["age_days"] = age_days
        row["folder_size"] = folder_size
        row["folder_exists"] = folder_exists

        # Fill missing metadata from workspace.yaml
        if not row.get("repository"):
            ws = get_workspace_yaml(sid)
            row["repository"] = ws.get("repository", "")
            row["branch"] = ws.get("branch", "")

        candidates.append(row)

    return candidates


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


templates.env.filters["format_size"] = format_size


def cleanup_stale_locks() -> int:
    """Remove inuse.*.lock files where the PID is dead. Returns count removed."""
    removed = 0
    for lock_file in SESSION_STATE_DIR.glob("*/inuse.*.lock"):
        pid_str = lock_file.stem.split(".")[-1]
        try:
            pid = int(pid_str)
            os.kill(pid, 0)  # alive — keep it
        except (ValueError, ProcessLookupError):
            try:
                lock_file.unlink()
                removed += 1
            except Exception:
                pass
        except PermissionError:
            pass  # alive but can't signal — keep it
    return removed


@app.get("/cleanup", response_class=HTMLResponse)
async def cleanup_page(
    request: Request,
    no_summary: bool = True,
    low_turns: bool = True,
    older_than_days: int = 30,
):
    """Session cleanup page with filters and bulk actions."""
    candidates = get_cleanup_candidates(
        no_summary=no_summary,
        low_turns=low_turns,
        older_than_days=older_than_days,
    )
    total_size = sum(c["folder_size"] for c in candidates)

    # Count stale locks
    stale_locks = 0
    for lock_file in SESSION_STATE_DIR.glob("*/inuse.*.lock"):
        pid_str = lock_file.stem.split(".")[-1]
        try:
            pid = int(pid_str)
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            stale_locks += 1
        except PermissionError:
            pass

    return templates.TemplateResponse(
        request, "cleanup.html",
        context={
            "candidates": candidates,
            "total_size": total_size,
            "stale_locks": stale_locks,
            "filters": {
                "no_summary": no_summary,
                "low_turns": low_turns,
                "older_than_days": older_than_days,
            },
        },
    )


@app.post("/api/cleanup/preview", response_class=JSONResponse)
async def cleanup_preview(request: Request):
    """Preview what would be cleaned up for given session IDs."""
    body = await request.json()
    session_ids = body.get("session_ids", [])

    total_size = 0
    details = []
    for sid in session_ids:
        session_dir = SESSION_STATE_DIR / sid
        size = 0
        if session_dir.exists():
            for f in session_dir.rglob("*"):
                if f.is_file():
                    size += f.stat().st_size
        total_size += size
        details.append({"id": sid, "folder_size": size, "folder_exists": session_dir.exists()})

    return {
        "count": len(session_ids),
        "total_size": total_size,
        "total_size_human": format_size(total_size),
        "details": details,
    }


@app.post("/api/cleanup/purge", response_class=JSONResponse)
async def cleanup_purge(request: Request):
    """Full purge: delete session folder + DB rows."""
    body = await request.json()
    session_ids = body.get("session_ids", [])
    active_pids = get_active_sessions()

    if not session_ids:
        return JSONResponse({"status": "error", "message": "No sessions specified"}, status_code=400)

    # Safety: never delete active sessions
    safe_ids = [sid for sid in session_ids if sid not in active_pids]
    skipped = len(session_ids) - len(safe_ids)

    deleted_folders = 0
    deleted_db = 0
    freed_bytes = 0

    # Delete session-state folders
    for sid in safe_ids:
        session_dir = SESSION_STATE_DIR / sid
        if session_dir.exists():
            try:
                size = sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
                shutil.rmtree(session_dir)
                freed_bytes += size
                deleted_folders += 1
            except Exception:
                pass

    # Delete from session-store.db (open in write mode)
    try:
        conn = sqlite3.connect(str(SESSION_STORE_DB))
        conn.execute("PRAGMA journal_mode=WAL")
        for sid in safe_ids:
            conn.execute("DELETE FROM search_index WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM session_refs WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM session_files WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM checkpoints WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            deleted_db += 1
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
    except Exception as e:
        return JSONResponse(
            {"status": "partial", "message": f"Folders deleted but DB error: {e}",
             "deleted_folders": deleted_folders},
            status_code=500
        )

    return {
        "status": "ok",
        "deleted_folders": deleted_folders,
        "deleted_db": deleted_db,
        "freed_bytes": freed_bytes,
        "freed_human": format_size(freed_bytes),
        "skipped_active": skipped,
        "message": f"Purged {deleted_db} sessions, freed {format_size(freed_bytes)}"
            + (f" (skipped {skipped} active)" if skipped else ""),
    }


@app.post("/api/cleanup/stale-locks", response_class=JSONResponse)
async def api_cleanup_stale_locks():
    """Remove stale lock files."""
    removed = cleanup_stale_locks()
    return {"status": "ok", "removed": removed, "message": f"Removed {removed} stale lock files"}


# --- Main ---

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8420, log_level="info")
