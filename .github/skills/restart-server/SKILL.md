---
name: restart-server
description: Restart the Agency Session Dashboard server on port 8420. Use when dashboard code has been changed and needs reloading.
---

# Restart Dashboard Server

Restart the Agency Session Dashboard FastAPI server running on port 8420.

## Steps

1. Find the PID currently listening on port 8420:
   ```bash
   lsof -ti:8420
   ```

2. If a PID is found, kill it:
   ```bash
   kill <PID>
   ```
   Wait 1-2 seconds for the port to free.

3. Start the server in the background (detached):
   ```bash
   cd ~/code/work/agency-office && source .venv/bin/activate && python app.py &>/dev/null &
   ```

4. Wait 2 seconds, then verify:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8420/
   ```
   Expected: `200`

5. Report the result to the user.

## Notes
- The server runs on **http://127.0.0.1:8420/**
- App source is at `~/code/work/agency-office/app.py`
- Python venv is at `~/code/work/agency-office/.venv/`
- If kill fails because the port is already free, just proceed to step 3
