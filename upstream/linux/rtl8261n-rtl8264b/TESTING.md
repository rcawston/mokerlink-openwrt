# Testing the RTL8261N and RTL8264B patches on the MokerLink

Test only; nothing here is submitted. The patches change how phylib
reads and sets up the board's PHYs, so the test runs on the RAM-boot test
image of the RTL930x switch series (`upstream/linux/rtl930x-switch/test/`
on the `rtl930x-switch` branch), which is the only net-next kernel with a
driver for the switch's ports. Nothing is written to flash.

## The board

| Ports | PHY | MDIO bus, address |
|---|---|---|
| lan1, lan2, lan3 | RTL8261BE, one each | 1:00, 2:00, 3:00 |
| lan4-lan7 | one RTL8264B | 0:00-0:03 |

The vendor U-Boot sets the PHYs up when its network starts (`rtk network
on`), which the RAM boot does anyway. The patches rely on that.

## Two images

- **Before**: the test image that the `rtl930x-switch` workflow built on
  21 October from de750a3 (artifact `rtl930x-switch-test-image-de750a3…`).
  It has no PHY driver entries for these PHYs at all, neither these
  patches nor `test/realtek-phy-rtl8261n-rtl8264b.patch`, so the generic
  Clause 45 driver binds.
- **After**: the switch series' test image with this series' two diffs
  (`01-rtl8261n.diff`, then `02-rtl8264b.diff`) applied by
  `scripts/build-rtl930x-test-image.sh` in place of
  `test/realtek-phy-rtl8261n-rtl8264b.patch`. That test-only patch must
  not be applied as well: it claims the same PHY IDs.

## Host

A NIC at `192.168.N.2/24` for front port N, and `iperf3 -s`. The 2.5G NIC
covers 10, 100, 1000 and 2500 Mb/s, and the 10G NIC 10 Gb/s; 5 Gb/s isn't
tested. On Windows, set the speed in the adapter's Advanced properties
("Speed & Duplex"), and note for each setting whether the NIC
autonegotiates with only that speed advertised or forces the speed (most
drivers autonegotiate at 1 Gb/s and above, and force 10 and 100 Mb/s).

On the switch, for port N:

```sh
ip addr add 192.168.N.1/24 dev lanN
ip link set lanN up
```

**Link at the PHY.** Several steps ask whether the PHY has a link. Judge
that from phylib, not from traffic: `ethtool lanN` says `Link detected:
yes` with a speed, and the kernel log has `lanN: Link is Up - …`. The
switch driver may still turn the port off when the MAC disagrees (`PHY
link is …, but the MAC reports …; port disabled`), and then ping fails
although the PHY linked. Record both.

## Steps

Record everything that is printed (or the whole console log) and the
host's setting for each step.

### Before (generic driver)

1. **Driver.** `dmesg | grep 'driver \['`. Expected: all seven lines say
   `Generic Clause 45 PHY`.
2. **1 Gb/s, the bug.** Host at 1000 Mb/s. Connect lan2, then lan5, and
   run `ethtool lanN` on each. Expected: `Speed: 100Mb/s`, no 1000baseT
   in `Link partner advertised link modes`, and the switch driver's line
   `PHY link is 100Mbps/Full, but the MAC reports 1Gbps/Full; port
   disabled`. This output is the "before" for the commit messages.
3. **10 Mb/s.** The generic driver advertises 10BASE-T, so this shows
   whether the PHYs can link at 10 Mb/s at all. On lan2, then lan5:
   - host autonegotiating 10 Mb/s only, if the NIC can: wait 30 s;
   - host forced to 10 Mb/s: wait 30 s.

   For each, record whether the PHY has a link and at which speed and
   duplex. Outcome:
   - no PHY link at 10 Mb/s in either case: patch 1's 10BASE-T change
     stands;
   - a PHY link at 10 Mb/s: the PHYs do link at 10 Mb/s. Stop and
     report it: `rtl826xb_get_features` must then keep 10BASE-T, and the
     commit messages and cover letter change.

### After (these patches)

4. **Driver.** `dmesg | grep 'driver \['`, and
   `grep . /sys/bus/mdio_bus/devices/*/c45_phy_ids/mmd1_device_id`.
   Expected: lan1-lan3 (MDIO buses 1-3) say `Realtek RTL8261N/BE 10Gbps
   PHY`, lan4-lan7 (bus 0, addresses 0-3) say `Realtek RTL8264B 10Gbps
   PHY`, and the MMD 1 IDs are 0x001ccaf3 and 0x001cc813. If a PHY still
   says `Generic Clause 45 PHY`, then either its MMD 1 ID differs or its
   model register (VEND1 0x103) didn't read as expected over the mainline
   MDIO driver: stop and report it with the IDs.
5. **Abilities.** `ethtool lan1` and `ethtool lan5` with no cable.
   Expected supported and advertised modes: 100baseT/Half,
   100baseT/Full, 1000baseT/Full, 2500baseT/Full, 5000baseT/Full and
   10000baseT/Full, and no 10baseT.
6. **Speeds.** On every port lan1-lan7, with the host autonegotiating
   each speed it has (100, 1000 and 2500 Mb/s on the 2.5G NIC; 10 Gb/s on
   the 10G NIC), wait for the link, then:
   - `ethtool lanN`: the speed matches the host's, `Duplex: Full`, and
     `Link partner advertised link modes` lists the host's speed;
   - the kernel log has `Link is Up - <speed>/Full` for the port, and no
     `but the MAC reports` line;
   - `ping -c 3 192.168.N.2` gets replies;
   - at 1000 Mb/s and at the fastest speed, also `iperf3 -c 192.168.N.2
     -t 10` and `iperf3 -c 192.168.N.2 -t 10 -R`;
   - at 1000 Mb/s, also `ethtool --show-eee lanN`.

   The 1000 Mb/s `ethtool` output of lan2 and lan5 is the "after" for the
   commit messages. If time is short, at least lan2 (RTL8261BE) and lan5
   (RTL8264B) at every speed, and the other ports at 1000 Mb/s.
7. **Advertising.** On lan2, then on lan5, with the host autonegotiating
   all its speeds:
   - `ethtool -s lanN advertise 0x020` (1000baseT/Full only): the link
     comes back at 1000 Mb/s;
   - `ethtool -s lanN advertise 0x008` (100baseT/Full only): the link
     comes back at 100 Mb/s. This shows that the 1000BASE-T advertisement
     in the vendor register can be turned off;
   - `ethtool -s lanN advertise 0x1800000001028` (100baseT/Full,
     1000baseT/Full, 2500baseT/Full, 5000baseT/Full and 10000baseT/Full):
     the link comes back at the host's fastest speed.
8. **Forced 100 Mb/s.** On lan2, then lan5, with the host forced to
   100 Mb/s Full:
   - with the switch autonegotiating: record the PHY's speed and duplex
     (parallel detection gives 100 Mb/s Half, or no speed);
   - `ethtool -s lanN autoneg off speed 100 duplex full`: the PHY links
     at 100 Mb/s Full and ping works. Then `ethtool -s lanN autoneg on`.
9. **10 Mb/s.** As in step 3. Expected: no PHY link in either case, as
   in step 3. With the host autonegotiating 10 Mb/s only, there is
   nothing in common now that the PHY doesn't advertise 10BASE-T. If the
   forced case links, report it, as in step 3.
10. **Link changes.** On lan2 and lan5 at 1000 Mb/s:
    - unplug the cable for 5 s and plug it in again: the link comes back
      at 1000 Mb/s;
    - `ip link set lanN down`, wait 5 s, then `ip link set lanN up`: the
      link comes back at 1000 Mb/s. Record whether the host's link
      stayed up while lanN was down. It is expected to, as the entries
      don't suspend the PHY, like the generic driver.
11. **Kernel log.** At the end, `dmesg | grep -i -E
    'warn|error|bug|phy'`. Expected: no new warnings since step 4.
12. **Optional: without U-Boot's setup.** Boot the "after" image without
    `rtk network on` (if U-Boot can still load it, e.g. from flash or
    serial) and repeat step 6 at 1000 Mb/s on lan2. This documents what
    the cover letter means by relying on the boot loader; whatever the
    result, record it.

## What the patches may say afterwards

- **Commit messages.** A `Tested on` paragraph that names the board, and
  the speeds that passed in step 6 for that PHY. Add `ethtool` output at
  1000 Mb/s before (step 2) and after (step 6), cut to the speed, duplex
  and link partner lines.
- **Cover letter's Testing section.** Exactly the steps that passed, on
  this board, booted from the vendor U-Boot after `rtk network on`, with
  the RTL930x switch series (not posted yet) as the MAC driver. Also:
  - 5 Gb/s untested;
  - the RTL8251L and RTL8254B untested (not on the board, and left to the
    generic driver);
  - hosts other than USXGMII untested;
  - everything else build-tested only.
- **If step 3 or 9 shows a PHY link at 10 Mb/s**, drop the 10BASE-T
  change from patch 1 and from the cover letter.
