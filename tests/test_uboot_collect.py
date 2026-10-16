#!/usr/bin/env python3
"""End-to-end test of scripts/uboot-collect.py against tests/fake_uboot.py.

Runs over a pseudo-terminal (Linux/macOS), so it needs pyserial but no
hardware. Checks a clean line, a lossy line (characters lost in both
directions, so retries must happen) and resuming an interrupted dump.

    python3 tests/test_uboot_collect.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COLLECT = HERE.parent / "scripts" / "uboot-collect.py"
FAKE = HERE / "fake_uboot.py"


def run(tmp, flash_path, extra, drop_in=0.0, drop_out=0.0, seed=1, at_prompt=True):
    master, slave = os.openpty()
    port = os.ttyname(slave)
    fake = subprocess.Popen([sys.executable, str(FAKE), str(master), str(flash_path),
                             "--drop-in", str(drop_in), "--drop-out", str(drop_out),
                             "--seed", str(seed)] + (["--at-prompt"] if at_prompt else []),
                            pass_fds=[master])
    try:
        res = subprocess.run([sys.executable, str(COLLECT), "--port", port, *extra],
                             cwd=tmp, capture_output=True, text=True, timeout=600)
    finally:
        fake.kill()
        fake.wait()
        os.close(master)
        os.close(slave)
    return res


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        flash = os.urandom(0x40000)
        flash_path = tmp / "flash.bin"
        flash_path.write_bytes(flash)

        # 0. Switch still booting and no --auto-esc: refuse with a hint
        res = run(tmp, flash_path, ["--out", str(tmp / "none"), "--skip-info"], at_prompt=False)
        assert res.returncode == 1 and "--auto-esc" in res.stdout, res.stdout + res.stderr
        print("no prompt without --auto-esc: ok")

        # 1. Clean line, autoboot stopped by the program: information,
        #    wiring decode and a small dump
        out = tmp / "clean"
        res = run(tmp, flash_path, ["--out", str(out), "--skip-link-test", "--auto-esc",
                                    "--size", "0x8000", "--chunk", "0x1000"], at_prompt=False)
        assert res.returncode == 0, res.stdout + res.stderr
        assert (out / "full-flash.bin").read_bytes() == flash[:0x8000]
        info = (out / "uboot-info.log").read_text(encoding="latin-1")
        assert "bootcmd=boota" in info and "RTL9300# rtk network on" in info, info
        wiring = (out / "uboot-wiring.txt").read_text()
        assert "RTL8261N/BE 001ccaf3" in wiring and "&mdio_bus1 {" in wiring, wiring
        assert "matches U-Boot" in res.stdout, res.stdout
        print("clean line: ok")

        # 2. Lossy line: input characters and md output bytes get lost
        out = tmp / "lossy"
        res = run(tmp, flash_path, ["--out", str(out), "--skip-info",
                                    "--size", "0x10000", "--chunk", "0x1000"],
                  drop_in=0.003, drop_out=0.00005, seed=7)
        assert res.returncode == 0, res.stdout + res.stderr
        assert (out / "full-flash.bin").read_bytes() == flash[:0x10000]
        errors = (out / "dump-errors.log").read_text()
        assert errors.strip(), "expected retries on a lossy line"
        print(f"lossy line: ok ({len(errors.splitlines())} chunk retries)")

        # 3. Resume: forget half the chunks, damage them, continue
        image = out / "full-flash.bin"
        data = bytearray(image.read_bytes())
        lost = list(range(0x8000, 0x10000, 0x1000))
        for off in lost:
            data[off:off + 0x1000] = b"\0" * 0x1000
        image.write_bytes(bytes(data))
        (out / "full-flash.progress.json").write_text(json.dumps(
            {"base": 0xB4000000, "size": 0x10000, "chunk": 0x1000,
             "done": [o for o in range(0, 0x10000, 0x1000) if o not in lost]}))
        res = run(tmp, flash_path, ["--resume", str(out), "--size", "0x10000", "--chunk", "0x1000"])
        assert res.returncode == 0, res.stdout + res.stderr
        assert "Resuming: 8 of 16" in res.stdout, res.stdout
        assert image.read_bytes() == flash[:0x10000]
        print("resume: ok")

    print("uboot-collect: all checks passed")


if __name__ == "__main__":
    main()
