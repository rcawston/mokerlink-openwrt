#!/usr/bin/env bash
# Builds the RTL8261N and RTL8264B PHY patches as they go to netdev, on the
# tip of net-next (about 15 minutes):
#
#   - the series applies (scripts/make-upstream.py) and every patch passes
#     checkpatch --strict at 80 columns, apart from the Signed-off-by its
#     author adds
#   - every patch builds on its own, as a module, for the Realtek MIPS SoCs
#     and for x86_64, with W=1 and sparse, and adds no compiler, sparse or
#     kernel-doc -Wall warning to drivers/net/phy/realtek. Warnings that
#     net-next itself has there are counted on its tip first and not held
#     against the patches
#   - the whole series links, for both
#
# Needs git, python3, perl, sparse v0.6.5-rc1 or later (net-next hands it
# __typeof_unqual__, which the 0.6.4 release can't parse), a MIPS cross
# compiler (Debian/Ubuntu: gcc-mips-linux-gnu) and the kernel's build tools
# (gcc, make, bc, bison, flex, libelf-dev, libssl-dev). The clone is cached
# in build/check/net-next-phy, the patches and the logs go to
# build/check/rtl8261n-rtl8264b.

set -euo pipefail
# sort and comm must agree on the order
export LC_ALL=C

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=${CHECK_CACHE:-$REPO_ROOT/build/check}
NET=$CACHE/net-next-phy
OUT=$CACHE/rtl8261n-rtl8264b
LOGS=$OUT/logs
DRV=drivers/net/phy/realtek
CROSS=${CROSS_COMPILE:-mips-linux-gnu-}
JOBS=$(nproc)

for tool in git python3 perl sparse make "${CROSS}gcc" gcc; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

mkdir -p "$CACHE"
rm -rf "$OUT"
mkdir -p "$LOGS"

# An older sparse fails on every file of the driver, and so would hide
# its warnings
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

python3 "$REPO_ROOT/scripts/make-upstream.py" linux "$NET" rtl8261n-rtl8264b \
	--author "CI <ci@example.org>" -o "$OUT/patches"
TIP=$(git -C "$NET" rev-parse HEAD)

fail=0

echo "==> checkpatch --strict"
for p in "$OUT"/patches/*.patch; do
	case $p in */0000-cover-letter.patch) continue ;; esac
	log=$LOGS/$(basename "$p" .patch).checkpatch
	if (cd "$NET" && ./scripts/checkpatch.pl --strict --no-signoff \
		--max-line-length=80 "$p") >"$log" 2>&1; then
		echo "  ok: $(basename "$p")"
	else
		echo "  FAIL: $(basename "$p")"
		cat "$log"
		fail=1
	fi
done

kmake() { # mips|x86_64 make-arguments...
	local arch=$1
	shift
	if [ "$arch" = mips ]; then
		make -C "$NET" O="$OUT/build-mips" ARCH=mips CROSS_COMPILE="$CROSS" "$@"
	else
		make -C "$NET" O="$OUT/build-x86_64" ARCH=x86_64 "$@"
	fi
}

config() { # mips|x86_64
	local file=$OUT/build-$1/.config
	kmake "$1" -s allnoconfig
	if [ "$1" = mips ]; then
		"$NET/scripts/config" --file "$file" \
			-d MIPS_GENERIC_KERNEL -e MACH_REALTEK_RTL \
			-d CPU_R4X00 -e CPU_MIPS32_R2 -e CPU_BIG_ENDIAN
	fi
	"$NET/scripts/config" --file "$file" \
		-e MODULES -e NET -e NETDEVICES -e PHYLIB -m REALTEK_PHY
	kmake "$1" -s olddefconfig
	local want=(CONFIG_REALTEK_PHY=m)
	[ "$1" = mips ] && want+=(CONFIG_MACH_REALTEK_RTL=y CONFIG_CPU_BIG_ENDIAN=y)
	for sym in "${want[@]}"; do
		grep -q "^$sym\$" "$file" ||
			{ echo "$sym not in the $1 configuration" >&2; exit 1; }
	done
}

# The warnings and errors that the compiler, sparse and kernel-doc report
# in the driver, without line numbers, so that net-next's own can be told
# from new ones; and any error elsewhere, as sparse goes on after one but
# hasn't checked all of the driver then
warnings() { # log
	grep -E "(^|/)$DRV/[^: ]+:[0-9]+:([0-9]+:)? (warning|error):|^Warning: $DRV/[^: ]+:[0-9]+ |:[0-9]+:([0-9]+:)? error:" "$1" |
		sed -E -e "s|^(.*/)?($DRV/[^: ]+):[0-9]+:([0-9]+:)?|\\2:|" \
			-e "s|^(Warning: $DRV/[^: ]+):[0-9]+ |\\1: |" | sort || true
}

# One build of the driver with W=1 and sparse, and kernel-doc -Wall; the
# warnings go to $LOGS/ARCH-NAME.warnings. C=1 only checks what is
# compiled, so the step fails unless realtek_main.c was compiled and
# checked again, and it fails if kernel-doc itself breaks
build_step() { # mips|x86_64 name
	local log=$LOGS/$1-$2.log
	kmake "$1" -j"$JOBS" W=1 C=1 "$DRV/" >"$log" 2>&1 || return 1
	if ! grep -qE "CC \[M\] +$DRV/realtek_main\.o" "$log" ||
		! grep -qE "CHECK +(.*/)?$DRV/realtek_main\.c" "$log"; then
		echo "realtek_main.c was not compiled and checked" >>"$log"
		return 1
	fi
	(cd "$NET" && ./scripts/kernel-doc -none -Wall "$DRV"/*.[ch]) \
		>"$LOGS/$1-$2.kernel-doc" 2>&1 || true
	if grep -qE '^Traceback|^Error|error:' "$LOGS/$1-$2.kernel-doc"; then
		cat "$LOGS/$1-$2.kernel-doc" >>"$log"
		return 1
	fi
	cat "$log" "$LOGS/$1-$2.kernel-doc" >"$log.all"
	warnings "$log.all" >"$LOGS/$1-$2.warnings"
}

for arch in mips x86_64; do
	echo "==> Every patch on its own: $arch, W=1, sparse, kernel-doc"
	git -C "$NET" checkout -q "$BASE"
	config "$arch"
	if ! build_step "$arch" base; then
		echo "  FAIL (build): net-next itself"
		tail -40 "$LOGS/$arch-base.log"
		fail=1
		continue
	fi
	echo "  net-next: $(wc -l <"$LOGS/$arch-base.warnings") warnings in $DRV"
	n=0
	for commit in $(git -C "$NET" rev-list --reverse "$BASE..$TIP"); do
		n=$((n + 1))
		git -C "$NET" checkout -q "$commit"
		subject=$(git -C "$NET" log -1 --format=%s "$commit")
		if ! build_step "$arch" "$n"; then
			echo "  FAIL (build): $n $subject"
			tail -40 "$LOGS/$arch-$n.log"
			fail=1
			continue
		fi
		new=$(comm -13 "$LOGS/$arch-base.warnings" "$LOGS/$arch-$n.warnings")
		if [ -n "$new" ]; then
			echo "  FAIL (warnings): $n $subject"
			echo "$new"
			fail=1
		else
			echo "  ok: $n $subject"
		fi
	done

	echo "==> The series, linked: $arch"
	git -C "$NET" checkout -q "$TIP"
	if ! kmake "$arch" -j"$JOBS" vmlinux modules >"$LOGS/$arch-link.log" 2>&1; then
		echo "  FAIL"
		tail -40 "$LOGS/$arch-link.log"
		fail=1
	elif grep -q '^WARNING: modpost' "$LOGS/$arch-link.log"; then
		echo "  FAIL (modpost)"
		grep '^WARNING: modpost' "$LOGS/$arch-link.log"
		fail=1
	else
		echo "  ok"
	fi
done

# Who the patches go to, for git send-email
(cd "$NET" && ./scripts/get_maintainer.pl --nogit --nogit-fallback \
	"$OUT"/patches/000[1-9]-*.patch) >"$LOGS/get_maintainer.txt" 2>&1 || true

if [ "$fail" -ne 0 ]; then
	echo "rtl8261n-rtl8264b: checks failed, logs in $LOGS" >&2
	exit 1
fi
echo "rtl8261n-rtl8264b: the patches apply, pass checkpatch and build without new warnings"
