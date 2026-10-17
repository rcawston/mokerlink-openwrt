#!/usr/bin/env python3
"""A small imitation of the switch's U-Boot console, for testing uboot-collect.py.

    fake_uboot.py PTY_MASTER_FD FLASH_FILE [--drop-in P] [--drop-out P]

Speaks over a pseudo-terminal: boot banner with a short Esc window, a prompt
with character echo, Ctrl-C, and the commands the collector uses (md, mw,
crc32, printenv, rtk network on, ...). Flash reads come from FLASH_FILE
mapped at 0xb4000000. To exercise the error handling it can lose received
characters (--drop-in) and bytes of md output (--drop-out), like a serial
line without flow control.
"""

import argparse
import os
import random
import select
import sys
import time
import zlib

PROMPT = b"RTL9300# "
FLASH_BASE = 0xB4000000
SWITCH_BASE = 0xBB000000


class Fake:
    def __init__(self, fd, flash, drop_in, drop_out, seed):
        self.fd = fd
        self.flash = flash
        self.drop_in = drop_in
        self.drop_out = drop_out
        self.rng = random.Random(seed)
        self.regs = {
            0xCA00: 0x0FFF5500, 0xCA04: 0x000495DD, 0xCA08: 0x55560001, 0xCA0C: 0x00C0A8AB,
            0xCB80: 0x0A418820, 0xCB84: 0x16A480E6, 0xCB88: 0x2207B9AC, 0xCB8C: 0x2F6A8272,
            0xCB90: 0x000D8C41, 0x0194: 0x0034D7DF, 0x0198: 0x000007DF, 0x02A0: 0x0034D34D,
            0x02A4: 0x0000068D, 0xCB10: 0x00000001,
        }

    def out(self, data, lossy=False):
        if lossy and self.drop_out:
            data = bytes(b for b in data if self.rng.random() >= self.drop_out)
        os.write(self.fd, data)

    def getc(self, timeout=None):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        try:
            c = os.read(self.fd, 1)
        except OSError:
            sys.exit(0)
        if not c:
            sys.exit(0)
        return c

    def boot(self):
        self.out(b"\r\n\r\nU-Boot 2011.12.(1.0.0.2) (Dec 27 2024 - 09:10:37)\r\n\r\n"
                 b"Board: RTL9300 CPU:800MHz LX:175MHz DDR:600MHz\r\nDRAM:  128 MB\r\n"
                 b"Hit Esc key to stop autoboot:  0")
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
            c = self.getc(0.05)
            if c == b"\x1b":
                return True
        self.out(b"\r\n## Booting kernel from Legacy Image at 81000000 ...\r\n")
        return False

    def run(self, line):
        for cmd in line.split(";"):
            self.cmd(cmd.split())

    def cmd(self, argv):
        if not argv:
            return
        name = argv[0]
        if name == "version":
            self.out(b"\r\nU-Boot 2011.12.(1.0.0.2) (Dec 27 2024 - 09:10:37)\r\n")
        elif name == "printenv":
            self.out(b"baudrate=115200\r\nbootcmd=boota\r\nipaddr=192.168.2.1\r\n")
        elif name in ("printsys", "flshow", "flinfo"):
            self.out(name.encode() + b" output\r\n")
        elif name == "rtk" and argv[1:] == ["network", "on"]:
            self.out(b"Enable network\r\nPlease wait for PHY init-time ...\r\n\r\n")
        elif name.startswith("md"):
            width = {"md.b": 1, "md.w": 2, "md.l": 4, "md": 4}[name]
            addr, count = int(argv[1], 16), int(argv[2], 16)
            self.md(addr, count, width)
        elif name == "mw.l":
            addr, val = int(argv[1], 16), int(argv[2], 16)
            self.mw(addr - SWITCH_BASE, val)
        elif name == "crc32":
            addr, size = int(argv[1], 16), int(argv[2], 16)
            crc = zlib.crc32(self.read(addr, size)) & 0xFFFFFFFF
            self.out(f"CRC32 for {addr:08x} ... {addr + size - 1:08x} ==> {crc:08x}\r\n".encode())
        else:
            self.out(f"Unknown command '{name}' - try 'help'\r\n".encode())

    def read(self, addr, size):
        if FLASH_BASE <= addr < FLASH_BASE + len(self.flash):
            off = addr - FLASH_BASE
            return self.flash[off:off + size]
        out = b""
        for a in range(addr, addr + size, 4):
            out += self.regs.get(a - SWITCH_BASE, 0).to_bytes(4, "big")
        return out[:size]

    def mw(self, off, val):
        self.regs[off] = val
        if off == 0xCB74 and val & 1:  # SMI read: answer like an RTL8261BE
            dev, reg = (self.regs.get(0xCB7C, 0) >> 16) & 0x1F, self.regs.get(0xCB7C, 0) & 0xFFFF
            data = {(1, 1): 0x0006, (1, 2): 0x001C, (1, 3): 0xCAF3,
                    (30, 0xC1): 0x0127}.get((dev, reg), 0xFFFF)
            self.regs[0xCB78] = (self.regs.get(0xCB78, 0) & 0xFFFF0000) | data
            self.regs[0xCB74] = val & ~1
        if off == 0x03B0 and val & 1:  # SerDes read
            self.regs[0x03B4] = 0x0101
            self.regs[0x03B0] = val & ~1

    def md(self, addr, count, width):
        data = self.read(addr, count * width)
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            words = " ".join(chunk[j:j + width].hex() for j in range(0, len(chunk), width))
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            self.out(f"{addr + i:08x}: {words}    {text}\r\n".encode("latin-1"), lossy=True)

    def loop(self, at_prompt=False):
        if not at_prompt:
            while not self.boot():
                time.sleep(0.5)
            self.out(b"\r\n" + PROMPT)
        line = b""
        while True:
            c = self.getc()
            if self.drop_in and self.rng.random() < self.drop_in:
                continue  # lost on the wire
            if c in (b"\r", b"\n"):
                self.out(b"\r\n")
                self.run(line.decode("latin-1"))
                self.out(PROMPT)
                line = b""
            elif c == b"\x03":
                self.out(b"<INTERRUPT>\r\n" + PROMPT)
                line = b""
            elif c == b"\x1b":
                pass  # ignored at the prompt
            else:
                line += c
                self.out(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fd", type=int)
    ap.add_argument("flash")
    ap.add_argument("--drop-in", type=float, default=0.0)
    ap.add_argument("--drop-out", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--at-prompt", action="store_true",
                    help="start at the prompt, as if autoboot was stopped earlier")
    args = ap.parse_args()
    with open(args.flash, "rb") as f:
        flash = f.read()
    Fake(args.fd, flash, args.drop_in, args.drop_out, args.seed).loop(args.at_prompt)


if __name__ == "__main__":
    main()
