#!/usr/bin/env python3
"""Regression test for scripts/uboot-wiring.py.

Runs the generated U-Boot commands against a model of the RTL9303 SMI
registers and RTL8264B PHYs, then checks that the decoder recovers the
wiring and lane polarity. Run: python3 tests/test_uboot_wiring.py
"""

import os
import re
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "uboot-wiring.py")

# Model of the RTL9303 SMI/SerDes registers and the PHYs behind them.
BASE = 0xBB000000


def build(layout, polarity, sfp_port_map, link_port, sds_pol=None):
    regs = {}
    def setf(off, lsb, width, val):
        regs[off] = (regs.get(off, 0) & ~(((1 << width) - 1) << lsb)) | (val << lsb)
    phys = {}
    for port, (bus, addr) in layout.items():
        setf(0xCA08 + (port // 16) * 4, (port % 16) * 2, 2, bus)
        setf(0xCB80 + (port // 6) * 4, (port % 6) * 5, 5, addr)
        regs[0xCA00] = regs.get(0xCA00, 0) | 1 << (16 + bus) | 1 << (20 + bus)
        phys[(bus, addr)] = {(1, 2): 0x001C, (1, 3): 0xC813, (30, 0xC1): 0x0005 | polarity.get(port, 0)}
    if sfp_port_map:
        bus, addr = sfp_port_map
        setf(0xCA08 + 4, (27 % 16) * 2, 2, bus); setf(0xCB80 + 16, (27 % 6) * 5, 5, addr)
    sds = {0: 0x1F, 1: 0x1F, 2: 0x0D, 3: 0x0D, 4: 0x0D, 5: 0x0D, 6: 0x0D, 7: 0x0D, 8: 0x0D, 9: 0x1A, 10: 0x1F, 11: 0x1F}
    fields = {0: (0x194, 0), 1: (0x194, 6), 2: (0x194, 12), 3: (0x194, 18), 4: (0x2A0, 0), 5: (0x2A0, 6),
              6: (0x2A0, 12), 7: (0x2A0, 18), 8: (0x2A4, 0), 9: (0x2A4, 6), 10: (0x198, 0), 11: (0x198, 6)}
    for n, m in sds.items():
        setf(fields[n][0], fields[n][1], 5, m)
    regs[0xCB10] = 1 << link_port
    # SerDes register file: (physical SerDes, page, reg) -> value. Unrelated
    # bits are set so the decoder must mask correctly.
    sds_regs = {}
    for n in range(2, 10):
        tx, rx = (sds_pol or {}).get(n, (False, False))
        sds_regs[(n, 6, 2)] = 0x0101 | (tx << 14) | (rx << 13)
        sds_regs[(10 if n == 3 else n, 0, 0)] = 0x1403 | (tx << 8) | (rx << 9)
    phys["sds"] = sds_regs
    return regs, phys


def run(  # execute U-Boot md.l/mw.l lines against the model, return the console log
        cmds, regs, phys):
    out = []
    for line in cmds.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        out.append("RTL9300# " + line)
        for c in line.split(";"):
            c = c.split()
            if c[0] == "mw.l":
                off = int(c[1], 16) - BASE; val = int(c[2], 16); regs[off] = val
                if off == 0x3B0 and val & 1:
                    key = ((val >> 2) & 0x1F, (val >> 7) & 0x3F, (val >> 13) & 0x1F)
                    regs[0x3B4] = phys["sds"].get(key, 0)
                    regs[0x3B0] = val & ~1
                if off == 0xCB74 and val & 1:
                    port = regs[0xCB78] >> 16
                    bus = (regs.get(0xCA08 + (port // 16) * 4, 0) >> ((port % 16) * 2)) & 3
                    addr = (regs.get(0xCB80 + (port // 6) * 4, 0) >> ((port % 6) * 5)) & 31
                    dev, reg = (regs[0xCB7C] >> 16) & 31, regs[0xCB7C] & 0xFFFF
                    v = phys.get((bus, addr), {}).get((dev, reg), 0xFFFF)
                    regs[0xCB78] = (regs[0xCB78] & 0xFFFF0000) | v
                    regs[0xCB74] = val & ~1
            elif c[0] == "md.l":
                start = int(c[1], 16); n = int(c[2], 16)
                for i in range(0, n, 4):
                    ws = [regs.get(start - BASE + 4 * (i + j), 0) for j in range(min(4, n - i))]
                    out.append(f"{start + 4 * i:08x}: " + " ".join(f"{w:08x}" for w in ws) + "    " + "." * (4 * len(ws)))
            elif c[0] == "rtk":
                out.append("Enable network")
    return "\n".join(out)


def decode(log):
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
        f.write(log)
    try:
        return subprocess.run([sys.executable, SCRIPT, "decode", f.name],
                              capture_output=True, text=True, check=True).stdout
    finally:
        os.unlink(f.name)


def main():
    cmds = subprocess.run([sys.executable, SCRIPT, "commands"],
                          capture_output=True, text=True, check=True).stdout

    # Hasivo-like: second quad on bus 3, SFP port left unmapped (aliases to bus 0 addr 0)
    layout = {0: (0, 0), 8: (0, 1), 16: (0, 2), 20: (0, 3), 24: (3, 16), 25: (3, 17), 26: (3, 18)}
    pol = {0: 0x80, 8: 0x40, 16: 0x80, 20: 0x40, 24: 0x80, 25: 0x40, 26: 0x80}
    out = decode(run(cmds, *build(layout, pol, None, 16)))
    for port, (bus, addr) in layout.items():
        assert f"PHY_C45({port}, {addr})" in out, out
    assert "&mdio_bus3 {" in out and "PHY_C45(27" not in out, out
    assert "&phy0 { tx-polarity = <PHY_POL_INVERT>; };" in out, out
    assert "&phy8 { rx-polarity = <PHY_POL_INVERT>; };" in out, out
    assert "MAC port 27 answered with a PHY ID but its lane is 10GBASE-R" in out, out
    assert "link on MAC port(s) [16]" in out, out

    # Both quads on bus 0, one port with both lanes inverted, CRLF line endings
    layout = {0: (0, 0), 8: (0, 1), 16: (0, 2), 20: (0, 3), 24: (0, 4), 25: (0, 5), 26: (0, 6)}
    log = run(cmds, *build(layout, {25: 0xC0}, (0, 7), 0)).replace("\n", "\r\n")
    out = decode(log)
    assert "&mdio_bus3" not in out and "PHY_C45(26, 6)" in out, out
    assert "&phy25 { tx-polarity = <PHY_POL_INVERT>; rx-polarity = <PHY_POL_INVERT>; };" in out, out
    assert "&phy0 {" not in out, out
    assert "&serdes" not in out, out

    # Discrete RTL8261BE PHYs on bus 1 and SoC-side rx inversion on every
    # copper lane plus tx inversion on the SFP lane (MokerLink 10GT080M style)
    layout = {0: (0, 0), 8: (0, 1), 16: (0, 2), 20: (0, 3), 24: (1, 4), 25: (1, 5), 26: (1, 6)}
    sds_pol = {n: (False, True) for n in range(2, 9)}
    sds_pol[9] = (True, False)
    out = decode(run(cmds, *build(layout, {}, None, 24, sds_pol)))
    assert "&mdio_bus1 {" in out and "PHY_C45(24, 4)" in out, out
    for n in range(2, 9):
        assert f"&serdes{n} {{ rx-polarity = <PHY_POL_INVERT>; }};" in out, out
    assert "&serdes9 { tx-polarity = <PHY_POL_INVERT>; };" in out, out
    assert not re.search(r"&phy\d+ \{", out), out

    print("uboot-wiring decoder: all checks passed")


if __name__ == "__main__":
    main()
