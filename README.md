# OpenWrt for the MokerLink POE-10G07110GSM

Open-source replacement firmware for the MokerLink POE-10G07110GSM
(7× 10GBASE-T PoE++ + 1× 10G SFP+, Realtek RTL9303), aimed at one thing:
a switch that forwards traffic correctly and keeps doing it.

It is [OpenWrt](https://openwrt.org) plus board support for this switch,
pinned to an exact source revision, with defaults for a plain layer-2
switch and tools to bring up the hardware safely.

> **Status.** Tested on a real unit, booted from RAM and installed to
> flash: all seven copper ports forward traffic, with links tested at
> 100 Mbit/s and 1, 2.5 and 10 Gbit/s; the SFP+ cage works at 10 Gbit/s
> with a 10G module; SFP+ modules can be swapped, 1G ones included; the
> reset button works; the fans run in the fan controller's automatic mode
> like stock; link changes are logged once; and PoE powers 802.3at and
> 802.3bt devices within a 260 W budget by class, including a fix in the
> Linux PoE core for that budget. PoE needs the RTL8239 firmware copied
> out of your stock image
> ([docs/05-porting.md](docs/05-porting.md#the-rtl8239-firmware)). The
> port LEDs, the green PoE LEDs included, are set up by OpenWrt itself.
> Not tested yet: the factory image. Open points are listed in
> [docs/05-porting.md](docs/05-porting.md#open-points).

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
- Fan control like the stock firmware, run by the fan controller itself:
  50 % normally, full speed when hot
- PoE (802.3af/at/bt) on ports 1-7, all on by default, with the PoE LEDs
  and a 260 W power budget with port priorities; needs the RTL8239
  firmware from your stock image
- SFP+ with module EEPROM and diagnostics (`ethtool -m`), 1G modules,
  and the reset button (short press reboots, 5 s resets to defaults)
- Tools on the switch: `mokerlink-probe`, `mokerlink-backup`,
  `mokerlink-polarity-scan`, `mokerlink-gpio-watch`, `mokerlink-fan`,
  `mokerlink-poe-test`, `mokerlink-hw-test`

## How to install, in short

The full procedure is in [docs/03-install.md](docs/03-install.md).

1. **Console:** connect a serial console and capture the stock boot log
   ([docs/02-serial-console.md](docs/02-serial-console.md)).
2. **RAM boot:** boot the OpenWrt RAM image over TFTP from U-Boot. Flash
   is not touched, and a power cycle returns to stock.
3. **Back up and check:** run `mokerlink-backup` and keep the files safe,
   then check that every port passes traffic.
4. **Flash:** write OpenWrt to flash with `sysupgrade`.

Without a serial console, the stock web UI installs the factory image
directly ([docs/03-install.md](docs/03-install.md#flashing-from-the-stock-web-ui)).

Going back to stock is covered in [docs/04-recovery.md](docs/04-recovery.md).

## Getting the images

- **GitHub Actions:** every push that changes more than the documentation
  builds the firmware. Download it from the run's *Artifacts* section under
  the repository's *Actions* tab.
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
  | `*-squashfs-sysupgrade.bin` | Flash image, installed with `sysupgrade` from OpenWrt |
  | `*-squashfs-factory.bix` | Flash image for the stock firmware's web upgrade page |

## Repository layout

| Path | Contents |
|---|---|
| `openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts` | Board description (device tree) |
| `patches/` | OpenWrt changes: image recipe, MAC address source, kernel packages, a fix for the switch driver's link change reporting |
| `openwrt/target/linux/realtek/files-6.18`, `patches-6.18/83x-*` | New kernel drivers with their bindings: RTL8239 PoE (`realtek-rtl8239`), the shared-SCL I²C buses (`i2c-gpio-shared-scl`) and the PoE LEDs (`leds-rtl9300-port`), plus two binding patches for upstream (`833`, `834`) |
| `files/` | Files added to the firmware: switch defaults and bring-up tools |
| `config/` | Pinned OpenWrt and feed revisions, package selection |
| `upstream/` | Commit messages for the OpenWrt and Linux submissions ([docs/08-upstreaming.md](docs/08-upstreaming.md)) |
| `scripts/` | `build.sh`, `check-dts.sh`, `make-upstream.py` (builds the OpenWrt and Linux submissions), `uimage-remagic.py`, `uboot-wiring.py` (reads the port wiring from U-Boot), `uboot-md2bin.py` (turns a U-Boot flash dump into a binary), `uboot-collect.py` (collects all U-Boot information and dumps the flash over the serial port, CRC-verified), `extract-poe-firmware.py` (copies the RTL8239 PoE firmware out of a stock image) |
| `tests/` | Tests for `uboot-wiring.py` and `uboot-collect.py` against a simulated U-Boot |
| `docs/` | Hardware notes, console, install, recovery, porting, everyday use |

## Documentation

1. [Hardware](docs/01-hardware.md): chips, wiring, flash layout
2. [Serial console and U-Boot](docs/02-serial-console.md)
3. [Install](docs/03-install.md): RAM boot, backup, verification, flashing
4. [Recovery](docs/04-recovery.md): back to stock, unbricking
5. [Board support](docs/05-porting.md): how each part of the switch is
   supported, where the values come from, open points
6. [Using the switch](docs/06-using.md): defaults, VLANs, updates, and
   why traffic may not forward
7. [Build and install from Windows](docs/07-windows.md): WSL2 build, TFTP,
   RAM test and flashing, step by step
8. [Upstreaming](docs/08-upstreaming.md): submitting the board to OpenWrt
   and the drivers to Linux

## Risks

Opening the case and replacing the firmware can void the warranty, and a
mistake while writing flash can leave the switch unbootable until it is
recovered over the serial console. The procedure keeps the boot loader
read-only and keeps a full flash backup, so a recoverable state is always
available. Work through the steps in order and don't skip the backup.

## License

The device tree, scripts and documentation are GPL-2.0-or-later, like the
OpenWrt code they extend.
