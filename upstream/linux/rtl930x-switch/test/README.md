# Testing the RTL930x switch driver series on the MokerLink

Test only; nothing here is submitted. The image is net-next with the
series, booted from RAM by the vendor U-Boot, with a small initramfs
(busybox, ethtool, iperf3) and a guided test script. The flash is never
written: every partition is read-only in the test device tree, and a power
cycle returns to whatever is installed.

## Getting the image

The `rtl930x-switch` workflow builds it on every push that changes the
series, and uploads `uImage-rtl930x-switch-test.bin` (MokerLink magic
0x83800000) and `uImage-rtl930x-switch-test-stdmagic.bin` with the build
logs. On a Linux machine:

```sh
scripts/check-rtl930x-switch.sh
scripts/build-rtl930x-test-image.sh
# build/check/rtl930x-switch/image/uImage-rtl930x-switch-test.bin
```

## What the image relies on

- **The vendor U-Boot** sets up the SerDes, the PHYs with their lane
  polarity, and the hardware PHY polling registers when its network
  starts (`rtk network on`), which the RAM boot does anyway. The series
  doesn't program the SerDes yet, and mainline's MDIO driver sets only
  the polling bit of each port.
- **The PHYs** have no mainline driver (RTL8261BE, RTL8264B), so the
  generic Clause 45 driver binds. Step 3 of the test is the first to show
  whether that is enough on top of what U-Boot set up.
- **No PoE driver.** Step 2 still shows that ethtool's PSE lookup reaches
  the PHY of a port that is down ("No PSE is attached", not "No PHY").
  Powering a PD from a down port needs a build with the RTL8239 driver
  and the PSE poll series, and `-DWITH_PSE` for the device tree; that is
  a separate, manual step.

## Booting

As for the OpenWrt RAM image (docs/03-install.md), with the test image:

```text
rtk network on
tftpboot 0x84f00000 uImage-rtl930x-switch-test.bin
bootm 0x84f00000
```

Use the `-stdmagic` file if `bootm` answers `Bad Magic Number`. The
console is the usual one at 115200 8N1. The image names the ports lan1-lan8
after the front panel; lan8, the SFP+ cage, is not created by this series.

## Running the test

On the host: a NIC at `192.168.N.2/24` for front port N (lan1:
`192.168.1.2`), `iperf3 -s`, `ethtool` and `tcpdump`. Then on the switch:

```sh
rtl9300-switch-test lan1
```

It asks before every step that needs a cable or the host, and every step
can be skipped. The report is `/tmp/rtl9300-switch-test.txt`; copy it off
the console (`cat`) when it is done, together with `dmesg`.

| Step | Checks |
|---|---|
| 1 | the driver binds; lan1-lan7 exist; the SFP port is skipped with its message; no warning, lockdep or DMA-API report |
| 2 | the 7 PHYs attached (from the kernel log, as there are no phydev links in sysfs) and their driver; with every port down, ethtool's PSE lookup finds the PHY, and the DMA engine raises next to no interrupts (no storm) |
| 3 | each port links with the host, at the speeds the host can set |
| 4 | ping and iperf3 TCP both ways and UDP; the interface and ethtool standard statistics; how many interrupts the DMA engine raised while sending UDP, which shows whether the TX done interrupt fires |
| 5 | VLAN 100 tagged frames both ways; tagged and untagged frames unchanged (tcpdump -e on the host). By hand, also priority-tagged (VID 0) and 802.1ad frames |
| 6 | MTU 9000 with an 8972-byte ping with DF from the host; MTU 12262 accepted |
| 7 | isolation: the host can't reach a device on another switch port |
| 8 | 100 down/up cycles under traffic; traffic afterwards; MemAvailable before and after |
| 9 | unbind and bind the driver |

## What the cover letter may say afterwards

Exactly the steps that passed, on this board, booted from the vendor
U-Boot after `rtk network on`, with the PHY driver that bound (step 2
reports it), and "build-tested only" for RTL9301, RTL9302B and RTL9302C.
Step 4's interrupt count shows whether the TX done interrupt fires. If it
doesn't, only the timer frees sent frames, once a tick, which caps TX
whenever the ring fills; then the series either drops the interrupt or
the cover letter says so.
