# Porting: from assumptions to a verified device tree

The board description is
[`openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts`](../openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts).
Every detail that still needs confirming on real hardware is marked
`UNVERIFIED` there. This page covers how to confirm or fix each one.

## Workflow

1. Edit the device tree (or the image recipe in `patches/`).
2. Run `scripts/check-dts.sh`. It compiles the device tree against the
   pinned OpenWrt and checks that the flash layout, image size and uImage
   magic agree.
3. Build: push to GitHub and download the artifact, or run
   `scripts/build.sh`.
4. RAM-boot the new image ([03-install.md, step 1](03-install.md#1-boot-openwrt-from-ram))
   and re-run `mokerlink-probe`.

## RAM size

Confirmed: 128 MiB (`DRAM: 128 MB`), the default in `rtl930x.dtsi`.
Declaring more RAM than fitted crashes the kernel, so leave it.

## Console speed

Confirmed: 115200 8N1, the `rtl930x.dtsi` default.

## Flash layout

Confirmed: the device tree matches the partition table the stock firmware
prints at boot ([01-hardware.md](01-hardware.md#flash-layout)). If a future
hardware revision differs, copy its table into the `partitions` node, keep
everything except `firmware` read-only, and set `IMAGE_SIZE` in the patch
to the new firmware partition size.

## uImage magic

The image is built with `0x83800000`, like the other MokerLink switches. If
`md.b b4300000 4` in U-Boot shows `27 05 19 56`, or only the
`-stdmagic` RAM image boots, change `0x83800000` to `0x27051956` in both
places:

- `openwrt,ih-magic` in the device tree (`firmware` partition)
- `UIMAGE_MAGIC` in `patches/0001-*.patch` (change only the value on the
  `+  UIMAGE_MAGIC := ...` line)

`check-dts.sh` fails if the two disagree.

## PHY addresses and SerDes mapping

On RTL9303 each 10G MAC port has a fixed SerDes lane:

| MAC port | 0 | 8 | 16 | 20 | 24 | 25 | 26 | 27 |
|---|---|---|---|---|---|---|---|---|
| SerDes | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |

A copper port is declared with two macros:

```dts
&mdio_bus0 { PHY_C45(0, 0) };                    /* PHY for MAC port 0: SMI bus 0, address 0 */
SWITCH_PORT_SDS(0, 1, 2, 0, usxgmii)             /* MAC port 0 = lan1, SerDes 2 */
```

On this board the vendor U-Boot maps the PHYs like this (read from the
switch's SMI registers after `rtk network on`), and the device tree follows
it:

| MAC port | SMI bus | Address | PHY |
|---|---|---|---|
| 0 | 1 | 0 | RTL8261BE (ID `001c:caf3` read back) |
| 8 | 2 | 0 | RTL8261BE |
| 16 | 3 | 0 | RTL8261BE |
| 20, 24, 25, 26 | 0 | 0, 1, 2, 3 | RTL8264B quad |
| 27 | - | - | SFP+ (no PHY) |

Confirmed in a RAM boot: front ports 1-7 are `lan1`-`lan7`, in this MAC
port order. So front ports 1-3 are the RTL8261BEs and front ports 4-7 the
RTL8264B.

Signs that a PHY address or bus is wrong: `mokerlink-probe` shows no
driver for a port, the port is missing entirely, or `dmesg` shows PHY
errors for it. For another board revision, read the wiring from U-Boot the
same way and decode it with `scripts/uboot-wiring.py decode`. It prints
the `&mdio_busN` lines and the polarity lines ready to paste.

If the SFP+ cage is not the last port, swap its port node with the
copper port that is really on SerDes 9.

## SerDes lane polarity

The lane polarity between switch chip and PHY can be set on either end: on
the PHY (`&phyN { tx-polarity/rx-polarity }`, RTL826x driver) or on the
switch SerDes (`&serdesN { ... }`, PCS driver). Both drivers program it
from the device tree on every boot and default to "normal". A wrong value
makes a port show link but pass no frames in one or both directions. The
MokerLink 10GT080M, for example, inverts on the SerDes side.

The vendor U-Boot programs the correct values, so the
[U-Boot wiring read](02-serial-console.md#read-the-port-wiring) gives them
directly. On this board the RTL8261BE links are normal, and each RTL8264B
link is inverted on the PHY in one direction and on the SerDes in the
other; the device tree carries these values, and with them every copper
port passed traffic in the RAM boot. To check another unit, or find the values when U-Boot did not set them, run
the scan from a RAM-booted OpenWrt with a host at `192.168.2.254` on the
port under test:

```sh
mokerlink-polarity-scan lan2 192.168.2.254
```

It tries all four combinations on the PHY side at runtime and prints the
line to use, e.g. `&phy8 { rx-polarity = <PHY_POL_INVERT>; };`. A
mismatch on a differential lane can be corrected at either end, so this
works whichever side the board needs it on. "Normal" needs no line.

## SFP+ cage (full support)

Stage 1 runs the cage as a bare 10GBASE-R link. That works with 10G optics,
DAC cables and 10GBASE-T SFP+ modules, but not with 1G modules or for
module information (`ethtool -m lan8`). Full support needs the I²C bus of
the cage and its MOD-DEF0 (module present) GPIO, ideally also LOS and
TX-DISABLE:

- **MOD-DEF0:** run `mokerlink-gpio-watch`, then insert and remove a module.
  The input that toggles is MOD-DEF0 (low = module present).
- **LOS:** with a module inserted, plug and unplug the fiber or the DAC peer.
- **I²C:** the stock firmware (board table `9303M7XC1XF`) reads the module
  EEPROM at `0x50` over a bit-banged bus: SCL on GPIO 8, SDA on GPIO 11.
  GPIO 8 is also the SCL line of the fan controller's bus, which the device
  tree already claims, and Linux's `i2c-gpio` cannot share one SCL GPIO
  between two buses. So the cage needs either the RTL9303's own I²C master
  for both devices (if its SCL and SDA lines are on GPIO 8, 9 and 11) or a
  driver that switches SDA lines on one bus.

Then replace the `port@27` node with an SFP description, following
`rtl9303_hasivo_f1100w-4sx-4xgt-common.dtsi` in OpenWrt:

```dts
/ {
	sfp0: sfp-p8 {
		compatible = "sff,sfp";
		i2c-bus = <&i2c_sfp0>;
		mod-def0-gpios = <&gpio0 N GPIO_ACTIVE_LOW>;  /* N from gpio-watch */
		#thermal-sensor-cells = <0>;
	};
};

&i2c_mst1 {
	status = "okay";
	i2c_sfp0: i2c@0 { reg = <0>; };
};

/* in ethernet-ports: */
SWITCH_PORT_SFP(27, 8, 9, 0, 0)
```

## Reset button

A wrong reset-button GPIO is dangerous: OpenWrt reboots on a short press and
**factory-resets on a press longer than 5 seconds**. A floating pin can
trigger either at random. So the button is left out until confirmed.

Run `mokerlink-gpio-watch` and press the button a few times. Note the GPIO
and its level while pressed (0 means active low). Then add:

```dts
#include <dt-bindings/input/input.h>
#include <dt-bindings/gpio/gpio.h>

/ {
	keys {
		compatible = "gpio-keys";

		button-reset {
			label = "reset";
			gpios = <&gpio0 N GPIO_ACTIVE_LOW>;
			linux,code = <KEY_RESTART>;
		};
	};
};
```

Use `GPIO_ACTIVE_HIGH` if it reads 1 while pressed. On supported RTL930x
boards it is usually GPIO 5 (active low) or GPIO 20.

## System LED

A GPIO LED is an output, and driving the wrong output can hold a PHY or the
PoE controller in reset. Only add it once the pin is known from the board
traces or the stock firmware's GPIO setup. The candidates are pins that
`mokerlink-probe` lists as outputs. On supported boards it is often GPIO 21
or GPIO 0.

## Port LEDs

Without a `realtek,rtl9300-leds` node, the port LEDs keep the boot loader's
configuration, which is usually right. To define them yourself, copy the
`led_set` node of a similar board (the Hasivo S1100W-8XGT-SE uses four LEDs
per port) and add `led-set = <0>` to the ports, using `SWITCH_PORT_LED`
instead of `SWITCH_PORT_SDS`. A wrong LED set only lights the wrong LEDs.

## PoE

**Not supported yet.** The board has two Realtek **RTL8239-VB** PSE chips,
and no PSE microcontroller in front of them. The stock firmware talks to the
chips themselves over `uart1`:

- its boot log prints `find RTL8239-VB` once per chip
- its PoE driver (`ski.ko`) has functions like `rtl8239_init_chip_reset`,
  `rtl8239_transmit_image_packages_boardcast` and
  `rtl8239_check_image_upgrade_pass`
- it carries RTL8239 firmware images, such as
  `pse_image_RTL8239_Bt_Dumb_mode_V0_B_bin`, and sends them to the chips at
  boot
- `show poe` in the stock CLI reads voltage, current and temperature per
  port straight from the chips

OpenWrt's `realtek-pse-mcu` driver is for boards where an MCU running
Realtek's PoE firmware sits in front of the PSE chips (Zyxel XMG1915-10EP,
the Ubiquiti PoE switches and others). Declared on this board's `uart1`, it
timed out (`failed to read MCU info`) because nothing here speaks that
protocol. The device tree therefore declares no PSE. It keeps `uart1`
enabled, muxed as the stock firmware uses it, so `/dev/ttyS1` is there for
the work below.

### Does PoE work without any driver?

Unknown, and it is the next thing to test. The names "Dumb mode" and
"image upgrade" suggest the RTL8239s may keep their firmware in their own
memory and power devices on their own, without any management. Test it
while running OpenWrt, from RAM or from flash, after a cold boot:

1. Plug a PoE device (camera, access point, phone) into one front port.
2. Note whether it powers up, and after how long.

- **It powers up:** PoE works without management. The chips decide power
  per port by themselves (classification, budget). There is no per-port
  control or readout in OpenWrt.
- **It stays dark:** the chips wait for the firmware that the stock firmware
  sends at every boot. PoE then needs a loader for OpenWrt that does what the
  stock driver does over `/dev/ttyS1`: reset the chips, send the firmware
  image and start it.

### A loader (if needed)

The firmware images belong to Realtek and can't be shipped in this
repository. A loader would take the image from the stock firmware you
already have (the `...en.bin` uImage from MokerLink, or the RUNTIME part of
`full-flash.bin`) at install time and keep it in the OpenWrt overlay. The
UART protocol (framing, chip addressing, the download and start sequence)
has to be worked out from the stock `ski.ko` and then checked on the switch
with a PoE device connected. That is a project of its own. Until it is
done, use the stock firmware if you need PoE.

## Fans and temperature

The fans are driven by an **ADT7476** fan controller at I²C address `0x2e`.
Its power-on default is full speed, which is why the fans run at full speed
in U-Boot. The stock firmware (board table `9303M7XC1XF` in `board_conf.ko`,
fan code in `ski.ko`) reaches it over a bit-banged I²C bus, SCL on GPIO 8
and SDA on GPIO 9, and:

1. starts monitoring (register `0x40` = `0x05`), puts all three PWM outputs
   in manual mode (`0x5c`-`0x5e` = `0xe2`) and pin 14 in GPIO mode (`0x7d` =
   `0x03`)
2. sets the fans to 50 % (duty `0x80` on all three PWM outputs)
3. from then on reads the chip's three temperature sensors (its own and two
   remote diodes) and takes the hottest: above 65 °C full speed (`0xff`),
   otherwise 50 %

OpenWrt does the same. The device tree declares the bus (`i2c-gpio`) and the
chip (`adt7475` driver), and the `mokerlink-fan` service sets manual mode
and switches between 50 % and full speed. It adds a 5 °C hysteresis, and
full speed while any 10G PHY is above 95 °C. When the service stops or the
switch reboots, the fans go back to full speed. The settings are in
`/etc/config/mokerlink`, section `fan`.

Check it on the switch:

```sh
mokerlink-fan status     # temp1-3, fan RPM, pwm1-3 = 128 (50 %), pwmN_enable = 1
logread -e mokerlink-fan # every speed change, with the temperatures
```

`no ADT7476 found` means the chip did not answer on the bus
(`dmesg | grep -i adt`); the fans then keep running at full speed.

Still to confirm on the switch: which PWM outputs and tach inputs have fans
(the stock firmware sets all three PWM outputs alike), and which remote
diodes are connected (`temp1_fault` or `temp3_fault` reads 1 for an open
one, and `mokerlink-fan` ignores it).

## A factory image for the stock web UI

The format is known now: the stock web upgrade accepts any uImage with
magic `0x83800000` and valid CRCs, with no signature, so the
`*-initramfs-kernel.bin` image installs from the stock web interface as it
is ([03-install.md](03-install.md#flashing-from-the-stock-web-ui)). A
separate `factory` image would only rename it; the MokerLink 2G080GM in
OpenWrt ships the same kind of plain uImage as `.bix`.

## Upstreaming

Once everything is verified, the device tree, the image recipe and the
`02_network` entry can be submitted to OpenWrt as a pull request against
`main`, following the commit format of the Hasivo and MokerLink commits
(device specification, flash layout, install steps). Then OpenWrt
maintains the board and it appears in official builds.
