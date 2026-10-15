#!/usr/bin/env python3
"""Copy a legacy U-Boot uImage, replacing the header magic.

    uimage-remagic.py INPUT NEW_MAGIC OUTPUT

Realtek switch boot loaders often only accept a vendor-specific magic in
place of the standard 0x27051956. Only the magic and the header CRC change;
the payload and its CRC stay as they are.
"""

import struct
import sys
import zlib

HEADER_LEN = 64
STD_MAGIC = 0x27051956


def header_crc(header: bytes) -> int:
    # The header CRC is computed with the CRC field itself zeroed.
    return zlib.crc32(header[:4] + b"\0\0\0\0" + header[8:HEADER_LEN]) & 0xFFFFFFFF


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    src, magic_arg, dst = sys.argv[1:]
    new_magic = int(magic_arg, 0)

    with open(src, "rb") as f:
        data = bytearray(f.read())

    if len(data) < HEADER_LEN:
        print(f"{src}: too short for a uImage", file=sys.stderr)
        return 1

    old_magic, old_hcrc, _time, size = struct.unpack(">IIII", data[:16])
    if old_hcrc != header_crc(data):
        print(f"{src}: header CRC mismatch, not a valid uImage", file=sys.stderr)
        return 1
    if HEADER_LEN + size > len(data):
        print(f"{src}: payload truncated ({len(data) - HEADER_LEN} < {size} bytes)",
              file=sys.stderr)
        return 1
    data_crc = struct.unpack(">I", data[24:28])[0]
    if zlib.crc32(data[HEADER_LEN:HEADER_LEN + size]) & 0xFFFFFFFF != data_crc:
        print(f"{src}: payload CRC mismatch", file=sys.stderr)
        return 1

    struct.pack_into(">I", data, 0, new_magic)
    struct.pack_into(">I", data, 4, header_crc(data))

    with open(dst, "wb") as f:
        f.write(data)

    print(f"{dst}: magic 0x{old_magic:08x} -> 0x{new_magic:08x}"
          + (" (standard)" if new_magic == STD_MAGIC else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
