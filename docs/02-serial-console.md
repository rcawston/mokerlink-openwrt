# Serial console and U-Boot

Everything in the install procedure happens through the serial console. It
is also the way back if anything goes wrong, so set it up first.

## Connecting

The POE-10G07110GSM has no external console port. The console is the
internal header **J4**, labelled `GND`, `TXD`, `RXD`, `3V3`.

Use a **3.3 V** USB-TTL adapter (CP2102, FT232RL, CH340; if it has a
voltage jumper, set it to 3.3 V) and connect:

| J4 | Adapter |
|---|---|
| `GND` | GND |
| `TXD` | RX |
| `RXD` | TX |
| `3V3` | **not connected** |

The labels are from the switch's side, so TXD goes to the adapter's RX. If
nothing appears, swap TX and RX; that is harmless at 3.3 V. If J4 has only
pads, solder in a 4-pin 2.54 mm header. Never use an RS-232 adapter: its
±12 V levels destroy the SoC's pins. When powering the switch with the case
open, route the cable away from the power supply board.

Terminal settings: **115200 baud, 8N1, no flow control**. For example:

```sh
picocom -b 115200 /dev/ttyUSB0     # Linux/macOS (Ctrl-A Ctrl-X quits)
```

On Windows, use PuTTY with *Serial*, the COM port, and 115200.

## Capture the stock boot log

Before anything else, power the switch on with the terminal **logging to a
file** (`picocom ... --logfile stock-boot.log`, or *Session → Logging* in
PuTTY) and let the stock firmware boot completely. This one log usually
answers half the open questions: SoC, RAM size, flash chip and layout, PHY
types, and the boot command.

## Entering U-Boot

This U-Boot prints `Hit Esc key to stop autoboot:  0`: the delay is zero,
so there is no time to react. Press **Esc** repeatedly (or hold it down)
while you switch the power on, until a prompt such as `RTL9300#` appears
instead of `## Booting kernel`.

Some vendors protect U-Boot with a password. The Hasivo boards ask for one
after Esc (`Hs2021cfgmg`, then type `XXXX`). If yours asks for a password,
note the exact prompt and try that one. Otherwise ask for help with the
exact text you see.

## Facts to record in U-Boot

Run these and keep the output. None of them changes anything:

```
printenv
bdinfo
md.b b4300000 40
```

- `printenv`: note **`bootcmd`** (needed to go back to stock), `ipaddr`,
  `serverip`, `ethaddr`, and anything that mentions a board model.
- `md.b b4300000 40` shows the first 64 bytes of the firmware partition
  (flash is mapped at `0xb4000000`, firmware at offset `0x300000`). The
  first four bytes are the uImage magic the boot loader accepts:
  - `83 80 00 00`: MokerLink magic. Use the normal `*-initramfs-kernel.bin`.
  - `27 05 19 56`: standard magic. Use `*-initramfs-kernel-stdmagic.bin`.
  - anything else: stop here and report it; the flash layout differs
    from the assumption.

  The bytes from offset `0x20` are the image name in ASCII, often naming
  the vendor SDK version.

## Read the port wiring

Once U-Boot has brought up the switch (`rtk network on`), its registers hold
the manufacturer's own port configuration: which bus and address each 10G
PHY is on, the mode of every SerDes lane, and the lane polarity on both
ends of every PHY link (switch SerDes and PHY). Reading them settles the biggest open questions before OpenWrt
is ever booted.

The `md` commands only display registers. The `mw` lines ask the switch to
read one PHY or SerDes register (for the PHY reads the port mask is set to
0, so they cannot write to a PHY). Nothing is saved, and a power cycle
clears it all.

1. Start logging the terminal to a file.
2. Connect a cable from **front port 1** to a running PC.
3. Interrupt U-Boot and paste these commands, a few lines at a time:

```
rtk network on
md.l bb00ca00 4
md.l bb00cb80 5
md.l bb000194 2
md.l bb0002a0 2
md.l bb00cb10 1
```

```
mw.l bb00cb70 0; mw.l bb00cb78 00000000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00000000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00000000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00080000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00080000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00080000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00100000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00100000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00100000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00140000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00140000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00140000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00180000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00180000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00180000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00190000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00190000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 00190000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001a0000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001a0000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001a0000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001b0000; mw.l bb00cb7c 00010002; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001b0000; mw.l bb00cb7c 00010003; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb00cb70 0; mw.l bb00cb78 001b0000; mw.l bb00cb7c 001e00c1; mw.l bb00cb74 3; md.l bb00cb74 3
mw.l bb0003b0 00004309; md.l bb0003b0 2
mw.l bb0003b0 00000009; md.l bb0003b0 2
mw.l bb0003b0 0000430d; md.l bb0003b0 2
mw.l bb0003b0 00000029; md.l bb0003b0 2
mw.l bb0003b0 00004311; md.l bb0003b0 2
mw.l bb0003b0 00000011; md.l bb0003b0 2
mw.l bb0003b0 00004315; md.l bb0003b0 2
mw.l bb0003b0 00000015; md.l bb0003b0 2
mw.l bb0003b0 00004319; md.l bb0003b0 2
mw.l bb0003b0 00000019; md.l bb0003b0 2
mw.l bb0003b0 0000431d; md.l bb0003b0 2
mw.l bb0003b0 0000001d; md.l bb0003b0 2
mw.l bb0003b0 00004321; md.l bb0003b0 2
mw.l bb0003b0 00000021; md.l bb0003b0 2
mw.l bb0003b0 00004325; md.l bb0003b0 2
mw.l bb0003b0 00000025; md.l bb0003b0 2
```

4. Move the cable to front port 2 and run `md.l bb00cb10 1` again. Repeat for every
   port, including the SFP+ port with a module and peer. Note in the log
   which front port each run was for.
5. Send the log, or decode it yourself:

   ```sh
   python3 scripts/uboot-wiring.py decode uboot.log
   ```

   It prints a table of every port (bus, address, PHY ID, lane mode,
   polarity) and the device tree lines to use.

The command list comes from `python3 scripts/uboot-wiring.py commands`.

## Back up the flash from U-Boot

The whole flash can be read without booting anything: it is mapped at
`0xb4000000`. With the terminal logging to a file:

```
md.l b4000000 400000
crc32 b4000000 1000000
```

`md.l` prints the 16 MiB as text, which takes well over an hour at 115200
baud. Then convert the log on the PC and check it against U-Boot's CRC:

```sh
python3 scripts/uboot-md2bin.py dump.log full-flash.bin --base 0xb4000000 --size 0x1000000
```

It reports missing or inconsistent lines (dump that range again and add it
to the log) and prints `MATCH` when the result equals U-Boot's CRC32. This
backup is as good as the one `mokerlink-backup` makes from a RAM-booted
OpenWrt, which is much faster.

## Network in U-Boot

U-Boot's network is off until `rtk network on` is run. It uses `ipaddr` as
its own address and `serverip` as the TFTP server. For a laptop with
address `192.168.2.100` directly attached to port 1:

```
setenv ipaddr 192.168.2.2
setenv serverip 192.168.2.100
rtk network on
ping 192.168.2.100
```

These settings last only until the next reboot unless you run `saveenv`. The
install guide uses `saveenv` exactly once, at the end.

## Stock firmware shell (optional)

Realtek SDK firmware often hides a debug shell. On the Hasivo boards it is
reached by pressing Ctrl-T on the console (or in the SSH CLI) and entering
`switchrtk`, then `s`. This is not needed for the installation, but a shell
on the stock firmware lets you read its `/proc/mtd` and kernel log, which
confirms the flash layout before any flash write.
