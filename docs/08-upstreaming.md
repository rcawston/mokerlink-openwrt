# Upstreaming

The board support and the three new kernel drivers are written to be
submitted: the board and the drivers to OpenWrt, the drivers and their
bindings to Linux. `scripts/make-upstream.py` builds the submissions from
this repository; the commit messages are in `upstream/`.

## What goes where

| Part | OpenWrt | Linux (subsystem tree, list) |
|---|---|---|
| shared-SCL I²C buses: binding, driver | `realtek: add driver for GPIO I2C buses sharing one SCL line` (pending patches `830-*`, `kmod-i2c-gpio-shared-scl`) | `i2c` series: i2c tree, linux-i2c |
| RTL8239 PSE: binding, driver | `realtek: add Realtek RTL8239 PSE driver` (`831-*`, `kmod-pse-realtek-rtl8239`) | `pse` series: net-next, netdev |
| RTL9300 port LEDs: binding, switch binding, driver | `realtek: add RTL9300 port LED driver` (`832-*`, `kmod-leds-rtl9300-port`) | `leds` series: leds tree, linux-leds (the switch binding patch also to netdev) |
| PSE LED trigger names in `leds/common.yaml` | not needed | `pse-led-triggers`: with the PSE LED trigger series |
| the device | `realtek: rtl930x: add support for MokerLink POE-10G07110GSM` | none |
| PSE core fix: a PI disabled with software power control is not enabled again by the retry of its power domain | `835-*`; in OpenWrt it belongs in `target/linux/generic/pending-6.18`, as a separate pull request | `pse-fix`: net tree as a fix (`Fixes: ffef61d6d273`), netdev; made against mainline |
| link change fix in the switch driver | `realtek: dsa: let phylib read the PHY on a port link change` (`patches/0003-*`, a patch to OpenWrt's own driver, ready to send as it is) | none |

Not submitted: the `mokerlink-*` tools and the first-boot defaults in
`files/`.

## PoE budget defaults

With the PoE supply in the device tree, the ports start switched off until
netifd's `pse` device option switches them on; this repository's
first-boot defaults set it for lan1-lan7. OpenWrt has no board default
for that option yet (`ucidef_set_poe` belongs to the `realtek-poe`
package). For the device commit that means either adding such a default
to `board.d`, or leaving the supply out of the device tree upstream, so
that the ports run in auto mode, on and without a budget, as in the stock
firmware.

## Fan control

The fan control is part of the device commit: a case in the realtek
target's `base-files/etc/init.d/hwmon_fancontrol` (in `patches/0001-*`),
which programs a board's fan controller once at boot. The same file
already puts the same ADT7476 into its automatic mode for the Plasma Cloud
ESX28/PSX28; this board's case sets the stock behaviour (all outputs
following the hottest sensor, 50 % up to 64 °C, full speed from 66 °C)
and keeps the fans at 50 % below the start temperature rather than off.
The device tree can't express any of this: the adt7475 binding allows a
start duty only in a deprecated form.

In Linux, the general solution would be thermal support in `adt7475`:
registering its PWM outputs as cooling devices (`cooling-levels` from
`fan-common.yaml`, as `emc2305` and `max6650` do), so that a board's
`thermal-zones` drive them, including from the 10G PHYs' temperature
sensors. That is driver work beyond this board and not needed for
OpenWrt.

## Dependencies

- The RTL8239 driver uses `devm_pse_poll_helper()` and, for the PoE LEDs,
  the PSE LED triggers. Both come from Carlo Szelinsky's PSE series that
  OpenWrt carries as `target/linux/generic/pending-6.18/897-*`. Upstream,
  the `pse` series has to wait for it or go on top of it. OpenWrt already
  has it.
- The `pse-led-triggers` binding patch belongs with that series' LED
  trigger patches. Expect the device tree maintainers to object to trigger
  names that contain a Linux I²C bus number (`pse-1-0022:...`), and to ask
  for `trigger-sources = <&pse_port1>` instead. That needs the trigger
  series to match LEDs by `trigger-sources`, so raise it with its author.
- The device commit needs the three driver commits before it.

## Building the submissions

You review the patches, you sign them off, and you send them: an AI
assistant must not add a `Signed-off-by`, only you can certify the
[Developer's Certificate of Origin](https://www.kernel.org/doc/html/latest/process/submitting-patches.html#sign-your-work-the-developer-s-certificate-of-origin).
The kernel asks for an `Assisted-by: LLM [TOOL1] [TOOL2]` tag on code
written with an AI assistant, and for the changelog to say so when a tool
found the problem
([coding-assistants](https://docs.kernel.org/process/coding-assistants.html),
[generated-content](https://docs.kernel.org/process/generated-content.html));
OpenWrt has a proposal to follow the kernel's rules. The script requires
the tag's value and adds your sign-off only with `--signoff`. Author and
maintainer default to your `git config` identity.

OpenWrt, on a branch of a clean `openwrt.git` main checkout:

```sh
git -C ~/openwrt checkout -b mokerlink-poe-10g07110gsm origin/main
scripts/make-upstream.py openwrt ~/openwrt --signoff \
	--assisted-by "<LLM>"
# four commits on the branch, and the patches in build/upstream/openwrt
```

Open the pull request on GitHub from that branch, after a test build of
it. If main moved since the pinned commit, the device commit may need a
rebase (the image recipe and `02_network` lists change often).

Linux, one series at a time, each on a branch of its subsystem tree:

```sh
git -C ~/linux checkout -b rtl8239 net-next/main
scripts/make-upstream.py linux ~/linux pse --signoff \
	--assisted-by "<LLM>"
# commits on the branch, the series with a cover letter in build/upstream/linux/pse
```

The Kconfig and Makefile hunks are made against v6.18 and applied with a
three-way merge; the MAINTAINERS entry is added in alphabetical order with
you as maintainer. Then, in the kernel tree:

```sh
./scripts/checkpatch.pl --strict -g net-next/main..HEAD
make dt_binding_check DT_SCHEMA_FILES=net/pse-pd/realtek,rtl8239.yaml
./scripts/get_maintainer.pl build/upstream/linux/pse/*.patch
git send-email --to=netdev@vger.kernel.org --cc=... build/upstream/linux/pse/*.patch
```

The script puts the tree in the subject prefix (`[PATCH net-next]` for
`pse`); send net-next series only while net-next is open. Older checkpatch
versions don't know `Assisted-by` and report it as a non-standard
signature.

The PSE core fix goes to the net tree on its own, as `[PATCH net]`. It is
made against mainline (it touches the same functions as in OpenWrt's
kernel, without OpenWrt's pending PSE patches around them), was tested on
the switch in OpenWrt and build-tested on mainline, and is a regular bug
for the kernel's threat model (it needs an admin action or an unplug), so
it goes to the list, not to the security team:

```sh
git -C ~/linux fetch https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net.git main
git -C ~/linux checkout -b pse-fix FETCH_HEAD
scripts/make-upstream.py linux ~/linux pse-fix --signoff --assisted-by "<LLM>"
cd ~/linux
./scripts/checkpatch.pl --strict -g HEAD~1..HEAD
./scripts/get_maintainer.pl ~/mokerlink/build/upstream/linux/pse-fix/*.patch
git send-email --to=netdev@vger.kernel.org --cc=... ~/mokerlink/build/upstream/linux/pse-fix/*.patch
```

`scripts/make-upstream.py patches` writes the kernel patches as OpenWrt
carries them to `build/upstream/patches`, for a quick review without a
tree.

## What reviewers will likely ask

- **RTL8239:** there is no datasheet; the driver follows the vendor
  firmware. The firmware is not redistributable, so it is not in
  linux-firmware and the driver works without it until it is installed.
  Expect questions about the broadcast download (each chip's instance
  sends the image to the shared address; the other chip ignores it),
  about the second address as an ancillary device, and about the two
  modes: auto without a supply, the static budget with one. In budget
  mode a port is switched on by putting it in auto mode rather than with
  the power-on register, so that the chip handles the disconnection as
  it does in the stock firmware.
- **Shared-SCL buses:** reviewers may suggest i2c-gpio plus a mux, or the
  SoC's I²C master. A mux can't switch SDA lines without hardware for it,
  and the master is ruled out by the PoE firmware download (one 25 KiB
  write; the chips NAK a split one). Both are explained in the cover
  letter.
- **Port LEDs:** whether the switch's DSA driver should own the port LEDs
  instead (there is no upstream RTL9300 DSA driver yet; the switch's other
  children are separate drivers on its syscon).
