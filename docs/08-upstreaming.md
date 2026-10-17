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

Not submitted: the `mokerlink-*` tools and the fan control service. Without
the service the fans run at full speed, as the ADT7476 does from power-on;
the adt7475 driver cannot set a start duty cycle from the device tree
without a deprecated binding form. A fan control solution OpenWrt accepts
is an open point for the device commit.

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
The kernel asks for an `Assisted-by: AGENT:MODEL_VERSION` tag on code
written with an AI assistant
([coding-assistants](https://docs.kernel.org/process/coding-assistants.html));
OpenWrt has a proposal to follow the kernel's rules. The script requires
the tag and adds your sign-off only with `--signoff`. Author and maintainer
default to your `git config` identity.

OpenWrt, on a branch of a clean `openwrt.git` main checkout:

```sh
git -C ~/openwrt checkout -b mokerlink-poe-10g07110gsm origin/main
scripts/make-upstream.py openwrt ~/openwrt --signoff \
	--assisted-by "Claude:<model version>"
# four commits on the branch, and the patches in build/upstream/openwrt
```

Open the pull request on GitHub from that branch, after a test build of
it. If main moved since the pinned commit, the device commit may need a
rebase (the image recipe and `02_network` lists change often).

Linux, one series at a time, each on a branch of its subsystem tree:

```sh
git -C ~/linux checkout -b rtl8239 net-next/main
scripts/make-upstream.py linux ~/linux pse --signoff \
	--assisted-by "Claude:<model version>"
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

For net-next, put `net-next` in the subject prefix
(`git format-patch --subject-prefix="PATCH net-next"`) and send only while
net-next is open. Older checkpatch versions don't know `Assisted-by` and
report it as a non-standard signature.

`scripts/make-upstream.py patches` writes the kernel patches as OpenWrt
carries them to `build/upstream/patches`, for a quick review without a
tree.

## What reviewers will likely ask

- **RTL8239:** there is no datasheet; the driver follows the vendor
  firmware. The firmware is not redistributable, so it is not in
  linux-firmware and the driver works without it until it is installed.
  Expect questions about the broadcast download (each chip's instance
  sends the image to the shared address; the other chip ignores it) and
  about the second address as an ancillary device.
- **Shared-SCL buses:** reviewers may suggest i2c-gpio plus a mux, or the
  SoC's I²C master. A mux can't switch SDA lines without hardware for it,
  and the master is ruled out by the PoE firmware download (one 25 KiB
  write; the chips NAK a split one). Both are explained in the cover
  letter.
- **Port LEDs:** whether the switch's DSA driver should own the port LEDs
  instead (there is no upstream RTL9300 DSA driver yet; the switch's other
  children are separate drivers on its syscon).
