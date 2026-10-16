# Recovery

OpenWrt never writes the boot loader, its environment or the board data
(those partitions are read-only in the device tree). So the switch can
always reach U-Boot, and from U-Boot it can always boot the RAM image.

## OpenWrt in flash doesn't boot or misbehaves

1. Interrupt U-Boot on the serial console.
2. RAM-boot a working image exactly as in
   [03-install.md, step 1](03-install.md#1-boot-openwrt-from-ram).
3. From there, either `sysupgrade -n` a fixed image, or go back to stock
   (below).

## Back to the stock firmware

You need `firmware.bin` from the backup (the stock RUNTIME partition). The
stock boot command is `boota`.

1. RAM-boot OpenWrt as in [step 1](03-install.md#1-boot-openwrt-from-ram).
   Don't do this from OpenWrt running from flash: it would overwrite the
   filesystem it is running from.
2. Copy the stock image over and write it back:

   ```sh
   # on the PC
   scp -O firmware.bin root@192.168.2.1:/tmp/
   # on the switch: check it is the file you backed up, then write it
   sha256sum /tmp/firmware.bin    # must match the line in sha256sums
   mtd write /tmp/firmware.bin firmware
   ```

3. If you changed the boot command, reboot, interrupt U-Boot and restore
   it:

   ```
   setenv bootcmd boota
   saveenv
   reset
   ```

The stock firmware then boots with its configuration intact: OpenWrt never
touched the configuration partition.

## Writing from U-Boot only

If no OpenWrt RAM image boots, you can write the stock image from U-Boot,
provided it has the `sf` command (check with `help`). After `rtk network on`
and the `ipaddr`/`serverip` setup from step 1:

```
tftpboot 0x84f00000 firmware.bin
sf probe 0
sf erase 0x300000 0xc00000
sf write 0x84f00000 0x300000 0xc00000
```

Double-check the numbers before pressing Enter. They must be exactly the
firmware partition (offset `0x300000`, size `0xc00000`). Writing at a wrong
offset can overwrite the boot loader.

## Last resort: SPI programmer

If the boot loader itself is ever damaged (OpenWrt cannot do this, but a
wrong `sf` command can), `full-flash.bin` can be written back with an SPI
programmer (for example a CH341A with a SOIC-8 clip on the flash chip,
switch unpowered, 3.3 V programmer). This restores the switch exactly to
the state of the backup.
