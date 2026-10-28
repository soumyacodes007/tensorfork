// TensorFork compatibility proxy.
//
// Sits between bittensor clients and the Chopsticks engine:
//   client (SDK / btcli)  ->  ws://127.0.0.1:9944 (this proxy)  ->  ws://127.0.0.1:<engine port>
//
// Why it exists:
// - The bittensor SDK sends hex params without a "0x" prefix (e.g. state_call
//   args). A real subtensor node accepts that; Chopsticks rejects it with
//   "hexadecimal string doesn't start with 0x". We normalize those params here.
// - It gives TensorFork one stable port (9944, what `--network local` resolves
//   to) no matter where the engine runs.
// - The SDK decodes reads at block N with the runtime of N's *parent*. A fork
//   starts at block N, so the engine has never seen the parent and answers
//   state_getRuntimeVersion(parent) with null. On a fork the runtime can only
//   change via blocks built on the fork (which the engine knows), so a null
//   answer for a pinned hash falls back to the head's runtime version.
//
// Usage: node proxy.mjs --listen 9944 --upstream ws://127.0.0.1:8000

import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(path.join(process.env.TENSORFORK_ENGINE_DIR ?? process.cwd(), "node_modules", "noop.js"));
const { WebSocketServer, WebSocket } = require("ws");

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => (cur.startsWith("--") ? [...acc, [cur.slice(2), arr[i + 1]]] : acc), []),
);
const LISTEN = Number(args.listen ?? 9944);
const HOST = args.host ?? "127.0.0.1"; // 0.0.0.0 only inside a container, published on host loopback
const UPSTREAM = args.upstream ?? "ws://127.0.0.1:8000";

// method -> indexes of params that are hex byte strings
const HEX_PARAMS = {
  state_call: [1],
  state_getStorage: [0],
  state_getStorageAt: [0],
  state_getStorageHash: [0],
  state_getStorageSize: [0],
  state_getKeys: [0],
  state_getKeysPaged: [0, 2],
  state_getChildStorage: [0, 1],
  author_submitExtrinsic: [0],
  author_submitAndWatchExtrinsic: [0],
  system_dryRun: [0],
  payment_queryInfo: [0],
  payment_queryFeeDetails: [0],
};
const HEX_ARRAY_PARAMS = { state_queryStorageAt: [0], state_subscribeStorage: [0] };

// RPC aliases a real node registers but Chopsticks only implements under the canonical name.
const METHOD_ALIASES = { account_nextIndex: "system_accountNextIndex" };

const isBareHex =(v) => typeof v === "string" && v.length > 0 && v.length % 2 === 0 && /^[0-9a-fA-F]+$/.test(v);
const fix = (v) => (isBareHex(v) ? `0x${v}` : v);

function normalize(msg) {
  if (Array.isArray(msg)) return msg.map(normalize);
  if (msg && METHOD_ALIASES[msg.method]) msg.method = METHOD_ALIASES[msg.method];
  if (!msg || !Array.isArray(msg.params)) return msg;
  for (const i of HEX_PARAMS[msg.method] ?? []) msg.params[i] = fix(msg.params[i]);
  for (const i of HEX_ARRAY_PARAMS[msg.method] ?? []) {
    if (Array.isArray(msg.params[i])) msg.params[i] = msg.params[i].map(fix);
  }
  return msg;
}

const server = new WebSocketServer({ host: HOST, port: LISTEN });
// Requests whose null result should be retried at the head (see header comment).
const HEAD_FALLBACK = new Set(["state_getRuntimeVersion", "chain_getRuntimeVersion"]);

server.on("connection", (client) => {
  const upstream = new WebSocket(UPSTREAM);
  const pending = [];
  const fallbacks = new Map(); // request id -> method, for pinned-hash runtime lookups
  const sendUp = (m) => (upstream.readyState === WebSocket.OPEN ? upstream.send(m) : pending.push(m));
  const track = (msg) => {
    for (const m of Array.isArray(msg) ? msg : [msg]) {
      if (m && HEAD_FALLBACK.has(m.method) && Array.isArray(m.params) && m.params[0]) fallbacks.set(m.id, m.method);
    }
  };
  upstream.on("open", () => pending.splice(0).forEach((m) => upstream.send(m)));
  upstream.on("message", (data) => {
    const text = data.toString();
    try {
      const msg = JSON.parse(text);
      if (!Array.isArray(msg) && fallbacks.has(msg.id)) {
        const method = fallbacks.get(msg.id);
        fallbacks.delete(msg.id);
        if (msg.result === null) {
          sendUp(JSON.stringify({ jsonrpc: "2.0", id: msg.id, method, params: [] }));
          return;
        }
      }
    } catch {
      // not JSON: forward untouched
    }
    if (client.readyState === WebSocket.OPEN) client.send(text);
  });
  upstream.on("close", () => client.close());
  upstream.on("error", () => client.close());
  client.on("message", (data) => {
    let out = data.toString();
    try {
      const msg = normalize(JSON.parse(out));
      track(msg);
      out = JSON.stringify(msg);
    } catch {
      // not JSON: forward untouched
    }
    sendUp(out);
  });
  client.on("close", () => upstream.close());
});
server.on("listening", () => console.log(`tensorfork proxy listening on ws://${HOST}:${LISTEN} -> ${UPSTREAM}`));
