# MDIO polling setup for the Realtek Otto switches

A series for net-next: the upstream MDIO driver
(`drivers/net/mdio/mdio-realtek-rtl9300.c`) sets up the hardware PHY
polling itself, as OpenWrt's MDIO driver (`mdio-realtek-otto.c`) does,
instead of leaving all but the polling bits to the boot loader.
`cover.txt` and the commit messages explain it; the patches are
`NN-name.txt` (message) and `NN-name.diff` (change), and
`scripts/make-upstream.py linux TREE mdio-polling` builds them on a
net-next checkout.

| Patch | Families | What |
|---|---|---|
| 01 | RTL839x | the global polling enable |
| 02 | RTL838x | medium detection on the combo ports 24-27 |
| 03 | RTL930x, RTL931x | the link source of each MAC (SerDes, 1G PHY or multi-gigabit PHY), from the bus type, when its PHY attaches |
| 04 | RTL930x, RTL931x | the status register of a bus, and Realtek's Lite modes, from a table of PHY models |
| 05 | RTL930x, RTL931x | the 10G polling registers, from the same table |
| 06 | all | the polling of ports without a PHY |

Earlier work: Daniel Golle's RFC "net: mdio: setup RealTek MDIO polling
registers" (January 2026), and the review there that led to the
`notify_phy_attach` hook this series uses.

## Checks

`scripts/check-mdio-polling.sh` applies the series to the tip of net-next,
runs checkpatch --strict on every patch, builds every patch on its own for
MIPS with W=1 and sparse, links the series for MIPS and compile-tests it
for x86_64. The `rtl930x-switch` workflow runs it on every push that
changes the series.

## Testing

On the MokerLink POE-10G07110GSM (RTL9303, RTL8261N and RTL8264B PHYs):
the RTL930x switch driver's test image carries this series, and
`rtl9300-mdio-test` checks it (`../rtl930x-switch/test/README.md`).

The other families and PHYs need other boards. Through OpenWrt, which
already backports the driver's other patches, the series can be tested on
all four families: branch `mdio-polling-openwrt` switches the realtek
target from `mdio-realtek-otto` to this driver (`patches/0005`), with the
four net-next commits OpenWrt's kernel lacks (031-35 to 031-38) and this
series (725-01 to 725-06) in OpenWrt's form.
