#!/usr/bin/env python3
"""Collect the board information and dump the flash through the U-Boot console.

    py uboot-collect.py --port COM3

Needs Python 3 and pyserial (`py -m pip install pyserial`). Run it from the
repository's scripts folder (it uses uboot-wiring.py next to it). The steps:

1. Finds the U-Boot prompt. Either get to it first (for example in PuTTY,
   then close PuTTY so the port is free), or pass --auto-esc and power the
   switch on while the program sends Esc to stop autoboot.
2. Runs the information and port wiring commands, including a guided link
   test where you move a cable from port to port. It saves the transcript
   (uboot-info.log) and the decoded wiring (uboot-wiring.txt).
3. Dumps the 16 MiB flash in chunks. Each chunk is checked against U-Boot's
   own crc32 of the same range and read again if anything was lost on the
   serial line. At the end the whole image is checked once more. A dump that
   was interrupted continues where it stopped with --resume DIR.

Every command is typed one short burst at a time, and the echo is checked
before Enter is pressed, because this U-Boot has no flow control and loses
characters from long pastes. The only commands sent are read commands
(md, crc32, printenv, ...) and, for the wiring readout, writes to the
switch's read-request registers, which change nothing permanently.
Nothing is ever saved or written to flash.

A full dump at 115200 baud takes about two hours.
"""

import argparse
import contextlib
import datetime
import importlib.util
import io
import json
import re
import sys
import time
import zlib
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing. Install it with:  py -m pip install pyserial")

FLASH_BASE = 0xB4000000
FLASH_SIZE = 0x1000000
ESC = b"\x1b"
CTRL_C = b"\x03"
PROMPT_RE = re.compile(rb"\n([A-Za-z0-9_.-]{1,32}# )")
MD_L_LINE = re.compile(r"^([0-9a-fA-F]{8}): ([0-9a-fA-F]{8}) ([0-9a-fA-F]{8}) "
                       r"([0-9a-fA-F]{8}) ([0-9a-fA-F]{8})(?:\s|$)")
CRC_LINE = re.compile(r"CRC32 for ([0-9a-fA-F]+) \.\.\. ([0-9a-fA-F]+) ==> ([0-9a-fA-F]{8})")
LINK_REG = "bb00cb10"

INFO_COMMANDS = ["version", "printenv", "printsys", "flshow", "flinfo", "md.b b4300000 40"]


class ConsoleError(Exception):
    pass


def load_wiring_module():
    path = Path(__file__).with_name("uboot-wiring.py")
    spec = importlib.util.spec_from_file_location("uboot_wiring", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UBoot:
    """A U-Boot prompt on a serial port."""

    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.02, write_timeout=5)
        if hasattr(self.ser, "set_buffer_size"):  # Windows: bigger driver buffer
            with contextlib.suppress(Exception):
                self.ser.set_buffer_size(rx_size=1 << 20, tx_size=1 << 12)
        self.prompt = None
        self.transcript = None  # text file to log commands and output to

    # -- low level -------------------------------------------------------

    def _read_some(self):
        return self.ser.read(self.ser.in_waiting or 1)

    def _drain(self, quiet=0.3, limit=3.0):
        """Read until the line has been quiet for `quiet` seconds."""
        buf = bytearray()
        end = time.monotonic() + limit
        last = time.monotonic()
        while time.monotonic() < end:
            chunk = self._read_some()
            if chunk:
                buf += chunk
                last = time.monotonic()
            elif time.monotonic() - last > quiet:
                break
        return bytes(buf)

    def _read_exact(self, n, timeout):
        buf = bytearray()
        end = time.monotonic() + timeout
        while len(buf) < n:
            buf += self.ser.read(n - len(buf))
            if time.monotonic() > end:
                raise ConsoleError(f"echo timeout, got {bytes(buf)!r}")
        return bytes(buf)

    def _read_until_prompt(self, timeout):
        tail = b"\n" + self.prompt
        buf = bytearray()
        end = time.monotonic() + timeout
        while True:
            chunk = self._read_some()
            if chunk:
                buf += chunk
                if buf.endswith(tail):
                    # A prompt only ever follows a line break; md's ASCII
                    # column never contains one, so this cannot be data.
                    return bytes(buf)
            elif time.monotonic() > end:
                raise ConsoleError(f"no prompt within {timeout}s")

    def _type(self, text):
        data = text.encode("ascii")
        for i in range(0, len(data), 8):
            part = data[i:i + 8]
            self.ser.write(part)
            echo = self._read_exact(len(part), timeout=3)
            if echo != part:
                raise ConsoleError(f"echo mismatch: sent {part!r}, got {echo!r}")

    def _recover(self):
        """Abandon a garbled line (Ctrl-C) and get back to a clean prompt."""
        self.ser.write(CTRL_C)
        time.sleep(0.2)
        self._drain(quiet=0.3)
        self.ser.write(b"\r")
        with contextlib.suppress(ConsoleError):
            self._read_until_prompt(5)
        self._drain(quiet=0.2)

    # -- public ----------------------------------------------------------

    def attach(self, auto_esc=False, wait=300):
        """Find the U-Boot prompt; with auto_esc, stop autoboot to get there."""
        buf = bytearray(self._drain(quiet=0.2))
        for wake in (b"\r", CTRL_C + b"\r"):  # Ctrl-C stops a command still printing
            self.ser.write(wake)
            seen = self._drain(quiet=0.5)
            buf += seen
            m = PROMPT_RE.search(seen)
            if m and seen.rstrip().endswith(m.group(1).rstrip()):
                self.prompt = m.group(1)
                break
        if self.prompt is None and not auto_esc:
            raise ConsoleError("no U-Boot prompt on this port. Get to the U-Boot prompt first "
                               "(e.g. in PuTTY), close PuTTY, and run this again; or run with "
                               "--auto-esc and power-cycle the switch.")
        if self.prompt is None:
            print("Power the switch on now (or unplug and replug its power).")
            print("Sending Esc to stop autoboot ...")
            end = time.monotonic() + wait
            warned = False
            while self.prompt is None:
                if time.monotonic() > end:
                    raise ConsoleError("no U-Boot prompt; check the cable, port and baud rate")
                self.ser.write(ESC)
                buf += self._read_some()
                # Only look after the last kernel start: a prompt before it
                # belongs to a boot that has already gone past U-Boot.
                booted = buf.rfind(b"Booting kernel")
                m = PROMPT_RE.search(buf, booted + 1 if booted >= 0 else 0)
                if m and booted < m.start():
                    self.prompt = m.group(1)
                if booted >= 0 and not warned and m is None:
                    print("Too late, the stock firmware is booting. Power-cycle the switch again.")
                    warned = True
                if len(buf) > 1 << 16:
                    del buf[:-4096]
                time.sleep(0.02)
            # Esc characters still queued in U-Boot's input end up on the
            # prompt line; clear it.
            time.sleep(0.5)
            self._recover()

        out = self.run("version", log=False)
        if "U-Boot" not in out:
            raise ConsoleError(f"prompt {self.prompt!r} is not U-Boot. Power-cycle the switch "
                               "and run this again.")
        print(f"At the U-Boot prompt ({self.prompt.decode().strip()}).")

    def run(self, cmd, timeout=20, tries=5, log=True):
        """Run one command; return its output with CRLF normalised."""
        last = None
        for _ in range(tries):
            try:
                self._type(cmd)
                self.ser.write(b"\r")
                raw = self._read_until_prompt(timeout)
                break
            except ConsoleError as e:
                last = e
                self._recover()
        else:
            raise ConsoleError(f"{cmd!r} failed: {last}")
        text = raw[:-len(self.prompt)].decode("latin-1")
        text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\n")
        if log and self.transcript:
            self.transcript.write(f"{self.prompt.decode()}{cmd}\n{text}")
            self.transcript.flush()
        return text


def link_ports(md_output):
    m = re.search(r"^%s: ([0-9a-fA-F]{8})" % LINK_REG, md_output, re.M)
    if not m:
        return None
    value = int(m.group(1), 16)
    return [p for p in (0, 8, 16, 20, 24, 25, 26, 27) if value >> p & 1]


def collect_info(ub, outdir, wiring, link_test=True):
    log_path = outdir / "uboot-info.log"
    with open(log_path, "w", encoding="latin-1", newline="\n") as log:
        ub.transcript = log
        print("\n== Board information")
        for cmd in INFO_COMMANDS:
            print(f"  {cmd}")
            ub.run(cmd, timeout=60)

        print("\n== Port wiring")
        if link_test:
            input("Plug a cable from front port 1 into a running PC, then press Enter ... ")
        print("  rtk network on (takes a few seconds)")
        ub.run("rtk network on", timeout=120)
        cmds = [c for c in wiring.commands().splitlines()
                if c and not c.startswith("#") and c != "rtk network on" and LINK_REG not in c]
        for i, cmd in enumerate(cmds, 1):
            print(f"\r  register reads {i}/{len(cmds)}", end="", flush=True)
            ub.run(cmd)
        print()

        if link_test:
            print("\n== Link test: which internal port each front port is")
            for n in range(1, 9):
                label = f"front-port-{n}" + ("-sfp" if n == 8 else "")
                if n == 1:
                    answer = input("Cable in front port 1 with the link LED on? Press Enter "
                                   "(s = skip) ... ")
                elif n == 8:
                    answer = input("Front port 8 is the SFP+ cage: insert a module or DAC cable "
                                   "connected to a peer, wait for link, press Enter (s = skip) ... ")
                else:
                    answer = input(f"Move the cable to front port {n}, wait about 5 s for the "
                                   "link LED, press Enter (s = skip) ... ")
                if answer.strip().lower().startswith("s"):
                    continue
                ub.run(f"echo {label}")
                ports = link_ports(ub.run(f"md.l {LINK_REG} 1"))
                print(f"  front port {n}: link on internal port(s) {ports if ports else 'none'}")
        ub.transcript = None

    decoded = io.StringIO()
    with contextlib.redirect_stdout(decoded):
        wiring.decode(log_path.read_text(encoding="latin-1"))
    (outdir / "uboot-wiring.txt").write_text(decoded.getvalue())
    print("\n" + decoded.getvalue())
    print(f"Saved {log_path} and {outdir / 'uboot-wiring.txt'}")


def read_chunk(ub, addr, size):
    """Read `size` bytes at `addr` with md.l and verify them with crc32."""
    out = ub.run(f"md.l {addr:x} {size // 4:x}", timeout=60, log=False)
    data = bytearray()
    expect = addr
    for line in out.splitlines():
        m = MD_L_LINE.match(line)
        if not m:
            continue
        if int(m.group(1), 16) != expect:
            return None, f"line for {int(m.group(1), 16):08x}, expected {expect:08x}"
        data += bytes.fromhex("".join(m.groups()[1:]))
        expect += 16
    if len(data) != size:
        return None, f"got {len(data)} of {size} bytes"
    crc_out = ub.run(f"crc32 {addr:x} {size:x}", timeout=60, log=False)
    m = CRC_LINE.search(crc_out)
    if not m or int(m.group(1), 16) != addr or int(m.group(2), 16) != addr + size - 1:
        return None, f"unreadable crc32 output {crc_out.strip()!r}"
    if int(m.group(3), 16) != zlib.crc32(data) & 0xFFFFFFFF:
        return None, "CRC mismatch"
    return bytes(data), None


def read_verified(ub, addr, size, errlog, stats, min_size=0x100):
    """Read a range until it verifies; halve it when it keeps failing.

    A smaller range has less output that can be damaged, so on a bad line
    the dump still makes progress instead of retrying the same size forever.
    """
    tries = 3 if size > min_size else 10
    for attempt in range(1, tries + 1):
        data, why = read_chunk(ub, addr, size)
        if data is not None:
            return data
        stats["retries"] += 1
        errlog.write(f"{time.strftime('%H:%M:%S')} {addr:08x}+{size:x} try {attempt}: {why}\n")
        errlog.flush()
        ub._recover()
    if size <= min_size:
        raise ConsoleError(f"{addr:08x}+{size:x} failed {tries} times; see dump-errors.log")
    half = size // 2
    return (read_verified(ub, addr, half, errlog, stats, min_size)
            + read_verified(ub, addr + half, half, errlog, stats, min_size))


def dump_flash(ub, outdir, base, size, chunk):
    image = outdir / "full-flash.bin"
    progress_path = outdir / "full-flash.progress.json"
    errlog = open(outdir / "dump-errors.log", "a")
    done = set()
    if progress_path.exists():
        state = json.loads(progress_path.read_text())
        if (state.get("base"), state.get("size"), state.get("chunk")) == (base, size, chunk):
            done = set(state["done"])
            print(f"Resuming: {len(done)} of {size // chunk} chunks already verified.")
    if not image.exists() or image.stat().st_size != size:
        with open(image, "wb") as f:
            f.write(b"\xff" * size)
        done.clear()

    def save_progress():
        progress_path.write_text(json.dumps({"base": base, "size": size, "chunk": chunk,
                                             "done": sorted(done)}))

    total = size // chunk
    stats = {"retries": 0}
    start = time.monotonic()
    fresh = 0
    print(f"\n== Flash dump: {size:#x} bytes from {base:#010x} in {total} chunks")
    try:
        with open(image, "r+b") as f:
            for i in range(total):
                off = i * chunk
                if off in done:
                    continue
                data = read_verified(ub, base + off, chunk, errlog, stats)
                f.seek(off)
                f.write(data)
                f.flush()
                done.add(off)
                fresh += 1
                if fresh % 16 == 0:
                    save_progress()
                left = total - len(done)
                rate = (time.monotonic() - start) / fresh
                eta = datetime.timedelta(seconds=int(rate * left))
                print(f"\r  {len(done)}/{total} chunks  {100 * len(done) / total:5.1f}%  "
                      f"ETA {eta}  retries {stats['retries']}   ", end="", flush=True)
    finally:
        save_progress()
        errlog.close()
    print()

    print("Checking the whole image against U-Boot's crc32 ...")
    crc_out = ub.run(f"crc32 {base:x} {size:x}", timeout=600, log=False)
    m = CRC_LINE.search(crc_out)
    whole = zlib.crc32(image.read_bytes()) & 0xFFFFFFFF
    if not m or int(m.group(3), 16) != whole:
        raise ConsoleError(f"final CRC check failed (U-Boot: {crc_out.strip()!r}, "
                           f"file: {whole:08x}). Run again with --resume to re-check.")
    print(f"  CRC32 {whole:08x} matches U-Boot.")
    import hashlib
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (outdir / "sha256sums").write_text(f"{digest}  full-flash.bin\n")
    progress_path.unlink()
    print(f"Saved {image} (sha256 {digest})")


def main():
    ap = argparse.ArgumentParser(
        description="Collect board information and dump the flash through U-Boot.",
        epilog="Example:  py uboot-collect.py --port COM3   (switch already at the U-Boot prompt)")
    ap.add_argument("--port", required=True, help="serial port, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--auto-esc", action="store_true",
                    help="stop autoboot by sending Esc while you power-cycle the switch "
                         "(default: the switch must already be at the U-Boot prompt)")
    ap.add_argument("--out", type=Path, help="output folder (default: a new dated folder)")
    ap.add_argument("--resume", type=Path, metavar="DIR",
                    help="continue an interrupted dump in DIR (skips the information step)")
    ap.add_argument("--skip-info", action="store_true", help="only dump the flash")
    ap.add_argument("--skip-dump", action="store_true", help="only collect the information")
    ap.add_argument("--skip-link-test", action="store_true",
                    help="no interactive cable moving (the link test is left out)")
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=0x1000,
                    help="dump chunk size (default 0x1000)")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=FLASH_BASE, help=argparse.SUPPRESS)
    ap.add_argument("--size", type=lambda x: int(x, 0), default=FLASH_SIZE, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.size % args.chunk or args.chunk % 16:
        sys.exit("--chunk must be a multiple of 16 that divides the flash size")

    outdir = args.resume or args.out or Path(f"mokerlink-{time.strftime('%Y%m%d-%H%M%S')}")
    outdir.mkdir(parents=True, exist_ok=True)
    wiring = load_wiring_module()

    ub = UBoot(args.port, args.baud)
    try:
        ub.attach(auto_esc=args.auto_esc)
        if not (args.skip_info or args.resume):
            collect_info(ub, outdir, wiring, link_test=not args.skip_link_test)
        if not args.skip_dump:
            dump_flash(ub, outdir, args.base, args.size, args.chunk)
    except KeyboardInterrupt:
        print(f"\nStopped. Continue later with:  py {Path(__file__).name} --port {args.port} "
              f"--resume {outdir}")
        return 1
    except ConsoleError as e:
        print(f"\nERROR: {e}")
        return 1
    print(f"\nDone. Everything is in {outdir.resolve()}")
    print("Power-cycle the switch to return to the stock firmware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
