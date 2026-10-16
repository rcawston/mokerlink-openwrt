# Serial console and U-Boot

Everything in the install procedure happens through the serial console. It
is also the way back if anything goes wrong, so set it up first.

## Connecting

- **RJ45 "Console" port on the front**, if your unit has one: use a standard
  RJ45-to-USB console cable (the Cisco-style blue cable).
- **Internal 4-pin header**: use a **3.3 V** USB-TTL adapter (CP2102,
  FT232RL, CH340). Connect GND to GND, the switch TX to adapter RX and the
  switch RX to adapter TX. Leave the 3.3 V pin unconnected. Never use an
  RS-232 adapter on the internal header: its ±12 V levels destroy the
  SoC's pins.

Terminal settings: **38400 baud, 8N1, no flow control**. If you see
garbage, try 115200. For example:

```sh
picocom -b 38400 /dev/ttyUSB0      # Linux/macOS (Ctrl-A Ctrl-X quits)
```

On Windows, use PuTTY with *Serial*, the COM port, and 38400.

## Capture the stock boot log

Before anything else, power the switch on with the terminal **logging to a
file** (`picocom ... --logfile stock-boot.log`, or *Session → Logging* in
PuTTY) and let the stock firmware boot completely. This one log usually
answers half the open questions: SoC, RAM size, flash chip and layout, PHY
types, and the boot command.

## Entering U-Boot

Power-cycle the switch and watch for a line like `Hit Esc key to stop
autoboot` or `Hit any key to stop autoboot`, then press that key (Esc,
Ctrl-C or Space) repeatedly right away. You should get a prompt such as
`RTL9300#`.

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
PHY is on, the mode of every SerDes lane, and the lane polarity of every
RTL8264B. Reading them settles the biggest open questions before OpenWrt
is ever booted.

The `md` commands only display registers. The `mw` lines ask the switch to
read one PHY register (the port mask is set to 0, so they cannot write to a
PHY). Nothing is saved, and a power cycle clears it all.

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
