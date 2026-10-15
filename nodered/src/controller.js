// Power Watchdog session controller. Body of a Node-RED function node (2 outputs).
//
// Decides when to run the streaming reader: it runs ONLY while at least one browser is showing the widget
// ("viewing"), and is stopped shortly after the last viewer leaves, so the Watchdog (which accepts a single
// Bluetooth connection) is free for other apps the rest of the time.
//
// Inputs, by msg.topic:
//   "lease"  payload {id, viewing}  heartbeat from a widget instance (viewing:false = it went away)
//   "retry"                          user pressed Retry / Resume
//   "stdout" payload string          raw output of the reader (JSON lines, possibly split across chunks)
//   "exit"   payload {code,signal}   the reader process ended
//   "tick"                           one-second heartbeat of this node (see output 3)
// Output 1 -> the exec node (spawn mode, command "env"): {payload:"<args>"} to start, {kill:"SIGTERM"} to stop.
// Output 2 -> the dashboard widget: last reading merged with {state, reason, detail, stale, message}.
// Output 4 -> a "file" node (overwrite): the keep-alive file the reader watches (see below).
// Output 5 -> a "file" node (delete): removes the keep-alive file.
// Output 3 -> a 1 s delay node wired back to this node's input: asks for the next tick, only while there is
//             something to watch (a viewer, or a session running), so an idle flow does no work at all.
//
// Settings come from the flow's environment variables (Node-RED tab > Environment):
//   PW_DIR (install dir), PW_CONFIG (config file), PW_LAUNCHER ("sudo" or empty), PW_ARGS (extra reader flags),
//   PW_GRACE (s, default 15), PW_LEASE (s, default 15), PW_CAP (s, default 3600),
//   PW_KEEPALIVE (keep-alive file, default /dev/shm/power-watchdog.keepalive), PW_KA_TIMEOUT (s, default 15).
//
// Why a keep-alive file: signals cannot be relied on. When the reader runs as root through sudo, Node-RED (a normal
// user) is not allowed to signal it, so a plain "kill" silently does nothing and the Watchdog stays connected. The
// controller therefore also refreshes a small file while the reader should run and deletes it to stop the reader;
// the reader exits by itself when the file disappears or stops changing.
//
// Signals are only a last resort, and deliberately NOT the first thing sent: in Node-RED 2.x the exec node forgets a
// process whose kill failed (an EPERM from a root-owned reader), and then never reports that process's exit. So a stop
// first removes the file and waits for the exit report; only if nothing happens for a few seconds are signals tried.

var NOW = Date.now();
var num = function (v, d) { var n = parseFloat(v); return isFinite(n) && n > 0 ? n : d; };
var CFG = {
    grace: num(env.get("PW_GRACE"), 15) * 1000,
    lease: num(env.get("PW_LEASE"), 15) * 1000,
    cap: num(env.get("PW_CAP"), 3600) * 1000,
    termAfter: 4000,
    killEvery: 5000,
    stuckAfter: 30000,
    keepalive: env.get("PW_KEEPALIVE") || "/dev/shm/power-watchdog.keepalive",
    kaTimeout: num(env.get("PW_KA_TIMEOUT"), 15)
};
var MESSAGES = {
    in_use_or_unreachable: "Watchdog not reachable. It may be in use by the Hughes app, switched off, or out of range.",
    adapter_not_ready: "The Bluetooth adapter on this device is not ready.",
    no_data: "Connected, but the Watchdog sent no readings.",
    not_configured: "Not set up: the device address is missing from the config file.",
    stream_ended: "The connection ended unexpectedly.",
    paused: "Paused to free the Watchdog. Tap Resume to continue.",
    stop_failed: "Could not confirm the Watchdog was released. Tap Retry; if it stays connected, restart Node-RED."
};

var S = context.get("S");
if (!S) {
    S = { state: "idle", leases: {}, last: null, buf: "", gotLive: false, startedAt: 0, graceUntil: 0,
          stopSentAt: 0, killed: false, afterStop: "idle", restart: false, reason: null, detail: null,
          tickPending: false, ka: 0 };
}

function viewers() {
    var n = 0;
    Object.keys(S.leases).forEach(function (id) {
        if (S.leases[id] > NOW) { n++; } else { delete S.leases[id]; }
    });
    return n;
}

// What the widget should show for the internal state.
function shown() {
    switch (S.state) {
        case "live": return "live";
        case "grace": return S.gotLive ? "live" : "connecting";
        case "stopping":
            if (S.reason === "stop_failed") { return "unavailable"; }
            return S.afterStop === "paused" ? "paused" : "connecting";
        case "starting":
        case "idle": return "connecting";
        default: return S.state;   // "unavailable", "paused"
    }
}

function widget() {
    var p = {};
    if (S.last) { Object.keys(S.last).forEach(function (k) { p[k] = S.last[k]; }); }
    p.state = shown();
    p.stale = p.state !== "live";
    if (S.reason) { p.reason = S.reason; p.detail = S.detail; p.message = MESSAGES[S.reason] || S.reason; }
    node.send([null, { topic: "watchdog", payload: p }, null, null, null]);
}

function status() {
    var map = { idle: ["grey", "ring", "idle (nobody viewing)"], starting: ["yellow", "ring", "connecting"],
        live: ["green", "dot", "live"], grace: ["blue", "ring", "releasing soon"], stopping: ["yellow", "ring", "stopping"],
        unavailable: ["red", "ring", "unavailable"], paused: ["grey", "dot", "paused"] };
    var m = map[S.state];
    node.status({ fill: m[0], shape: m[1], text: m[2] + " | viewers: " + viewers() });
}

function scheduleTick() {
    var want = S.state !== "idle" || Object.keys(S.leases).length > 0;
    if (want && !S.tickPending) {
        S.tickPending = true;
        node.send([null, null, { topic: "tick" }, null, null]);
    }
}

function command() {
    var dir = env.get("PW_DIR") || "/opt/power-watchdog";
    var parts = [];
    if (env.get("PW_LAUNCHER")) { parts.push(env.get("PW_LAUNCHER")); }
    parts.push("python3", dir + "/power_watchdog_stream.py", "--exit-with-parent", "--stop-on-stdin-eof");
    parts.push("--keepalive-file", CFG.keepalive, "--keepalive-timeout", String(CFG.kaTimeout));
    if (env.get("PW_CONFIG")) { parts.push("--config", env.get("PW_CONFIG")); }
    if (env.get("PW_ARGS")) { parts.push(env.get("PW_ARGS")); }
    return parts.join(" ");
}

// The keep-alive file's content must keep changing; the reader stops when it stops changing or disappears.
function refreshKeepalive() {
    S.ka += 1; S.kaGone = false;
    node.send([null, null, null, { filename: CFG.keepalive, payload: String(NOW) + ":" + S.ka }, null]);
}

// Remove the file once per session (a second delete would only make Node-RED log "file not found").
function removeKeepalive() {
    if (S.kaGone) { return; }
    S.kaGone = true;
    node.send([null, null, null, null, { filename: CFG.keepalive, payload: "" }]);
}

function start() {
    S.state = "starting"; S.gotLive = false; S.startedAt = NOW; S.buf = ""; S.reason = null; S.detail = null;
    S.killed = false; S.restart = false; S.afterStop = "idle"; S.lastKill = 0;
    refreshKeepalive();
    node.send([{ payload: command() }, null, null, null, null]);
}

function stop(next) {
    S.state = "stopping"; S.afterStop = next; S.stopSentAt = NOW; S.lastKill = 0; S.termSent = false; S.killed = false;
    removeKeepalive();   // works whoever owns the reader; no signal yet (see the note at the top)
}

function finishStop() {
    S.state = S.afterStop;
    if (S.state === "paused") { S.reason = "paused"; S.detail = null; }
    if (S.state === "idle") { S.reason = null; }
    if (S.restart && viewers() > 0) { start(); }
}

function handleLine(text) {
    var m;
    try { m = JSON.parse(text); } catch (e) { return; }
    if (!m || !m.state) { return; }
    if (S.state === "stopping") { return; }
    if (m.state === "live") {
        S.gotLive = true; S.reason = null; S.detail = null;
        S.last = { timestamp: m.timestamp, line1: m.line1, line2: m.line2, total_energy_kwh: m.total_energy_kwh,
                   error_code: m.error_code };
        if (S.state !== "grace") { S.state = "live"; }
    } else if (m.state === "unavailable") {
        S.state = "unavailable"; S.reason = m.reason || "in_use_or_unreachable"; S.detail = m.detail || null;
    }
    widget();
}

function evaluate() {
    var v = viewers();
    switch (S.state) {
        case "idle":
            if (v > 0) { start(); widget(); }
            break;
        case "starting":
        case "live":
            if (v === 0) { S.state = "grace"; S.graceUntil = NOW + CFG.grace; widget(); }
            else if (NOW - S.startedAt > CFG.cap) { stop("paused"); widget(); }
            break;
        case "grace":
            if (v > 0) { S.state = S.gotLive ? "live" : "starting"; widget(); }
            else if (NOW >= S.graceUntil) { stop("idle"); }
            break;
        case "stopping":
            if (v > 0 && S.afterStop === "idle") { S.restart = true; }
            if (!S.termSent && NOW - S.stopSentAt >= CFG.termAfter) {        // the file did not do it: try signals
                S.termSent = true; S.lastKill = NOW;
                node.send([{ kill: "SIGTERM", payload: "" }, null, null, null, null]);
            } else if (S.termSent && NOW - S.lastKill >= CFG.killEvery) {      // and keep trying; never assume it is gone
                S.lastKill = NOW; S.killed = true;
                node.send([{ kill: "SIGKILL", payload: "" }, null, null, null, null]);
            }
            if (NOW - S.stopSentAt > CFG.stuckAfter && S.reason !== "stop_failed") {
                S.reason = "stop_failed"; S.detail = null; widget();
            }
            break;
        case "unavailable":
        case "paused":
            if (v === 0) { S.state = "idle"; S.reason = null; S.detail = null; }
            break;
    }
}

var t = msg.topic;
if (t === "lease") {
    var before = viewers();
    var p = msg.payload || {};
    if (p.id) {
        if (p.viewing) { S.leases[p.id] = NOW + CFG.lease; } else { delete S.leases[p.id]; }
    }
    if (before === 0 && viewers() > 0 && (S.state === "unavailable" || S.state === "paused")) {
        start(); widget();   // a fresh page visit is an explicit request to try again
    }
    evaluate();
} else if (t === "retry") {
    if (S.state === "stopping") {
        S.lastKill = NOW; S.termSent = true;
        node.send([{ kill: S.killed ? "SIGKILL" : "SIGTERM", payload: "" }, null, null, null, null]);
    } else if (viewers() > 0 && (S.state === "unavailable" || S.state === "paused" || S.state === "idle")) {
        start(); widget();
    }
} else if (t === "stdout") {
    S.buf += (typeof msg.payload === "string" ? msg.payload : String(msg.payload));
    var lines = S.buf.split("\n");
    S.buf = lines.pop();
    lines.forEach(function (l) { l = l.trim(); if (l) { handleLine(l); } });
} else if (t === "exit") {
    if (S.state === "stopping") {
        S.reason = null; finishStop(); widget();
    } else if (S.state === "starting" || S.state === "live" || S.state === "grace") {
        S.state = "unavailable"; S.reason = S.reason || "stream_ended"; S.detail = S.detail || null;
        removeKeepalive(); widget();
    } else if (S.state === "unavailable") {
        removeKeepalive();   // the reader reported "unavailable" itself and has now exited; leave no stale file behind
    }
} else if (t === "tick") {
    S.tickPending = false;
    evaluate();
    if (S.state === "starting" || S.state === "live" || S.state === "grace") {
        if (!S.kaAt || NOW - S.kaAt >= 2000) { S.kaAt = NOW; refreshKeepalive(); }
    }
}
scheduleTick();
status();
context.set("S", S);
return null;
