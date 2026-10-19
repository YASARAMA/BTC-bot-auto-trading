// Starts an isolated backend for the end-to-end tests: fresh databases under .e2e/, the
// bundled sample candles imported, then the API and the built web interface on port 8765.
// Set CAT_PYTHON if the virtual environment is not at claude-adaptive-trader/.venv.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const backend = join(root, "backend");
const config = join(here, "config.e2e.yaml");
const python = process.env.CAT_PYTHON ?? join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

if (!existsSync(python)) {
  console.error(`Python not found at ${python}. Create the virtual environment (see README) or set CAT_PYTHON.`);
  process.exit(1);
}
rmSync(join(root, ".e2e"), { recursive: true, force: true });
const imported = spawnSync(python, ["-m", "app", "--config", config, "import-samples"], { cwd: backend, stdio: "inherit" });
if (imported.status !== 0) process.exit(imported.status ?? 1);

const server = spawn(python, ["-m", "app", "--config", config, "serve"], { cwd: backend, stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.kill(signal));
server.on("exit", (code) => process.exit(code ?? 0));
