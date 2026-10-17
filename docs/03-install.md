# Install

The order matters: **RAM boot → backup → verify → flash**. Up to step 4 the
switch runs OpenWrt from RAM only, and a power cycle brings back the stock
firmware unchanged.

> **In a hurry, no serial console?** The stock web UI accepts our image
> directly — there is no firmware signature, only the uImage magic and CRC
> that our build already produces. See
> [Flashing from the stock web UI](#flashing-from-the-stock-web-ui) below.
> For the **first** boot on an unverified unit the serial/RAM route here is
> still safer, because the web UI has no fallback if the network does not
> come up. Once the port map is confirmed, the web UI is the easy path.

## 0. Prepare

- Serial console working and the stock boot log captured
  ([02-serial-console.md](02-serial-console.md)). The stock boot command
  is `bootcmd=boota`.
- The port wiring read from U-Boot and put into the device tree
  ([02-serial-console.md](02-serial-console.md#read-the-port-wiring)). This
  is optional, but it usually makes the first RAM image work on every
  copper port.
- The images (see the README): `*-initramfs-kernel.bin`,
  `*-initramfs-kernel-stdmagic.bin` and `*-squashfs-sysupgrade.bin`.
- A PC with a TFTP server serving the image directory: `tftpd-hpa` or
  `dnsmasq --enable-tftp --tftp-root=DIR --port=0` on Linux, Tftpd64 on
  Windows. Allow UDP port 69 through the PC's firewall.
- Give the PC the static address **192.168.2.254/24** (the TFTP server
  address the stock U-Boot expects) and connect it to **port 1** with a
  cable. Disconnect the switch from your network and
  unplug all PoE devices for now.

## 1. Boot OpenWrt from RAM

Interrupt U-Boot ([02-serial-console.md](02-serial-console.md#entering-u-boot))
and run:

```
rtk network on
tftpboot 0x84f00000 openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-initramfs-kernel.bin
bootm 0x84f00000
```

U-Boot's defaults are `ipaddr=192.168.2.1` (the switch) and
`serverip=192.168.2.254` (the PC), so no `setenv` is needed. The stock
firmware uses the uImage magic `0x83800000`, which the normal RAM image
has; use the `-stdmagic.bin` file only if `bootm` answers `Bad Magic Number`.

When the console shows `Please press Enter to activate this console`, press
Enter. You now have a root shell.

| Symptom | Likely cause |
|---|---|
| `Bad Magic Number` | wrong uImage magic: use the other RAM image |
| `TFTP error` / timeouts | PC firewall, wrong `serverip`, or cable not in a port U-Boot enabled. Try `ping 192.168.2.254` in U-Boot |
| garbage after `Starting kernel` | console speed mismatch: the kernel uses 115200, like the stock U-Boot. Report it |
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

- [x] **RAM:** 128 MiB, as declared (stock boot log).
- [x] **Flash layout:** matches the stock partition table
      ([01-hardware.md](01-hardware.md#flash-layout)).
- [ ] **Backup:** `full-flash.bin` is on your PC and `sha256sum -c` passes.
- [ ] **PHYs:** the summary shows `RTL8261BE 10Gbps PHY` for lan1-lan3 and
      `RTL8264B 10Gbps PHY` for lan4-lan7.
- [ ] **Every copper port passes traffic.** Move the PC cable from port to
      port and `ping 192.168.2.1` from the PC. If a port has link but no
      ping, run this from the serial console and put the line it prints
      into the device tree:
      ```sh
      mokerlink-polarity-scan lan3 192.168.2.254
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
- [ ] **Heat:** fans running (at full speed: OpenWrt has no fan control
      for this board yet), and the temperatures in `mokerlink-probe` stay
      below about 100 °C under load.
- [ ] **PoE:** not supported by OpenWrt yet. If you have a PoE device,
      plug it in and note whether it powers up anyway; that decides how PoE
      support gets done ([05-porting.md](05-porting.md#poe)). If you need
      PoE now, keep the stock firmware.

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
image) and reboots. The stock boot command `boota` loads the uImage from
the firmware partition. OpenWrt's image has the same header format and
magic, so it should boot without any change to U-Boot.

Two cases need a different boot command (interrupt U-Boot, then):

- **`boota` refuses the image** and stays at the U-Boot prompt:
  ```
  setenv bootcmd 'bootm 0xb4300000'
  saveenv
  reset
  ```
- **Ports that worked in the RAM test stay dead** after booting from flash.
  The RAM test always runs after `rtk network on`, which lets U-Boot bring
  up the switch and PHYs first, and OpenWrt may rely on that:
  ```
  setenv bootcmd 'rtk network on; boota'
  saveenv
  reset
  ```

The first boot from flash takes a minute longer while OpenWrt formats its
settings area. Then:

1. Find the switch's address in your router's DHCP leases (hostname
   `mokerlink-switch`) once it is on your network, or use the fallback
   `192.168.2.1` when it is connected directly to a PC.
2. Open `http://<address>/` and **set a root password** (or run `passwd`
   on the console).
3. Read [06-using.md](06-using.md).

## Flashing from the stock web UI

The stock firmware's own firmware-upgrade page will accept and install our
image, with no serial console, no TFTP and no soldering. This was confirmed
by reading the stock firmware: its web upgrade checks only that the uploaded
file is a uImage with magic `0x83800000` and valid header and data CRC32s.
**There is no cryptographic signature and no model lock on the image.** Our
build already produces exactly that magic, so the stock web UI treats it as
a normal upgrade: it writes the whole file to the 12 MiB RUNTIME partition
and reboots. The configuration partitions are never touched.

The stock image is itself a single uImage with its root filesystem embedded
in the kernel, which is exactly the shape of our `*-initramfs-kernel.bin`.
That makes the initramfs image the safest thing to upload first.

1. **Upload the RAM image.** In the stock web UI, open the firmware upgrade
   page (*Management → Upgrade*, "image" / local HTTP upload), choose
   `...-initramfs-kernel.bin`, and upgrade. The switch reboots into OpenWrt
   running from RAM (rootfs in the initramfs), booted from flash by `boota`.
2. **Reach it.** Give the PC `192.168.2.254/24`, wait ~60 s, then open
   `http://192.168.2.1` or `ssh root@192.168.2.1`. On your network instead,
   look for DHCP hostname `mokerlink-switch`.
3. **Verify the hardware** exactly as in [step 3](#3-verify-the-hardware)
   above (`mokerlink-probe`, the port and PoE checks). Share the results.
4. **Make it persistent.** Once the ports check out, copy the sysupgrade
   image over and install it, as in [step 4](#4-write-openwrt-to-flash):
   ```sh
   sysupgrade -n -v /tmp/openwrt-...-squashfs-sysupgrade.bin
   ```
   This replaces the RAM-only install with a normal flash install that keeps
   its settings across reboots.

The one catch: this path has **no boot-loader safety net**. If OpenWrt boots
but the network never comes up (for example a wrong port map on a unit we
have not tested yet), the stock web UI is gone and you cannot re-flash
without the serial console. So keep `full-flash.bin` and, for a first boot
on an unverified unit, prefer the serial/RAM route at the top of this page.

**Back to stock the same way.** The stock image you have (the `...en.bin`
uImage) can go straight back onto the RUNTIME partition from OpenWrt, no
serial needed, because it is a plain uImage that fits the partition:

```sh
# from OpenWrt, with the stock uImage in /tmp
mtd -r write /tmp/stock.bin firmware
```

The switch reboots into stock with its original configuration intact
(OpenWrt never wrote the stock config partitions). See
[04-recovery.md](04-recovery.md) for the full-flash route if the boot loader
or board data is ever damaged.

## Updating later

Test every new build from RAM first: `tftpboot` plus `bootm` as in step 1.
That doesn't touch flash. Then install it from the running system with
`sysupgrade -v <file>` (settings are kept) or via *System → Backup / Flash
Firmware* in the web UI.
