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
The PoE LEDs are driven in software instead: see [PoE LEDs](#poe-leds).

## PoE

**Works with the Realtek firmware installed**, tested on ports 1 and 5
(one on each chip) with an 802.3bt access point (class 7) and an 802.3at
camera (class 4): detection, classification, power, measurements,
switching ports off and on with ethtool, and the green PoE LEDs. A device
without PoE is left unpowered. Without the firmware the chips detect
nothing and power nothing.

The board has two Realtek **RTL8239-VB** PSE chips and no PSE
microcontroller. Everything below comes from the stock firmware (board
tables in `board_conf.ko`, PoE code in `ski.ko`) and the tests; there is no
RTL8239 datasheet.

**Wiring.** Both chips are on the bit-banged I²C bus with SDA on GPIO 10
(SCL GPIO 8, shared with the fan and SFP+ buses). Each answers on two
addresses, one per 4-channel half: the first chip on `0x20`/`0x21`, the
second on `0x22`/`0x23`. All RTL8239s also listen on the broadcast address
`0x60` for the firmware download. Every front port is 4-pair (802.3bt) on
two consecutive channels:

| Front port | Chip | Address | Channels |
|---|---|---|---|
| 1 | second | `0x22` | 0+1 |
| 2 | second | `0x22` | 2+3 |
| 3 | second | `0x23` | 0+1 (chip channels 4+5) |
| 4 | first | `0x20` | 0+1 |
| 5 | first | `0x20` | 2+3 |
| 6 | first | `0x21` | 0+1 (chip channels 4+5) |
| 7 | first | `0x21` | 2+3 (chip channels 6+7) |

**How the stock firmware runs them.** The registers follow TI's TPS2388x.
At every boot it resets each half (`0x1a` = `0x20`, all channels off), puts
the chips in download mode (`0xec` = `0x03`) and sends a 25460-byte Realtek
firmware image ("BT Dumb mode") to `0x60`, register `0xf4`, in one write.
A port is switched on by putting both its channels in auto mode (two bits
each in `0x12`) and off by clearing them; detection, classification, 4-pair
operation and the power budget are left to the chip firmware. Every 30 s
it checks bit 5 of `0xec` (firmware running) and reloads the image if it
dropped. Status comes from `0x10` (power enable and power good), `0x0c` +
channel (class), `0x30`/`0x32` + 4 × channel (current, voltage) and `0x2c`
(temperature). The tests showed the event registers (`0x00`-`0x0b`) behave
like the TPS2388x ones too, and `0x2e`/`0x2f` read the 54 V supply.

**In OpenWrt** the `realtek-rtl8239` driver does the same through the
Linux PSE framework:

- all ports are on at boot, like the stock firmware's default; the chips
  power only valid PoE devices
- `ethtool --show-pse lanN` shows the state, class, voltage and power,
  `ethtool --set-pse lanN c33-pse-admin-control disable|enable` switches a
  port
- the firmware image is loaded at probe if it is installed, and looked for
  again every 30 s otherwise; the driver also reloads it if the chip stops
  running it

### PoE LEDs

The stock firmware lights LED 2 of each RJ45 port (the third LED of the
port in the switch's serial LED stream) while the port delivers power. It
takes that LED under software control of the RTL9303 LED controller, which
OpenWrt's switch driver doesn't do, so the LED stayed dark in round 1. The
new driver does the same, and round 2 confirmed it. The
registers, from the vendor SDK's register table (`rtk.ko`):

| Register | Offset | Use |
|---|---|---|
| `SW_LED_LOAD` | `0xcc48` | bit 0: apply the software LED modes |
| `LED_PORT_SW_EN_CTRL` | `0xcc4c` + port / 8 × 4 | 4 bits per port: LED under software control |
| `LED_PORT_SW_CTRL` | `0xcc5c` + port × 4 | 3-bit mode per LED, copper from bit 0, fibre from bit 12: 0 off, 7 on, 1-6 blink |

The new `leds-rtl9300-port` driver exposes such LEDs as LED class devices
(`/sys/class/leds/green:poe-1` to `-7`), and the device tree binds each to
the PSE core's `pse-1-0022:port0:delivering`-style trigger, as the Hasivo
S600WP does with its PoE LEDs. I²C aliases keep the bus number in those
names fixed.

### The RTL8239 firmware

The image belongs to Realtek and can't be shipped in this repository or in
OpenWrt. Copy it out of the stock firmware you have, on the PC:

```sh
python3 scripts/extract-poe-firmware.py POE-10G07110GSM_F11134_0220_en.bin
# or from the flash backup:
python3 scripts/extract-poe-firmware.py full-flash.bin
```

It writes `rtl8239-bt.bin` only if the image is byte-identical to the known
one (SHA-256). Install it on the switch as
`/lib/firmware/realtek/rtl8239-bt.bin` (it stays in the overlay across
reboots once OpenWrt runs from flash). PoE needs it.

### Testing

With a PoE device, optionally a device without PoE (a laptop), and the
switch RAM-booted:

```sh
# on the PC
scp -O rtl8239-bt.bin root@192.168.2.1:/tmp/
# on the switch, serial console or SSH
mokerlink-poe-test
```

The script installs the firmware and waits for the driver to load it,
records port 1 and then port 5 with the PoE device once a second (link,
PSE state, LED, chip registers), asks what the LEDs show and switches the
port off and on. If the PoE LED stays dark it lights the LEDs of port 1 one
by one, and it optionally records port 1 with a device without PoE, at its
normal speed and then at 100 Mbps. Power-cycle the switch afterwards.

Results:

- round 1: fans at 50 % (4500-4800 rpm, fan 3 not fitted); no PoE without
  the firmware; with it, both chips load it in about 3 s each and power an
  802.3bt PD (Ruckus T750SE, class 7, 5-10 W) within seconds; ethtool
  disable/enable works
- round 1: the RTL9303 I²C master 1 is wired to these buses: with
  `mokerlink-i2c-master-test`, SDA0 reaches the ADT7476 and SDA1 the
  RTL8239s, so GPIO 8 is its SCL
- round 2: the RTL8239s take the firmware only as one write. Sent in
  16-byte writes, the most I²C master 1 moves per transfer, the chips
  accept the first and NAK the second. So the PoE bus has to stay
  bit-banged, and since the three buses share SCL, the fan and SFP+ buses
  stay on `i2c-gpio-shared-scl` too
- round 2: an Axis camera (class 4) on ports 1 and 5 is powered within
  seconds and passes traffic at 1 Gbit/s. The chips power both pairsets
  for it (about 64 + 54 mA at 52.7 V, 6-7 W)
- round 2: LED 2 of each port is the green PoE LED, as in the stock
  firmware; it lights while the port delivers and goes off when the port
  is switched off or the device unplugged. LEDs 0 and 1 are the port's
  link LED (orange and green), LED 3 is not fitted. The boot loader sets
  up three LEDs per port, which the driver relies on
- round 2: a device without PoE links at 1 Gbit/s and at 100 Mbit/s and
  stays up, and is not powered. The T750SE's link drops in round 1
  (100 Mbps, down twice and back 25 s later, over and over) were the
  access point's own behaviour while it starts up

Still open:

- right after plug-in, every device's link went down and up 2-4 times in
  the first 10 s before staying up, on both PHY types and with or without
  PoE. It settles by itself; it looks like the 10G PHYs' link-up
  behaviour rather than PoE, and is not investigated yet
- which channel of a pair is alternative A and which B

### Upstreaming

The kernel parts are written for upstream: the drivers
(`drivers/net/pse-pd/realtek-rtl8239.c`,
`drivers/i2c/busses/i2c-gpio-shared-scl.c`,
`drivers/leds/leds-rtl9300-port.c`) and their bindings in
`openwrt/target/linux/realtek/files-6.18`, with the Kconfig/Makefile hooks
in `patches-6.18/830`-`832`. The shared-SCL bus driver is needed rather
than the upstream `i2c-rtl9300` on the same pins: the RTL8239s refuse their
firmware in the 16-byte writes that controller is limited to (round 2).
Two binding patches carried here belong upstream too:

- `833`: lets the upstream RTL9301 switch binding have the port LED
  controller as a child, like its `i2c@36c` and `mdio-controller@ca00`.
  This is why the LED node follows the upstream conventions for those
  children: a `realtek,rtl9301-*` compatible and a `reg` with its
  register offset (`led-controller@cc48`). It goes upstream with the LED
  driver and its binding.
- `834`: accepts the PSE core's LED trigger names
  (`pse-<controller>:port<n>:delivering|enabled|fault`) in
  `linux,default-trigger`. The triggers themselves are still an OpenWrt
  pending series (`897-02`, `897-03`), so this belongs with that series.
  Expect the device tree maintainers to object to names that contain a
  Linux I²C bus number, and to ask for `trigger-sources = <&pse_port1>`
  instead. That needs the trigger series to match LEDs by
  `trigger-sources`, so the series author would have to agree.

All bindings pass `make dt_binding_check`, and all three drivers pass
`checkpatch.pl --strict`. With 833 and 834, the board's PSE, I²C and LED
nodes validate against them. The switch node itself still fails, but only
because OpenWrt's `rtl930x.dtsi` differs from the upstream switch binding
(`ethernet`, `pcs`, `thermal` and other children). Before submitting, the
submitter replaces the placeholder authorship (copyright lines,
`maintainers` in the bindings, patch `From:`) with their own and adds a
`Signed-off-by`. The firmware stays out of tree.

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
