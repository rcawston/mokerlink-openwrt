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

| | Fact | Source |
|---|---|---|
| SoC | Realtek **RTL9303** (8× 10G MAC, MIPS 34Kc, 800 MHz) | chip marking, U-Boot `Board: RTL9300` |
| RAM | **128 MiB** | U-Boot `DRAM: 128 MB` |
| Flash | **16 MiB** Winbond W25Q128 SPI NOR (ID `EF4018`) | U-Boot `SPI-F:` line |
| 10G PHYs | three single **RTL8261BE** (front ports 1-3) plus one **RTL8264B** quad (front ports 4-7) | vendor hardware profile `RTL9303_8264B_3X8261BE_XGE_POE`, RAM boot |
| PoE | two **RTL8239-VB** PSE chips, driven **directly** by the CPU (no PSE MCU) | chip marking, stock log `find RTL8239-VB` (once per chip) |
| PoE link | bit-banged **I²C**, SDA GPIO 10: first RTL8239 at `0x20`/`0x21`, second at `0x22`/`0x23` (one address per 4-channel half), broadcast `0x60`. The stock firmware sends a 25460-byte Realtek firmware image to both chips at every boot, in one write (they refuse it split up); without it they detect and power nothing | stock firmware (`board_conf.ko` table `poeDevConf_9303M7XC1XF_BT`, `ski.ko`), tests on the switch |
| I²C | three buses bit-banged on SoC GPIOs with one common SCL (GPIO 8): fan controller (SDA 9), PoE (SDA 10), SFP+ EEPROM (SDA 11). The same pins are the RTL9303's I²C master 1 (SCL, SDA0-SDA2), which reaches the chips but can't send the PoE firmware | stock firmware (`board_conf.ko`, SDK `r9300_i2c_dev_init`), tests on the switch |
| Port LEDs | three per RJ45 port in the switch's serial LED stream: LEDs 0 and 1 the link LED (orange, green), LED 2 the green PoE LED, which the stock firmware drives in software | stock firmware (`poe_led_detect_monitor` in `ski.ko`), tests on the switch |
| System LED | works as the boot loader sets it up | RAM boot |
| GPIO expander | Realtek **RTL8231** (LEDs/GPIOs) is detected, but the stock firmware uses none of its pins on this board | stock SDK: `RTL8231 probe: (found)`, `board_conf.ko` |
| Console | header **J4** (GND, TXD, RXD, 3V3, 3.3 V logic), **115200 8N1** | board, boot log |
| Boot loader | Realtek U-Boot 2011.12 (1.0.0.2, Dec 2024), **Esc** stops autoboot, `bootcmd=boota` | boot log, `printenv` |
| Stock image format | uImage with magic **`0x83800000`**, LZMA kernel, rootfs embedded in the kernel initramfs | `md.b b4300000 40`, stock image |
| Stock upgrade check | **no signature**: web UI accepts any uImage with magic `0x83800000` and valid header/data CRC32, writes the whole file to RUNTIME | stock firmware (`sal_sys_fwImageCrc32_check`) |
| PHY map | RTL8261BE on MAC 0/8/16 (SMI bus 1/2/3, address 0); RTL8264B on MAC 20/24/25/26 (bus 0, addresses 0-3) | U-Boot SMI registers |
| Lane polarity | RTL8261BE links normal; each RTL8264B link inverted once on the PHY and once on the SerDes (alternating tx/rx) | U-Boot PHY and SerDes registers, RAM boot |
| Front port order | front ports 1-7 are `lan1`-`lan7` (MAC ports 0, 8, 16, 20, 24, 25, 26), all pass traffic | port test in a RAM boot |
| Link speeds | copper links at 100 Mbit/s, 1, 2.5 and 10 Gbit/s; the SFP+ cage (`lan8`, MAC port 27 on SerDes 9) at 10 Gbit/s with a 10G SR module and at 1 Gbit/s with a 1G copper module | owner's tests in a RAM boot |
| Fan controller | **ADT7476** at I²C address `0x2e`, on a bit-banged bus: SCL GPIO 8, SDA GPIO 9. Runs the fans at full speed from power-on until programmed; the stock firmware sets 50 %, and full speed above 65 °C. Two fans, on tach inputs 1 and 2 (about 4500-4800 rpm at 50 %); all three temperature sensors (local and two remote diodes) connected | stock firmware (`board_conf.ko` board table `9303M7XC1XF`, `ski.ko`), RAM boot |
| Reset button | SoC GPIO 5, active low (reads 1 while released) | stock firmware (`resetGpio5` in `board_conf.ko`), RAM boot |
| SFP+ cage signals | module present GPIO 13 (low = in), LOS GPIO 12 (high = no signal), TX disable GPIO 14 (high = off); no TX fault line | stock firmware (`board_gpio_init` in `board_conf.ko`), module removal and insertion in a RAM boot |
| Stock firmware | "KT-NOS" 1.1.1.34 | boot log |

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

## Opening the case

The console header J4 is inside, so the case has to be opened once. Unplug
power and PoE devices first. The 10G PHY heatsinks are glued or
spring-loaded: leave them in place unless you have thermal pads to replace
them.

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
