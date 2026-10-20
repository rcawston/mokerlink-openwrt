#!/usr/bin/env bash
# Builds the PSE core fixes as they go to netdev, on the tip of the net tree
# (a few minutes): each set of series applies, and pse_core.c compiles
# without warnings with W=1. pse-prio-fix applies on top of pse-fix, and
# pse-detect-fix both on its own and on top of the other two.
#
# Needs git, python3 and the kernel's build tools (gcc, make, bc, bison,
# flex, libelf-dev, libssl-dev). The clone is cached in build/check/net.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CACHE=${CHECK_CACHE:-$REPO_ROOT/build/check}
NET=$CACHE/net
LOG=$CACHE/pse-fix-build.log

mkdir -p "$CACHE"
if [ -d "$NET/.git" ]; then
	git -C "$NET" fetch -q --depth 1 origin main
	git -C "$NET" checkout -q -f -B main FETCH_HEAD
else
	git clone -q --depth 1 --branch main \
		https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net.git "$NET"
fi
BASE=$(git -C "$NET" rev-parse HEAD)
echo "net at $(git -C "$NET" log -1 --format='%h %s')"

cd "$NET"
make -s defconfig
scripts/config -e REGULATOR -e PSE_CONTROLLER
make -s olddefconfig
grep -qx 'CONFIG_PSE_CONTROLLER=y' .config || { echo "PSE_CONTROLLER not enabled" >&2; exit 1; }

build() { # series...: applied in this order on the tip of net
	local series
	git checkout -q -f -B main "$BASE"
	for series in "$@"; do
		python3 "$REPO_ROOT/scripts/make-upstream.py" linux "$NET" "$series" \
			--author "CI <ci@example.org>" -o "$CACHE/$series"
	done
	make -s -j"$(nproc)" W=1 drivers/net/pse-pd/pse_core.o 2>&1 | tee "$LOG"
	# Only the PSE core's own warnings: W=1 also applies to what make
	# prepare builds first
	if grep -q '^drivers/net/pse-pd/.*warning:' "$LOG"; then
		echo "pse_core.c has warnings with W=1 ($*)" >&2
		exit 1
	fi
	echo "builds on net without warnings (W=1): $*"
}

build pse-fix
build pse-fix pse-prio-fix pse-detect-fix
build pse-detect-fix
