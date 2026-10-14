// Tests for nodered/src/controller.js (the session controller), run outside Node-RED with a fake clock.
// Run:  node tests/test_controller.js
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(path.join(__dirname, "..", "nodered", "src", "controller.js"), "utf8");
const FN = new Function("msg", "node", "context", "env", "Date", SRC);

// The harness gives the controller ONLY what a real Node-RED function node provides: node.send/status/log/warn/error/on,
// context.get/set and env.get. (An earlier version of this harness offered node.receive, which does not exist in real
// function nodes, and hid a bug.) It also plays the part of the 1 s delay node wired back into the controller.
function harness(envVars) {
    let now = 1000000;
    const sent = [];
    const ctx = {};
    let tickRequested = false;
    const context = { get: (k) => ctx[k], set: (k, v) => { ctx[k] = v; } };
    const env = { get: (k) => (envVars || {})[k] };
    const node = {
        id: "pwv_ctrl", name: "Session controller",
        send: (arr) => {
            assert.ok(Array.isArray(arr) && arr.length === 3, "function node has exactly 3 outputs");
            if (arr[2]) { tickRequested = true; }
            sent.push(arr);
        },
        status: (st) => { node.lastStatus = st; },
        log() {}, warn() {}, error() {}, on() {}
    };
    const run = (m) => FN(m, node, context, env, { now: () => now });
    const h = {
        sent, ctx, node,
        send: run,
        lease: (id, viewing) => run({ topic: "lease", payload: { id: id, viewing: viewing !== false } }),
        out: (obj) => run({ topic: "stdout", payload: JSON.stringify(obj) + "\n" }),
        exit: (code) => run({ topic: "exit", payload: { code: code === undefined ? 0 : code } }),
        retry: () => run({ topic: "retry" }),
        tickRequested: () => tickRequested,
        // advance the fake clock in 1 s steps; deliver a tick each second if (and only if) one was requested
        advance: (seconds, heartbeatIds) => {
            for (let i = 0; i < seconds; i++) {
                now += 1000;
                if (heartbeatIds && i % 5 === 4) { heartbeatIds.forEach((id) => h.lease(id, true)); }
                if (tickRequested) { tickRequested = false; run({ topic: "tick" }); }
            }
        },
        starts: () => sent.filter((s) => s[0] && s[0].payload && !s[0].kill).map((s) => s[0].payload),
        kills: () => sent.filter((s) => s[0] && s[0].kill).map((s) => s[0].kill),
        widgets: () => sent.filter((s) => s[1]).map((s) => s[1].payload),
        lastWidget: () => { const w = h.widgets(); return w[w.length - 1]; },
        state: () => ctx.S.state
    };
    return h;
}

const LIVE = { state: "live", timestamp: 1790000000, error_code: 0, total_energy_kwh: 8533.1,
    line1: { voltage: 122.4, amps: 0.7, watts: 52.4, frequency: 60.04, energy_kwh: 4680.6 },
    line2: { voltage: 123.3, amps: 2.12, watts: 173.3, frequency: 60.05, energy_kwh: 3853.1 } };

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("nothing starts while nobody is viewing, and no timer runs", () => {
    const h = harness();
    h.send({ topic: "tick" });
    assert.strictEqual(h.starts().length, 0);
    assert.strictEqual(h.tickRequested(), false, "an idle controller must not ask for ticks");
});

test("the first viewer starts the reader with the expected command", () => {
    const h = harness({ PW_DIR: "/coachproxy", PW_LAUNCHER: "sudo", PW_CONFIG: "/etc/power_watchdog.conf" });
    h.lease("a");
    assert.strictEqual(h.starts().length, 1);
    assert.strictEqual(h.starts()[0], "sudo python3 /coachproxy/power_watchdog_stream.py --exit-with-parent --stop-on-stdin-eof --config /etc/power_watchdog.conf");
    assert.strictEqual(h.state(), "starting");
    assert.strictEqual(h.lastWidget().state, "connecting");
});

test("default command has no launcher and the default install dir", () => {
    const h = harness();
    h.lease("a");
    assert.strictEqual(h.starts()[0], "python3 /opt/power-watchdog/power_watchdog_stream.py --exit-with-parent --stop-on-stdin-eof");
});

test("live readings are forwarded to the widget", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    const w = h.lastWidget();
    assert.strictEqual(w.state, "live");
    assert.strictEqual(w.stale, false);
    assert.strictEqual(w.line1.voltage, 122.4);
    assert.strictEqual(w.line2.watts, 173.3);
    assert.strictEqual(h.state(), "live");
});

test("a reading split across two output chunks is still understood", () => {
    const h = harness();
    h.lease("a");
    const text = JSON.stringify(LIVE) + "\n";
    h.send({ topic: "stdout", payload: text.slice(0, 40) });
    assert.strictEqual(h.state(), "starting");
    h.send({ topic: "stdout", payload: text.slice(40) });
    assert.strictEqual(h.state(), "live");
});

test("unrelated or broken output lines are ignored", () => {
    const h = harness();
    h.lease("a");
    h.send({ topic: "stdout", payload: "hello\n{not json}\n{\"nostate\":1}\n" });
    assert.strictEqual(h.state(), "starting");
});

test("the reader is stopped 15 s after the last viewer leaves, not before", () => {
    const h = harness();
    h.lease("a", true);
    h.out(LIVE);
    h.lease("a", false);
    assert.strictEqual(h.state(), "grace");
    assert.strictEqual(h.lastWidget().state, "live");   // still showing live data while waiting
    h.advance(14);
    assert.deepStrictEqual(h.kills(), []);
    h.advance(2);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
    h.exit(0);
    assert.strictEqual(h.state(), "idle");
    h.advance(3);
    assert.strictEqual(h.tickRequested(), false, "ticks must stop when idle with no viewers");
});

test("coming back during the grace period keeps the same connection", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.lease("a", false);
    h.advance(8);
    h.lease("b", true);
    h.advance(30, ["b"]);
    assert.deepStrictEqual(h.kills(), []);
    assert.strictEqual(h.starts().length, 1);
    assert.strictEqual(h.state(), "live");
});

test("a viewer that just vanishes (closed browser, lost WiFi) is released after lease + grace", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.advance(14);
    assert.deepStrictEqual(h.kills(), []);
    h.advance(20);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
});

test("heartbeats every 5 s keep the connection up", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.advance(120, ["a"]);
    assert.deepStrictEqual(h.kills(), []);
    assert.strictEqual(h.state(), "live");
});

test("two viewers: the reader stays until the last one leaves", () => {
    const h = harness();
    h.lease("a"); h.lease("b");
    h.out(LIVE);
    h.lease("a", false);
    h.advance(40, ["b"]);
    assert.deepStrictEqual(h.kills(), []);
    h.lease("b", false);
    h.advance(16);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
});

test("PW_GRACE is honoured", () => {
    const h = harness({ PW_GRACE: "5" });
    h.lease("a");
    h.out(LIVE);
    h.lease("a", false);
    h.advance(4);
    assert.deepStrictEqual(h.kills(), []);
    h.advance(2);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
});

test("unavailable is reported once, with a plain-language message, and is not retried automatically", () => {
    const h = harness();
    h.lease("a");
    h.send({ topic: "stdout", payload: JSON.stringify({ state: "unavailable", reason: "in_use_or_unreachable", detail: "x" }) + "\n" });
    h.exit(3);
    const w = h.lastWidget();
    assert.strictEqual(w.state, "unavailable");
    assert.ok(/Hughes app/.test(w.message));
    h.advance(300, ["a"]);   // viewer stays for five minutes
    assert.strictEqual(h.starts().length, 1, "must not retry on its own");
});

test("last good reading stays on screen, dimmed, when unavailable", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.send({ topic: "stdout", payload: JSON.stringify({ state: "unavailable", reason: "no_data" }) + "\n" });
    const w = h.lastWidget();
    assert.strictEqual(w.state, "unavailable");
    assert.strictEqual(w.stale, true);
    assert.strictEqual(w.line1.voltage, 122.4);
});

test("Retry starts a new attempt while someone is viewing", () => {
    const h = harness();
    h.lease("a");
    h.send({ topic: "stdout", payload: JSON.stringify({ state: "unavailable", reason: "in_use_or_unreachable" }) + "\n" });
    h.exit(3);
    h.retry();
    assert.strictEqual(h.starts().length, 2);
    assert.strictEqual(h.lastWidget().state, "connecting");
});

test("Retry with nobody viewing does nothing", () => {
    const h = harness();
    h.retry();
    assert.strictEqual(h.starts().length, 0);
});

test("a fresh page visit after an unavailable result tries again", () => {
    const h = harness();
    h.lease("a");
    h.send({ topic: "stdout", payload: JSON.stringify({ state: "unavailable", reason: "no_data" }) + "\n" });
    h.exit(3);
    h.lease("a", false);
    h.advance(2);
    assert.strictEqual(h.state(), "idle");
    h.lease("a", true);
    assert.strictEqual(h.starts().length, 2);
});

test("the reader ending on its own while live is reported, not hidden", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.exit(1);
    const w = h.lastWidget();
    assert.strictEqual(w.state, "unavailable");
    assert.strictEqual(w.reason, "stream_ended");
});

test("a session is paused after the cap, then resumes on Retry", () => {
    const h = harness({ PW_CAP: "60" });
    h.lease("a");
    h.out(LIVE);
    h.advance(65, ["a"]);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
    h.exit(0);
    assert.strictEqual(h.state(), "paused");
    const w = h.lastWidget();
    assert.strictEqual(w.state, "paused");
    assert.ok(/Resume/.test(w.message));
    h.lease("a", true);
    h.retry();
    assert.strictEqual(h.starts().length, 2);
});

test("if the reader ignores SIGTERM it is escalated to SIGKILL and the controller still recovers", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.lease("a", false);
    h.advance(16);
    assert.deepStrictEqual(h.kills(), ["SIGTERM"]);
    h.advance(6);
    assert.deepStrictEqual(h.kills(), ["SIGTERM", "SIGKILL"]);
    h.advance(4);   // no exit message ever arrives
    assert.strictEqual(h.state(), "idle");
    h.advance(3);
    assert.strictEqual(h.tickRequested(), false);
});

test("a viewer arriving while the reader is shutting down gets a fresh start afterwards", () => {
    const h = harness();
    h.lease("a");
    h.out(LIVE);
    h.lease("a", false);
    h.advance(16);                 // SIGTERM sent, waiting for exit
    h.lease("b", true);
    assert.strictEqual(h.starts().length, 1, "must not start a second reader while the first is still ending");
    h.exit(0);
    assert.strictEqual(h.starts().length, 2);
    assert.strictEqual(h.state(), "starting");
});

test("a lease without an id is ignored", () => {
    const h = harness();
    h.send({ topic: "lease", payload: { viewing: true } });
    assert.strictEqual(h.starts().length, 0);
});

test("a redeploy resets state (on-start snippet)", () => {
    const h = harness();
    h.lease("a");
    const startSnippet = fs.readFileSync(path.join(__dirname, "..", "nodered", "src", "controller_on_start.js"), "utf8");
    new Function("context", startSnippet)({ get: (k) => h.ctx[k], set: (k, v) => { h.ctx[k] = v; } });
    assert.strictEqual(h.ctx.S, null);
});

test("the controller only uses APIs a real function node has (regression: node.receive does not exist)", () => {
    assert.ok(!/node\.receive|setInterval|setTimeout/.test(SRC), "no timers or node.receive in the controller");
    const used = (SRC.match(/\bnode\.([a-zA-Z]+)/g) || []).map((x) => x.slice(5));
    used.forEach((name) => assert.ok(["send", "status", "log", "warn", "error", "on", "done"].indexOf(name) >= 0, "node." + name));
});

test("a tick is requested exactly once at a time while something is being watched", () => {
    const h = harness();
    h.lease("a");
    const ticks = () => h.sent.filter((x) => x[2]).length;
    assert.strictEqual(ticks(), 1);
    h.lease("a", true);
    h.out(LIVE);
    assert.strictEqual(ticks(), 1, "must not queue a second tick before the first arrives");
    h.advance(1);
    assert.strictEqual(ticks(), 2);
});

let failed = 0;
tests.forEach(([name, fn]) => {
    try { fn(); console.log("ok   - " + name); }
    catch (e) { failed++; console.log("FAIL - " + name + "\n       " + (e && e.message ? e.message.split("\n")[0] : e)); }
});
console.log("\n" + (tests.length - failed) + "/" + tests.length + " passed");
process.exit(failed ? 1 : 0);
