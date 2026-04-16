import { execFile, spawn } from "node:child_process";
import { joinSession } from "@github/copilot-sdk/extension";

const PORT = 8420;
const APP_DIR = "/Users/ssmith/code/work/agency-office";

function run(cmd, args) {
    return new Promise((resolve) => {
        execFile(cmd, args, { timeout: 10000 }, (err, stdout, stderr) => {
            resolve({ ok: !err, stdout: (stdout || "").trim(), stderr: (stderr || "").trim() });
        });
    });
}

const session = await joinSession({
    tools: [
        {
            name: "restart_dashboard",
            description:
                "Restart the Agency Session Dashboard server (FastAPI on port 8420). " +
                "Kills any existing process on the port, starts a fresh one, and verifies it responds.",
            parameters: { type: "object", properties: {}, required: [] },
            handler: async () => {
                const lsof = await run("lsof", ["-ti", `:${PORT}`]);
                if (lsof.ok && lsof.stdout) {
                    const pid = lsof.stdout.split("\n")[0].trim();
                    if (/^\d+$/.test(pid)) {
                        await run("kill", [pid]);
                        await new Promise((r) => setTimeout(r, 1500));
                    }
                }

                const child = spawn(
                    `${APP_DIR}/.venv/bin/python`,
                    ["app.py"],
                    { cwd: APP_DIR, detached: true, stdio: "ignore" },
                );
                child.unref();

                await new Promise((r) => setTimeout(r, 2500));

                const check = await run("curl", [
                    "-s", "-o", "/dev/null", "-w", "%{http_code}",
                    `http://127.0.0.1:${PORT}/`,
                ]);

                if (check.ok && check.stdout === "200") {
                    return `Dashboard restarted on http://127.0.0.1:${PORT}/`;
                }
                return `Server may have started but health check returned: ${check.stdout || check.stderr}`;
            },
        },
    ],
});
