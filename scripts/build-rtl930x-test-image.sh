#!/usr/bin/env bash
# Builds a RAM-boot test image of net-next with the RTL930x switch driver
# series, for the MokerLink POE-10G07110GSM (about 15 minutes):
#
#   - the kernel, with the test configuration and the test device tree
#     appended (upstream/linux/rtl930x-switch/test/), and for the test only
#     the MDIO polling series, a log of the polling setup the boot loader
#     left, and the PHY driver entries the board needs for 1 Gb/s links
#   - an initramfs with a static busybox, ethtool and iperf3, and the test
#     scripts; the switch driver's names the ports lan1-lan8 at boot
#   - as a uImage for the vendor U-Boot, with the MokerLink magic
#     (uImage-rtl930x-switch-test.bin) and the standard one (-stdmagic.bin)
#
# Run scripts/check-rtl930x-switch.sh first: it leaves the series applied
# in build/check/net-next. Needs its tools, plus curl, cpio, xz, bzip2,
# u-boot-tools (mkimage), device-tree-compiler and autoconf/automake.
# Output in build/check/rtl930x-switch/image.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=${CHECK_CACHE:-$REPO_ROOT/build/check}
NET=$CACHE/net-next
TEST=$REPO_ROOT/upstream/linux/rtl930x-switch/test
SRC=$CACHE/test-src
OUT=$CACHE/rtl930x-switch/image
KBUILD=$CACHE/rtl930x-switch/build-test
ROOT=$OUT/root
CROSS=${CROSS_COMPILE:-mips-linux-gnu-}
HOST=${CROSS%-}
JOBS=$(nproc)

BUSYBOX=busybox-1.36.1
LIBMNL=libmnl-1.0.5
ETHTOOL=ethtool-6.11
IPERF=iperf-3.17.1

for tool in curl cpio mkimage dtc gzip "${CROSS}gcc"; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done
[ -d "$NET/drivers/net/ethernet/realtek/rtl9300" ] ||
	{ echo "run scripts/check-rtl930x-switch.sh first" >&2; exit 1; }

rm -rf "$OUT" "$KBUILD"
mkdir -p "$SRC" "$OUT" "$ROOT"/{bin,sbin,usr/bin,usr/sbin,dev,proc,sys,tmp}

fetch() { # url
	local file
	file=$SRC/$(basename "$1")
	[ -f "$file" ] || curl -fsSL -o "$file" "$1"
	tar -C "$SRC" -xf "$file"
}

echo "==> busybox"
fetch "https://busybox.net/downloads/$BUSYBOX.tar.bz2"
make -s -C "$SRC/$BUSYBOX" CROSS_COMPILE="$CROSS" defconfig
sed -i -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' \
	-e 's/^CONFIG_TC=y/# CONFIG_TC is not set/' "$SRC/$BUSYBOX/.config"
make -s -C "$SRC/$BUSYBOX" CROSS_COMPILE="$CROSS" oldconfig </dev/null >/dev/null
make -s -C "$SRC/$BUSYBOX" CROSS_COMPILE="$CROSS" -j"$JOBS" busybox
cp "$SRC/$BUSYBOX/busybox" "$ROOT/bin/"

echo "==> ethtool"
fetch "https://www.netfilter.org/pub/libmnl/$LIBMNL.tar.bz2"
fetch "https://www.kernel.org/pub/software/network/ethtool/$ETHTOOL.tar.xz"
(cd "$SRC/$LIBMNL" && ./configure -q --host="$HOST" --prefix="$SRC/sysroot" \
	--enable-static --disable-shared && make -s -j"$JOBS" install)
(cd "$SRC/$ETHTOOL" && PKG_CONFIG_LIBDIR="$SRC/sysroot/lib/pkgconfig" \
	./configure -q --host="$HOST" LDFLAGS=-static && make -s -j"$JOBS")
cp "$SRC/$ETHTOOL/ethtool" "$ROOT/sbin/"

echo "==> iperf3"
fetch "https://downloads.es.net/pub/iperf/$IPERF.tar.gz"
(cd "$SRC/$IPERF" && ./configure -q --host="$HOST" --disable-shared \
	--enable-static-bin --without-openssl && make -s -j"$JOBS")
cp "$SRC/$IPERF/src/iperf3" "$ROOT/usr/bin/"

echo "==> initramfs"
install -m 0755 "$TEST/init" "$ROOT/init"
install -m 0755 "$TEST/rtl9300-switch-test" "$TEST/rtl9300-mdio-test" "$ROOT/usr/sbin/"
"${CROSS}strip" "$ROOT/bin/busybox" "$ROOT/sbin/ethtool" "$ROOT/usr/bin/iperf3"
# The kernel opens /dev/console for init before init mounts devtmpfs. A
# directory can't hold the device node without root, a cpio list can
echo 'nod /dev/console 0600 0 0 c 5 1' >"$OUT/initramfs.list"

echo "==> kernel"
# On top of the switch series, for the test only: the MDIO polling series
# (upstream/linux/mdio-polling), a patch that logs the polling registers
# as the boot loader left them, and the PHY driver entries the board needs
# for 1 Gb/s links, the two patches of the rtl8261n-rtl8264b series
TEST_PATCHES="$(ls "$REPO_ROOT"/upstream/linux/mdio-polling/[0-9]*.diff)
$TEST/mdio-polling-dump.patch
$TEST/phy-01-rtl8261n.diff
$TEST/phy-02-rtl8264b.diff"
APPLIED=""
# The tree goes back to the series alone however the script ends
revert_test_patches() {
	for p in $APPLIED; do
		git -C "$NET" apply -R "$p"
	done
}
trap revert_test_patches EXIT
for p in $TEST_PATCHES; do
	git -C "$NET" apply "$p"
	APPLIED="$p $APPLIED"
done
make -s -C "$NET" O="$KBUILD" ARCH=mips CROSS_COMPILE="$CROSS" allnoconfig
cat "$TEST/config" - >"$KBUILD/test.config" <<EOF
CONFIG_INITRAMFS_SOURCE="$ROOT $OUT/initramfs.list"
CONFIG_CMDLINE_BOOL=y
CONFIG_CMDLINE="console=ttyS0,115200"
CONFIG_CMDLINE_OVERRIDE=y
EOF
(cd "$NET" && ARCH=mips CROSS_COMPILE="$CROSS" scripts/kconfig/merge_config.sh \
	-m -O "$KBUILD" "$KBUILD/.config" "$KBUILD/test.config" >/dev/null)
make -s -C "$NET" O="$KBUILD" ARCH=mips CROSS_COMPILE="$CROSS" olddefconfig
for sym in RTL9300_SWITCH MACH_REALTEK_RTL MIPS_RAW_APPENDED_DTB MDIO_REALTEK_RTL9300 \
	COMPAT_32BIT_TIME DEVMEM; do
	grep -q "^CONFIG_$sym=y" "$KBUILD/.config" ||
		{ echo "CONFIG_$sym is not set in the test kernel" >&2; exit 1; }
done
make -C "$NET" O="$KBUILD" ARCH=mips CROSS_COMPILE="$CROSS" -j"$JOBS" \
	vmlinux.bin >"$OUT/kernel.log" 2>&1 || { tail -40 "$OUT/kernel.log"; exit 1; }

echo "==> device tree"
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
	-I "$NET/include" -I "$NET/arch/mips/boot/dts/realtek" \
	"$TEST/mokerlink-poe-10g07110gsm.dts" |
	dtc -q -I dts -O dtb -o "$OUT/mokerlink-poe-10g07110gsm.dtb" -

echo "==> uImage"
load=$(awk '$3 == "_text" { print substr($1, length($1) - 7) }' "$KBUILD/System.map")
entry=$(awk '$3 == "kernel_entry" { print substr($1, length($1) - 7) }' "$KBUILD/System.map")
cat "$KBUILD/arch/mips/boot/vmlinux.bin" "$OUT/mokerlink-poe-10g07110gsm.dtb" |
	gzip -9 >"$OUT/vmlinux-dtb.bin.gz"
mkimage -q -A mips -O linux -T kernel -C gzip -a "0x$load" -e "0x$entry" \
	-n "net-next rtl930x-switch test" -d "$OUT/vmlinux-dtb.bin.gz" \
	"$OUT/uImage-rtl930x-switch-test-stdmagic.bin"
python3 "$REPO_ROOT/scripts/uimage-remagic.py" \
	"$OUT/uImage-rtl930x-switch-test-stdmagic.bin" 0x83800000 \
	"$OUT/uImage-rtl930x-switch-test.bin"
git -C "$NET" log --oneline -12 >"$OUT/series.txt"
for p in $TEST_PATCHES; do
	echo "test only, not a commit: $(basename "$(dirname "$p")")/$(basename "$p")"
done >>"$OUT/series.txt"
cp "$KBUILD/.config" "$OUT/kernel.config"
rm -rf "$ROOT" "$OUT/initramfs.list" "$OUT/vmlinux-dtb.bin.gz"

ls -l "$OUT"
echo "load 0x$load, entry 0x$entry"
