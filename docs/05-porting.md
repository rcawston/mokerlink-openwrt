# Board support

The board description is
[`openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts`](../openwrt/target/linux/realtek/dts/rtl9303_mokerlink_poe-10g07110gsm.dts),
the image recipe is in `patches/0001-*.patch`, and the new kernel drivers
are in `openwrt/target/linux/realtek/files-6.18`. This page explains how
each part of the switch is supported, where the values come from, and how
to check them on another unit or hardware revision.

## Making a change

1. Edit the device tree (or the image recipe in `patches/`).
2. Run `scripts/check-dts.sh`. It checks that the board patches apply to
   the pinned OpenWrt, compiles the device tree, and checks that the flash
   layout, image size and uImage magic agree.
3. Build: push to GitHub and download the artifact, or run
   `scripts/build.sh`.
4. RAM-boot the new image ([03-install.md, step 1](03-install.md#1-boot-openwrt-from-ram))
   and run `mokerlink-probe`.

## Memory, console and flash

RAM (128 MiB) and the console (115200 8N1) are the `rtl930x.dtsi`
defaults. Declaring more RAM than is fitted crashes the kernel.

The `partitions` node copies the partition table the stock firmware prints
at boot ([01-hardware.md](01-hardware.md#flash-layout)). Everything except
`firmware` is read-only, and `IMAGE_SIZE` in the image recipe equals the
firmware partition size. A hardware revision with another table needs both
changed.

The image is built with the uImage magic `0x83800000`, which the stock
boot loader and web upgrader expect. A unit whose U-Boot shows
`27 05 19 56` at `md.b b4300000 4` would need `0x27051956` in both places:

- `openwrt,ih-magic` in the device tree (`firmware` partition)
- `UIMAGE_MAGIC` in `patches/0001-*.patch` (change only the value on the
  `+  UIMAGE_MAGIC := ...` line)

`check-dts.sh` fails if the two disagree.

## PHYs and SerDes

On RTL9303 each 10G MAC port has a fixed SerDes lane:

| MAC port | 0 | 8 | 16 | 20 | 24 | 25 | 26 | 27 |
|---|---|---|---|---|---|---|---|---|
| SerDes | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |

A copper port is declared with two macros:

```dts
&mdio_bus0 { PHY_C45(0, 0) };                    /* PHY for MAC port 0: SMI bus 0, address 0 */
SWITCH_PORT_SDS(0, 1, 2, 0, usxgmii)             /* MAC port 0 = lan1, SerDes 2 */
```

The PHY map comes from the vendor U-Boot (its SMI registers after
`rtk network on`, [02-serial-console.md](02-serial-console.md#read-the-port-wiring)):

| Front port | MAC port | SMI bus | Address | PHY |
|---|---|---|---|---|
| 1-3 | 0, 8, 16 | 1, 2, 3 | 0 | RTL8261BE (ID `001c:caf3`) |
| 4-7 | 20, 24, 25, 26 | 0 | 0, 1, 2, 3 | RTL8264B quad |
| 8 (SFP+) | 27 | - | - | none |

Front ports 1-7 are `lan1`-`lan7`. Signs of a wrong PHY address or bus:
`mokerlink-probe` shows no driver for a port, the port is missing, or
`dmesg` shows PHY errors for it. For another board revision, read the
wiring from U-Boot the same way and decode it with
`scripts/uboot-wiring.py decode`, which prints the `&mdio_busN` and
polarity lines ready to paste.

## SerDes lane polarity

The lane polarity between switch chip and PHY can be set on either end: on
the PHY (`&phyN { tx-polarity/rx-polarity }`, RTL826x driver) or on the
switch SerDes (`&serdesN { ... }`, PCS driver). Both drivers program it
from the device tree on every boot and default to "normal". A wrong value
makes a port show link but pass no frames in one or both directions.

The vendor U-Boot programs the correct values, so the
[U-Boot wiring read](02-serial-console.md#read-the-port-wiring) gives them
directly. On this board the RTL8261BE links are normal, and each RTL8264B
link is inverted on the PHY in one direction and on the SerDes in the
other. The device tree carries these values.

When U-Boot didn't set the polarity (another revision, or a port that
shows link but no traffic), find it from a RAM-booted OpenWrt with a host
at `192.168.2.254` on the port under test:

```sh
mokerlink-polarity-scan lan2 192.168.2.254
```

It tries all four combinations on the PHY side at runtime and prints the
line to use, e.g. `&phy8 { rx-polarity = <PHY_POL_INVERT>; };`. A
mismatch on a differential lane can be corrected at either end, so this
works whichever side the board needs it on. "Normal" needs no line.

## Link changes

The switch raises an interrupt when a port's MAC link changes. For a
port with a PHY, OpenWrt's switch driver used to pass that to phylink,
which took the link down and straight back up from the PHY state of the
last poll (the PHYs are polled once a second), so every link loss was
logged as down, up 14 ms later, and down again within a second.
`patches/0003-*.patch` makes the driver let phylib read the PHY instead,
which also reports a new link without waiting for the poll.

With that out of the way, a cable plugged in from a device that is
already up gives one clean link-up: the PHY completes autonegotiation,
then reports link, and the kernel follows. Devices that start up when
plugged in, PoE devices in particular, take their own link down and up
while they boot (a boot loader that links with flow control, then the
operating system that links again); that is the device, not the port.

## SFP+ cage

`lan8` (MAC port 27, SerDes 9) has an `sff,sfp` node: the module EEPROM is
on the bit-banged bus with SDA GPIO 11, and the cage signals are SoC GPIOs,
as the stock firmware registers them for this board (`board_gpio_init` in
`board_conf.ko`, the branch for hardware profile `0x8de847`):

| Signal | Stock name | GPIO | Level |
|---|---|---|---|
| module present (MOD-DEF0) | `MDO_FX_0` | 13 | low = module in |
| loss of signal | `LOS_FX_0` | 12 | high = no signal |
| transmitter disable | `TX_DIS_FX_0` | 14 | high = off |

There is no transmitter fault line. With the cage described, phylink reads
the module and switches SerDes 9 to match it: 10GBASE-R for a 10G module,
SGMII with in-band status for a 1G copper module (whose PHY, a Marvell
88E1111 on the tested one, it drives over the module's I²C), 1000BASE-X
for a 1G fibre module. Modules can be swapped while the switch runs, and
`ethtool -m lan8` shows the module EEPROM, with diagnostics for modules
that have them.

## Reset button

SoC GPIO 5, active low, from the stock firmware (`buttonConfSys_9303M5XC2XF`
and `resetGpio5` in `board_conf.ko`, which this board's `buttonConf`
uses). The device tree declares it as `KEY_RESTART`, so OpenWrt's button
handler applies: a press shorter than 1 s reboots, one longer than 5 s
resets to defaults. The stock firmware factory-resets on a press of 5 s or
longer too, and ignores the button if it reads pressed at boot.
`mokerlink-gpio-watch` shows the GPIO level live.

## LEDs

The system LED works as the boot loader leaves it, so the device tree has
no LED for it. A GPIO LED is an output, and driving the wrong output can
hold a PHY or the PoE controller in reset, so OpenWrt's boot and upgrade
blinking would need the pin identified first.

Each RJ45 port has three LEDs: LED 0 and LED 1 are the orange and green
halves of the link LED, LED 2 is the green PoE LED. The vendor SDK sets
the LED controller up: in U-Boot as part of `rtk network on`, which the
RAM boot runs, and in the stock firmware in its switch driver. OpenWrt
booted from flash by `boota` gets neither, so the device tree's
`realtek,rtl9300-leds` node does it, with the values the vendor SDK writes
for this board's hardware profile (`rtl9300_led_config` in `rtk.ko`, from
the profile that `_hwp_led_parse` in `rtcore.ko` reads):

| Setting | Value |
|---|---|
| LED mode (`LED_GLB_CTRL` bits 1:0) | 3, bi-colour scan (`realtek,led-mode`) |
| LED polarity (`LED_GLB_CTRL` bit 22) | 1 (no `active-low`) |
| LEDs per port | 3, on ports 0, 8, 16, 20, 24-26 and the SFP+ port 27 |
| LED set 0, copper ports | LED 0 `0x0aaa`: 100M/1G/2.5G/5G link and activity; LED 1 `0x0a01`: 10G link and activity; LED 2 none |
| LED set 1, SFP+ port | LED 0 `0x0aa8`: 100M/1G/2.5G link and activity; LED 1 `0x0a01`: 10G; LED 2 none |

The `led_setN` cells list the LEDs from the last to the first, as
OpenWrt's switch driver writes them. OpenWrt's driver also puts every port
in the copper, fibre and combo port masks and selects the port's set for
both media, where the SDK puts the copper ports only in the copper mask
and port 27 only in the fibre mask (all in the combo mask) and selects a
set only for the port's own medium; with one set per port that selects the
same LEDs. Booted from flash with this, the registers read back as above,
a 1 Gbit/s link lights its link LED orange, and the PoE LEDs follow the
power of their ports. The PoE LEDs are driven in software: see
[PoE LEDs](#poe-leds).

## PoE

The board has two Realtek **RTL8239-VB** PSE chips and no PSE
microcontroller. Everything below comes from the stock firmware (board
tables in `board_conf.ko`, PoE code in `ski.ko`) and tests on the switch;
there is no RTL8239 datasheet.

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
operation and power limits are left to the chip firmware. Every 30 s it
checks bit 5 of `0xec` (firmware running) and reloads the image if it
dropped.

The operating mode has two bits per channel in `0x12`: `00` off, `10`
semi-automatic, `11` auto. The stock firmware uses only off and auto. In
semi-automatic mode the chip detects and classifies a PD but doesn't power
it until the host writes `0x19` (bits 3:0 power a channel on, bits 7:4
switch it off), exactly as on the TPS2388x.

Status registers:

- `0x10`: power enable and power good per channel
- `0x0c` + channel: class in bits 7:4, 1-8 for classes 1-8 and `0xc` for
  class 0; detection result in bits 3:0, `4` for a valid PD
- `0x30`/`0x32` + 4 × channel: current (125/4096 mA per step) and voltage
  (1/512 V per step)
- `0x2c`: temperature; `0x2e`/`0x2f`: the 54 V supply
- `0x00`-`0x0b`: events, as on the TPS2388x

**Behaviour.** Without the firmware the chips detect and power nothing.
With it, each chip loads the image in about 3 s, and a valid PD gets power
within seconds of plug-in. Both pairsets are powered even for a class 4
device (an 802.3at camera drew about 64 + 54 mA at 52.7 V). A device
without PoE is not powered and links normally. PDs take their own link
down and up while they boot (see [Link changes](#link-changes)); an 802.3bt
access point went down for about 25 s at a time, over and over, while it
started.

**Why the PoE bus is bit-banged.** The three buses' pins are also the
RTL9303's I²C master 1 (SCL GPIO 8, SDA0-SDA2 on GPIOs 9-11), and the
master does reach the ADT7476 and the RTL8239s. But it moves at most 16
bytes per transfer, and the RTL8239s take their firmware only as one
write: sent in 16-byte writes, they accept the first and NAK the second.
So the PoE bus is bit-banged, and since the three buses share SCL, the fan
and SFP+ buses are too: three `i2c-gpio` buses with the same SCL GPIO,
which `i2c-gpio` has handled since Linux 7.3 (OpenWrt's
`patches-6.18/033-02-v7.3-*`). It requests SCL once and serializes the
transfers of the three buses.

**In OpenWrt** the `realtek-rtl8239` driver runs the chips through the
Linux PSE framework, with a power budget:

- the device tree gives the PoE ports a supply of 260 W, the datasheet's
  total, and the PSE core keeps that budget with the static strategy: it
  allots each PD the power of its class (4 W for class 1 up to 90 W for
  class 8, 15.4 W for class 0) and powers a port only while that fits.
  When a PD doesn't fit, ports of lower priority (higher number) on the
  same chip are switched off for it. A port left waiting is powered when
  a port on the same chip frees power, or when it is switched on again
- for that the channels wait in semi-automatic mode. When the chip has
  classified a PD, the driver reports it, and the core switches the port
  on by putting it in auto mode, where the chip powers the PD and removes
  the power when it goes away, as in the stock firmware. A port that
  loses power goes back to waiting
- the ports start switched off; netifd switches them on with the `pse`
  device option. board.json gives that option a default of on for
  lan1-lan7, which netifd reads with this repository's patch to it
  (`openwrt/package/network/config/netifd/patches/`)
- `ethtool --show-pse lanN` shows the state, class, voltage, power and
  priority, `ethtool --set-pse lanN c33-pse-admin-control disable|enable`
  switches a port and `ethtool --set-pse lanN prio N` sets its priority
  (0 is the highest); `/sys/class/regulator/regulator.N/power_requested_milliwatt`
  of the `poe` regulator is the power allotted
- the firmware image is loaded at probe if it is installed, and looked for
  again every 30 s otherwise; the driver also reloads it if a chip stops
  running it

Without the supply in the device tree, the driver runs the chips as the
stock firmware does: all ports in auto mode from probe, and the chips
power any valid PD without a budget.

### PoE LEDs

The stock firmware lights LED 2 of each RJ45 port while the port delivers
power, by taking that LED under software control of the RTL9303 LED
controller. The registers, from the vendor SDK's register table
(`rtk.ko`):

| Register | Offset | Use |
|---|---|---|
| `SW_LED_LOAD` | `0xcc48` | bit 0: apply the software LED modes |
| `LED_PORT_SW_EN_CTRL` | `0xcc4c` + port / 8 × 4 | 4 bits per port: LED under software control |
| `LED_PORT_SW_CTRL` | `0xcc5c` + port × 4 | 3-bit mode per LED, copper from bit 0, fibre from bit 12: 0 off, 7 on, 1-6 blink |

OpenWrt's switch driver doesn't use them, so the `leds-rtl9300-port`
driver exposes such LEDs as LED class devices (`/sys/class/leds/green:poe-1`
to `-7`). The device tree binds each to the PSE core's
`pse-1-0022:port0:delivering`-style trigger, as the Hasivo S600WP does with
its PoE LEDs; I²C aliases keep the bus number in those names fixed. The
LED lights while the port delivers and goes off when the port is switched
off or the device unplugged.

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
`/lib/firmware/realtek/rtl8239-bt.bin`; the driver loads it within 30 s.
Once OpenWrt runs from flash it stays in the overlay across reboots, and
`sysupgrade` keeps it (`/lib/upgrade/keep.d/pse-realtek-rtl8239`, from
the driver's package), but a `sysupgrade -n` or a factory reset removes
it.

The chips stay powered through a reboot and keep running the firmware they
were given, so after a `reboot` or a `sysupgrade` PoE works even when the
file is missing; only a power cycle stops them. The driver warns at boot
when it finds a chip running a firmware without the file installed
(`firmware still running from before the reboot: install ...`). To check:

```sh
ls -l /lib/firmware/realtek/rtl8239-bt.bin
sysupgrade -l | grep rtl8239      # kept by the next sysupgrade
dmesg | grep rtl8239              # "firmware running" once per chip at boot
```

### Checking PoE

With a PoE device, optionally a device without PoE (a laptop), and the
switch RAM-booted:

```sh
# on the PC
scp -O rtl8239-bt.bin root@192.168.2.1:/tmp/
# on the switch, serial console or SSH
mokerlink-poe-test
```

The script installs the firmware and waits for the driver to load it,
records port 1 and then port 5 (one port on each chip) with the PoE device
once a second (link, PSE state, LED, chip registers), asks what the LEDs
show and switches the port off and on. If the PoE LED stays dark it lights
the LEDs of port 1 one by one, and it optionally records port 1 with a
device without PoE, at its normal speed and then at 100 Mbps. Power-cycle
the switch afterwards.

## Fans and temperature

The fans are driven by an **ADT7476** fan controller at I²C address `0x2e`,
on the bit-banged bus with SDA GPIO 9. Its power-on default is full speed,
which is why the fans run at full speed in U-Boot. Two fans are fitted, on
tach inputs 1 and 2; all three temperature sensors (the chip's own and two
remote diodes) are connected. The stock firmware (board table
`9303M7XC1XF` in `board_conf.ko`, fan code in `ski.ko`):

1. starts monitoring (register `0x40` = `0x05`), puts all three PWM outputs
   in manual mode (`0x5c`-`0x5e` = `0xe2`) and pin 14 in GPIO mode (`0x7d` =
   `0x03`)
2. sets the fans to 50 % (duty `0x80` on all three PWM outputs)
3. from then on reads the three temperature sensors and takes the hottest:
   above 65 °C full speed (`0xff`), otherwise 50 %

OpenWrt has the chip do this by itself. The device tree declares the bus
and the chip (`adt7475` driver), and at boot the realtek target's
`/etc/init.d/hwmon_fancontrol` puts the ADT7476 into its automatic mode,
through the driver's sysfs files:

- all three PWM outputs follow the hottest of the three sensors
  (`pwmN_auto_channels_temp` = 7)
- 50 % up to 64 °C (`pwmN_auto_point1_pwm` = 128,
  `tempN_auto_point1_temp` = 64000), rising to full speed at 66 °C
  (`tempN_auto_point2_temp` = 66000, the chip's smallest range of 2 °C)
- 50 % rather than off below 64 °C (`pwmN_stall_disable` = 1; the chip's
  default would stop the fans there)

That is the stock behaviour, with a 2 °C ramp in place of the step at
65 °C. No daemon is involved: the chip keeps controlling the fans if the
software hangs, and across a reboot until it loses power. Without this
setup the fans run at full speed.

```sh
mokerlink-fan    # temperatures, fan RPM, pwm1-3 (128 = 50 %), pwmN_enable = 2 (automatic)
```

`no ADT7476 found` means the chip did not answer on the bus
(`dmesg | grep -i adt`); the fans then keep running at full speed.

## Images

The stock web upgrade accepts any uImage with magic `0x83800000` and valid
CRCs, with no signature, and writes the whole file to the firmware
partition. The build makes two images for it
([03-install.md](03-install.md#flashing-from-the-stock-web-ui)):

- `*-squashfs-factory.bix`: kernel and root filesystem in one file, the
  same layout as the sysupgrade image, for a direct flash install. Other
  switches with this stock firmware family build it the same way in
  OpenWrt (MokerLink 2G080GM, GoodTop GT-SWTXG8FM)
- `*-initramfs-kernel.bin`: OpenWrt running from RAM, booted from flash,
  to try it before installing the sysupgrade image

## Open points

- **Not yet tested on hardware:** the factory image. Tested: an 802.3bt
  class 7 PD and an 802.3at class 4 PD, the fans in automatic mode, the
  link change fix, the port LEDs set up from the device tree when booted
  from flash, the PoE firmware loaded from flash after a power cycle, the
  three `i2c-gpio` buses sharing SCL (fan controller, PoE firmware
  download after a power cycle, SFP+ module), the PoE default from
  board.json (on without a `pse` option, off with `pse` 0, still on after
  a change that restarts the port), the firmware kept by sysupgrade
  through the driver package's keep.d entry, and the PoE budget with the
  PSE core fix below (a PD gets the power of its class; switching a port
  off holds; nothing stays allotted after switching off or unplugging;
  priorities can be set).
- **PSE core fixes:** in the Linux PSE core, disabling a port (admin
  disable or PD unplugged) re-checked the waiting ports before the port's
  own state was cleared, so it enabled that same port again: switching off
  didn't hold, and the budget leaked with every unplug.
  `patches-6.18/835-*` clears the state first, tested on the switch, and
  has been sent to netdev and to OpenWrt
  ([08-upstreaming.md](08-upstreaming.md#status)). Port priorities didn't
  take effect either: a port switched off for one of higher priority was
  powered again straight away, and when the port of higher priority was
  waiting for power (its PD just classified, or its priority changed),
  the core recursed until the kernel stack overflowed. `836-*` and
  `837-*` switch off only the ports needed to make room, chosen from the
  lowest priority before switching any, power them again if that fails,
  and retry waiting ports from the highest priority; not tested on the
  switch.
- **PoE budget across the two chips:** both chips share the 260 W, but the
  PSE core sheds and retries only ports of the chip that has the event
  (front ports 1-3 on one, 4-7 on the other). A port of one chip doesn't
  get power freed on the other until something changes on its own chip.
  Fixing that is a PSE core change; the RTL8239 series' cover letter asks
  about it, once that series is sent.
- **PoE pairsets:** which channel of a port is alternative A and which B.
- **Fans and the PHY temperatures:** the ADT7476 doesn't see the 10G
  PHYs' own sensors, as in the stock firmware. Letting those drive the
  fans needs thermal support in the `adt7475` driver; see
  [08-upstreaming.md](08-upstreaming.md#fan-control).

## Upstreaming

See [08-upstreaming.md](08-upstreaming.md): the drivers go to OpenWrt as
pending patches and to Linux, and the board to OpenWrt, all built by
`scripts/make-upstream.py`. All bindings pass `make dt_binding_check` and
both drivers `checkpatch.pl --strict`. With patches 833 and 834, the
board's PSE and LED nodes validate against the bindings; the I²C buses use
the standard `i2c-gpio` binding. The switch
node itself still fails, but only because OpenWrt's `rtl930x.dtsi` differs
from the upstream switch binding (`ethernet`, `pcs`, `thermal` and other
children).
