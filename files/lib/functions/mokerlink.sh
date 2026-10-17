# shellcheck shell=sh
# Shared helpers for the mokerlink-* tools: . /lib/functions/mokerlink.sh

# The PHY behind a switch port. Sets PHY_DEV (its sysfs directory), PHY_BUS
# (the MDIO bus name the mdio tool takes), PHY_ADDR (decimal) and
# PHY_DRIVER. Returns 1 for a port without a PHY (the SFP+ port).
#
# DSA attaches the PHY before the port's network device is registered, so
# the kernel never creates /sys/class/net/<port>/phydev. Find the MDIO device
# whose device tree phandle is the port's phy-handle instead.
# shellcheck disable=SC2034  # PHY_* are the results, read by the callers
mokerlink_phy_of_port() {
	local handle dev name drv
	PHY_DEV="" PHY_BUS="" PHY_ADDR="" PHY_DRIVER=""
	handle=/sys/class/net/$1/of_node/phy-handle
	[ -f "$handle" ] || return 1
	for dev in /sys/bus/mdio_bus/devices/*; do
		cmp -s "$handle" "$dev/of_node/phandle" 2>/dev/null || continue
		name=${dev##*/}                 # e.g. realtek-mdio-0:03
		PHY_DEV=$dev
		PHY_BUS=${name%:*}
		PHY_ADDR=$((0x${name##*:}))
		drv=$(readlink "$dev/driver" 2>/dev/null)
		PHY_DRIVER=${drv##*/}
		return 0
	done
	return 1
}
