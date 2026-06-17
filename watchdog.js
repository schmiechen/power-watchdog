const noble = require('@abandonware/noble');

const SERVICE_UUID = 'ffe0';
const TX_CHAR_UUID = 'ffe2';

// Device name prefix from LightBlue scan
const DEVICE_NAME_PREFIX = 'PMD';

let tempBytes = null;

function byte4ToDouble(arr, offset) {
  const b0 = arr[offset] & 0xFF;
  const b1 = arr[offset + 1] & 0xFF;
  const b2 = arr[offset + 2] & 0xFF;
  const b3 = arr[offset + 3] & 0xFF;
  return (b0 * 16777216) + (b1 * 65536) + (b2 * 256) + b3;
}

function parseCombinedPacket(bytes) {
  if (bytes.length !== 40) return null;

  const volt = byte4ToDouble(bytes, 3) / 10000.0;
  const amp = byte4ToDouble(bytes, 7) / 10000.0;
  const watt = byte4ToDouble(bytes, 11) / 10000.0;
  const energy = byte4ToDouble(bytes, 15) / 10000.0;
  const freq = byte4ToDouble(bytes, 31) / 100.0;

  const errorCode = bytes[19];
  const isLine2 = (bytes[37] === 1 && bytes[38] === 1 && bytes[39] === 1);
  const line = isLine2 ? 2 : 1;

  return { line, volt, amp, watt, energy, freq, errorCode };
}

function handleNotification(data) {
  const bytes = [...data];

  // First packet of a pair: starts with 01 03 20
  if (bytes.length >= 3 && bytes[0] === 0x01 && bytes[1] === 0x03 && bytes[2] === 0x20) {
    tempBytes = bytes;
    return;
  }

  // Second packet: combine with stored first packet
  if (tempBytes && tempBytes.length === 20 && bytes.length === 20) {
    const combined = [...tempBytes, ...bytes];
    tempBytes = null;

    const result = parseCombinedPacket(combined);
    if (result) {
      const timestamp = new Date().toLocaleTimeString();
      console.log(
        `[${timestamp}] Line ${result.line} | ` +
        `${result.volt.toFixed(1)}V | ` +
        `${result.amp.toFixed(1)}A | ` +
        `${result.watt.toFixed(1)}W | ` +
        `${result.freq.toFixed(1)}Hz | ` +
        `Energy: ${result.energy.toFixed(1)} kWh` +
        (result.errorCode ? ` | Error: ${result.errorCode}` : '')
      );
    }
  } else {
    tempBytes = null;
  }
}

console.log('Scanning for Power Watchdog...');

noble.on('stateChange', (state) => {
  if (state === 'poweredOn') {
    noble.startScanning([SERVICE_UUID], false);
  } else {
    noble.stopScanning();
  }
});

noble.on('discover', (peripheral) => {
  const name = peripheral.advertisement.localName || '';
  if (!name.startsWith(DEVICE_NAME_PREFIX)) return;

  console.log(`Found: ${name} (${peripheral.id})`);
  noble.stopScanning();

  peripheral.connect((err) => {
    if (err) {
      console.error('Connect error:', err);
      return;
    }
    console.log('Connected!');

    peripheral.discoverSomeServicesAndCharacteristics(
      [SERVICE_UUID],
      [TX_CHAR_UUID],
      (err, services, characteristics) => {
        if (err) {
          console.error('Discover error:', err);
          return;
        }

        const txChar = characteristics.find(c => c.uuid === TX_CHAR_UUID);
        if (!txChar) {
          console.error('TX characteristic not found');
          return;
        }

        console.log('Subscribed to TX notifications. Receiving data...\n');

        txChar.on('data', handleNotification);
        txChar.subscribe((err) => {
          if (err) console.error('Subscribe error:', err);
        });
      }
    );
  });

  peripheral.on('disconnect', () => {
    console.log('Disconnected.');
    process.exit(0);
  });
});
