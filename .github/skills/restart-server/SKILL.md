---
name: restart-server
description: Restart the Agency Session Dashboard server on port 8420. Use when dashboard code has been changed and needs reloading.
---

# Restart Dashboard Server

Run the restart script:

```bash
~/code/work/agency-office/restart.sh
```

This kills any existing process on port 8420, starts the server, and health-checks it.

## Notes
- The server runs on **http://127.0.0.1:8420/**
- Logs at `/tmp/dashboard.log`
