# Hardware

## Published specifications

From the MokerLink datasheet and manual:

| | |
|---|---|
| Ports | 7× 10GBASE-T RJ45 (10G/5G/2.5G/1G/100M), PoE++ 802.3af/at/bt; 1× 10G SFP+ |
| PoE | up to 90 W per port, 260 W total budget |
| Switching capacity | 160 Gbps |
| Management | web, L2 managed (VLAN, LACP, QoS, ACL, mirroring, SNMP) |
| Cooling | fan(s), "smart" speed control |

## What is on the board

Confirmed from the chip markings and the stock boot log:

| | Fact | Source |
|---|---|---|
| SoC | Realtek **RTL9303** (8× 10G MAC, MIPS 34Kc, 800 MHz) | chip marking, U-Boot `Board: RTL9300` |
| RAM | **128 MiB** | U-Boot `DRAM: 128 MB` |
| Flash | **16 MiB** Winbond W25Q128 SPI NOR (ID `EF4018`) | U-Boot `SPI-F:` line |
| 10G PHYs | three single **RTL8261BE** (front ports 1-3) plus one **RTL8264B** quad (front ports 4-7) | vendor hardware profile `RTL9303_8264B_3X8261BE_XGE_POE`, RAM boot |
| PoE | two **RTL8239-VB** PSE chips, driven **directly** by the CPU (no PSE MCU) | chip marking, stock log `find RTL8239-VB` (once per chip) |
| PoE link | `uart1` (`/dev/ttyS1`); the stock firmware loads RTL8239 firmware images over it at boot | stock firmware (`ski.ko`) |
| GPIO expander | Realtek **RTL8231** (LEDs/GPIOs) | stock SDK: `RTL8231 probe: (found)` |
| Console | header **J4** (GND, TXD, RXD, 3V3, 3.3 V logic), **115200 8N1** | board, boot log |
| Boot loader | Realtek U-Boot 2011.12 (1.0.0.2, Dec 2024), **Esc** stops autoboot, `bootcmd=boota` | boot log, `printenv` |
| Stock image format | uImage with magic **`0x83800000`**, LZMA kernel, rootfs embedded in the kernel initramfs | `md.b b4300000 40`, stock image |
| Stock upgrade check | **no signature**: web UI accepts any uImage with magic `0x83800000` and valid header/data CRC32, writes the whole file to RUNTIME | stock firmware (`sal_sys_fwImageCrc32_check`) |
| PHY map | RTL8261BE on MAC 0/8/16 (SMI bus 1/2/3, address 0); RTL8264B on MAC 20/24/25/26 (bus 0, addresses 0-3) | U-Boot SMI registers |
| Lane polarity | RTL8261BE links normal; each RTL8264B link inverted once on the PHY and once on the SerDes (alternating tx/rx) | U-Boot PHY and SerDes registers, RAM boot |
| Front port order | front ports 1-7 are `lan1`-`lan7` (MAC ports 0, 8, 16, 20, 24, 25, 26), all pass traffic | port test in a RAM boot |
| Fans | full speed from power-on, in U-Boot and under OpenWrt; only the stock firmware slows them, once it has booted | RAM boot |
| Stock firmware | "KT-NOS" 1.1.1.34 | boot log |

Still assumed:

| | Assumption | Source of the assumption |
|---|---|---|
| SFP+ | MAC port 27 on SerDes 9 | eighth RTL9303 10G lane |
| PoE without stock firmware | unknown whether the RTL8239s power devices on their own | needs a test with a PoE device under OpenWrt |

The device tree marks each assumption as `UNVERIFIED`, and
[05-porting.md](05-porting.md) explains how to check each one.

## Flash layout

Confirmed by the partition table the stock firmware prints at boot:

| Offset | Size | Stock name | OpenWrt name | Written by OpenWrt? |
|---|---|---|---|---|
| `0x000000` | 896 KiB | LOADER | u-boot | never |
| `0x0e0000` | 64 KiB | BDINFO | u-boot-env | never (read-only) |
| `0x0f0000` | 64 KiB | SYSINFO | u-boot-env2 | never (read-only) |
| `0x100000` | 1 MiB | JFFS2 CFG | jffs | never (read-only) |
| `0x200000` | 1 MiB | JFFS2 LOG | jffs2 | never (read-only) |
| `0x300000` | 12 MiB | RUNTIME | firmware | **yes**, by sysupgrade |
| `0xf00000` | 1 MiB | OEMINFO | oeminfo | never (read-only) |

There is a single firmware slot (no second RUNTIME), so a firmware write
always replaces the stock firmware. Keep the backup.

## Looking inside

The case has to be opened anyway for the console header J4, and photos
settle most open questions at once. Unplug power and PoE devices first. The 10G PHY heatsinks are glued or spring-loaded on most of these
boards: leave them in place unless you have thermal pads to replace them.

Note the markings of:

- the main chip under the large heatsink (confirmed: RTL9303)
- the PHY chips near the RJ45 ports (confirmed: one RTL8264B, three RTL8261BE)
- the PoE chips (confirmed: two RTL8239-VB, talked to directly by the SoC
  over `uart1`; the stock firmware shows no separate PoE microcontroller)
- the SPI flash (8-pin chip, e.g. FM25Q128, W25Q128, GD25Q256)
- the RAM chips, which give the RAM size
- any small chip near the fans (fan controller or MCU)
- the serial console header: J4 (GND, TXD, RXD, 3V3)

Take a sharp photo of both sides of the board. These answer most questions
in [05-porting.md](05-porting.md).

## Heat

10GBASE-T PHYs run hot, typically 60-90 °C on the die at 10G. The fans run
at full speed from power-on, in U-Boot and under OpenWrt, and only the stock
firmware turns them down once it has booted. So nothing on the board picks
the speed by itself; the SoC sets it, directly or through a chip it
programs, and OpenWrt does not do that yet
([05-porting.md](05-porting.md#fans-and-temperature)). Full speed is the
safe side, only louder: at the first RAM boot the PHYs read 31-46 °C and
the SoC 43 °C. If the fans ever stop under OpenWrt, power off and report it.
`mokerlink-probe` prints the PHY and SoC temperatures.
