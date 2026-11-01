#!/usr/bin/env node
/**
 * Douyin signature helper -- Node is used for signing only, not as a data plane.
 *
 * The third-party, obfuscated webmssdk bundle in
 * ``tools/dyhub-main/third_party/douyin-sign/sign.js`` is the single source of
 * truth for the WebSocket ``signature`` (X-Bogus).  Re-implementing it in Python
 * would be brittle, so the native Python collector shells out to this file for
 * every new WebSocket handshake.  All HTTP, WebSocket, protobuf and reconnect
 * work stays in Python.
 *
 * Usage:
 *   node scripts/douyin_sign.mjs <md5-hex>        # print X-Bogus signature
 *   node scripts/douyin_sign.mjs --a-bogus <str>  # print a_bogus (optional)
 *
 * The path to the signature bundle can be overridden with DOUYIN_SIGN_JS.
 * The bundle is AGPL-3.0 third-party code; do not edit or vendor it into OLV.
 */

import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));

// Keep the UA identical to the collector's HTTP/WebSocket headers: the signed
// query string and the request headers must describe the same browser.
export const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0";

// Primary location: alongside this checkout (C:\code\ailive\tools\...).
const WORKSPACE_TOOLS = "C:\\code\\ailive\\tools\\dyhub-main\\third_party\\douyin-sign";

function firstExisting(candidates) {
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) return candidate;
  }
  return candidates[candidates.length - 1];
}

const DEFAULT_SIGN_JS = firstExisting([
  resolve(HERE, "../../../tools/dyhub-main/third_party/douyin-sign/sign.js"),
  resolve(WORKSPACE_TOOLS, "sign.js"),
]);
const DEFAULT_ABOGUS_JS = firstExisting([
  resolve(HERE, "../../../tools/dyhub-main/third_party/douyin-sign/a_bogus.js"),
  resolve(WORKSPACE_TOOLS, "a_bogus.js"),
]);

let signApi = null;
let abogusApi = null;

function signBundlePath() {
  return process.env.DOUYIN_SIGN_JS || DEFAULT_SIGN_JS;
}

function abogusBundlePath() {
  return process.env.DOUYIN_ABOGUS_JS || DEFAULT_ABOGUS_JS;
}

function loadSignApi() {
  if (signApi) return signApi;
  const bundle = signBundlePath();
  const source = readFileSync(bundle, "utf8");
  const context = vm.createContext({
    console,
    navigator: { userAgent: UA },
  });
  vm.runInContext(source, context, { filename: bundle, timeout: 20000 });
  if (typeof context.get_sign !== "function") {
    throw new Error("sign.js did not expose get_sign()");
  }
  signApi = context;
  return signApi;
}

function loadAbogusApi() {
  if (abogusApi) return abogusApi;
  const bundle = abogusBundlePath();
  const source = readFileSync(bundle, "utf8");
  const context = vm.createContext({ console });
  vm.runInContext(source, context, { filename: bundle, timeout: 20000 });
  if (typeof context.get_ab !== "function") {
    throw new Error("a_bogus.js did not expose get_ab()");
  }
  abogusApi = context;
  return abogusApi;
}

/** Return the WebSocket X-Bogus signature for a lowercase MD5 hex digest. */
export function getSign(md5) {
  const api = loadSignApi();
  const signature = api.get_sign(String(md5));
  if (typeof signature !== "string" || signature.length === 0) {
    throw new Error("sign.js returned an empty signature");
  }
  return signature;
}

/** Return the HTTP a_bogus signature for a query/parameter string. */
export function getABogus(paramStr, userAgent = UA) {
  const api = loadAbogusApi();
  const signature = api.get_ab(String(paramStr), String(userAgent));
  if (typeof signature !== "string" || signature.length === 0) {
    throw new Error("a_bogus.js returned an empty signature");
  }
  return signature;
}

// Snake-case aliases match the names exposed by the third-party bundles.
export const get_sign = getSign;
export const get_a_bogus = getABogus;

function usage() {
  return [
    "usage: node douyin_sign.mjs <md5-hex>",
    "       node douyin_sign.mjs --a-bogus <param-str>",
  ].join("\n");
}

async function main(argv) {
  if (argv.length === 0 || argv[0] === "-h" || argv[0] === "--help") {
    process.stderr.write(usage() + "\n");
    process.exitCode = 1;
    return;
  }
  if (argv[0] === "--a-bogus" || argv[0] === "--ab") {
    const paramStr = argv[1];
    if (!paramStr) throw new Error("--a-bogus requires a parameter string");
    process.stdout.write(getABogus(paramStr) + "\n");
    return;
  }
  const md5 = argv[0].trim();
  if (!/^[0-9a-fA-F]{32}$/.test(md5)) {
    throw new Error("expected a 32-character MD5 hex digest");
  }
  process.stdout.write(getSign(md5.toLowerCase()) + "\n");
}

function invokedDirectly() {
  if (!process.argv[1]) return false;
  try {
    // The checkout may be reached through a junction (e.g. C:\code -> ...),
    // so compare real paths instead of the URL spelling.
    const invoked = realpathSync(resolve(process.argv[1]));
    const self = realpathSync(fileURLToPath(import.meta.url));
    return process.platform === "win32"
      ? invoked.toLowerCase() === self.toLowerCase()
      : invoked === self;
  } catch {
    return false;
  }
}

if (invokedDirectly()) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`douyin_sign: ${error.message}\n`);
    process.exitCode = 1;
  });
}
