#!/usr/bin/env python3
"""Extract the RTL8239 PoE firmware image from the stock MokerLink firmware.

The stock firmware loads a Realtek firmware image into both RTL8239 PoE
chips at every boot. Realtek does not allow redistributing it, so it is not
part of this repository or of the OpenWrt image; this script copies it out
of a stock firmware file you already have.

Input, any of:
  - the stock upgrade image (e.g. POE-10G07110GSM_F11134_0220_en.bin)
  - a full flash dump (full-flash.bin from mokerlink-backup or
    uboot-collect.py), or the RUNTIME partition (mtd5) on its own

Output: rtl8239-bt.bin. Copy it to the switch as
/lib/firmware/realtek/rtl8239-bt.bin.

How: the stock image is a uImage with an LZMA kernel whose gzip initramfs
holds an xz squashfs of kernel modules. The PoE driver module (ski.ko)
carries the image as pse_image_RTL8239_Bt_Dumb_mode_V0_B_bin. The script
decompresses every xz block of that squashfs and finds the image by its
SHA-256, so only a byte-identical image is ever written.

Python 3 standard library only.
"""

import argparse
import gzip
import hashlib
import lzma
import struct
import sys
import zlib

UIMAGE_MAGIC = 0x83800000           # MokerLink stock uImage magic
RUNTIME_OFFSET = 0x300000           # RUNTIME partition in a full flash dump
UIMAGE_HEADER = 64

# pse_image_RTL8239_Bt_Dumb_mode_V0_B_bin in the stock firmware F11134_0220
KNOWN_IMAGES = {
    "58719f29b3d9c80b609189d7b26618b1463b24c5175db4e5ab891aeec6e1c2c1":
        ("RTL8239 802.3bt dumb mode V0_B", 25460, bytes.fromhex("000039820f00bc5f")),
}


def find_uimage(data):
    """Offset of the stock uImage: at 0 (image, mtd5) or 0x300000 (flash)."""
    for off in (0, RUNTIME_OFFSET):
        if len(data) >= off + UIMAGE_HEADER and \
           struct.unpack_from(">I", data, off)[0] == UIMAGE_MAGIC:
            return off
    raise SystemExit("no stock uImage (magic 0x83800000) at offset 0 or 0x300000")


def kernel_of(data, off):
    size = struct.unpack_from(">I", data, off + 12)[0]
    body = data[off + UIMAGE_HEADER:off + UIMAGE_HEADER + size]
    if len(body) != size:
        raise SystemExit("uImage is truncated")
    dec = lzma.LZMADecompressor(lzma.FORMAT_ALONE)
    return dec.decompress(body)


def initramfs_of(kernel):
    """The gzip-compressed cpio archive embedded in the kernel."""
    pos = kernel.find(b"\x1f\x8b\x08")
    while pos >= 0:
        try:
            out = zlib.decompressobj(31).decompress(kernel[pos:])
            if out[:6] in (b"070701", b"070702"):
                return out
        except zlib.error:
            pass
        pos = kernel.find(b"\x1f\x8b\x08", pos + 1)
    raise SystemExit("no initramfs found in the stock kernel")


def xz_blocks(blob):
    """Decompress every xz stream in blob (the squashfs data blocks)."""
    out = bytearray()
    pos = blob.find(b"\xfd7zXZ\x00")
    while pos >= 0:
        try:
            dec = lzma.LZMADecompressor(lzma.FORMAT_XZ)
            out += dec.decompress(blob[pos:pos + (1 << 20)])
        except lzma.LZMAError:
            pass
        pos = blob.find(b"\xfd7zXZ\x00", pos + 6)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stock", help="stock firmware image or flash dump")
    ap.add_argument("-o", "--output", default="rtl8239-bt.bin")
    args = ap.parse_args()

    with open(args.stock, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":            # a gzipped dump
        data = gzip.decompress(data)

    stream = xz_blocks(initramfs_of(kernel_of(data, find_uimage(data))))
    for digest, (name, size, prefix) in KNOWN_IMAGES.items():
        pos = stream.find(prefix)
        while pos >= 0:
            image = stream[pos:pos + size]
            if hashlib.sha256(image).hexdigest() == digest:
                with open(args.output, "wb") as f:
                    f.write(image)
                print(f"{args.output}: {name}, {size} bytes, sha256 {digest}")
                print("Copy it to the switch as /lib/firmware/realtek/rtl8239-bt.bin")
                return 0
            pos = stream.find(prefix, pos + 1)

    sys.exit("no known RTL8239 image in this firmware; please report its version")


if __name__ == "__main__":
    sys.exit(main())
