# Hughes Power Watchdog (Gen I) Bluetooth LE protocol

Unofficial notes, reverse-engineered from the vendor's Android app (`com.hughes.epo`, v3.2.1), a GATT dump of a real unit,
and packet captures of the iOS app talking to it. Not affiliated with Hughes Autoformers. Everything below marked
**(observed)** was measured on a real Gen I 50 A unit; everything marked **(from app code)** comes from decompiled app code
and was not independently confirmed on the air.

## Golden rules

1. **One connection at a time (observed).** While a phone, tablet or script is connected, the Watchdog stops advertising and
   nobody else can see it. Hold the connection only while you need it, and always disconnect cleanly.
2. **Read-only is enough.** All the measurements arrive as notifications. You never need to write anything except the standard
   "enable notifications" descriptor write (and the unit streams data even before that, see below).
3. **Never send the device commands you don't understand** (table at the end). Some of them delete records, reset the unit, or
   switch its relay.

## Discovery

| Property | Value |
|---|---|
| Advertised name | `PMD` followed by padding and a device code, e.g. `PMD      0A2403E309` |
| Advertised service | `0xFFE0` |
| Manufacturer data | company id `0xF8F0` + the last 4 bytes of the device address (the address itself is also in the clear) |
| Address type | public; address vendor prefix resolves to Texas Instruments |
| Advert rate while idle | about 2 per second |

The app only auto-connects to devices registered in it (it scans the QR code printed on the unit). That registration is an
app feature, not a protocol requirement: any central can connect by address.

## GATT layout (observed)

| Service | Characteristic | Properties | Use |
|---|---|---|---|
| `0x180A` Device Information | model, firmware, date, system id, manufacturer, PnP id | read | model string `TC,R2#6,1,20,S`, firmware `V1.0` |
| `0xFFE0` | `0xFFE2` | read, notify | **data stream (notifications)** |
| `0xFFE0` | `0xFFF5` | read, write, write-without-response | command channel used by the app |
| `0xFFE0` | `0x1003` (write), `0x1004` (read), `0x1005` (read/write) | | unexplored; do not write |
| `f000ffd0-0451-4000-b000-000000000000` | `f000ffd1` | write, write-without-response | unexplored (a TI vendor-style service, possibly firmware update); **do not write** |

Handles on the unit we tested: `FFE2` value handle `0x0020`, its notification descriptor (CCCD) `0x0021`, `FFF5` value `0x001D`.
Handles can differ between firmware versions; discover them (`gatttool --characteristics`) rather than assuming.
Note: some older notes name the command characteristic `FFE1`; on the unit we tested there is no `FFE1`, the command
characteristic is `FFF5`.

## Connection behavior (observed)

- The unit streams notifications about 0.3 s after the connection is made, **before** any CCCD write. The official iOS app
  still writes `0100` to the CCCD afterwards.
- Connection parameters negotiated by the iOS app: interval 30 ms, supervision timeout 2 s. BlueZ defaults also work.
- After a clean disconnect the unit starts advertising again within about 1 to 3 seconds.
- A connected unit does not advertise, so a second phone/app sees nothing until the first disconnects.
- We found no evidence that the unit goes to sleep while idle: one unit advertised continuously for hours after power-up with
  nobody connected.
- If a client dies without disconnecting, the unit frees itself only when the link times out; always disconnect cleanly.

## Data format

Readings arrive as pairs of 20-byte notifications on `0xFFE2`, about one pair per second **per line**. A 50 A unit reports two
lines (Line 1 and Line 2); a 30 A unit is expected to report one. The first packet of a pair starts with `01 03 20`; the
second packet continues it. Concatenate them (40 bytes):

| Bytes | Field | Conversion |
|---|---|---|
| 0-2 | header | `01 03 20` |
| 3-6 | Voltage | `byte4(3) / 10000` V |
| 7-10 | Current | `byte4(7) / 10000` A |
| 11-14 | Power | `byte4(11) / 10000` W |
| 15-18 | Energy | `byte4(15) / 10000` kWh (lifetime counter for that line) |
| 19 | Error code | `0` = OK, see below |
| 31-34 | Frequency | `byte4(31) / 100` Hz |
| 37-39 | Line id | `00 00 00` = Line 1, `01 01 01` = Line 2 |

`byte4` is a big-endian unsigned 32-bit integer. Other bytes are not decoded.

### Error code byte (from app code, matches the printed manual)

| Code | Meaning (shown on the unit as) |
|---|---|
| 0 | OK |
| 1 / 2 | Line 1 / Line 2 voltage error (E1 / E2): outside 104-132 V |
| 3 / 4 | Line 1 / Line 2 over-current (E3 / E4) |
| 5 / 6 | Line 1 / Line 2 neutral reversed (E5 / E6) |
| 7 | Missing ground (E7) |
| 8 | Neutral missing (E8) |
| 9 | Surge protection used up (E9) |
| 11 / 12 | Line 1 / Line 2 frequency error (F1 / F2) |

## Commands the official app sends (from app code; DO NOT SEND casually)

The app writes ASCII text to `0xFFF5`. The only one observed on the air in a normal session is `POWER ON TIME` (sent twice when
the live-data screen opens). It is a query for how long the unit has been powered.

| Text | Effect |
|---|---|
| `POWER ON TIME` | query: how long the unit has been on |
| `SET:T<date>` | sets the unit's clock |
| `SET:BL<0-4>` | sets the display backlight level |
| `DEL:REC<n>` / `DEL:RECA` | **deletes** one / all stored error records |
| `RELAY ON` | **switches the output relay on** (power to the coach) |
| `RESEt` | **resets** the unit |

The software in this repository never writes any of these.
