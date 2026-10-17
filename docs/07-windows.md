# Build and install from Windows

Everything below runs on a Windows 10/11 PC: the build in WSL2 (Ubuntu
inside Windows), the install over the serial console (PuTTY) and Ethernet
(TFTP). Keep your flash dump (`full-flash.bin`) safe before you start.

## Part 1: Build the firmware (once, 1-3 hours)

### 1.1 Install Ubuntu in WSL2

In PowerShell **as administrator**:

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot when asked, then open "Ubuntu 24.04" from the Start menu and create a
user name and password.

OpenWrt's build breaks on the Windows folders WSL adds to `PATH` (they
contain spaces). In the Ubuntu window:

```sh
printf '[interop]\nappendWindowsPath = false\n' | sudo tee /etc/wsl.conf
```

Then in PowerShell run `wsl --shutdown` and open Ubuntu again.

### 1.2 Install the build tools

In Ubuntu:

```sh
sudo apt update
sudo apt install -y build-essential clang flex bison g++ gawk gcc-multilib g++-multilib \
  gettext git libncurses-dev libssl-dev python3-setuptools python3-dev rsync swig unzip \
  zlib1g-dev file wget device-tree-compiler gh
```

### 1.3 Get the repository

The repository is private, so log in to GitHub once (follow the prompts,
"Login with a web browser" is easiest):

```sh
gh auth login
gh repo clone rcawston/mokerlink ~/mokerlink
```

Keep it in your Ubuntu home folder (`~`), not under `/mnt/c`: the Windows
file system is too slow and is case-insensitive, which breaks the build.

### 1.4 Build

```sh
cd ~/mokerlink
scripts/check-dts.sh     # about a minute
scripts/build.sh         # 1-3 hours the first time, about 25 GB of disk
```

If it fails, it repeats the failing step with full output. Send the last
100 or so lines.

The images are in `~/mokerlink/out/`. Copy them to a Windows folder for the
TFTP server:

```sh
mkdir -p /mnt/c/tftp
cp ~/mokerlink/out/*.bin ~/mokerlink/out/sha256sums /mnt/c/tftp/
```

You need two of them:

- `openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-initramfs-kernel.bin`
  (the RAM image, for testing)
- `openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-squashfs-sysupgrade.bin`
  (the flash image)

Later builds after `git pull` only rebuild what changed and take minutes.

## Part 2: Prepare the PC

1. **TFTP server:** install Tftpd64 (pjo2.github.io/tftpd64). In
   *Settings*, enable only the **TFTP Server** (switch the DHCP server
   **off**), set the base directory to `C:\tftp`, and allow it through the
   Windows firewall when asked.
2. **Ethernet address:** Settings → Network → Ethernet → IP assignment →
   Edit → Manual, IPv4 on: address `192.168.2.254`, mask `255.255.255.0`, no
   gateway. This is the TFTP server address the stock U-Boot expects; the
   switch's U-Boot uses `192.168.2.1`.
3. **Cables:** PC Ethernet to **front port 1**. Disconnect the switch from
   your network and unplug PoE devices for now.
4. **Console:** PuTTY on COM3, 115200, with *Session → Logging → All session
   output* to a file. Send that log after the first boot.

## Part 3: Test from RAM (nothing is written to flash)

1. Power-cycle the switch while pressing Esc in PuTTY until `RTL9300#`
   appears.
2. Type:

   ```
   rtk network on
   tftpboot 0x84f00000 openwrt-realtek-rtl930x-mokerlink_poe-10g07110gsm-initramfs-kernel.bin
   bootm 0x84f00000
   ```

   If `tftpboot` times out, check the firewall and the PC address, then try
   the cable in another front port.
3. OpenWrt boots. When the console shows `Please press Enter to activate
   this console`, press Enter for a root shell.
4. After about 60 seconds without a DHCP server, the switch takes the
   fallback address `192.168.2.1`. From the PC: `ping 192.168.2.1`, and the
   web interface at `http://192.168.2.1`.
5. On the console, run `mokerlink-probe` and send its output with the boot
   log. I check them before you go on.
6. **Port test:** in a Windows command prompt run `ping -t 192.168.2.1`.
   Move the PC cable through front ports 1-7, a few seconds each. Note
   which ports answer, and after each move run `mokerlink-probe` on the
   console: the `lanN` showing `up` is that front port. If a port shows link
   but ping fails, note it.
7. **PoE** (optional): `dmesg | grep -i pse` on the console, then plug one
   PoE device into a port and see whether it powers up.

To go back to stock at any point in Part 3, just power-cycle the switch.

## Part 4: Write OpenWrt to flash

Only after the RAM test passes and I have checked the logs.

1. Still running from RAM, open `http://192.168.2.1` → *System → Backup /
   Flash Firmware* → *Flash new firmware image*.
2. Choose the `...-squashfs-sysupgrade.bin` file, **untick "Keep
   settings"**, and continue. It writes only the firmware partition, then
   reboots.
3. Watch PuTTY. The stock boot command (`boota`) should start OpenWrt from
   flash. The first boot takes a minute longer. If instead:
   - U-Boot stops at the prompt because `boota` refuses the image, type
     `setenv bootcmd 'bootm 0xb4300000'`, `saveenv`, `reset`.
   - The ports that worked in Part 3 stay dead, type
     `setenv bootcmd 'rtk network on; boota'`, `saveenv`, `reset`.
4. Open `http://192.168.2.1` and set a root password (*System →
   Administration*).
5. Connect the switch to your network. It takes an address from your
   router's DHCP (hostname `mokerlink-switch`) and keeps `192.168.2.1` only
   as a fallback when no DHCP server answers.

Later updates: build again, test the new RAM image as in Part 3 if you
like, then flash it in the web interface with "Keep settings" ticked.

## Going back to stock

Your dump contains the stock firmware. Cut it out in Ubuntu:

```sh
dd if=full-flash.bin of=firmware.bin bs=64k skip=48 count=192
```

Then RAM-boot OpenWrt (Part 3) and write it back as in
[04-recovery.md](04-recovery.md#back-to-the-stock-firmware). If you changed
the boot command, set it back with `setenv bootcmd boota`, `saveenv`.
