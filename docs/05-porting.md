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

U-Boot prints `DRAM: <size>` at power-on. The device tree declares 128 MiB,
which is safe on any unit: declaring more RAM than fitted crashes the
kernel, while declaring less only leaves memory unused. For 512 MiB add,
inside `/ { ... }`:

```dts
memory@0 {
	device_type = "memory";
	reg = <0x00000000 0x10000000>, /* 256 MiB lowmem */
	      <0x20000000 0x10000000>; /* 256 MiB highmem */
};
```

For 256 MiB: `reg = <0x00000000 0x10000000>;`.

## Console speed

If U-Boot runs at 115200 rather than 38400, change `stdout-path` in
`chosen` to `"serial0:115200n8"`.

## Flash layout

Compare the partition table printed by the stock firmware during boot (or
its `/proc/mtd`) with the table in [01-hardware.md](01-hardware.md#flash-layout-assumed).
Also check the stock `bootcmd`: booting from `0xb4300000` confirms that the
firmware starts at offset `0x300000`. If the chip is larger than 16 MiB
(`mokerlink-backup` warns about it), the layout definitely differs. Copy
the stock table into the `partitions` node, keep everything except
`firmware` read-only, and set `IMAGE_SIZE` in the patch to the new firmware
partition size.

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

Signs that the PHY address or bus is wrong: `mokerlink-probe` shows no
driver for a port, the port is missing entirely, or `dmesg` shows PHY
errors for it.

Where to find the real wiring:

- **The stock boot log.** Realtek SDK firmware usually prints its port, PHY
  and SerDes table while initializing the switch. Look for lines
  mentioning `RTL8264`, `RTL8261`, `SMI`, `SDS` or `serdes`, and for a
  board/hardware-profile name.
- **Board photos.** The PHY chip positions show which ports share a quad
  PHY: RTL8264(B) is a quad, RTL8261 a single.
- **Known layouts** to try, from supported boards:

  | Board | Ports 1-4 | Ports 5-8 |
  |---|---|---|
  | Hasivo S1100W-8XGT-SE (assumed here) | bus 0, addr 0-3 | bus 3, addr 16-19 |
  | Hasivo F1100W-4SX-4XGT (singles) | SFP | bus 2 addr 0, bus 3 addr 16, bus 0 addr 16, bus 1 addr 0 |
  | MokerLink 2G080GM (2.5G) | bus 0, addr 1-4 | bus 1, addr 1-4 |

If the SFP+ cage is not the last port, swap its port node with the
copper port that is really on SerDes 9.

## SerDes lane polarity

The RTL826x PHY driver programs the lane polarity between switch chip and
PHY from the device tree on every boot. A wrong value makes a port show
link but pass no frames in one or both directions. With a host at
`192.168.2.100` on the port under test:

```sh
mokerlink-polarity-scan lan2 192.168.2.100
```

It tries all four combinations at runtime and prints the line to use, e.g.
`&phy8 { rx-polarity = <PHY_POL_INVERT>; };`. Replace the line for that
PHY in the device tree. "Normal" needs no line.

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

Stage 1 does not talk to the PoE hardware. What happens depends on the
design:

- **Autonomous PoE controller** (powers ports on its own after detection):
  PoE works under OpenWrt as it does on stock, just without per-port
  control. Check with one PoE device.
- **Hasivo design** (HS104 PSE chips and an STC8 MCU on a bit-banged I²C bus
  on GPIO 22/23): OpenWrt has drivers. If the photos show HS104 chips,
  copy the `i2c_scl23_sda22` node from OpenWrt's
  `rtl9303_hasivo_s1100wp-8xgt-se.dts`, adjust the PSE channels to 7
  ports, and add `kmod-pse-hasivo-hs104 kmod-mfd-hasivo-stc8` to
  `DEVICE_PACKAGES` in the patch.
- **Realtek PSE MCU** (RTL8238/RTL8239 behind a microcontroller on a UART):
  supported in OpenWrt by `realtek-poe`. This needs the UART and the MCU
  protocol version.

Identify the chips first (photos). Only scan an I²C bus (`i2cdetect -r`)
once its pins are known, because a scan toggles them.

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
