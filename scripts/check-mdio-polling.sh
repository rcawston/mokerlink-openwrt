#!/usr/bin/env bash
# Builds the MDIO polling series as it goes to netdev, on the tip of
# net-next (about 10 minutes):
#
#   - the series applies (scripts/make-upstream.py) and every patch passes
#     checkpatch --strict, apart from the Signed-off-by its author adds
#   - every patch builds on its own for the Realtek MIPS SoCs, with W=1 and
#     sparse, without a warning in the driver
#   - the whole series builds and links for MIPS and, compile tested, for
#     x86_64
#
# Needs the tools of scripts/check-rtl930x-switch.sh. The clone is shared
# with it, in build/check/net-next; the patches and the logs go to
# build/check/mdio-polling.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=${CHECK_CACHE:-$REPO_ROOT/build/check}
NET=$CACHE/net-next
OUT=$CACHE/mdio-polling
LOGS=$OUT/logs
DRV=drivers/net/mdio/mdio-realtek-rtl9300
CROSS=${CROSS_COMPILE:-mips-linux-gnu-}
JOBS=$(nproc)

for tool in git python3 perl sparse make "${CROSS}gcc" gcc; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

mkdir -p "$CACHE"
rm -rf "$OUT"
mkdir -p "$LOGS"

# An older sparse fails on every file, and so would hide the driver's
# warnings
echo 'static int a; static __typeof_unqual__(a) b;' >"$OUT/sparse-probe.c"
probe=$(sparse "$OUT/sparse-probe.c" 2>&1 || true)
case $probe in
*error:*)
	echo "sparse $(sparse --version) can't parse __typeof_unqual__," \
		"which net-next uses: v0.6.5-rc1 or later is needed" >&2
	echo "$probe" >&2
	exit 1
	;;
esac

if [ -d "$NET/.git" ]; then
	git -C "$NET" fetch -q --depth 1 origin main
	git -C "$NET" checkout -q -f -B main FETCH_HEAD
	git -C "$NET" clean -q -fdx
else
	git clone -q --depth 1 --branch main \
		https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net-next.git "$NET"
fi
BASE=$(git -C "$NET" rev-parse HEAD)
echo "net-next at $(git -C "$NET" log -1 --format='%h %s')"

python3 "$REPO_ROOT/scripts/make-upstream.py" linux "$NET" mdio-polling \
	--author "CI <ci@example.org>" -o "$OUT/patches"

fail=0

# A warning or error the compiler or sparse reports in the driver, or an
# error in any file: sparse goes on after one, but hasn't checked all of
# the driver, and the build doesn't fail
driver_warnings() { # log
	grep -E "(^|/)$DRV\.c:[0-9]+:([0-9]+:)? (warning|error):|:[0-9]+:([0-9]+:)? error:" "$1" |
		head -40 || true
}

echo "==> checkpatch --strict"
for p in "$OUT"/patches/*.patch; do
	case $p in */0000-cover-letter.patch) continue ;; esac
	log=$LOGS/$(basename "$p" .patch).checkpatch
	if (cd "$NET" && ./scripts/checkpatch.pl --strict --no-signoff "$p") \
		>"$log" 2>&1; then
		echo "  ok: $(basename "$p")"
	else
		echo "  FAIL: $(basename "$p")"
		cat "$log"
		fail=1
	fi
done

MIPS_BUILD=$OUT/build-mips
X86_BUILD=$OUT/build-x86_64

config_mips() {
	"$NET/scripts/config" --file "$MIPS_BUILD/.config" \
		-d MIPS_GENERIC_KERNEL -e MACH_REALTEK_RTL \
		-d CPU_R4X00 -e CPU_MIPS32_R2 -e CPU_BIG_ENDIAN \
		-e MODULES -e NET -e NETDEVICES -e OF \
		-e PHYLIB -e MDIO_DEVICE -e MDIO_BUS -m MDIO_REALTEK_RTL9300
	make -s -C "$NET" O="$MIPS_BUILD" ARCH=mips CROSS_COMPILE="$CROSS" olddefconfig
}

echo "==> Every patch on its own: MIPS, W=1, sparse"
make -s -C "$NET" O="$MIPS_BUILD" ARCH=mips CROSS_COMPILE="$CROSS" allnoconfig
n=0
for commit in $(git -C "$NET" rev-list --reverse "$BASE..HEAD"); do
	n=$((n + 1))
	git -C "$NET" checkout -q "$commit"
	config_mips
	log=$LOGS/mips-$n.log
	subject=$(git -C "$NET" log -1 --format=%s "$commit")
	if ! make -C "$NET" O="$MIPS_BUILD" ARCH=mips CROSS_COMPILE="$CROSS" \
		-j"$JOBS" W=1 C=1 drivers/net/mdio/ >"$log" 2>&1; then
		echo "  FAIL (build): $n $subject"
		tail -40 "$log"
		fail=1
		continue
	fi
	warnings=$(driver_warnings "$log")
	if [ -n "$warnings" ]; then
		echo "  FAIL (warnings): $n $subject"
		echo "$warnings"
		fail=1
	else
		echo "  ok: $n $subject"
	fi
done

if ! grep -q '^CONFIG_MDIO_REALTEK_RTL9300=m' "$MIPS_BUILD/.config"; then
	echo "MDIO_REALTEK_RTL9300 not enabled in the MIPS configuration" >&2
	fail=1
fi

echo "==> The series, linked: MIPS"
if make -C "$NET" O="$MIPS_BUILD" ARCH=mips CROSS_COMPILE="$CROSS" -j"$JOBS" \
	vmlinux modules >"$LOGS/mips-link.log" 2>&1; then
	echo "  ok"
else
	echo "  FAIL"
	tail -40 "$LOGS/mips-link.log"
	fail=1
fi

echo "==> The series, compile tested: x86_64, W=1, sparse"
make -s -C "$NET" O="$X86_BUILD" ARCH=x86_64 CROSS_COMPILE= allnoconfig
"$NET/scripts/config" --file "$X86_BUILD/.config" \
	-e COMPILE_TEST -e OF -e MODULES -e NET -e NETDEVICES \
	-e PHYLIB -e MDIO_BUS -m MDIO_REALTEK_RTL9300
make -s -C "$NET" O="$X86_BUILD" ARCH=x86_64 CROSS_COMPILE= olddefconfig
if ! grep -q '^CONFIG_MDIO_REALTEK_RTL9300=m' "$X86_BUILD/.config"; then
	echo "MDIO_REALTEK_RTL9300 not enabled in the x86_64 configuration" >&2
	fail=1
elif make -C "$NET" O="$X86_BUILD" ARCH=x86_64 CROSS_COMPILE= -j"$JOBS" W=1 C=1 \
	drivers/net/mdio/ >"$LOGS/x86_64.log" 2>&1 &&
	make -C "$NET" O="$X86_BUILD" ARCH=x86_64 CROSS_COMPILE= -j"$JOBS" vmlinux modules \
	>"$LOGS/x86_64-link.log" 2>&1; then
	warnings=$(driver_warnings "$LOGS/x86_64.log")
	if [ -n "$warnings" ]; then
		echo "  FAIL (warnings)"
		echo "$warnings"
		fail=1
	else
		echo "  ok"
	fi
else
	echo "  FAIL"
	tail -40 "$LOGS/x86_64.log" "$LOGS/x86_64-link.log" 2>/dev/null
	fail=1
fi

if [ "$fail" -ne 0 ]; then
	echo "mdio-polling: checks failed, logs in $LOGS" >&2
	exit 1
fi
echo "mdio-polling: the series applies, passes checkpatch and builds without warnings"
