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
| 10G PHYs | one **RTL8264B** quad (ports 1-4) plus three single **RTL8261BE** (ports 5-7) | vendor hardware profile `RTL9303_8264B_3X8261BE_XGE_POE` |
| PoE | two **RTL8239-VB** PSE chips behind a PSE MCU | chip marking, stock log `find RTL8239-VB` (twice) |
| PoE MCU link | most likely **UART** | stock SDK: `UART probe: (found)`, no I²C device |
| GPIO expander | Realtek **RTL8231** (LEDs/GPIOs) | stock SDK: `RTL8231 probe: (found)` |
| Console | header **J4** (GND, TXD, RXD, 3V3, 3.3 V logic), **115200 8N1** | board, boot log |
| Boot loader | Realtek U-Boot 2011.12 (1.0.0.2, Dec 2024), **Esc** stops autoboot | boot log |
| Stock firmware | "KT-NOS" 1.1.1.34 | boot log |

Still assumed:

| | Assumption | Source of the assumption |
|---|---|---|
| PHY addresses | RTL8264B on SMI bus 0 at 0-3; RTL8261BEs on bus 1 at 4-6 | common to similar boards; MokerLink 10GT080M numbering |
| Lane polarity | normal on both ends of every link | hardware default |
| SFP+ | MAC port 27 on SerDes 9 | eighth RTL9303 10G lane |
| uImage magic | `0x83800000` | MokerLink 2G080GM and 10GT080M |
| PoE MCU | `uart1`, 115200 baud, gen2 protocol | Zyxel XMG1915-10EP (RTL930x, RTL8239 MCU on `uart1`) |

The PHY addresses and lane polarity can be read from U-Boot
([02-serial-console.md](02-serial-console.md#read-the-port-wiring)). The
device tree marks each assumption as `UNVERIFIED`, and
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
- the PoE chips (confirmed: two RTL8239-VB) and **the small
  microcontroller that manages them**: a 20-48 pin chip near the RTL8239s,
  marked e.g. Nuvoton, GD32, STM32, HK32 or Holtek. Its pins lead to the
  bus the SoC uses for PoE.
- the SPI flash (8-pin chip, e.g. FM25Q128, W25Q128, GD25Q256)
- the RAM chips, which give the RAM size
- any small chip near the fans (fan controller or MCU)
- the serial console header: J4 (GND, TXD, RXD, 3V3)

Take a sharp photo of both sides of the board. These answer most questions
in [05-porting.md](05-porting.md).

## Heat

10GBASE-T PHYs run hot, typically 60-90 °C on the die at 10G. Watch the fans
the first time OpenWrt runs. If the fans are controlled by a separate MCU
(as on the Hasivo boards), they behave the same under any firmware. If they
stop or behave differently under OpenWrt, power off and report it.
`mokerlink-probe` prints the PHY and SoC temperatures.
