# Install

The order matters: **RAM boot → backup → verify → flash**. Up to step 4 the
switch runs OpenWrt from RAM only, and a power cycle brings back the stock
firmware unchanged.

## 0. Prepare

- Serial console working and the stock boot log captured
  ([02-serial-console.md](02-serial-console.md)). Note the original
  `bootcmd` from `printenv`.
- The images (see the README): `*-initramfs-kernel.bin`,
  `*-initramfs-kernel-stdmagic.bin` and `*-squashfs-sysupgrade.bin`.
- A PC with a TFTP server serving the image directory: `tftpd-hpa` or
  `dnsmasq --enable-tftp --tftp-root=DIR --port=0` on Linux, Tftpd64 on
  Windows. Allow UDP port 69 through the PC's firewall.
- Give the PC the static address **192.168.2.100/24** and connect it to
  **port 1** with a cable. Disconnect the switch from your network and
  unplug all PoE devices for now.

## 1. Boot OpenWrt from RAM

Interrupt U-Boot ([02-serial-console.md](02-serial-console.md#entering-u-boot))
and run:

```
setenv ipaddr 192.168.2.2
setenv serverip 192.168.2.100
rtk network on
tftpboot 0x84f00000 openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-initramfs-kernel.bin
bootm 0x84f00000
```

Use the `-stdmagic.bin` file instead if `md.b b4300000 40` showed
`27 05 19 56`, or if `bootm` answers `Bad Magic Number`.

When the console shows `Please press Enter to activate this console`, press
Enter. You now have a root shell.

| Symptom | Likely cause |
|---|---|
| `Bad Magic Number` | wrong uImage magic: use the other RAM image |
| `TFTP error` / timeouts | PC firewall, wrong `serverip`, or cable not in a port U-Boot enabled. Try `ping 192.168.2.100` in U-Boot |
| garbage after `Starting kernel` | console speed: U-Boot is not at 38400. Report it; the device tree needs the right speed |
| stops at `Starting kernel` | report the full log |
| kernel panic mentioning memory | report the full log |

Nothing has been written to flash. To go back to stock at any point before
step 4, just power-cycle.

## 2. Back up the flash

On the switch console:

```sh
mokerlink-backup
```

It reads every partition twice, compares the copies, checks that the
partitions cover the whole chip, and writes `full-flash.bin` plus the
individual partitions to `/tmp/mokerlink-backup` (in RAM).

The switch has no address yet: with no DHCP server around it brings up the
fallback `192.168.2.1` after 60 seconds (`ip addr` shows it). Then, on the
PC:

```sh
scp -O 'root@192.168.2.1:/tmp/mokerlink-backup/*' .
sha256sum -c sha256sums
```

Or open `http://192.168.2.1/mokerlink-backup/full-flash.bin` (and the
other files listed by `mokerlink-backup`) in a browser.

**Keep these files in two places.** `full-flash.bin` holds your unit's boot
loader, MAC address and board data. It is the only complete way back to
stock (see [04-recovery.md](04-recovery.md)).

If `mokerlink-backup` warns about the flash size or refuses to continue,
stop: the flash layout in the device tree is wrong and must be fixed first.

## 3. Verify the hardware

```sh
mokerlink-probe
```

It prints a port summary and writes a full report to `/tmp`. Copy the report
to the PC (`scp -O root@192.168.2.1:/tmp/mokerlink-probe-*.txt .`). If you
want help, share it together with the stock boot log.

Then go through this list, fixing the device tree as described in
[05-porting.md](05-porting.md) whenever an item fails. After each fix,
rebuild and repeat from step 1 with the new RAM image.

- [ ] **RAM:** U-Boot's `DRAM:` line matches the device tree (128 MiB is
      declared by default; more RAM only needs a one-line change).
- [ ] **Flash layout:** the stock boot log or `/proc/mtd` of the stock
      firmware matches [the assumed layout](01-hardware.md#flash-layout-assumed),
      and the stock `bootcmd` boots from `0xb4300000`.
- [ ] **Backup:** `full-flash.bin` is on your PC and `sha256sum -c` passes.
- [ ] **PHYs:** the summary shows an RTL826x driver for lan1-lan7.
- [ ] **Every copper port passes traffic.** Move the PC cable from port to
      port and `ping 192.168.2.1` from the PC. If a port has link but no
      ping, run this from the serial console and put the line it prints
      into the device tree:
      ```sh
      mokerlink-polarity-scan lan3 192.168.2.100
      ```
- [ ] **SFP+ port** (lan8): with a 10G module or DAC cable to a 10G peer,
      it shows link and passes traffic.
- [ ] **Full speed:** with two hosts and `iperf3`, each copper port reaches
      about 9.4 Gbit/s at 10G, and `ethtool -S lanN` shows no growing error
      or drop counters.
- [ ] **Jumbo frames**, if you use them: `ping -M do -s 8972 <other host>`
      between two hosts through the switch works.
- [ ] **Soak test:** an hour of `iperf3` across several ports leaves no
      link flaps or errors in `dmesg`.
- [ ] **Heat:** fans running, and the temperatures in `mokerlink-probe`
      stay below about 100 °C under load.
- [ ] **PoE** (if you need it): plug in one known PoE device and check that
      it powers up. Stage 1 does not control PoE (see
      [05-porting.md](05-porting.md#poe)).

## 4. Write OpenWrt to flash

Only after every item above passes. Still in the RAM-booted OpenWrt, copy
the sysupgrade image to the switch and install it:

```sh
# on the PC
scp -O openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-squashfs-sysupgrade.bin root@192.168.2.1:/tmp/
# on the switch
sysupgrade -n -v /tmp/openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-squashfs-sysupgrade.bin
```

This writes only the 12 MiB `firmware` partition (the stock RUNTIME
image) and reboots. Interrupt U-Boot on the way up and set the boot command:

```
setenv bootcmd 'rtk network on; bootm 0xb4300000'
saveenv
reset
```

`rtk network on` lets U-Boot bring up the switch and PHYs before Linux
starts, the same state they were in during the RAM-boot tests (the
supported Hasivo boards boot the same way). `bootm 0xb4300000` boots the
image in the firmware partition.

The first boot from flash takes a minute longer while OpenWrt formats its
settings area. Then:

1. Find the switch's address in your router's DHCP leases (hostname
   `mokerlink-switch`) once it is on your network, or use the fallback
   `192.168.2.1` when it is connected directly to a PC.
2. Open `http://<address>/` and **set a root password** (or run `passwd`
   on the console).
3. Read [06-using.md](06-using.md).

## Updating later

Test every new build from RAM first: `tftpboot` plus `bootm` as in step 1.
That doesn't touch flash. Then install it from the running system with
`sysupgrade -v <file>` (settings are kept) or via *System → Backup / Flash
Firmware* in the web UI.
