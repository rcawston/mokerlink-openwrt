# RTL8261N and RTL8264B PHY entries

Two patches for net-next that add `drivers/net/phy/realtek` entries for
the board's PHYs, so that phylib reads 1 Gb/s links right. Without them
the generic Clause 45 driver binds and takes a 1 Gb/s link for 100 Mb/s.
The RTL930x switch series needs them on this board, but neither series
depends on the other's code.

| File | What |
|---|---|
| `01-rtl8261n.txt`, `.diff` | the RTL8261N (RTL8261BE) entry, its features function and model match |
| `02-rtl8264b.txt`, `.diff` | the RTL8264B entry |
| `cover.txt` | the cover letter: what, why no firmware, the question to Realtek, merge path, testing |
| `TESTING.md` | the hardware test on the MokerLink and what the patches may claim afterwards |

The diffs are made against net-next (279d5c8afcd, 21 October 2026). The
second changes lines the first adds, so each is its own diff.

## Checking

`scripts/check-rtl8261n-rtl8264b.sh`, which the `rtl8261n-rtl8264b`
workflow runs on every push that changes the patches, applies them to
the tip of net-next with `scripts/make-upstream.py` and runs checkpatch
`--strict`, then builds every patch alone with `W=1`, sparse and
kernel-doc for MIPS and x86_64. It also writes the `get_maintainer.pl`
output to its logs.

## Sending

Not before:
- the CI build and the board test have passed and the cover letter's
  Testing section (and the commit messages' test lines) say what they
  showed;
- net-next is open again. It was closed for the merge window on 21
  October 2026 (Andrew Lunn, on another Realtek PHY patch that day);
  while it is closed, netdev takes only RFCs (`[RFC net-next]`).

`--signoff` adds your Signed-off-by, which only you can give:

```sh
git -C ~/net-next checkout -B rtl8261n-rtl8264b net-next/main
scripts/make-upstream.py linux ~/net-next rtl8261n-rtl8264b --signoff
git send-email --to=netdev@vger.kernel.org \
    --cc=andrew@lunn.ch --cc=hkallweit1@gmail.com --cc=linux@armlinux.org.uk \
    --cc=davem@davemloft.net --cc=edumazet@kernel.org --cc=kuba@kernel.org \
    --cc=pabeni@redhat.com --cc=linux-kernel@vger.kernel.org \
    --cc=javen_xu@realsil.com.cn --cc=daniel@makrotopia.org \
    --cc=info@balika011.hu --cc=amadeus@jmu.edu.cn --cc=nb@tipi-net.de \
    build/upstream/linux/rtl8261n-rtl8264b/*.patch
```

Compare the first two lines of Cc with the CI's `get_maintainer.txt`
first. The rest are the people who worked on these PHYs:
- Javen Xu of Realtek, who wrote the RTL8261C/D support; the cover
  letter asks him about firmware and the model register;
- Daniel Golle and Balázs Triszka, OpenWrt's driver for these PHYs;
- Chukun Pan, who raised the RTL8261 variant names on the RTL8261C/D
  series;
- Nicolai Buchwitz, who asked on that series which variants have which
  abilities.
