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
| PoE link | bit-banged **I²C**, SDA GPIO 10: first RTL8239 at `0x20`/`0x21`, second at `0x22`/`0x23` (one address per 4-channel half), broadcast `0x60`. The stock firmware sends a 25460-byte Realtek firmware image to both chips at every boot; without it they detect and power nothing | stock firmware (`board_conf.ko` table `poeDevConf_9303M7XC1XF_BT`, `ski.ko`), `mokerlink-poe-test` |
| I²C | three buses bit-banged on SoC GPIOs with one common SCL (GPIO 8): fan controller (SDA 9), PoE (SDA 10), SFP+ EEPROM (SDA 11). The same pins are the RTL9303's I²C master 1 (SCL, SDA0-SDA2) | stock firmware (`board_conf.ko`, SDK `r9300_i2c_dev_init`), `mokerlink-i2c-master-test` |
| GPIO expander | Realtek **RTL8231** (LEDs/GPIOs) | stock SDK: `RTL8231 probe: (found)` |
| Console | header **J4** (GND, TXD, RXD, 3V3, 3.3 V logic), **115200 8N1** | board, boot log |
| Boot loader | Realtek U-Boot 2011.12 (1.0.0.2, Dec 2024), **Esc** stops autoboot, `bootcmd=boota` | boot log, `printenv` |
| Stock image format | uImage with magic **`0x83800000`**, LZMA kernel, rootfs embedded in the kernel initramfs | `md.b b4300000 40`, stock image |
| Stock upgrade check | **no signature**: web UI accepts any uImage with magic `0x83800000` and valid header/data CRC32, writes the whole file to RUNTIME | stock firmware (`sal_sys_fwImageCrc32_check`) |
| PHY map | RTL8261BE on MAC 0/8/16 (SMI bus 1/2/3, address 0); RTL8264B on MAC 20/24/25/26 (bus 0, addresses 0-3) | U-Boot SMI registers |
| Lane polarity | RTL8261BE links normal; each RTL8264B link inverted once on the PHY and once on the SerDes (alternating tx/rx) | U-Boot PHY and SerDes registers, RAM boot |
| Front port order | front ports 1-7 are `lan1`-`lan7` (MAC ports 0, 8, 16, 20, 24, 25, 26), all pass traffic | port test in a RAM boot |
| Fan controller | **ADT7476** at I²C address `0x2e`, on a bit-banged bus: SCL GPIO 8, SDA GPIO 9. Runs the fans at full speed from power-on until programmed; the stock firmware sets 50 %, and full speed above 65 °C | stock firmware (`board_conf.ko` board table `9303M7XC1XF`, `ski.ko`), RAM boot |
| Stock firmware | "KT-NOS" 1.1.1.34 | boot log |

Still assumed:

| | Assumption | Source of the assumption |
|---|---|---|
| SFP+ | MAC port 27 on SerDes 9 | eighth RTL9303 10G lane |
| PoE LEDs | LED 2 of each RJ45 port in the switch's serial LED stream, under software control | stock firmware (`poe_led_detect_monitor` in `ski.ko`), round 2 of `mokerlink-poe-test` |

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
  over I²C; there is no separate PoE microcontroller)
- the SPI flash (8-pin chip, e.g. FM25Q128, W25Q128, GD25Q256)
- the RAM chips, which give the RAM size
- the fan controller (confirmed from the stock firmware: ADT7476)
- the serial console header: J4 (GND, TXD, RXD, 3V3)

Take a sharp photo of both sides of the board. These answer most questions
in [05-porting.md](05-porting.md).

## Heat

10GBASE-T PHYs run hot, typically 60-90 °C on the die at 10G. The fans are
driven by an ADT7476 fan controller, which runs them at full speed from
power-on until software programs it: in U-Boot, and in OpenWrt builds
without fan control, they stay at full speed. The stock firmware, and
OpenWrt's `mokerlink-fan` service, run them at 50 % and switch to full
speed when it gets hot ([05-porting.md](05-porting.md#fans-and-temperature)).
If the fans ever stop under OpenWrt, power off and report it.
`mokerlink-probe` prints the PHY and SoC temperatures, and
`mokerlink-fan status` the fan controller's.
