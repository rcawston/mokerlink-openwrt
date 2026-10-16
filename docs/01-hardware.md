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

Confirmed from the chip markings:

| | Chip | Notes |
|---|---|---|
| SoC | Realtek **RTL9303** | 8× 10G MAC, MIPS 34Kc, 800 MHz |
| 10G PHYs | Realtek **RTL8264B** | quad 10GBASE-T PHY: one for ports 1-4, a second for ports 5-7 |
| PoE | Realtek **RTL8239** | PSE silicon, managed by a small MCU that the SoC talks to over I²C or UART |

Still assumed, from similar supported switches:

| | Assumption | Source of the assumption |
|---|---|---|
| PHY wiring | second RTL8264B on SMI bus 3, addresses 16-18 | Hasivo S1100W(P)-8XGT-SE |
| SFP+ | MAC port 27 on SerDes 9 | eighth RTL9303 10G lane |
| Flash | 16 MiB SPI NOR, standard Realtek SDK layout | Hasivo S1100W, MokerLink 2G080GM |
| RAM | at least 128 MiB (Hasivo: 512 MiB) | U-Boot prints the real size |
| Console | 38400 8N1 | RTL930x U-Boot default |
| uImage magic | `0x83800000` | MokerLink 2G080GM and 10GT080M |
| PoE MCU | I²C, address `0x20`, gen2 protocol | Ubiquiti USW Pro XG 8 PoE and Zyxel XS1930-12HP (both RTL8239) |

The RTL8239 means this is not the Hasivo design (Hasivo uses HS104 PSE
chips), so the Hasivo-derived PHY wiring and polarity are only a starting
guess. The fastest way to replace the guesses with facts is to read them
from U-Boot ([02-serial-console.md](02-serial-console.md#read-the-port-wiring)).
The device tree marks each assumption as `UNVERIFIED`, and
[05-porting.md](05-porting.md) explains how to check each one.

## Flash layout (assumed)

| Offset | Size | Stock name | OpenWrt name | Written by OpenWrt? |
|---|---|---|---|---|
| `0x000000` | 896 KiB | LOADER | u-boot | never |
| `0x0e0000` | 64 KiB | BDINFO | u-boot-env | never (read-only) |
| `0x0f0000` | 64 KiB | SYSINFO | u-boot-env2 | never (read-only) |
| `0x100000` | 1 MiB | JFFS2_CFG | jffs | never (read-only) |
| `0x200000` | 1 MiB | JFFS2_LOG | jffs2 | never (read-only) |
| `0x300000` | 12 MiB | RUNTIME | firmware | **yes**, by sysupgrade |
| `0xf00000` | 1 MiB | OEMINFO | oeminfo | never (read-only) |

The stock firmware prints its own partition table during boot. Compare it
with this one before flashing.

## Looking inside

Not required for a RAM boot if the switch has an external console port, but
the photos settle most open questions at once. Unplug power and PoE devices
first. The 10G PHY heatsinks are glued or spring-loaded on most of these
boards: leave them in place unless you have thermal pads to replace them.

Note the markings of:

- the main chip under the large heatsink (confirmed: RTL9303)
- the PHY chips near the RJ45 ports (confirmed: RTL8264B), and how many
- the PoE chips (confirmed: RTL8239), how many, and **the small
  microcontroller that manages them**: a 20-48 pin chip near the RTL8239s,
  marked e.g. Nuvoton, GD32, STM32, HK32 or Holtek. Its pins lead to the
  bus the SoC uses for PoE.
- the SPI flash (8-pin chip, e.g. FM25Q128, W25Q128, GD25Q256)
- the RAM chips, which give the RAM size
- any small chip near the fans (fan controller or MCU)
- the serial console header, usually 4 pins: GND, TX, RX, 3.3 V

Take a sharp photo of both sides of the board. These answer most questions
in [05-porting.md](05-porting.md).

## Heat

10GBASE-T PHYs run hot, typically 60-90 °C on the die at 10G. Watch the fans
the first time OpenWrt runs. If the fans are controlled by a separate MCU
(as on the Hasivo boards), they behave the same under any firmware. If they
stop or behave differently under OpenWrt, power off and report it.
`mokerlink-probe` prints the PHY and SoC temperatures.
