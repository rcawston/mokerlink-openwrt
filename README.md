# OpenWrt for the MokerLink POE-10G07110GSM

Open-source replacement firmware for the MokerLink POE-10G07110GSM
(7× 10GBASE-T PoE++ + 1× 10G SFP+, Realtek RTL9303), aimed at one thing:
a switch that forwards traffic correctly and keeps doing it.

It is [OpenWrt](https://openwrt.org) plus board support for this switch,
pinned to an exact source revision, with defaults for a plain layer-2
switch and tools to bring up the hardware safely.

> **Status: stage 1, RAM-tested on real hardware. PoE written, untested.**
> Booted from RAM on a real unit: all seven 10G PHYs come up, front ports
> 1-7 are `lan1`-`lan7`, and every copper port forwards traffic (tested at
> 1 Gbit/s). The PHY map and lane polarity come straight from the vendor
> U-Boot (`scripts/uboot-wiring.py`). **PoE** has a new driver for the two
> RTL8239 chips, built from what the stock firmware does, but has not run
> on the switch yet; it needs the RTL8239 firmware copied out of your stock
> image ([docs/05-porting.md](docs/05-porting.md#poe)). Fan control, also
> taken from the stock firmware, is in but not yet tested on the switch
> ([docs/05-porting.md](docs/05-porting.md#fans-and-temperature)). Still
> untested: SFP+, and copper at 2.5/5/10 Gbit/s.

## Why OpenWrt and not firmware from scratch

The forwarding itself happens in the RTL9303 switch chip. The firmware's job
is to configure that chip, its SerDes lanes and the 10G PHYs correctly. That
configuration is exactly where cheap switches usually fail: a wrong SerDes
polarity, for example, gives a port that shows link but passes no frames.
Writing this from scratch would mean re-implementing years of
reverse-engineering. OpenWrt's `realtek` target already supports more than
25 RTL930x switches, including several close relatives of this one. A small,
reviewable board description on top of it is the most reliable option.

## What you get

- All 8 ports bridged in hardware (VLAN 1, untagged), like an unmanaged
  switch, with VLANs configurable in the web UI
- Jumbo frames up to 9216 bytes on every port, EEE off, no DHCP server and
  no routing
- Management address by DHCP, with a fallback of `192.168.2.1/24` when no
  DHCP server answers within 60 seconds
- Web UI (LuCI, layer-2 edition) and SSH
- Hardware watchdog: a hung system reboots itself
- Fan control like the stock firmware: 50 % normally, full speed when hot
- PoE (802.3af/at/bt) on ports 1-7, all on by default, per port with
  `ethtool`; needs the RTL8239 firmware from your stock image (untested)
- Bring-up tools on the switch: `mokerlink-probe`, `mokerlink-backup`,
  `mokerlink-polarity-scan`, `mokerlink-gpio-watch`, `mokerlink-fan`,
  `mokerlink-poe-test`
Not yet supported in stage 1: SFP+ module EEPROM/DOM readout and 1G SFP
modules, the reset button, the system LED, and anything behind the RTL8231
GPIO expander. If you need PoE you can rely on today, stay on the stock
firmware.

## How to install, in short

The full procedure is in [docs/03-install.md](docs/03-install.md).

1. **Console:** connect a serial console, capture the stock boot log, and
   read the port wiring from U-Boot
   ([docs/02-serial-console.md](docs/02-serial-console.md)).
2. **RAM boot:** boot the OpenWrt RAM image over TFTP from U-Boot. Flash
   is not touched, and a power cycle returns to stock.
3. **Back up and probe:** run `mokerlink-backup` and keep the files safe.
   Run `mokerlink-probe` and `mokerlink-polarity-scan` and fix the device
   tree until every port passes traffic.
4. **Flash:** only then write OpenWrt to flash with `sysupgrade`.

Going back to stock is covered in [docs/04-recovery.md](docs/04-recovery.md).

## Getting the images

- **GitHub Actions:** every push builds the firmware. Download it from the
  run's *Artifacts* section under the repository's *Actions* tab.
- **Local build** on a Linux machine with the
  [OpenWrt build prerequisites](https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem):

  ```sh
  scripts/check-dts.sh   # fast checks (needs dtc), about a minute
  scripts/build.sh       # full build, 1-3 hours the first time
  ```

  The images land in `out/`:

  | File | Use |
  |---|---|
  | `*-initramfs-kernel.bin` | RAM image for TFTP boot, MokerLink uImage magic `0x83800000` |
  | `*-initramfs-kernel-stdmagic.bin` | Same RAM image with the standard uImage magic |
  | `*-squashfs-sysupgrade.bin` | Flash image, installed with `sysupgrade` from the RAM image |

## Repository layout

| Path | Contents |
|---|---|
| `openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts` | Board description (device tree); every unconfirmed detail is marked `UNVERIFIED` |
| `patches/` | OpenWrt changes: image recipe, MAC address source, kernel packages |
| `openwrt/target/linux/realtek/files-6.18`, `patches-6.18/83x-*` | New kernel drivers with their bindings: RTL8239 PoE (`realtek-rtl8239`) and the shared-SCL I²C buses (`i2c-gpio-shared-scl`) |
| `files/` | Files added to the firmware: switch defaults and bring-up tools |
| `config/` | Pinned OpenWrt and feed revisions, package selection |
| `scripts/` | `build.sh`, `check-dts.sh`, `uimage-remagic.py`, `uboot-wiring.py` (reads the port wiring from U-Boot), `uboot-md2bin.py` (turns a U-Boot flash dump into a binary), `uboot-collect.py` (collects all U-Boot information and dumps the flash over the serial port, CRC-verified), `extract-poe-firmware.py` (copies the RTL8239 PoE firmware out of a stock image) |
| `tests/` | Simulation test for `uboot-wiring.py` |
| `docs/` | Hardware notes, console, install, recovery, porting, everyday use |

## Documentation

1. [Hardware](docs/01-hardware.md): what is known and what to check inside
2. [Serial console and U-Boot](docs/02-serial-console.md)
3. [Install](docs/03-install.md): RAM boot, backup, verification, flashing
4. [Recovery](docs/04-recovery.md): back to stock, unbricking
5. [Porting](docs/05-porting.md): turning probe results into a verified
   device tree
6. [Using the switch](docs/06-using.md): defaults, VLANs, updates, and
   why traffic may not forward
7. [Build and install from Windows](docs/07-windows.md): WSL2 build, TFTP,
   RAM test and flashing, step by step

## Risks

Opening the case and replacing the firmware can void the warranty, and a
mistake while writing flash can leave the switch unbootable until it is
recovered over the serial console. The procedure keeps the boot loader
read-only and keeps a full flash backup, so a recoverable state is always
available. Work through the steps in order and don't skip the backup.

## License

The device tree, scripts and documentation are GPL-2.0-or-later, like the
OpenWrt code they extend.
