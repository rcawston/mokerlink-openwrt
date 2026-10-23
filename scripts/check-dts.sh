#!/usr/bin/env bash
# Fast checks that do not need a full OpenWrt build (about 1 minute):
#
#   - the board patch applies to the pinned OpenWrt commit, and the patches
#     to OpenWrt's netifd package to the netifd that the commit pins
#   - the device tree compiles against the pinned OpenWrt + kernel headers
#   - the image recipe and the device tree agree on uImage magic and
#     firmware partition size, and the flash partitions cover the chip
#     contiguously
#
# Needs git, cpp, dtc (Debian/Ubuntu: device-tree-compiler) and python3.
# Sparse checkouts are cached in build/check/.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=../config/openwrt.env
. "$REPO_ROOT/config/openwrt.env"

CACHE=${CHECK_CACHE:-$REPO_ROOT/build/check}
OWRT=$CACHE/openwrt
LINUX=$CACHE/linux
DTS_NAME=rtl9303_${OPENWRT_DEVICE}
DTS=$REPO_ROOT/openwrt/target/linux/realtek/dts/$DTS_NAME.dts

for tool in git cpp dtc python3; do
	command -v "$tool" >/dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done

sparse_clone() { # dir repo rev paths...
	local dir=$1 repo=$2 rev=$3
	shift 3
	if [ ! -d "$dir/.git" ]; then
		git init -q "$dir"
		git -C "$dir" remote add origin "$repo"
	fi
	# Also in a cached checkout, which may have fewer paths
	git -C "$dir" sparse-checkout set --no-cone "$@"
	if ! git -C "$dir" rev-parse -q --verify "$rev^{commit}" >/dev/null; then
		git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$rev"
	fi
	git -C "$dir" checkout -q -f "$rev" 2>/dev/null || git -C "$dir" checkout -q -f FETCH_HEAD
	git -C "$dir" clean -q -fdx
}

echo "==> Fetching pinned sources (sparse)"
mkdir -p "$CACHE"
# Every path that patches/ changes, for the check that they apply
sparse_clone "$OWRT" "$OPENWRT_REPO" "$OPENWRT_COMMIT" \
	/target/linux/realtek/ /target/linux/generic/ \
	/package/base-files/files/lib/functions/ /package/network/config/netifd/ \
	/package/kernel/linux/modules/
sparse_clone "$LINUX" "$LINUX_REPO" "$LINUX_TAG" \
	/include/dt-bindings/ /include/uapi/linux/

echo "==> Board patch applies to OpenWrt $OPENWRT_COMMIT"
for p in "$REPO_ROOT"/patches/*.patch; do
	git -C "$OWRT" apply --check "$p"
	git -C "$OWRT" apply "$p"
	echo "  ok: $(basename "$p")"
done

NETIFD_VERSION=$(sed -n 's/^PKG_SOURCE_VERSION:=//p' "$OWRT/package/network/config/netifd/Makefile")
echo "==> netifd patches apply to netifd $NETIFD_VERSION"
sparse_clone "$CACHE/netifd" https://github.com/openwrt/netifd.git "$NETIFD_VERSION" "/*"
for p in "$REPO_ROOT"/openwrt/package/network/config/netifd/patches/*.patch; do
	git -C "$CACHE/netifd" apply --check "$p"
	echo "  ok: $(basename "$p")"
done

echo "==> OpenWrt dt-bindings patches"
# Header changes OpenWrt carries on top of the kernel, e.g. PHY_POL_INVERT.
for p in "$OWRT"/target/linux/generic/{backport,pending,hack}-6.18/*.patch \
	 "$OWRT"/target/linux/realtek/patches-6.18/*.patch; do
	if grep -q '^+++ b/include/dt-bindings/' "$p"; then
		git -C "$LINUX" apply --include='include/dt-bindings/*' "$p"
		echo "  applied: $(basename "$p")"
	fi
done

echo "==> Compiling $DTS_NAME.dts"
out=$CACHE/out
mkdir -p "$out"
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
	-I "$REPO_ROOT/openwrt/target/linux/realtek/dts" \
	-I "$OWRT/target/linux/realtek/dts" \
	-I "$LINUX/include" \
	"$DTS" -o "$out/$DTS_NAME.pre.dts"
# The unit_address/interrupt warnings come from the shared rtl930x.dtsi and
# appear for every upstream RTL930x board; anything else fails the check.
dtc -q -I dts -O dtb -o "$out/$DTS_NAME.dtb" "$out/$DTS_NAME.pre.dts"
warnings=$(dtc -I dts -O dtb -o /dev/null "$out/$DTS_NAME.pre.dts" 2>&1 \
	| grep -v -e 'unit_address_vs_reg' -e 'interrupt_provider' -e 'interrupt_map' || true)
if [ -n "$warnings" ]; then
	printf '%s\n' "$warnings" >&2
	echo "unexpected dtc warnings" >&2
	exit 1
fi
dtc -q -I dtb -O dts -o "$out/$DTS_NAME.decompiled.dts" "$out/$DTS_NAME.dtb"
echo "  ok: $(stat -c %s "$out/$DTS_NAME.dtb") bytes"

echo "==> Consistency"
python3 - "$out/$DTS_NAME.decompiled.dts" "$OWRT/target/linux/realtek/image/rtl930x.mk" \
	"$OPENWRT_DEVICE" <<'EOF'
import re, sys

dts_path, mk_path, device = sys.argv[1:]
dts = open(dts_path).read()
mk = open(mk_path).read()
errors = []

m = re.search(r"define Device/%s\n(.*?)\nendef" % re.escape(device), mk, re.S)
if not m:
    sys.exit(f"Device/{device} not found in rtl930x.mk")
recipe = m.group(1)

def mk_var(name):
    v = re.search(r"^\s*%s\s*:=\s*(\S+)" % name, recipe, re.M)
    return v.group(1) if v else None

# Partitions, in flash order
parts = []
for node in re.finditer(r"partition@[0-9a-f]+ \{(.*?)\n\t*\};", dts, re.S):
    body = node.group(1)
    label = re.search(r'label = "([^"]+)"', body).group(1)
    off, size = (int(x, 16) for x in
                 re.search(r"reg = <(0x[0-9a-f]+) (0x[0-9a-f]+)>", body).groups())
    magic = re.search(r"openwrt,ih-magic = <(0x[0-9a-f]+)>", body)
    parts.append((off, size, label, "read-only" in body, magic and int(magic.group(1), 16)))
parts.sort()

end = 0
for off, size, label, ro, _ in parts:
    if off != end:
        errors.append(f"partition {label} starts at {off:#x}, expected {end:#x}")
    end = off + size
if end != 16 << 20:
    errors.append(f"partitions end at {end:#x}, expected 16 MiB")

fw = [p for p in parts if p[2] == "firmware"]
if len(fw) != 1:
    errors.append("expected exactly one 'firmware' partition")
else:
    _, fw_size, _, fw_ro, fw_magic = fw[0]
    if fw_ro:
        errors.append("firmware partition must be writable")
    img = mk_var("IMAGE_SIZE")
    if not img or not img.endswith("k") or int(img[:-1]) * 1024 != fw_size:
        errors.append(f"IMAGE_SIZE {img} != firmware partition {fw_size // 1024}k")
    mk_magic = mk_var("UIMAGE_MAGIC")
    mk_magic = int(mk_magic, 0) if mk_magic else 0x27051956
    dts_magic = fw_magic or 0x27051956
    if mk_magic != dts_magic:
        errors.append(f"UIMAGE_MAGIC {mk_magic:#x} != openwrt,ih-magic {dts_magic:#x}")

for _, _, label, ro, _ in parts:
    if label != "firmware" and not ro:
        errors.append(f"partition {label} should be read-only")

# first compatible in the decompiled tree is the root node's; dtc prints the
# string list as "a\0b"
compat = re.search(r'compatible = "([^"]+)"', dts).group(1).split("\\0")[0]
if compat != device.replace("_", ",", 1):
    errors.append(f"compatible {compat} does not match device {device}")

for e in errors:
    print("  ERROR:", e)
if errors:
    sys.exit(1)
for off, size, label, ro, _ in parts:
    print(f"  {off:#09x} {size:#09x} {label:<12} {'ro' if ro else 'rw'}")
print("  ok")
EOF

echo "==> All checks passed"
