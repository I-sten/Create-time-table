const fs = require("fs");
const path = require("path");

const root = __dirname;
const virtualEnvPython = process.platform === "win32"
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");

if (!fs.existsSync(virtualEnvPython)) {
  throw new Error(
    `Python virtual environment not found at ${virtualEnvPython}. ` +
    "Create it with: python -m venv .venv"
  );
}

module.exports = {
  apps: [
    {
      name: "timetable-api",
      cwd: root,
      script: virtualEnvPython,
      args: "-m uvicorn api:app --host 0.0.0.0 --port 8000",
      interpreter: "none",
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: "1G",
      kill_timeout: 10000,
      env: {
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
