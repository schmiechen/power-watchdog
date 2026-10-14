"""Extract the Watchdog's real notification packets from an Apple PacketLogger (.pklg) capture.

Reads a capture of the Hughes app talking to a Power Watchdog, keeps ONLY the 20-byte ATT notifications from the
Watchdog's connection (handle 0x0020 = FFE2 value), and writes them as JSON test fixtures. Nothing else from the
capture (other devices, names, addresses) is kept.

Usage: python3 -I tests/tools/extract_fixtures.py capture.pklg tests/fixtures/notifications.json [PEER-MAC]
"""
import json
import struct
import sys

path, out = sys.argv[1], sys.argv[2]
peer = (sys.argv[3] if len(sys.argv) > 3 else "F0:F8:F2:FA:24:03").upper()
peer_le = bytes(int(x, 16) for x in peer.split(":"))[::-1]
data = open(path, "rb").read()

recs, off = [], 0
while off + 13 <= len(data):
    (n,) = struct.unpack_from("<I", data, off)
    if n < 9 or off + 4 + n > len(data):
        break
    sec, us = struct.unpack_from("<II", data, off + 4)
    recs.append((sec + us / 1e6, data[off + 12], data[off + 13: off + 4 + n]))
    off += 4 + n

handles = set()
for ts, typ, p in recs:  # find the connection handle(s) to the peer
    if typ == 0x01 and len(p) > 13 and p[0] == 0x3E and p[2] in (0x01, 0x0A) and p[3] == 0:
        if p[8:14] == peer_le:
            handles.add((p[4] | p[5] << 8) & 0x0FFF)

pk, t0 = [], None
for ts, typ, p in recs:
    if typ != 0x03 or len(p) < 9 or ((p[0] | p[1] << 8) & 0x0FFF) not in handles:
        continue
    l2len, cid = struct.unpack("<HH", p[4:8])
    att = p[8:8 + l2len]
    if cid == 4 and len(att) == 23 and att[0] == 0x1B and (att[1] | att[2] << 8) == 0x0020:
        t0 = t0 or ts
        pk.append({"t": round(ts - t0, 3), "hex": att[3:].hex()})

json.dump({"description": "20-byte FFE2 notifications from a real Power Watchdog (Gen I 50A) session; offsets in seconds",
           "packets": pk}, open(out, "w"), indent=1)
print(f"connection handles {sorted(handles)}; wrote {len(pk)} packets spanning {pk[-1]['t'] if pk else 0:.1f}s -> {out}")
