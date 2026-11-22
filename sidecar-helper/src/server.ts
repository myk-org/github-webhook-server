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

const DEFAULT_SHUTDOWN_TIMEOUT_MS = 10_000;

export interface RunSidecarDeps {
  startSidecar?: () => StartedSidecarHandle;
  bindSidecarListenExit?: (handle: StartedSidecarHandle) => void;
  proc?: SidecarProcess;
  logError?: (...args: unknown[]) => void;
  shutdownTimeoutMs?: number;
}

export function runSidecarServer(
  deps: RunSidecarDeps = {},
): StartedSidecarHandle {
  const start = deps.startSidecar ?? startSidecar;
  const bindListenExit = deps.bindSidecarListenExit ?? bindSidecarListenExit;
  const proc = deps.proc ?? process;
  const logError = deps.logError ?? console.error;
  const shutdownTimeoutMs =
    deps.shutdownTimeoutMs ?? DEFAULT_SHUTDOWN_TIMEOUT_MS;

  const handle = start();
  bindListenExit(handle);

  proc.once("SIGTERM", async () => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const timeout = new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          reject(
            new Error(
              `Timed out closing sidecar after ${shutdownTimeoutMs}ms`,
            ),
          );
        }, shutdownTimeoutMs);
        timer.unref();
      });
      await Promise.race([handle.close(), timeout]);
    } catch (err: unknown) {
      logError("[sidecar] Failed to close cleanly on SIGTERM:", err);
      proc.exit(1);
      return;
    } finally {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
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
