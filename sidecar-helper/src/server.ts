import { bindSidecarListenExit, startSidecar } from "@myk-org/pi-sidecar";

const handle = startSidecar();
bindSidecarListenExit(handle);

process.once("SIGTERM", async () => {
  await handle.close();
  process.exit(0);
});
