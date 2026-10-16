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

If U-Boot runs at 115200 rather than 38400, change `stdout-path` in
`chosen` to `"serial0:115200n8"`.

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

On this board ports 1-4 sit on one RTL8264B quad PHY and ports 5-7 on three
discrete RTL8261BE PHYs (vendor hardware profile
`RTL9303_8264B_3X8261BE_XGE_POE`). Each discrete PHY has its own strapped
address, possibly on a different SMI bus.

Signs that the PHY address or bus is wrong: `mokerlink-probe` shows no
driver for a port, the port is missing entirely, or `dmesg` shows PHY
errors for it.

Where to find the real wiring:

- **U-Boot (best).** [Read the port wiring](02-serial-console.md#read-the-port-wiring)
  and decode the log with `scripts/uboot-wiring.py decode`. It prints the
  `&mdio_busN` lines and the polarity lines ready to paste.
- **The stock boot log.** Realtek SDK firmware usually prints its port, PHY
  and SerDes table while initializing the switch. Look for lines
  mentioning `RTL8264`, `RTL8261`, `SMI`, `SDS` or `serdes`, and for a
  board/hardware-profile name.
- **Board photos.** The PHY chip positions show which ports share a quad
  PHY: RTL8264(B) is a quad, RTL8261 a single.
- **Known layouts** to try, from supported boards:

  | Board | Ports 1-4 | Ports 5-8 |
  |---|---|---|
  | Hasivo S1100W-8XGT-SE, 2× RTL8264 (assumed here) | bus 0, addr 0-3 | bus 3, addr 16-19 |
  | XikeStor SKS8300-8T, 8× RTL8261N | bus 0, addr 0-3 | bus 1, addr 0-3 |
  | Ubiquiti USW Pro XG 8 PoE, 2× RTL8264B (RTL9313) | bus 0, addr 0-3 | bus 0, addr 4-7 |
  | Hasivo F1100W-4SX-4XGT (singles) | SFP | bus 2 addr 0, bus 3 addr 16, bus 0 addr 16, bus 1 addr 0 |
  | MokerLink 2G080GM (2.5G) | bus 0, addr 1-4 | bus 1, addr 1-4 |
  | MokerLink 10GT080M, 8× RTL8261BE (RTL9313) | bus 0, addr 0-3 | bus 1, addr 4-7 (assumed here for ports 5-7) |

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
directly. To confirm them, or find them when U-Boot did not set them, run
the scan from a RAM-booted OpenWrt with a host at `192.168.2.100` on the
port under test:

```sh
mokerlink-polarity-scan lan2 192.168.2.100
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
- **I²C:** the RTL9303 has two I²C masters. Most boards wire the SFP cages
  to `i2c_mst1` channels, as on the Hasivo F1100W. Confirm from the stock
  boot log or the board traces.

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

(add `#include <dt-bindings/gpio/gpio.h>` at the top).

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

The board has two Realtek **RTL8239-VB** PSE chips. They are not driven by
the SoC directly but by a small microcontroller (the "PSE MCU") running
Realtek's PoE firmware. OpenWrt supports it with the `realtek-pse-mcu`
driver (headed for Linux 7.3), which handles the RTL8239 with the "gen2"
protocol over I²C or UART.

The stock firmware's driver registers a UART and no I²C device, which
matches the Zyxel XMG1915-10EP: an RTL930x switch with its RTL8239 MCU on
`uart1` at 115200 baud. The device tree therefore declares the MCU there
(`kmod-pse-realtek-mcu-uart`). After a RAM boot, check:

```sh
dmesg | grep -i -e pse -e rtl8239
ethtool --show-pse lan1
```

- **The driver found the MCU:** it reports the RTL8239 chips, enables the
  PSE system and creates a PSE port per front port. Plug one PoE device
  into port 1 and check that `ethtool --show-pse lan1` shows it delivering
  power. If another port reports it, the PI order in the device tree
  (`pses = <&pse_piN>`) needs reordering. Ports can be switched with
  `ethtool --set-pse lanN c33-pse-admin-control disable` (or `enable`).
- **No answer from the MCU:** the driver logs an error and does nothing
  else. Try `current-speed = <19200>` (the driver's default), then I²C:
  on all supported I²C-attached RTL8239 boards the MCU is at `0x20`:

| Board | Bus | Address | Compatible |
|---|---|---|---|
| Zyxel XMG1915-10EP | `uart1`, 115200 baud | - | `realtek,pse-mcu-gen2` |
| Ubiquiti USW Pro XG 8 PoE | `i2c_mst2` channel 6 | `0x20` | `realtek,pse-mcu-gen2-smbus` |
| Ubiquiti USW Pro Max 24 PoE | `i2c_mst2` channel 0 | `0x20` | `realtek,pse-mcu-gen2-smbus` |
| Zyxel XS1930-12HP | `i2c_mst1` channel 3 | `0x20` | `realtek,pse-mcu-gen2-smbus` |
| Linksys LGS328MPC v2 | bit-banged I²C, SDA GPIO 9, SCL GPIO 17 | `0x2a` | `realtek,pse-mcu-gen2-i2c` |

To find an I²C channel, if it comes to that:

1. **Photos:** identify the MCU next to the RTL8239s and look up its pinout.
   Its I²C (or UART) pins lead towards the SoC.
2. **Stock boot log:** look for lines mentioning PoE, PSE, I2C or the MCU.
3. **Trial in a RAM boot**, once the photos rule out a conflict with other
   pins: declare the MCU on a candidate channel. The driver reports the
   MCU and its RTL8239 chips in `dmesg` if the guess is right, and fails
   harmlessly if not. Don't scan with `i2cdetect` first: a scan toggles
   every address on the bus.

An I²C variant, with `i2c_mst2` channel 6 as an example (remove the `&uart1` node first):

```dts
&i2c_mst2 {
	status = "okay";

	i2c_poe: i2c@6 {
		reg = <6>;
		#address-cells = <1>;
		#size-cells = <0>;

		pse: ethernet-pse@20 {
			compatible = "mokerlink,poe-10g07110gsm-pse", "realtek,pse-mcu-gen2-smbus";
			reg = <0x20>;

			pse-pis {
				#address-cells = <1>;
				#size-cells = <0>;

				PSE_PI(0)
				PSE_PI(1)
				PSE_PI(2)
				PSE_PI(3)
				PSE_PI(4)
				PSE_PI(5)
				PSE_PI(6)
			};
		};
	};
};

&phy0 { pses = <&pse_pi0>; };
&phy8 { pses = <&pse_pi1>; };
&phy16 { pses = <&pse_pi2>; };
&phy20 { pses = <&pse_pi3>; };
&phy24 { pses = <&pse_pi4>; };
&phy25 { pses = <&pse_pi5>; };
&phy26 { pses = <&pse_pi6>; };
```

Then replace `kmod-pse-realtek-mcu-uart` with `kmod-pse-realtek-mcu-i2c` in
`DEVICE_PACKAGES` in the patch.

## Fans and temperature

If the fans are run by a separate MCU, nothing is needed. If they turn out to
be SoC-controlled (they stop or behave differently under OpenWrt), stop
running the switch under load and report it. `mokerlink-probe` prints the
PHY temperatures (RTL826x sensors) and the SoC temperature.

## A factory image for the stock web UI

Later, once the stock firmware's upgrade file format is known (from a
MokerLink firmware file; support@mokerlink.com sends them on request), a
`factory` image can be added so that new users can install from the stock
web interface without a serial console. The MokerLink 2G080GM in OpenWrt
does this with a plain uImage named `.bix`.

## Upstreaming

Once everything is verified, the device tree, the image recipe and the
`02_network` entry can be submitted to OpenWrt as a pull request against
`main`, following the commit format of the Hasivo and MokerLink commits
(device specification, flash layout, install steps). Then OpenWrt
maintains the board and it appears in official builds.
