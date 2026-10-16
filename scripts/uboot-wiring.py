#!/usr/bin/env python3
"""Read the RTL9303 port wiring from U-Boot, without booting anything.

    uboot-wiring.py commands         print the commands to paste into U-Boot
    uboot-wiring.py decode LOGFILE   decode a console log of those commands

After `rtk network on`, the vendor U-Boot has set up the switch from the
manufacturer's own board profile: which SMI bus and address each port's PHY
is on, the mode of each SerDes lane, and the lane polarity on both ends of
each PHY link (switch SerDes and RTL826x PHY). The switch registers are read
with `md`. PHY and SerDes registers are read through the switch's indirect
access registers with `mw` + `md`: those commands only request a read. For
the PHY reads the port mask register is set to 0, so even a mistyped command
bit cannot write to a PHY.

The decoder prints a port table and the device tree lines for
openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts.
"""

import re
import sys

BASE = 0xBB000000  # RTL930x switch registers (physical 0x1b000000) in KSEG1

SMI_GLB_CTRL = 0xCA00
SMI_MAC_TYPE_CTRL = 0xCA04
SMI_POLLING_SEL = 0xCA08  # 2 bits per port, 16 ports per register
SMI_ACCESS_CTRL_0 = 0xCB70  # port mask for writes (kept 0)
SMI_ACCESS_CTRL_1 = 0xCB74  # command and status
SMI_ACCESS_CTRL_2 = 0xCB78  # port in [31:16] / data in [15:0]
SMI_ACCESS_CTRL_3 = 0xCB7C  # C45 device [20:16] and register [15:0]
SMI_PORT_ADDR = 0xCB80  # 5 bits per port, 6 ports per register
MAC_LINK_STS = 0xCB10
SDS_MODE_REGS = (0x0194, 0x0198, 0x02A0, 0x02A4)

CMD_READ_C45 = 0x2
CMD_RUN = 0x1
CMD_FAIL = 1 << 25

# SerDes indirect access: command [17:13] reg, [12:7] page, [6:2] SerDes,
# bit 0 busy (bit 1 would be write, never set here); data in the next word
SDS_ACCESS_CMD = 0x03B0
SDS_ACCESS_DATA = 0x03B4
SDS_CMD_BUSY = 0x1
# Lane polarity bits, as programmed by pcs-rtl-otto.c (rtpcs_930x_sds_config_polarity)
SDS_POL_10G = (6, 0x02, 1 << 14, 1 << 13)  # page, reg, tx (INV_HSO), rx (INV_HSI)
SDS_POL_1G = (0, 0x00, 1 << 8, 1 << 9)

# RTL9303: the eight 10G MAC ports and their fixed SerDes lanes
MAC_TO_SDS = {0: 2, 8: 3, 16: 4, 20: 5, 24: 6, 25: 7, 26: 8, 27: 9}

# SerDes n -> (register, lsb) of its 5-bit mode field
SDS_FIELD = {
    0: (0x0194, 0), 1: (0x0194, 6), 2: (0x0194, 12), 3: (0x0194, 18),
    4: (0x02A0, 0), 5: (0x02A0, 6), 6: (0x02A0, 12), 7: (0x02A0, 18),
    8: (0x02A4, 0), 9: (0x02A4, 6), 10: (0x0198, 0), 11: (0x0198, 6),
}
SDS_MODES = {
    0x02: "SGMII", 0x04: "1000BASE-X", 0x05: "100BASE-X", 0x06: "QSGMII",
    0x0D: "USXGMII", 0x10: "XSGMII", 0x16: "2500BASE-X", 0x1A: "10GBASE-R",
    0x1F: "off",
}

# PHY registers read per port: (MMD, register, meaning)
PHY_READS = ((1, 0x0002, "id_hi"), (1, 0x0003, "id_lo"), (30, 0x00C1, "serdes_cfg"))
HSI_INV = 0x40  # rx-polarity
HSO_INV = 0x80  # tx-polarity

PHY_NAMES = {
    0x001CC813: "RTL8264B", 0x001CCAF2: "RTL8264",
    0x001CCAF3: "RTL8261N/BE",  # the RTL8261BE reports the RTL8261N ID
}


def sds_phys(sds, page):
    """SerDes 3 keeps its pages below 4 in physical SerDes 10 (pcs-rtl-otto.c)."""
    return 10 if sds == 3 and page < 4 else sds


def sds_cmd(sds, page, reg):
    return reg << 13 | page << 7 | sds_phys(sds, page) << 2 | SDS_CMD_BUSY


def a(off):
    return f"{BASE + off:08x}"


def commands():
    out = [
        "# Paste into the U-Boot prompt a few lines at a time.",
        "# Plug a cable into front port 1 with the other end in a running PC first.",
        "rtk network on",
        f"md.l {a(SMI_GLB_CTRL)} 4",
        f"md.l {a(SMI_PORT_ADDR)} 5",
        f"md.l {a(0x0194)} 2",
        f"md.l {a(0x02A0)} 2",
        f"md.l {a(MAC_LINK_STS)} 1",
    ]
    for port in MAC_TO_SDS:
        for mmd, reg, _ in PHY_READS:
            out.append(
                f"mw.l {a(SMI_ACCESS_CTRL_0)} 0; "
                f"mw.l {a(SMI_ACCESS_CTRL_2)} {port << 16:08x}; "
                f"mw.l {a(SMI_ACCESS_CTRL_3)} {mmd << 16 | reg:08x}; "
                f"mw.l {a(SMI_ACCESS_CTRL_1)} {CMD_READ_C45 | CMD_RUN:x}; "
                f"md.l {a(SMI_ACCESS_CTRL_1)} 3"
            )
    for sds in MAC_TO_SDS.values():
        for page, reg, _, _ in (SDS_POL_10G, SDS_POL_1G):
            out.append(f"mw.l {a(SDS_ACCESS_CMD)} {sds_cmd(sds, page, reg):08x}; "
                       f"md.l {a(SDS_ACCESS_CMD)} 2")
    out += [
        "# Then move the cable to each other front port in turn and run this",
        "# once per port, noting which front port it was:",
        f"md.l {a(MAC_LINK_STS)} 1",
    ]
    return "\n".join(out)


MD_LINE = re.compile(r"^\s*([0-9a-fA-F]{8}):((?:\s+[0-9a-fA-F]{8}){1,4})")
MW_CTRL2 = re.compile(r"mw\.l\s+(?:0x)?%s\s+(?:0x)?([0-9a-fA-F]+)" % a(SMI_ACCESS_CTRL_2), re.I)
MW_CTRL3 = re.compile(r"mw\.l\s+(?:0x)?%s\s+(?:0x)?([0-9a-fA-F]+)" % a(SMI_ACCESS_CTRL_3), re.I)
MW_SDS = re.compile(r"mw\.l\s+(?:0x)?%s\s+(?:0x)?([0-9a-fA-F]+)" % a(SDS_ACCESS_CMD), re.I)


def parse(log):
    regs = {}
    link_samples = []
    phy = {}  # (port, mmd, reg) -> value or None (failed)
    sds = {}  # (physical SerDes, page, reg) -> value or None (failed)
    pending = None
    for line in log.splitlines():
        c2, c3 = MW_CTRL2.search(line), MW_CTRL3.search(line)
        if c2 and c3:
            port = int(c2.group(1), 16) >> 16
            c45 = int(c3.group(1), 16)
            pending = (port, (c45 >> 16) & 0x1F, c45 & 0xFFFF)
        m = MD_LINE.match(line)
        if not m:
            continue
        addr = int(m.group(1), 16)
        words = [int(w, 16) for w in m.group(2).split()]
        if addr == BASE + SDS_ACCESS_CMD and len(words) >= 2:
            cmd = words[0]
            key = ((cmd >> 2) & 0x1F, (cmd >> 7) & 0x3F, (cmd >> 13) & 0x1F)
            sds[key] = None if cmd & SDS_CMD_BUSY else words[1] & 0xFFFF
            continue
        if addr == BASE + SMI_ACCESS_CTRL_1 and len(words) >= 2:
            status, data = words[0], words[1]
            # The switch keeps the requested register in CTRL_3, so trust the
            # readback over the echoed command, which may not have run (a
            # garbled first word gives "Unknown command"). Same for the port
            # in the upper half of CTRL_2 when it is non-zero.
            port = pending[0] if pending else data >> 16
            if data >> 16 and data >> 16 != port:
                port = data >> 16
            if len(words) >= 3:
                mmd, reg = (words[2] >> 16) & 0x1F, words[2] & 0xFFFF
            elif pending:
                mmd, reg = pending[1:]
            else:
                continue
            ok = not status & (CMD_RUN | CMD_FAIL)
            phy[(port, mmd, reg)] = (data & 0xFFFF) if ok else None
            pending = None
            continue
        for i, w in enumerate(words):
            off = addr + 4 * i - BASE
            if off == MAC_LINK_STS:
                link_samples.append(w)
            else:
                regs[off] = w
    return regs, link_samples, phy, sds


def bits(value, lsb, width):
    return (value >> lsb) & ((1 << width) - 1)


def decode(log):
    regs, link_samples, phy, sds_regs = parse(log)
    missing = [f"{off:#06x}" for off in
               (SMI_GLB_CTRL, SMI_POLLING_SEL, SMI_POLLING_SEL + 4,
                *range(SMI_PORT_ADDR, SMI_PORT_ADDR + 20, 4), 0x0194, 0x02A0, 0x02A4)
               if off not in regs]
    if missing:
        print("Missing register dumps for offsets: " + ", ".join(missing), file=sys.stderr)

    glb = regs.get(SMI_GLB_CTRL, 0)
    print(f"SMI_GLB_CTRL {glb:#010x}: C45 buses "
          f"{[b for b in range(4) if glb >> (16 + b) & 1]}, "
          f"standard-register polling on buses {[b for b in range(4) if glb >> (20 + b) & 1]}")
    print()
    print(f"{'MAC':>3} {'SDS':>3} {'SerDes mode':<11} {'bus':>3} {'addr':>4}  {'PHY':<18} polarity")
    rows = []
    for port, sds in MAC_TO_SDS.items():
        sel = regs.get(SMI_POLLING_SEL + (port // 16) * 4)
        adr = regs.get(SMI_PORT_ADDR + (port // 6) * 4)
        bus = bits(sel, (port % 16) * 2, 2) if sel is not None else None
        addr = bits(adr, (port % 6) * 5, 5) if adr is not None else None
        reg, lsb = SDS_FIELD[sds]
        mode = SDS_MODES.get(bits(regs[reg], lsb, 5), f"{bits(regs[reg], lsb, 5):#x}") if reg in regs else "?"

        hi, lo = phy.get((port, 1, 2)), phy.get((port, 1, 3))
        if (port, 1, 2) not in phy or (port, 1, 3) not in phy:
            phy_id, phy_txt = None, "not read"
        elif hi is None or lo is None:
            phy_id, phy_txt = None, "read failed"
        elif (hi, lo) in ((0xFFFF, 0xFFFF), (0, 0)):
            phy_id, phy_txt = None, "none"
        else:
            phy_id = hi << 16 | lo
            phy_txt = f"{PHY_NAMES.get(phy_id, 'unknown')} {phy_id:08x}"
        cfg = phy.get((port, 30, 0xC1))
        tx = rx = None
        if phy_id and cfg is not None:
            tx, rx = bool(cfg & HSO_INV), bool(cfg & HSI_INV)
            pol = f"tx={'invert' if tx else 'normal'} rx={'invert' if rx else 'normal'} ({cfg:#06x})"
        else:
            pol = "-"
        rows.append((port, sds, mode, bus, addr, phy_id, tx, rx))
        print(f"{port:>3} {sds:>3} {mode:<11} {bus if bus is not None else '?':>3} "
              f"{addr if addr is not None else '?':>4}  {phy_txt:<18} {pol}")

    print()
    print("Switch-side SerDes lane polarity:")
    print(f"{'SDS':>3} {'10G path':<22} {'1G path':<22}")
    sds_pol = {}
    for port, sds in MAC_TO_SDS.items():
        cells = []
        for page, reg, tx_bit, rx_bit in (SDS_POL_10G, SDS_POL_1G):
            key = (sds_phys(sds, page), page, reg)
            val = sds_regs.get(key)
            if key not in sds_regs:
                cells.append(("not read", None))
            elif val is None:
                cells.append(("read failed", None))
            else:
                pol = (bool(val & tx_bit), bool(val & rx_bit))
                cells.append((f"tx={'invert' if pol[0] else 'normal'} rx={'invert' if pol[1] else 'normal'}", pol))
        print(f"{sds:>3} {cells[0][0]:<22} {cells[1][0]:<22}")
        mode = next(r[2] for r in rows if r[1] == sds)
        primary, other = (cells[1], cells[0]) if mode in ("SGMII", "1000BASE-X") else cells
        if primary[1] and other[1] and primary[1] != other[1]:
            print(f"    note: SerDes {sds} has different 10G and 1G polarity; using the {mode} path")
        if primary[1]:
            sds_pol[sds] = primary[1]

    if link_samples:
        print()
        print("Link status samples (MAC_LINK_STS), in the order they were taken:")
        for n, s in enumerate(link_samples, 1):
            up = [p for p in MAC_TO_SDS if s >> p & 1]
            print(f"  sample {n}: {s:#010x}  link on MAC port(s) {up or 'none'}")

    # A port U-Boot left unmapped reads as bus 0 address 0 and returns the
    # PHY of whichever port really sits there, so a PHY only counts for a
    # port whose lane is in a PHY mode and whose address is not taken.
    phys, seen = [], {}
    for r in rows:
        port, _, mode, bus, addr, phy_id, *_ = r
        if not phy_id:
            continue
        if mode in ("10GBASE-R", "1000BASE-X", "off"):
            print(f"note: MAC port {port} answered with a PHY ID but its lane is {mode}; "
                  f"treated as no PHY (it reads bus {bus} address {addr})")
            continue
        if (bus, addr) in seen:
            print(f"WARNING: MAC ports {seen[(bus, addr)]} and {port} both map to bus {bus} "
                  f"address {addr}; the dump is ambiguous for port {port}")
            continue
        seen[(bus, addr)] = port
        phys.append(r)

    if not phys:
        print("\nNo PHY answered: was `rtk network on` run before the reads?")
        return 1

    print("\nDevice tree lines (PHYs grouped by SMI bus):\n")
    for bus in sorted({r[3] for r in phys}):
        print(f"&mdio_bus{bus} {{")
        for port, _, _, b, addr, *_ in phys:
            if b == bus:
                print(f"\tPHY_C45({port}, {addr})")
        print("};\n")
    for port, _, _, _, _, _, tx, rx in phys:
        props = ([("tx-polarity = <PHY_POL_INVERT>;")] if tx else []) + \
                ([("rx-polarity = <PHY_POL_INVERT>;")] if rx else [])
        if props:
            print(f"&phy{port} {{ {' '.join(props)} }};")
    for sds, (tx, rx) in sds_pol.items():
        props = (["tx-polarity = <PHY_POL_INVERT>;"] if tx else []) + \
                (["rx-polarity = <PHY_POL_INVERT>;"] if rx else [])
        if props:
            print(f"&serdes{sds} {{ {' '.join(props)} }};")
    print()
    for port, sds, mode, *_ in rows:
        if not any(r[0] == port for r in phys):
            print(f"/* MAC port {port} (SerDes {sds}, {mode}): no PHY; the SFP+ cage if mode is 10GBASE-R */")
    return 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "commands":
        print(commands())
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "decode":
        with open(sys.argv[2], errors="replace") as f:
            return decode(f.read())
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
