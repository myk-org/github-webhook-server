import { realpathSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  bindSidecarListenExit,
  startSidecar,
  type StartedSidecarHandle,
} from "@myk-org/pi-sidecar";

export interface SidecarProcess {
  once(event: "SIGTERM", listener: () => void): unknown;
  exit(code?: number): unknown;
}

export interface RunSidecarDeps {
  startSidecar?: () => StartedSidecarHandle;
  bindSidecarListenExit?: (handle: StartedSidecarHandle) => void;
  proc?: SidecarProcess;
  logError?: (...args: unknown[]) => void;
}

export function runSidecarServer(
  deps: RunSidecarDeps = {},
): StartedSidecarHandle {
  const start = deps.startSidecar ?? startSidecar;
  const bindListenExit = deps.bindSidecarListenExit ?? bindSidecarListenExit;
  const proc = deps.proc ?? process;
  const logError = deps.logError ?? console.error;

  const handle = start();
  bindListenExit(handle);

  proc.once("SIGTERM", async () => {
    try {
      await handle.close();
    } catch (err: unknown) {
      logError("[sidecar] Failed to close cleanly on SIGTERM:", err);
      proc.exit(1);
      return;
    }
    proc.exit(0);
  });

  return handle;
}

function isMainModule(): boolean {
  const entry = process.argv[1];
  if (!entry) {
    return false;
  }
  const modulePath = fileURLToPath(import.meta.url);
  try {
    return realpathSync(entry) === realpathSync(modulePath);
  } catch {
    return resolve(entry) === modulePath;
  }
}

if (isMainModule()) {
  runSidecarServer();
}
