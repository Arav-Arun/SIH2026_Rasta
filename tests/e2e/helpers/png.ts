import { crc32, deflateSync } from 'node:zlib';

/** A small real PNG with content unique to this run, so its checksum is new. */
export function uniquePng(seed: string): Buffer {
  const width = 24;
  const height = 16;
  const raw = Buffer.alloc((width * 3 + 1) * height);
  const bytes = Buffer.from(seed);
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 3 + 1)] = 0;
    for (let x = 0; x < width * 3; x += 1) {
      raw[y * (width * 3 + 1) + 1 + x] =
        bytes[(x + y) % bytes.length] ^ (x * 7 + y * 13);
    }
  }
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
