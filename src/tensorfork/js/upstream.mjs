// TensorFork upstream shield.
//
//   Chopsticks engine  ->  ws://127.0.0.1:<listen> (this shield)  ->  public archive RPC(s)
//
// Lazy forking pulls thousands of storage keys from a public archive node, and
// those nodes flap: sockets drop (1006), handshakes stall, and big
// state_queryStorageAt batches (Chopsticks asks for 1000 keys at once) can run
// past the engine's 60 s timeout. The shield makes that upstream boring:
// - a small pool of sockets per endpoint, reconnected with backoff
// - bounded in-flight requests
// - per-attempt timeouts with retries (and endpoint rotation)
// - state_queryStorageAt split into small chunks and merged back
//
// Usage: node upstream.mjs --listen 9955 --upstream wss://a,wss://b [--pool 3] [--inflight 12]

import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(path.join(process.env.TENSORFORK_ENGINE_DIR ?? process.cwd(), "node_modules", "noop.js"));
const { WebSocketServer, WebSocket } = require("ws");

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => (cur.startsWith("--") ? [...acc, [cur.slice(2), arr[i + 1]]] : acc), []),
);
const LISTEN = Number(args.listen ?? 9955);
const ENDPOINTS = (args.upstream ?? "wss://archive.chain.opentensor.ai:443").split(",");
const POOL = Number(args.pool ?? 3);
const MAX_INFLIGHT = Number(args.inflight ?? 12);
const ATTEMPT_TIMEOUT_MS = Number(args["attempt-timeout"] ?? 25_000);
const MAX_ATTEMPTS = Number(args.attempts ?? 8);
const CHUNK = Number(args.chunk ?? 100);

const log = (...m) => console.log(new Date().toISOString(), ...m);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stats = { ok: 0, retries: 0, failed: 0, reconnects: 0 };

class Upstream {
  constructor(url, slot) {
    this.url = url;
    this.slot = slot;
    this.nextId = 1;
    this.pending = new Map(); // id -> {resolve, reject}
    this.ready = false;
    this.backoff = 500;
    this.connect();
  }

  connect() {
    const ws = new WebSocket(this.url, { handshakeTimeout: 15_000, maxPayload: 2 ** 30 });
    this.ws = ws;
    ws.on("open", () => {
      this.ready = true;
      this.backoff = 500;
      log(`upstream ${this.url}#${this.slot} connected`);
    });
    ws.on("message", (data) => {
      let msg;
      try {
        msg = JSON.parse(data.toString());
      } catch {
        return;
      }
      const p = this.pending.get(msg.id);
      if (p) {
        this.pending.delete(msg.id);
        p.resolve(msg);
      }
    });
    const down = (why) => {
      if (this.ws !== ws) return;
      this.ready = false;
      for (const p of this.pending.values()) p.reject(new Error(`socket closed: ${why}`));
      this.pending.clear();
      stats.reconnects++;
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 15_000);
    };
    ws.on("close", (code) => down(`close ${code}`));
    ws.on("error", (err) => down(err.message));
  }

  send(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  }

  get load() {
    return this.pending.size;
  }
}

const pool = ENDPOINTS.flatMap((url) => Array.from({ length: POOL }, (_, i) => new Upstream(url, i)));

let inflight = 0;
const waiters = [];
async function acquire() {
  while (inflight >= MAX_INFLIGHT) await new Promise((r) => waiters.push(r));
  inflight++;
}
function release() {
  inflight--;
  waiters.shift()?.();
}

function pick(attempt) {
  const ready = pool.filter((u) => u.ready);
  if (ready.length === 0) return null;
  ready.sort((a, b) => a.load - b.load);
  return ready[attempt % ready.length];
}

const RETRYABLE_CODES = new Set([-32005, -32603, -32000]);

async function request(method, params) {
  let lastErr;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const up = pick(attempt);
    if (!up) {
      await sleep(Math.min(500 * 2 ** attempt, 10_000));
      continue;
    }
    await acquire();
    try {
      const msg = await Promise.race([
        up.send(method, params),
        sleep(ATTEMPT_TIMEOUT_MS).then(() => {
          throw new Error(`timeout after ${ATTEMPT_TIMEOUT_MS}ms`);
        }),
      ]);
      if (msg.error && RETRYABLE_CODES.has(msg.error.code)) throw new Error(`upstream ${msg.error.code}: ${msg.error.message}`);
      stats.ok++;
      return msg;
    } catch (err) {
      lastErr = err;
      stats.retries++;
      log(`retry ${attempt + 1}/${MAX_ATTEMPTS} ${method} ${JSON.stringify(params).slice(0, 240)}: ${err.message}`);
      await sleep(Math.min(300 * 2 ** attempt, 8_000));
    } finally {
      release();
    }
  }
  stats.failed++;
  return { jsonrpc: "2.0", error: { code: -32603, message: `tensorfork upstream: ${method} failed: ${lastErr?.message}` } };
}

async function queryStorageAtChunked(params) {
  const [keys, at] = params;
  if (!Array.isArray(keys) || keys.length <= CHUNK) return request("state_queryStorageAt", params);
  const chunks = [];
  for (let i = 0; i < keys.length; i += CHUNK) chunks.push(keys.slice(i, i + CHUNK));
  const parts = await Promise.all(chunks.map((c) => request("state_queryStorageAt", [c, at])));
  const failed = parts.find((p) => p.error);
  if (failed) return failed;
  const block = parts[0].result?.[0]?.block ?? at;
  return { jsonrpc: "2.0", result: [{ block, changes: parts.flatMap((p) => p.result?.[0]?.changes ?? []) }] };
}

async function handle(msg) {
  const res = msg.method === "state_queryStorageAt" ? await queryStorageAtChunked(msg.params) : await request(msg.method, msg.params);
  return { ...res, id: msg.id };
}

const server = new WebSocketServer({ host: "127.0.0.1", port: LISTEN, maxPayload: 2 ** 30 });
server.on("connection", (client) => {
  client.on("message", async (data) => {
    let msg;
    try {
      msg = JSON.parse(data.toString());
    } catch {
      return;
    }
    const reply = Array.isArray(msg) ? await Promise.all(msg.map(handle)) : await handle(msg);
    if (client.readyState === WebSocket.OPEN) client.send(JSON.stringify(reply));
  });
});
server.on("listening", () => log(`tensorfork upstream shield on ws://127.0.0.1:${LISTEN} -> ${ENDPOINTS.join(", ")}`));
setInterval(() => log("stats", JSON.stringify({ ...stats, inflight, ready: pool.filter((u) => u.ready).length })), 30_000);
