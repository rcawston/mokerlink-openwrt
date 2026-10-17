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
md.b b4300000 40
```

Addresses must be exactly 8 hex digits. A typo such as `b43000000` reads
an invalid address and resets the switch, which looks like a hang but is
harmless.

- `printenv`: on this unit `bootcmd=boota`, `ipaddr=192.168.2.1`,
  `serverip=192.168.2.254`, `boardmodel=RTL9303_8264B_3X8261BE_XGE_POE`,
  plus `ethaddr` and `serialnumber`.
- `md.b b4300000 40` shows the first 64 bytes of the firmware partition
  (flash is mapped at `0xb4000000`, firmware at offset `0x300000`). The
  first four bytes are the uImage magic the boot loader accepts:
  - `83 80 00 00`: MokerLink magic. Use the normal `*-initramfs-kernel.bin`.
  - `27 05 19 56`: standard magic. Use `*-initramfs-kernel-stdmagic.bin`.
  - anything else: stop here and report it; the flash layout differs
    from the assumption.

  The bytes from offset `0x20` are the image name in ASCII, often naming
  the vendor SDK version.

## Collect everything automatically

`scripts/uboot-collect.py` does the reading in the next sections and the
flash backup in one run. It types each command in short bursts and checks
U-Boot's echo before pressing Enter, because this U-Boot has no flow control
and loses characters from fast pastes. It verifies every flash chunk against
U-Boot's `crc32` and reads it again when bytes were lost on the line.

On Windows:

1. Install Python 3 from python.org, then `py -m pip install pyserial`.
2. Download this repository (Code → Download ZIP on GitHub) and unpack it.
3. Get to the `RTL9300#` prompt in PuTTY, then **close PuTTY** so the COM
   port is free. The switch stays at the prompt.
4. In the repository's `scripts` folder:

   ```
   py uboot-collect.py --port COM3
   ```

It first saves the board information and port wiring (`uboot-info.log`,
`uboot-wiring.txt`), asking you to move a cable from port to port for the
link test. Then it dumps the flash to `full-flash.bin` in a new dated folder.
The dump takes about two hours at 115200 baud. If it is interrupted, run
the command it prints (`--resume <folder>`) to continue.

Options: `--auto-esc` stops autoboot by itself while you power-cycle the
switch (no PuTTY needed); `--skip-dump` or `--skip-info` runs only one
half; `--skip-link-test` leaves out the cable moving; `--link-test-only`
repeats just the link test (a few minutes, saves `uboot-link.log`).

Send `uboot-info.log` and `uboot-wiring.txt`. Keep `full-flash.bin` private
(it holds your MAC address and stock configuration) and in two places.

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

4. Move the cable to front port 2 and run `md.l bb00cb10 1` **twice** (the
   link bits latch low, so only the second read is current). Repeat for
   every port, including the SFP+ port with a module and peer. Note in the
   log which front port each run was for (this U-Boot has no `echo`).
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

U-Boot's network is off until `rtk network on` is run. It uses `ipaddr`
(default `192.168.2.1`) as its own address and `serverip` (default
`192.168.2.254`) as the TFTP server. With the PC at `192.168.2.254/24` on
port 1:

```
rtk network on
ping 192.168.2.254
```

This U-Boot has `md`, `mw`, `crc32`, `sf`, `tftpboot`, `bootm` and `boota`,
but no `bdinfo`.

## Stock firmware shell (optional)

Realtek SDK firmware often hides a debug shell. On the Hasivo boards it is
reached by pressing Ctrl-T on the console (or in the SSH CLI) and entering
`switchrtk`, then `s`. This is not needed for the installation, but a shell
on the stock firmware lets you read its `/proc/mtd` and kernel log, which
confirms the flash layout before any flash write.
