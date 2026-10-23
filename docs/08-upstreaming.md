# Upstreaming

The board support and the two new kernel drivers are written to be
submitted: the board and the drivers to OpenWrt, the drivers and their
bindings to Linux. `scripts/make-upstream.py` builds the submissions from
this repository; the commit messages are in `upstream/`.

The three I²C buses use `i2c-gpio`, which has handled buses that share one
SCL line since Linux 7.3 (`645c3d7d07fa`, "i2c: i2c-gpio: Enhance driver for
buses with shared SCL"), and which OpenWrt carries as
`target/linux/realtek/patches-6.18/033-02-v7.3-*`. An earlier
`i2c-gpio-shared-scl` driver of this repository did the same; its series
to Linux was withdrawn after a review pointed that out.

## Status

As of 23 October 2026:

| Submission | Where | State |
|---|---|---|
| PSE core fix | netdev, `[PATCH net]` | [v1](https://lore.kernel.org/netdev/20261018182005.1193-1-ross@r-sc.ca/) and [v2](https://lore.kernel.org/netdev/20261019220835.613-1-ross@r-sc.ca/) sent. v2 moves the retry of the power domain out of `_pse_pi_disable()` into its callers, as Kory Maincent asked, and keeps it after `pse_disable_pi_pol()`, as netdev's AI review found that leaving it out keeps PIs off whose budget wasn't needed; the priorities need a separate patch |
| PSE core fix | OpenWrt, generic pending patch | [openwrt/openwrt#25994](https://github.com/openwrt/openwrt/pull/25994), updated to v2, in quilt's format, which its review asked for |
| switch link change fix | OpenWrt | [openwrt/openwrt#25995](https://github.com/openwrt/openwrt/pull/25995), merged on 21 October, after the update Markus Stockhausen asked for: ports with in-band status keep the MAC link change, and the interrupt handler doesn't look at the PHY of an SFP module |
| RTL9300 port LEDs (`leds`) | linux-leds | [sent and withdrawn](https://lore.kernel.org/all/20261018194339.132-1-ross@r-sc.ca/): Andrew Lunn asked for port LEDs in the switch driver's port nodes, and Markus Stockhausen, who keeps OpenWrt's RTL930x switch driver, has no time to take it upstream yet |
| PoE LEDs in OpenWrt | OpenWrt | through [openwrt/openwrt#25341](https://github.com/openwrt/openwrt/pull/25341) (software-controlled LEDs in the realtek switch driver), tested on this board; the board's LED nodes follow once it is merged |
| shared-SCL I²C (`i2c`) | linux-i2c | [sent and withdrawn](https://lore.kernel.org/all/20261018194315.945-1-ross@r-sc.ca/): `i2c-gpio` does it |
| board.json PoE default | netifd | [openwrt/netifd#97](https://github.com/openwrt/netifd/pull/97), merged on 20 October |
| the board | OpenWrt | [openwrt/openwrt#26042](https://github.com/openwrt/openwrt/pull/26042), without the PoE LEDs; needs OpenWrt's netifd package updated to a version with openwrt/netifd#97 |
| PSE priority choices in the web UI | LuCI | [openwrt/luci#9141](https://github.com/openwrt/luci/pull/9141) |
| RTL8239 PSE (`pse`), PSE LED trigger names (`pse-led-triggers`) | netdev, linux-leds | not sent; wait for the PSE series they depend on ([Dependencies](#dependencies)) |
| RTL930x switch driver (`rtl930x-switch`) | netdev, `[PATCH net-next]` | not sent: 9 patches. The CI builds (W=1 and sparse) pass, and the board test passed: 10 Gb/s on all seven copper ports, and 2.5 Gb/s, 1 Gb/s and 100 Mb/s on ports with each PHY (the cover letter's Testing section). Its 1 Gb/s links need PHY driver entries for the board's RTL8261N and RTL8264B, which go to net-next separately (`rtl8261n-rtl8264b`). The design is in its `DESIGN.md` |

## What goes where

| Part | OpenWrt | Linux (subsystem tree, list) |
|---|---|---|
| RTL8239 PSE: binding, driver | `realtek: add Realtek RTL8239 PSE driver` (`831-*`, `kmod-pse-realtek-rtl8239`) | `pse` series: net-next, netdev |
| RTL9300 port LEDs: binding, switch binding, driver | not in the board pull request: the PoE LEDs follow with openwrt/openwrt#25341's software-controlled LEDs in the switch driver. This repository's own build keeps `832-*` and `kmod-leds-rtl9300-port` until then | `leds` series: leds tree, linux-leds (the switch binding patch also to netdev); withdrawn, see the status |
| PSE LED trigger names in `leds/common.yaml` | not needed | `pse-led-triggers`: with the PSE LED trigger series |
| board.json default for netifd's `pse` option | `base-files: add a uci-defaults function for the PSE default` (`patches/0004-*`), in the board pull request; netifd itself: `device: add a board.json default for the pse option`, a pull request to [openwrt/netifd](https://github.com/openwrt/netifd) (`netifd TREE`) | none |
| the device | `realtek: rtl930x: add support for MokerLink POE-10G07110GSM` | none |
| PSE core fix: a PI disabled with software power control is not enabled again by the retry of its power domain | `generic: pse-pd: don't enable a PI again while disabling it`: this repository's `835-*` as `generic/pending-6.18/898-*`, a separate pull request (`openwrt TREE pse-fix`) | `pse-fix`: net tree as a fix (`Fixes: ffef61d6d273`), netdev; made against mainline |
| link change fix in the switch driver | `realtek: dsa: let phylib read the PHY on a port link change` (`patches/0003-*`, a patch to OpenWrt's own driver), a separate pull request (`openwrt TREE dsa-fix`) | none |
| RTL930x switch driver: the DMA engine and the ports as standalone interfaces | none: OpenWrt keeps its own DSA driver | `rtl930x-switch`: net-next, netdev; made against net-next |

Not submitted: the `mokerlink-*` tools and the first-boot defaults in
`files/`.

## PoE budget defaults

With the PoE supply in the device tree, the ports start switched off until
netifd's `pse` device option switches them on. OpenWrt had no board
default for that option (`ucidef_set_poe` belongs to the `realtek-poe`
package), so this repository adds one, in three parts:

- netifd reads a default for `pse` from board.json's `network_device`, as
  it does for `gro` and the DSA conduit. A `pse` option in the port's UCI
  section takes precedence, and removing it there returns the port to the
  default. The default is filled in where netifd parses a device's UCI
  settings, so that it applies again when a change to the section
  restarts the device, and when a device without a section is claimed.
  The build carries it as a patch to OpenWrt's netifd package; upstream
  it is a pull request to netifd, after which OpenWrt's netifd package
  needs a bump.
- base-files gets `ucidef_set_network_device_pse PORT 0|1`. It writes a
  JSON boolean, which netifd's typed lookup needs; the existing
  `ucidef_set_network_device_gro` writes a string, so netifd never applies
  a GRO default given that way (nothing in the tree uses it).
- the device's case in `realtek_setup_poe()` sets it for lan1-lan7.

Without the netifd change, the default is ignored and the ports stay off,
so the board pull request names the netifd pull request it depends on.
The alternative, if reviewers prefer it, is to leave the supply out of
the device tree upstream: the ports then run in auto mode, on and without
a budget, as in the stock firmware.

The PoE firmware is the other part of the old first-boot setup that an
OpenWrt image needs: `kmod-pse-realtek-rtl8239` installs
`/lib/upgrade/keep.d/pse-realtek-rtl8239`, so that sysupgrade keeps the
firmware the owner copied in.

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
  has it. In October 2026 it was not in net-next: v5 ("net: pse-pd: add
  poll path and LED trigger support", April 2026) was the last version.
- The `pse-led-triggers` binding patch belongs with that series' LED
  trigger patches. Expect the device tree maintainers to object to trigger
  names that contain a Linux I²C bus number (`pse-1-0022:...`), and to ask
  for `trigger-sources = <&pse_port1>` instead. That needs the trigger
  series to match LEDs by `trigger-sources`, so raise it with its author.
- The device commit needs the RTL8239 driver commit and the base-files
  commit before it.
- The board's PoE LEDs need openwrt/openwrt#25341; until it is merged,
  an OpenWrt image of the board has them dark.

## Building the submissions

You review the patches, you sign them off, and you send them: only you
can certify the
[Developer's Certificate of Origin](https://www.kernel.org/doc/html/latest/process/submitting-patches.html#sign-your-work-the-developer-s-certificate-of-origin),
so the script adds your `Signed-off-by` only with `--signoff`, and
[coding-assistants](https://docs.kernel.org/process/coding-assistants.html)
still says that an AI agent must not add one. The kernel dropped its
`Assisted-by` tag in October 2026 ("docs: remove Assisted-by references",
applied to docs-next on 14 October), so the script adds none unless you
pass `--assisted-by`.
[generated-content](https://docs.kernel.org/process/generated-content.html)
still asks for the changelog to say when a tool found a problem.

Author and maintainer default to your `git config` identity. Pass
`--author "Name <email>"` if that is a GitHub noreply address: the kernel
needs an address that receives mail, and OpenWrt's formalities check
rejects noreply addresses.

### The trees

An `openwrt.git` checkout, and a Linux checkout with the subsystem trees
as remotes. The Linux history has to reach back to v6.18 (the Kconfig and
Makefile hunks merge against it) and to the commit in the fix's `Fixes:`
tag:

```sh
git clone https://github.com/openwrt/openwrt.git ~/openwrt
git clone --shallow-since=2025-01-01 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git ~/linux
cd ~/linux
K=https://git.kernel.org/pub/scm/linux/kernel/git
git remote add -t main net $K/netdev/net.git
git remote add -t main net-next $K/netdev/net-next.git
git remote add -t for-leds-next leds $K/lee/leds.git
git fetch --shallow-since=2025-01-01 --multiple net net-next leds
```

On Windows (Git Bash), clone both with `-c core.autocrlf=false`. Linux has
file names that differ only in case, so check out only what the series
need:

```sh
git -C ~/linux sparse-checkout set --cone Documentation/devicetree/bindings \
	drivers/leds drivers/net/pse-pd include/linux include/dt-bindings \
	scripts LICENSES
mkdir -p ~/linux/{arch,fs,init,ipc,kernel,lib}   # checkpatch looks for them
```

checkpatch's license check runs `scripts/spdxcheck.py`, which needs a
`python3` with `ply` and `GitPython`; `yamllint` and `dt-doc-validate` (from
`dtschema`) check the bindings' schemas. The full `make dt_binding_check`
and test builds need Linux, such as WSL2.

### OpenWrt

One pull request per branch, each from main:

```sh
for pr in dsa-fix pse-fix board; do
	git -C ~/openwrt checkout -B mokerlink-$pr origin/main
	scripts/make-upstream.py openwrt ~/openwrt $pr --signoff
done
# the commits on the branches, the patches in build/upstream/openwrt/PR
```

The board and the switch driver fix change OpenWrt's own files. Their
patches are made against the pinned commit and applied with a three-way
merge, so they follow main unless someone changed the same lines. The PSE
core fix becomes `898-*`, after the PSE series it applies on top of; once
it is on netdev, make its branch again with `--link
https://lore.kernel.org/...` so that the pending patch links to it. Push
the branches to your fork of openwrt/openwrt and open the pull requests on
GitHub. With `build realtek/rtl930x/mokerlink_poe-10g07110gsm` in its
description, OpenWrt's CI builds the board pull request's images and
comments with the result. The commit author's address should be one of
your GitHub account's, which OpenWrt's formalities check warns about
otherwise.

### netifd

The board.json default for the `pse` option, on a branch of a netifd
checkout (the build's patch is made against the version OpenWrt's netifd
package pins, and applied with a three-way merge):

```sh
git clone https://github.com/openwrt/netifd.git ~/netifd
git -C ~/netifd checkout -B pse-board-default origin/master
scripts/make-upstream.py netifd ~/netifd --signoff
# the commit on the branch, the patch in build/upstream/netifd
```

Push the branch to your fork of openwrt/netifd and open the pull request
there, mentioning Carlo Szelinsky, who added netifd's PSE options, and
Robert Marko, who merged them.

### Linux

One series at a time, each on its tree:

| Series | Base | Subject prefix |
|---|---|---|
| `pse-fix` | `net/main` | `[PATCH net]` |
| `leds` | `leds/for-leds-next` | `[PATCH]` |
| `pse` | `net-next/main`, once the PSE series it needs is in it | `[PATCH net-next]` |
| `rtl930x-switch` | `net-next/main` | `[PATCH net-next]` |

```sh
git -C ~/linux checkout -B mokerlink-pse-fix net/main
scripts/make-upstream.py linux ~/linux pse-fix -v 2 --signoff
cd ~/linux
./scripts/checkpatch.pl --strict -g net/main..HEAD
./scripts/get_maintainer.pl ~/mokerlink/build/upstream/linux/pse-fix/*.patch
git send-email --to=netdev@vger.kernel.org --cc=... ~/mokerlink/build/upstream/linux/pse-fix/*.patch
```

For a series with bindings, also run `make dt_binding_check
DT_SCHEMA_FILES=<binding>`. checkpatch warns that the binding patches may
need a MAINTAINERS update: the entry comes with the driver patch, in
alphabetical order with you as maintainer. Send net-next series only while
net-next is open.

A new version after a review: change the commit messages and diffs in
`upstream/linux/SERIES/`, write what changed since each earlier version,
with a link to it on lore, to `upstream/linux/SERIES/changes.txt`, and
build it with `-v N`. The changes go below the `---` line of a single
patch, or into the cover letter. The script empties the output directory
first, so `*.patch` is only the new version. netdev wants at least 24
hours between versions, and each version as a new thread rather than a
reply to the earlier one.

The PSE core fix goes to the net tree on its own. It is made against
mainline (it touches the same functions as in OpenWrt's kernel, without
OpenWrt's pending PSE patches around them), was tested on the switch in
OpenWrt and build-tested on mainline (`scripts/check-kernel.sh`, which
CI runs on the tip of the net tree with `W=1`), and is a regular bug for the
kernel's threat model (it needs an admin action or an unplug), so it goes
to the list, not to the security team.

The switch driver series is checked by `scripts/check-rtl930x-switch.sh`:
on the tip of net-next, checkpatch, then a `W=1` and sparse build of
every patch for MIPS and a `COMPILE_TEST` build for x86_64. The
`rtl930x-switch` workflow runs it on every push that changes the series,
together with `scripts/build-rtl930x-test-image.sh`, which builds a
RAM-boot test image of net-next with the series for this board. The
hardware test and its plan are in `upstream/linux/rtl930x-switch/test/`;
the cover letter's Testing section takes only what they showed.

`scripts/make-upstream.py patches` writes the kernel patches as OpenWrt
carries them to `build/upstream/patches`, for a quick review without a
tree. They are in the format of OpenWrt's `make target/linux/refresh`,
as in the pull requests: no git header lines, and the function in each
hunk header cut to 40 characters.

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
- **The I²C buses:** reviewers may ask why the board doesn't use the
  SoC's I²C master on the same pins. The PoE firmware download rules it
  out: it is one 25 KiB write, the master moves at most 16 bytes per
  transfer, and the chips NAK a split one.
- **Port LEDs:** they belong to the switch driver, in its port nodes: all
  of a port's LEDs with on/off control, and hardware offload of the
  triggers on top (Andrew Lunn on the `leds` series). In Linux that waits
  for an RTL930x switch driver, which `rtl930x-switch` starts; in
  OpenWrt, openwrt/openwrt#25341 adds
  software-controlled LEDs to the switch driver.
