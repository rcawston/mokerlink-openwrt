#!/usr/bin/env python3
"""Turn a U-Boot `md` dump captured from the serial console into a binary.

    uboot-md2bin.py LOGFILE OUTPUT [--base ADDR] [--size SIZE]

Dump the flash in U-Boot (it is mapped at 0xb4000000) with the terminal
logging to a file, for the whole 16 MiB chip:

    md.b b4000000 1000000      (or md.l, faster: fewer characters per byte)
    crc32 b4000000 1000000

The script reads every `md.b`, `md.w` or `md.l` line, writes the bytes to
OUTPUT, checks that nothing is missing and that repeated lines agree, and
compares the result with the CRC32 that U-Boot printed for the same range.
Words of md.w/md.l are stored big-endian, as on this MIPS CPU.
"""

import argparse
import re
import sys
import zlib

LINE = re.compile(r"^\s*([0-9a-fA-F]{8}):\s(.*)$")
CRC = re.compile(r"CRC32 for ([0-9a-fA-F]+) \.\.\. ([0-9a-fA-F]+) ==> ([0-9a-fA-F]{8})")


def parse(lines):
    """Return {address: bytes} per dump line, and the CRC32 lines seen."""
    chunks = {}
    conflicts = 0
    crcs = []
    for line in lines:
        c = CRC.search(line)
        if c:
            crcs.append(tuple(int(x, 16) for x in c.groups()))
            continue
        m = LINE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        # Hex words are separated by one space, the ASCII column by several.
        hexpart = re.split(r"\s{2,}", m.group(2).strip(), maxsplit=1)[0]
        words = hexpart.split()
        if not words or len({len(w) for w in words}) != 1 or len(words[0]) not in (2, 4, 8) \
                or not all(re.fullmatch(r"[0-9a-fA-F]+", w) for w in words):
            continue
        chunk = bytes.fromhex("".join(words))
        if addr in chunks and chunks[addr] != chunk:
            conflicts += 1
        chunks[addr] = chunk
    return chunks, conflicts, crcs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log")
    ap.add_argument("output")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=None,
                    help="start address (default: lowest address in the log)")
    ap.add_argument("--size", type=lambda x: int(x, 0), default=None,
                    help="number of bytes (default: up to the highest address)")
    args = ap.parse_args()

    with open(args.log, errors="replace") as f:
        chunks, conflicts, crcs = parse(f)
    if not chunks:
        print("no md output found in the log", file=sys.stderr)
        return 1

    base = min(chunks) if args.base is None else args.base
    if args.size is None:
        size = max(a + len(c) for a, c in chunks.items()) - base
    else:
        size = args.size
    out = bytearray(b"\xff" * size)
    seen = bytearray(size)
    for addr, chunk in chunks.items():
        lo, hi = max(addr, base) - base, min(addr + len(chunk), base + size) - base
        if lo >= hi:
            continue
        piece = chunk[lo + base - addr:hi + base - addr]
        if any(seen[lo:hi]) and any(seen[i] and out[i] != piece[i - lo] for i in range(lo, hi)):
            conflicts += 1
        out[lo:hi] = piece
        seen[lo:hi] = b"\x01" * (hi - lo)
    missing = seen.count(0)
    with open(args.output, "wb") as f:
        f.write(out)

    print(f"{args.output}: {size} bytes from {base:#010x}")
    ok = True
    if conflicts:
        print(f"ERROR: {conflicts} dump lines differ from an earlier dump of the same address")
        ok = False
    if missing:
        first = base + seen.index(0)
        print(f"ERROR: {missing} bytes missing, first at {first:#010x} "
              "(filled with 0xff). Dump that range again and add it to the log.")
        ok = False

    crc = zlib.crc32(out) & 0xFFFFFFFF
    print(f"CRC32 of the output: {crc:08x}")
    for start, end, ucrc in crcs:
        if start == base and end == base + size - 1:
            match = ucrc == crc
            print(f"U-Boot CRC32 for the same range: {ucrc:08x} -> {'MATCH' if match else 'MISMATCH'}")
            ok = ok and match
            break
    else:
        print(f"No U-Boot CRC32 for this range in the log. Run in U-Boot:\n"
              f"  crc32 {base:x} {size:x}\nand compare it with the value above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
