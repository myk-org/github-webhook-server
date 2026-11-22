import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { describe, it } from "node:test";
import { runSidecarServer } from "../dist/server.js";

function createFakeProcess() {
  const emitter = new EventEmitter();
  const exitCodes = [];
  let resolveExit;
  const exited = new Promise((resolve) => {
    resolveExit = resolve;
  });

  const proc = {
    once(event, listener) {
      emitter.once(event, listener);
      return proc;
    },
    emit(event) {
      return emitter.emit(event);
    },
    listenerCount(event) {
      return emitter.listenerCount(event);
    },
    exit(code) {
      exitCodes.push(code);
      resolveExit(code);
    },
    exitCodes,
    exited,
  };

  return proc;
}

describe("runSidecarServer", () => {
  it("starts the sidecar and binds listen-exit on startup", () => {
    const fakeHandle = {
      close: async () => {},
      ready: Promise.resolve(),
      stopped: new Promise(() => {}),
    };
    const calls = [];
    const proc = createFakeProcess();

    const returned = runSidecarServer({
      startSidecar: () => {
        calls.push("startSidecar");
        return fakeHandle;
      },
      bindSidecarListenExit: (handle) => {
        calls.push(["bindSidecarListenExit", handle]);
      },
      proc,
    });

    assert.equal(returned, fakeHandle);
    assert.equal(calls.length, 2);
    assert.equal(calls[0], "startSidecar");
    assert.deepEqual(calls[1], ["bindSidecarListenExit", fakeHandle]);
    assert.equal(proc.listenerCount("SIGTERM"), 1);
  });

  it("awaits handle.close() before exiting 0 on SIGTERM", async () => {
    const events = [];
    let releaseClose;
    const closeGate = new Promise((resolve) => {
      releaseClose = resolve;
    });

    const proc = createFakeProcess();
    const origExit = proc.exit.bind(proc);
    proc.exit = (code) => {
      events.push(`exit:${code}`);
      origExit(code);
    };

    const fakeHandle = {
      async close() {
        events.push("close:start");
        await closeGate;
        events.push("close:end");
      },
      ready: Promise.resolve(),
      stopped: new Promise(() => {}),
    };

    runSidecarServer({
      startSidecar: () => fakeHandle,
      bindSidecarListenExit: () => {},
      proc,
    });

    proc.emit("SIGTERM");
    await Promise.resolve();

    assert.deepEqual(events, ["close:start"]);
    assert.deepEqual(proc.exitCodes, []);

    releaseClose();
    const code = await proc.exited;

    assert.equal(code, 0);
    assert.deepEqual(events, ["close:start", "close:end", "exit:0"]);
    assert.deepEqual(proc.exitCodes, [0]);
  });

  it("logs and exits 1 cleanly if handle.close() rejects on SIGTERM", async (t) => {
    const unhandled = [];
    const onUnhandled = (reason) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);
    t.after(() => {
      process.off("unhandledRejection", onUnhandled);
    });

    const closeError = new Error("close failed");
    const logged = [];
    const proc = createFakeProcess();

    runSidecarServer({
      startSidecar: () => ({
        close: () => Promise.reject(closeError),
        ready: Promise.resolve(),
        stopped: new Promise(() => {}),
      }),
      bindSidecarListenExit: () => {},
      proc,
      logError: (...args) => {
        logged.push(args);
      },
    });

    proc.emit("SIGTERM");
    const code = await proc.exited;
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(code, 1);
    assert.deepEqual(proc.exitCodes, [1]);
    assert.equal(logged.length, 1);
    assert.equal(logged[0][1], closeError);
    assert.deepEqual(unhandled, []);
  });

  it("logs a timeout error and exits 1 if handle.close() never settles on SIGTERM", async (t) => {
    const unhandled = [];
    const onUnhandled = (reason) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandled);
    t.after(() => {
      process.off("unhandledRejection", onUnhandled);
    });

    const logged = [];
    const proc = createFakeProcess();

    runSidecarServer({
      startSidecar: () => ({
        close: () => new Promise(() => {}),
        ready: Promise.resolve(),
        stopped: new Promise(() => {}),
      }),
      bindSidecarListenExit: () => {},
      proc,
      logError: (...args) => {
        logged.push(args);
      },
      shutdownTimeoutMs: 25,
    });

    proc.emit("SIGTERM");
    const code = await proc.exited;
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(code, 1);
    assert.deepEqual(proc.exitCodes, [1]);
    assert.equal(logged.length, 1);
    assert.ok(logged[0][1] instanceof Error);
    assert.match(logged[0][1].message, /Timed out.*25ms/);
    assert.deepEqual(unhandled, []);
  });
});
