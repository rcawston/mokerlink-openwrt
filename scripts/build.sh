#!/usr/bin/env bash
# Build OpenWrt for the MokerLink POE-10G07110GSM.
#
#   scripts/build.sh [-j JOBS] [--clean]
#
# Checks out the pinned OpenWrt commit into build/openwrt, adds this
# repository's board support and rootfs files, and builds. The images land in
# out/. A first build downloads about 1 GB of sources and needs about 20 GB
# of disk and 1-3 hours, depending on the machine.
#
# Host requirements: https://openwrt.org/docs/guide-developer/toolchain/install-buildsystem

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=../config/openwrt.env
. "$REPO_ROOT/config/openwrt.env"

BUILD_DIR=${BUILD_DIR:-$REPO_ROOT/build}
OUT_DIR=${OUT_DIR:-$REPO_ROOT/out}
OPENWRT_DIR=$BUILD_DIR/openwrt
JOBS=$(nproc)
CLEAN=0

while [ $# -gt 0 ]; do
	case "$1" in
	-j) JOBS=$2; shift 2 ;;
	-j*) JOBS=${1#-j}; shift ;;
	--clean) CLEAN=1; shift ;;
	-h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "unknown argument: $1" >&2; exit 1 ;;
	esac
done

log() { printf '\n==> %s\n' "$*"; }

log "OpenWrt $OPENWRT_COMMIT"
mkdir -p "$BUILD_DIR"
if [ ! -d "$OPENWRT_DIR/.git" ]; then
	git init -q "$OPENWRT_DIR"
	git -C "$OPENWRT_DIR" remote add origin "$OPENWRT_REPO"
fi
if ! git -C "$OPENWRT_DIR" cat-file -e "$OPENWRT_COMMIT^{commit}" 2>/dev/null; then
	git -C "$OPENWRT_DIR" fetch --depth 1 origin "$OPENWRT_COMMIT"
fi

# The checkout under build/ is generated: reset tracked files to the pinned
# commit so the board patch always applies to a pristine tree. dl/, feeds/,
# staging_dir/ and build_dir/ are untracked and survive, so rebuilds are
# incremental.
git -C "$OPENWRT_DIR" checkout -q -f "$OPENWRT_COMMIT"
git -C "$OPENWRT_DIR" reset -q --hard "$OPENWRT_COMMIT"

log "Applying board support"
for p in "$REPO_ROOT"/patches/*.patch; do
	echo "  $(basename "$p")"
	git -C "$OPENWRT_DIR" apply --whitespace=nowarn "$p"
done
cp -r "$REPO_ROOT/openwrt/." "$OPENWRT_DIR/"

# Rootfs overlay: OpenWrt copies <buildroot>/files into every image.
rm -rf "$OPENWRT_DIR/files"
cp -r "$REPO_ROOT/files" "$OPENWRT_DIR/files"

log "Feeds"
cp "$REPO_ROOT/config/feeds.conf" "$OPENWRT_DIR/feeds.conf"
(cd "$OPENWRT_DIR" && ./scripts/feeds update -a && ./scripts/feeds install -a)

log "Configuration"
cp "$REPO_ROOT/config/mokerlink.diffconfig" "$OPENWRT_DIR/.config"
make -C "$OPENWRT_DIR" defconfig >/dev/null

# defconfig silently drops symbols it cannot satisfy; fail instead of
# building a different firmware than the one described in the diffconfig.
missing=0
while IFS= read -r line; do
	case "$line" in
	CONFIG_*=*)
		grep -qxF "$line" "$OPENWRT_DIR/.config" || { echo "  not applied: $line" >&2; missing=1; } ;;
	"# CONFIG_"*" is not set")
		sym=${line#"# "}; sym=${sym%" is not set"}
		if grep -q "^$sym=" "$OPENWRT_DIR/.config"; then
			echo "  still enabled: $sym" >&2; missing=1
		fi ;;
	esac
done < "$REPO_ROOT/config/mokerlink.diffconfig"
[ "$missing" = 0 ] || { echo "config/mokerlink.diffconfig did not apply cleanly" >&2; exit 1; }

if [ "$CLEAN" = 1 ]; then
	log "Cleaning"
	make -C "$OPENWRT_DIR" clean
fi

log "Downloading sources"
make -C "$OPENWRT_DIR" -j"$JOBS" download

log "Building with $JOBS jobs"
if ! make -C "$OPENWRT_DIR" -j"$JOBS"; then
	log "Parallel build failed, retrying single-threaded for a readable error"
	make -C "$OPENWRT_DIR" -j1 V=s
fi

log "Collecting images"
BIN_DIR=$OPENWRT_DIR/bin/targets/$OPENWRT_TARGET/$OPENWRT_SUBTARGET
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp "$BIN_DIR"/*"$OPENWRT_DEVICE"* "$OUT_DIR/"
cp "$BIN_DIR"/*.manifest "$OUT_DIR/" 2>/dev/null || true

# The stock boot loader may expect either the MokerLink uImage magic
# (0x83800000, what the image is built with) or the standard one. Provide
# both RAM images so either can be booted without a rebuild; check which one
# your boot loader wants with docs/03-install.md, step 1.
initramfs=$(ls "$OUT_DIR"/*"$OPENWRT_DEVICE"-initramfs-kernel.bin)
python3 "$REPO_ROOT/scripts/uimage-remagic.py" "$initramfs" 0x27051956 \
	"${initramfs%.bin}-stdmagic.bin"

(cd "$OUT_DIR" && sha256sum -- *.bin *.bix > sha256sums)
log "Done"
ls -l "$OUT_DIR"
