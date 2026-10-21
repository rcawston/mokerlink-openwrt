# An RTL930x switch driver for mainline Linux

Design for discussion, 20 October 2026; the first series has been
written since, and nothing has been sent anywhere. The facts were checked
against net-next at `d53a5309cc4a` (20 October 2026), OpenWrt at
`68cbfc1339` (this repository's OpenWrt tree), and the netdev review
archive on patchwork. "Verified" means read in the code, a binding or a
review thread; anything else is marked as an inference.

## Summary

- **Model:** a switchdev driver that owns the switch's CPU DMA engine
  (the "NIC"), as sparx5, lan966x and the VSC7514 driver of ocelot do,
  and not a DSA driver with a separate conduit. The accepted binding
  already describes the hardware this way, and the CPU tag lives only in
  the DMA descriptor, for which DSA has no TX path. The one attempt to
  add out-of-band tagging to DSA (IPQESS, 2022) didn't get in. Andrew
  Lunn advised switchdev for this chip in 2024 (section 3).
- **Binding:** no change for the first series. SerDes polarity and the
  LED controller need additions later.
- **First series** (net-next, about 11 patches): the NIC, standalone
  ports with phylink, PHYs attached at probe (so PSE works on a port that
  is down), MTU and statistics. It covers the seven copper ports of this
  board, not the SFP+ port, which needs the PCS of series 2. It relies on
  the boot loader for the SerDes setup and the hardware PHY polling
  registers, and says so.
- **Later series:** SerDes and PCS inside the driver (no fwnode PCS
  framework needed), bridge offload, VLANs, the link interrupt, port LEDs
  in the port nodes, LAG. A board DTS goes to the MIPS tree separately.
- **Needs from others:** the rest of the hardware polling setup in the
  MDIO driver (Markus Stockhausen), a PHY driver for the board's
  RTL8261BE and RTL8264B, and agreement from Markus, Chris Packham and
  Jonas Jelonek before any of this is posted.
- **Needs here:** a Linux build environment. Until there is one, the
  `rtl930x-switch` workflow builds the series on GitHub Actions.

## 1. The hardware

### The switch block

The RTL930x (RTL9301, RTL9302B, RTL9302C, RTL9303) is a MIPS 34Kc SoC,
big-endian, with DMA that is not cache-coherent. The switch is one 64 KiB
register block at `0x1b000000`, which mainline describes as a syscon. It
holds:

| Part | Registers (offset) | Mainline driver |
|---|---|---|
| global reset | `0x000c` (`RST_GLB_CTRL_0`) | `syscon-reboot` (`reboot@c`) |
| I2C masters | `0x036c`, `0x0388` | `i2c-rtl9300` |
| SerDes indirect access | `0x03b0`/`0x03b4` | none |
| SerDes mode select | `0x0194`, `0x0198`, `0x02a0`, `0x02a4`, submode `0x01cc`, `0x02d8` | none |
| MAC port control, max length | `0x3268 + 64 * port`, `0x326c + 64 * port` | none |
| MIB counters | `0x0664 + 256 * port`, private `0x2364 + 128 * port` | none |
| L2, VLAN and other tables | `0xb320`, `0xb340`, `0xb3a0`, `0xce04` | none |
| link change interrupt | `0xc628`, `0xc62c`, `0xc658`, `0xc660` | none |
| MDIO controller and PHY polling | `0xca00`-`0xcbbc` | `mdio-realtek-rtl9300` |
| MAC force mode, link status | `0xca1c + 4 * port`, `0xcb10`-`0xcb30` | none |
| LED controller | `0xcc00`-`0xcccb` | none |
| NIC DMA | `0xdf00`-`0xe050`, RX ring size `0x7c60`, RX ring counter `0x7c8c` | none |

(OpenWrt's register names and offsets, from its `rtl838x_eth.h`,
`rtl83xx/rtl930x.c`, `mac.h` and `pcs-rtl-otto.c`.)

- **Ports:** up to 28 MAC ports (0-27) and the CPU port 28.
- **SerDes:** 12 lanes.
  - Lanes 0-1 do QSGMII only (RTL9301 and RTL9302B).
  - Lanes 2-9 do SGMII, 1000BASE-X, 2500BASE-X, USXGMII (single, dual,
    quad or 8-port XSGMII) and 10GBASE-R.
  - Lanes 10-11 carry no port of their own, but logical lane 3 uses
    physical lane 10 for its lower register pages.
  - Even and odd lanes share a clock unit.
- **NIC:** a DMA engine that feeds the CPU port. 32 RX queues on up to 32
  rings and 2 TX rings, interrupt 24 (`nic`). Each ring entry points to a
  32-byte descriptor: buffer address, sizes, a "more" bit for frames that
  span descriptors, and ten 16-bit words of CPU tag. On RX the tag gives
  the source port (word 0, bits 13:8), the queue and the trap reason; on
  TX it gives the forwarding type (word 1, "physical" makes the
  destination mask apply), the priority and a 64-bit destination port
  mask (words 4-7). The tag never appears in the frame.
- **Hardware PHY polling:** the MDIO controller polls each PHY in the
  background and feeds link, speed, duplex and pause to the MAC.
  - Daniel Golle's commit message for the attach hooks (`4b1d5d51528`)
    says the MAC can't be given speed, duplex or flow control without
    the polling.
  - OpenWrt does force the MAC through `MAC_FORCE_MODE_CTRL` for the CPU
    port, fixed links and PHYs in SFP modules. So forcing works at least
    there, and it is unproven for a 10G port with a polled PHY.
  - `SMI_MAC_TYPE_CTRL` (`0xca04`) selects whether the MAC takes its
    state from a PHY (1G or multi-gig) or from the SerDes: one field per
    group of four ports for ports 0-23, one per port for 24-27.
  - OpenWrt keeps polling on for SFP ports too, so that the MAC gets the
    SerDes state (OpenWrt `rtl83xx/dsa.c:479-500`).
- **Interrupt 23** (`switch`): MAC link changes and other switch events.

### The board

MokerLink POE-10G07110GSM: RTL9303, 128 MiB RAM, 16 MiB SPI NOR, seven
10GBASE-T ports with PoE++ and one SFP+ cage.

| Front port | MAC port | SerDes | PHY (SMI bus, address) | Interface |
|---|---|---|---|---|
| 1-3 | 0, 8, 16 | 2, 3, 4 | RTL8261BE (1/0, 2/0, 3/0), ID `001c:caf3` | USXGMII 10G |
| 4-7 | 20, 24, 25, 26 | 5, 6, 7, 8 | RTL8264B quad (0/0-3) | USXGMII 10G; one direction of each link is inverted at both ends (PHY TX and SerDes RX, or the reverse), as U-Boot programs it |
| 8 | 27 | 9 | SFP+ cage on SoC GPIOs, EEPROM on a GPIO I2C bus | 10GBASE-R, or what the module needs |
| CPU | 28 | - | - | NIC |

PoE comes from two RTL8239 PSE controllers on a GPIO I2C bus; the PHY
nodes carry `pses`. The port LEDs run in bi-colour scan mode with three
LEDs per port. The third LED of each copper port is the PoE LED, which
has no hardware function and is switched by software.

The vendor U-Boot programs the SerDes, the PHYs (lane polarity included)
and the polling registers when its network is started (`rtk network on`),
which the RAM boot does before `tftpboot` (docs/03-install.md).

## 2. What exists

### Mainline (net-next `d53a5309cc4a`)

- **Switch binding** `realtek,rtl9301-switch.yaml` (Chris Packham,
  `92575a21823` and earlier, reviewed by Rob Herring and Krzysztof
  Kozlowski): `realtek,rtl930{1,2b,2c,3}-switch` followed by `syscon`,
  `simple-mfd`; interrupts `switch` and `nic`; children `reboot@`,
  `i2c@` and `mdio-controller@`; `ethernet-ports` from
  `ethernet-switch.yaml#/$defs/ethernet-ports`, whose port nodes take
  `ethernet-controller.yaml`. That allows `phy-handle`, `phy-mode`,
  `sfp`, `managed`, `fixed-link`, `label`, `mac-address`, `nvmem-cells`,
  `pcs-handle` (without arguments: `items: maxItems: 1`) and `leds` per
  port. No CPU port and no NIC node. The DSA `ethernet` property is not
  allowed.
- **MDIO driver** `drivers/net/mdio/mdio-realtek-rtl9300.c` (Chris
  Packham, reworked by Markus Stockhausen in 2026 for RTL838x, RTL839x
  and RTL931x; internal prefix `otto_emdio_`). It binds the
  `mdio-controller@ca00` child, takes the parent syscon regmap, and maps
  ports to PHYs from the parent's `ethernet-ports` `phy-handle`s.
  - It turns the hardware polling of all ports off at probe, then on for
    a port when phylib attaches that port's PHY (`notify_phy_attach`,
    `ee18e22c634`) and off on detach.
  - It sets only the bus interface and frequency in `SMI_GLB_CTRL`, and
    it never polls a port without a `phy-handle`. OpenWrt's MDIO driver
    also sets, per PHY model (`mdio-realtek-otto.c:707-745`, 818-890):
    - `SMI_MAC_TYPE_CTRL`;
    - the per-bus `POLL_SEL` bit of `SMI_GLB_CTRL`, so that the polling
      reads the RTL8261's and RTL8264B's resolution register;
    - the private polling register (`0xca10`);
    - for Aquantia PHYs only, the 10G polling registers
      (`0xcbb4`-`0xcbbc`).

    It also polls SerDes ports.
  - It exports nothing; the `mii_bus` hooks are its only interface.
- **I2C driver** `i2c-rtl9300.c` (Chris Packham): SMBus only, 16 bytes
  per transfer. SFP EEPROMs work through `sfp.c`'s SMBus path. This board
  doesn't use it (its buses are GPIO-driven).
- **DTS:** `rtl930x.dtsi`, `rtl9302c.dtsi` and the Cameo RTL9302C
  development board. The MDIO controller is disabled in the dtsi and the
  Cameo board never enables it, so mainline's only RTL930x board has no
  working MDIO. There is no `rtl9303.dtsi`, `board-realtek.c` matches only
  `realtek,rtl9302-soc`, and `vendor-prefixes.yaml` has no `mokerlink`.
- **PHYs:** the Realtek PHY driver has the RTL8261C, RTL8261CE, RTL8261D
  and RTL8224, but not this board's RTL8261BE or RTL8264B. OpenWrt adds
  them in a pending patch (`generic/pending-6.18/742-*`, Balázs Triszka)
  that needs PHY firmware files.
- **Nothing else:** no switch, NIC, tagger, SerDes, PCS or port LED
  driver for any Otto SoC, and no fwnode PCS framework
  (`drivers/net/pcs/` has lynx, mtk-lynxi, rzn1-miic and xpcs).

### OpenWrt

`target/linux/realtek/files-6.18/drivers/net/`:

- **`dsa/rtl83xx/`**, about 22,000 lines for RTL838x, RTL839x, RTL930x and
  RTL931x: L2, VLAN, LAG, STP, mirroring, ACL (PIE), QoS, L3 offload,
  stats, LEDs. GPL-2.0-only.
  - Started by John Crispin and Birger Koblitz (RTL930x, 2021).
  - Recent commits are mostly Gennaro Cimmino's (178 in September and
    October 2026) and Markus Stockhausen's (95).
- **`ethernet/realtek/rtl838x_eth.c`**, the NIC as a DSA conduit:
  - RX passes the source port as a `METADATA_HW_PORT_MUX` dst;
  - TX takes a fake 4-byte trailer from its own tagger (`tag_rtl_otto`,
    `patches-6.18/721-*`: port, `0xab 0xcd 0xef`) and moves it into the
    descriptor;
  - a DSA core patch adds a "trapped" flag to the metadata (`722-*`).
- **`pcs/pcs-rtl-otto.c`**, 5,093 lines, about 1,500 of them RTL930x
  only. It is a separate PCS provider device with `serdes@N` children and
  `pcs-handle = <&serdesN link>`, so it needs Christian Marangi's fwnode
  PCS series (`generic/pending-6.18/737-*`), which is not in net-next.
  SerDes access goes through a SerDes MDIO bus driver. Most of it is
  Jonas Jelonek's (339 commits; Markus Stockhausen 13).
- **Its DT differs from the mainline binding:** an `ethernet` NIC child
  with its own interrupt, a CPU `port@28` with `ethernet = <&ethernet0>`,
  an `otto-switch` fallback compatible, an MDIO controller without `reg`,
  and `pcs`, `mdio-serdes`, `mdio-aux`, `table` and `thermal` children.
  An OpenWrt DT doesn't validate against the mainline binding, and the
  reverse.

### Who is working on what

From the thread of the withdrawn `leds` series (read on patchwork):

- **Andrew Lunn,** 18 October
  (59f75e7d-9b56-441d-a4b5-58ad3862267e@lunn.ch): port LEDs belong in the
  switch's port nodes, starting with on/off control for all of them;
  hardware offload of triggers "is an extension, not a requirement".
- **Markus Stockhausen,** 19 October (010701dd5f94$d636ff70$82a4fe50$@gmx.de):
  taking the switch driver upstream "is currently beyond the time
  available". He has been keeping OpenWrt's Otto DSA driver going for two
  months, largely by reviewing contributors' patches.
- **Chris Packham,** 20 October
  (c5f79284-941a-4a93-a37e-398613d5ddfa@alliedtelesis.co.nz):
  - he knows of nobody working on a driver;
  - he considers "DSA the wrong model for the RTL9300 (mainly because the
    "nic" is not a true Ethernet interface it's just the DMA channel for
    trapped packets and FDB updates)";
  - Allied Telesis has moved to the ARMv8 RTL9311B and the Realtek SDK,
    but he would like an in-kernel driver.

### Earlier attempts and discussions upstream

- **No Otto series** has ever been posted for an Ethernet NIC, a switch
  (DSA or switchdev) or a tagger. The search covered patchwork's
  netdevbpf and linux-mips projects and marc.info's netdev archive; other
  lists were searched only for specific series.
- **SerDes:** Markus posted a generic PHY driver for the Otto SerDes,
  "phy: Realtek Otto SerDes: add new driver" (v1-v7, October 2024 to May
  2025, linux-phy). v7 got no answer and it was never merged.
- **"net: dsa: Realtek switch drivers", netdev, June-July 2024**
  (https://marc.info/?t=171832960600002), started by Chris:
  - **Chris** concluded that the NIC is only the switch's DMA engine,
    since its registers sit in the switch's register space.
  - **Andrew Lunn** (c19eb8d0-f89b-4909-bf14-dfffcdc7c1a6@lunn.ch):
    - DSA's wrapper (netdev to port index) is what a switchdev driver
      reimplements;
    - DSA tagging doesn't apply with DMA per user port;
    - without a conduit, tcpdump on a conduit is lost;
    - "But you should not try to impose
    DSA if it does not fit. There are 'simple' pure switchdev drivers
    ... Look at the microchip drivers for something to copy."
  - **Andrew, next day:** look at ocelot, which has both front ends. Its
    design suffers from the second one coming late.
  - **Chris** mentioned that the RTL930x also has a mode with the CPU
    disabled and an external CPU on SGMII, which is the classic DSA case.
  - **Luiz Angelo Daros de Luca** listed what OpenWrt's code would need
    for upstream: regmap, an abstraction layer instead of per-model
    branches, named constants, and a proper tagger.
- **MT7628, February 2026** (20260228234031.bimzpo4mmu6t5rty@skbuf):
  Vladimir Oltean said that if packet I/O works through the switch's own
  DMA rings without tags, a pure switchdev driver might be more
  appropriate. It was merged as DSA because its conduit is mtk_eth_soc.
- **Airoha EN7581:** stayed DSA because airoha_eth is a real MAC, usable
  without the switch.
- **IPQ4019 "IPQESS", 2022:** the closest precedent, a QCA8K switch fed
  by an SoC DMA engine with the tag in the descriptor.
  - Maxime Chevallier proposed `DSA_TAG_PROTO_OOB`, an out-of-band tagger
    using skb extensions (v8, patchwork 13032447).
  - Jakub Kicinski answered "This is what METADATA_HW_PORT_MUX is for"
    (20221104200530.3bbe18c6@kernel.org).
  - Vladimir Oltean (20221107200740.3zx7lwjtyei3fcqp@skbuf) declined to
    maintain out-of-band tagging machinery in DSA for hardware that
    "neither build[s] a 'switch with rings' nor a 'switch with tags', but
    rather 'a switch with somebody else's rings'", and suggested "one
    switch lib, two front ends".
  - On 15 November 2022 Vladimir still invited work on TX tag offload.
    Later he said the variant with an in-band tag would have been more
    palatable.
  - Florian Fainelli thought a tagger specific to one descriptor format
    "may be fine" (02d0cc2d-26b4-e655-c2b9-9514a29074f5@gmail.com).
  - Romain Gantois's 2023 cover letter says the DSA versions were
    rejected for needing out-of-band tagging in DSA. His switchdev rewrite
    was then criticised for copying DSA's code, and stalled at v3.
    Nothing of either is in net-next.

## 3. DSA or switchdev

### What the hardware is

In Vladimir Oltean's terms the RTL930x is a "switch with rings": the DMA
engine is part of the switch block, in the same registers and the same
device tree node, behind the same interrupt controller lines, and it can
reach nothing but the CPU port. Nothing on it can be used as an
independent Ethernet controller, and its CPU tag exists only in the DMA
descriptor; no frame on any wire carries it.

### DSA

DSA would mean a NIC driver as the conduit, a tagger, and a DSA switch
driver on top.

- **What DSA gives:** the user netdevs, phylink plumbing, bridge, FDB,
  VLAN, LAG, MDB, STP and mirroring glue, standalone versus bridged
  semantics, and attaching the PHY when the user port is created, so PSE
  works on a port that is down (`dsa_user_create` →
  `dsa_user_phy_setup`, verified). It is what OpenWrt uses, so features
  would port across from its 22k lines more directly, and Markus knows
  that structure.
- **RX** is supported: `dsa_switch_rcv()` takes the port from a
  `METADATA_HW_PORT_MUX` dst and skips the tagger. The tagger still needs
  a stub `.rcv`, because `netdev_uses_dsa()` tests it.
- **TX has no out-of-band path in DSA.** `dsa_user_xmit()` always calls
  the tagger. The only ways to reach the descriptor are:
  - a tag written into the frame and stripped again by the conduit (as
    airoha does with the MTK tag);
  - `skb->queue_mapping`, which carries only a port;
  - a TX metadata dst, which needs new DSA core work. Vladimir asked for
    a capability handshake for exactly this in 2022, and it was never
    built.

  For RTL930x the in-frame option means inventing a tag format that no
  hardware speaks, and OpenWrt's `tag_rtl_otto` is such a format.
  `dsa.rst` treats a tag format as ABI and wants every tagger testable
  with `dsa_loop`. The core-work option is what IPQESS asked for and
  didn't get.
- **The trapped versus forwarded mark** (`offload_fwd_mark`) is set for
  every metadata frame in the DSA core; OpenWrt needs a core patch
  (`722-*`) to get it right. Vladimir said in 2022 that it is "not
  really clear how to expand the HW_PORT_MUX model to provide QoS info
  and trap reason, without switching to per-packet allocation"
  (20221114181926.ms5yl5rwzx7oonzn@skbuf).
- **Binding change:**
  - a NIC child node with its own compatible, taking over the `nic`
    interrupt, which the current binding requires on the switch node;
  - `dsa.yaml` port semantics with a CPU `port@28 { ethernet = <&nic>; }`.

  The DT maintainers accepted the current binding without either. A NIC
  device that is a child of the switch device also can't get a device
  link to it (`device_is_dependent()`), so DSA logs a failure and carries
  on.

### switchdev with its own NIC

One platform driver for the switch node creates one netdev per port and
moves frames through the NIC itself: TX writes the port into the
descriptor, RX dispatches on the descriptor's source port.

- **Fits the binding as accepted:** the `nic` interrupt on the switch
  node, ports in `ethernet-ports`, no CPU port.
- **Fits the hardware:** no tagger, no conduit, no invented tag, no DSA
  core changes. The trap reason is right there for `offload_fwd_mark`.
- **Precedent:** every mainline switch with its own DMA is a switchdev
  driver: sparx5, lan966x, prestera, and ocelot's VSC7514 driver. Ocelot
  is the cleanest precedent: one switch library, a switchdev front end
  for the internal CPU (VSC7514), DSA front ends for switches reached
  over an external MAC (Felix, VSC7512), one binding that selects
  `ethernet-switch.yaml` or `dsa.yaml` by compatible.
- **The cost:** the driver implements what DSA would provide: the
  switchdev and netdev notifiers, bridge join and leave, FDB sync, VLAN
  filtering semantics, LAG uppers. sparx5 is 35k lines and lan966x 18k
  today. Their first series were small: lan966x was 6 patches with ports
  that "behave as simple NIC cards"; ocelot was 7, sparx5 10.
- **PSE:** a switchdev driver chooses where it attaches the PHY. sparx5
  and lan966x attach in `ndo_open` and so can't serve ethtool PSE while
  down; ocelot attaches at probe, and this driver would too (section 9).
- **OpenWrt** keeps its DSA driver either way. Moving OpenWrt's RTL930x
  boards to an upstream switchdev driver later would change two things:
  - **the netdev set:** no `eth0` conduit, which netifd and LuCI don't
    need;
  - **naming:** DSA names its ports from the DT `label`. A switchdev
    driver here doesn't (section 11), so OpenWrt would rename the ports
    in userspace, from `phys_port_name`.

### Comparison

| | DSA | switchdev |
|---|---|---|
| binding change for the first series | yes: NIC node, CPU port, `nic` interrupt moved | none |
| DSA core changes | TX descriptor tag path, trap flag | none |
| tag format | invented (`rtl_otto`) or core work | none needed |
| precedent | airoha (real MTK tag in-band), mtk_eth | ocelot VSC7514, sparx5, lan966x, prestera |
| review risk | the IPQESS objections; DT maintainers asked to change an accepted binding | volume of code to review; copying DSA's code |
| code to write | NIC conduit, tagger, DSA ops | NIC, switchdev glue, bridge logic |
| PSE on a down port | yes (DSA attaches at port creation) | yes, if the PHY is attached at probe |
| port LEDs in port nodes | per driver either way | per driver either way |
| sharing with OpenWrt | close to its structure | register code only |

### Recommendation

switchdev with its own NIC. DSA's real advantage, the free bridge glue,
is outweighed by the parts that DSA has no answer for on this hardware
(a descriptor-only TX tag and the trap mark) and by having to change an
accepted binding. Out-of-band tagging in DSA has been tried once and
didn't get in.

Andrew Lunn gave this advice for this chip in 2024 ("you should not try
to impose DSA if it does not fit"). Chris, who wrote the binding, came
to the same conclusion then and repeated it this week. Vladimir's
remark on the MT7628 points the same way (section 2). The IPQESS rewrite
shows the trap on this side: a switchdev driver that copies DSA's code
draws objections, so the driver uses the switchdev helpers
(`switchdev_bridge_port_offload()`, the deferred FDB notifier) the way
lan966x does, not DSA's internals. Two things could still change the
recommendation: Markus preferring DSA for a reason not visible here, or a
netdev maintainer saying so when asked. The coordination email asks
Markus, and the question can go to netdev as an RFC of the binding-free
first series before more is built.

To keep a later DSA front end possible ("one switch lib, two front
ends"), the register-level code (tables, port and MAC control, NIC,
SerDes) stays separate from the switchdev glue.

## 4. Binding

- **First series:** no change. The driver binds
  `realtek,rtl930{1,2b,2c,3}-switch`, takes the `nic` and `switch`
  interrupts by name, and reads ports, `phy-handle`, `phy-mode`, `sfp`,
  `managed` and MAC addresses from what the binding already allows.
- **SerDes series:** the board-specific lane polarity
  (`rx-polarity`/`tx-polarity` from `phy-common-props.yaml`) needs a
  place.
  - **Preferred:** a container node under the switch (OpenWrt calls it
    `pcs`) with `#address-cells = <1>` and `#size-cells = <0>`, holding
    `serdes@N` nodes that carry `phy-common-props`. Ports reference them
    with `pcs-handle = <&serdesN>` (no argument cell, as
    `ethernet-controller.yaml` requires).
    - The container is needed because the switch node's children are
      addressed by register offset (`reboot@c`, `i2c@36c`,
      `mdio-controller@ca00`) with `#size-cells = <1>`, so a lane number
      can't be a `reg` directly under it.
    - This is close to OpenWrt's DT and describes a real hardware unit.
  - **Alternative:** polarity properties on the port nodes. That is
    awkward for one lane carrying four or eight ports.

  This is a change to a binding in `bindings/net`, so it goes through
  net-next with the driver.
- **LED series:** the LED controller's global mode (serial, single-colour
  scan, bi-colour scan) and clock are board properties of the switch
  node; the LEDs themselves go in the port nodes' `leds`, which is
  already allowed (section 8).
- **Krzysztof Kozlowski on the original binding** (September 2024):
  bindings should be complete rather than grown piece by piece. He let
  MDIO and the ports wait until they were understood. Expect the same
  question for SerDes nodes and LED properties. Answer it by adding them
  in one binding patch once the SerDes and LED designs are settled, not
  property by property, and by saying in that patch what is still left
  out and why.
- **Board DTS (MIPS tree):** a `vendor-prefixes.yaml` entry for
  `mokerlink`, `realtek,rtl9303-soc` and the board in `realtek-rtl.yaml`,
  the SoC compatible in `board-realtek.c`, an `rtl9303.dtsi`, and the
  board.
  - **The RTL8264B package node:** check with `dtbs_check` that the
    board's `ethernet-phy-package@0` validates under
    `realtek,rtl9301-mdio.yaml`. That binding names only
    `ethernet-phy@` children, but the `mdio.yaml` it includes accepts
    any unit-addressed child, and `realtek,rtl8224-package.yaml` exists
    for RTL8224 packages.
  - **If it doesn't validate:** the MDIO binding needs a fix in
    `bindings/net` first, and the board series' cover letter states that
    merge order.

## 5. CPU port and NIC

- **One device:** the NIC is part of the switch driver (its own file),
  not a separate device or netdev.
- **Probe:**
  - Takes the syscon regmap of its own node, so it shares the regmap and
    its lock with the MDIO, I2C and reboot children. The children are
    created at boot because of `simple-mfd`; the switch driver doesn't
    populate them again.
  - Checks `MODEL_NAME_INFO` (`0x0004`) against the compatible.
  - Resets the NIC (`RST_GLB_CTRL_0` bits 1-2 with OpenWrt's settle
    delays), sets up the CPU port (`MAC_L2_PORT_CTRL(28)`, its forced
    1000/full mode, and the maximum lengths in both directions), and
    maps every trap reason to an RX ring.
- **RX:**
  - One or two rings to start (OpenWrt uses two of 128), more later for
    priorities. Buffers come from a page_pool; frames that span
    descriptors become skb frags.
  - NAPI runs on a dummy netdev (`alloc_netdev_dummy()`, as prestera
    does), so RX doesn't depend on which port happens to be up.
  - Each frame goes to the netdev of its source port. Frames from a port
    without a netdev, or one that is down, are dropped and counted.
  - The FCS is trimmed. There is no RX checksum offload: OpenWrt marks
    frames `CHECKSUM_UNNECESSARY` without evidence that the hardware
    checks L3/L4.
- **TX:**
  - One hardware ring, shared by all port netdevs under one spinlock, as
    in every comparable driver. The descriptor gets forwarding type
    "physical", `IGNORE_STP` and `BIT(port)` as the destination mask,
    and the priority.
  - The engine puts the FCS into the last four bytes of the buffer,
    whatever they hold (OpenWrt reuses its DSA tag's bytes for it), so
    they come from the tailroom without being written; only frames below
    the minimum are padded, and TCP's clones aren't copied.
  - Before the trigger, `DMA_IF_CTRL` is read until it reads back
    correctly, as OpenWrt does.
  - A full ring stops the queues of all open ports, and they wake
    together once a quarter of the ring is free (lan966x and ocelot stop
    and wake all ports too). Completions are reclaimed in NAPI, and in
    xmit when the ring is full.
  - The TX done interrupt (bit 0 of 0xe018/0xe024) is this driver's own
    reading: OpenWrt never enables it and reclaims from its RX poll. So
    while frames are in the ring, a timer kicks the engine and NAPI every
    tick, as stmmac's TX coalescing timer does; the hardware test counts
    the interrupts while sending UDP to show whether the interrupt
    fires, and with every port down to show that it doesn't storm.
  - The open ports are a bitmask changed under the TX lock in
    `ndo_open`/`ndo_stop`; RX delivers only to open ports, and stop and
    wake touch only them, so nothing reaches a netdev that isn't
    registered or is already gone (closing comes before the RCU grace
    period that unregistering waits for). `IFF_TX_SKB_SHARING` is
    cleared, as padding for the FCS changes the skb.
- **Descriptors:** `__be32`/`__be16` fields with explicit conversions.
  On this big-endian-only SoC they compile to nothing, and the code
  stays sparse-clean and correct under `COMPILE_TEST`. Rings live in
  coherent memory, with `dma_wmb()` before the OWN bit is handed over.
- **Standalone ports** (the only mode in series 1):
  - Every port's isolation row (`PORT_ISO_CTRL` table) holds only the
    CPU port; the CPU row holds every port.
  - Every port forwards in MSTI 0 (the boot loader may leave it
    otherwise), and `ST_CTRL` is cleared, as OpenWrt does.
  - SA learning is off on all ports and the CPU port; frames from new
    source addresses and past the learning limit are forwarded.
  - VLAN filtering is off. Tagged frames should then pass unchanged both
    ways, but the TX descriptor has no keep-tag bit, and the egress
    untag set and `VLAN_PORT_EGR_FLTR` may still apply. The code sets
    them explicitly: 0x8100 is the only TPID taken (for the inner tag),
    no port parses an outer tag, so S-tagged frames pass as payload
    (OpenWrt parses S-tags on the CPU port), and VID 0, which untagged
    frames get, is the only VID sent untagged. Test 5 checks it.
  - Broadcast and unknown unicast reach the CPU through the isolation.
  - `offload_fwd_mark` stays 0.
- **Interrupts:** `nic` for RX and TX done, `switch` for link changes
  (from series 5). `nic` is requested with `request_irq()` and freed
  explicitly, so that it is gone before the rings are; OpenWrt's DSA
  link interrupt is never freed.
- **Netdev API limits:** the netdevs must not use `request_ops_lock`,
  `queue_mgmt_ops` or `net_shaper_ops`. With the netdev ops lock,
  `phy_link_topo_add_phy()` refuses the PHY, and `phy_attach_direct()`
  fails as a whole (verified), so no PHY would be attached at all.

## 6. phylink, PHYs and hardware polling

- **phylink:** one instance per port, created at probe. The PHY is
  connected at probe with `phylink_of_phy_connect()`, before
  `register_netdev()` (as ocelot and DSA do). Andrew asked for `of_`
  rather than fwnode calls in DT-only drivers. `ndo_open` and `ndo_stop`
  only start and stop phylink.
  - Connecting the PHY runs the MDIO driver's `notify_phy_attach`, which
    turns on hardware polling for that port from probe on. Once the
    netdev has been up and down, `phy_stop()` has halted and suspended
    the PHY, so a down port shows no link, but it stays attached (which
    is what PSE needs).
  - **Deferral is the driver's job.** fw_devlink doesn't follow
    `phy-handle` (`drivers/of/property.c` has no binding for it), the
    MDIO controller is a child of the switch device anyway, and
    `phylink_of_phy_connect()` returns `-ENODEV` for a PHY that isn't
    registered. So for each port:
    - if `of_phy_find_device()` finds nothing and the PHY's MDIO
      controller and bus are enabled in the DT, probe returns
      `-EPROBE_DEFER`;
    - if they are disabled, the port is skipped with a message. That is
      the case on mainline's Cameo board, whose MDIO controller is never
      enabled.
    - the wait ends at the deferred probe timeout
      (`driver_deferred_probe_check_state()`), after which a missing
      PHY costs only its port; the NVMEM cell of a port's MAC address is
      waited for the same way, and a port whose cell never comes gets a
      random address. All of it happens before the hardware is touched,
      so a deferred probe costs nothing.
    - a PHY that fails to attach also costs only its port.
  - **The MDIO bus can defer too:** with `CONFIG_PSE_CONTROLLER`, a PHY
    whose node has `pses` returns `-EPROBE_DEFER` until its PSE driver
    has probed, and `of_mdiobus_register()` then gives up on the whole
    bus (verified in `fwnode_mdio.c` and `of_mdio.c`). On this board the
    switch therefore waits for the RTL8239 driver. That is correct, but
    it makes PSE a hard dependency of the ports.
- **Ports with a PHY (`phy-handle`):** the MAC's speed and duplex are
  never forced, because the polling engine sets them. Pause is forced to
  what phylink resolved, as OpenWrt's DSA driver does.
  - **Precedent:** mv88e6xxx's PPU. Russell King's code
    (`5d5b231da7ac`, `2b29cb9e3f7f`) leaves a port unforced in
    `MLO_AN_PHY` mode while the PPU updates it, and forces only fixed
    links and ports the PPU doesn't cover. b53 likewise forces nothing in
    PHY mode except the BCM63xx RGMII ports.
  - **Reviewers on the polling** (MDIO series, 2026):
    - Russell King rejected programming it from `phydev->supported` and
      proposed the attach callback that became `notify_phy_attach`.
    - Andrew Lunn wants workarounds kept inside the MDIO bus driver. He
      pointed out that the polling doesn't read Realtek's speed register,
      and suggested that `mac_link_up` compare the MAC's polled state
      with phylink's and drop the link, reporting the hardware as broken,
      when they disagree.
  - `mac_link_up` sets the TX and RX enables (`MAC_L2_PORT_CTRL` bits
    1:0) and, later, EEE. It does Andrew's check against `MAC_LINK_STS`
    and the speed and duplex status registers (`0xcb10`-`0xcb30`).
  - `mac_link_down` clears the enables.
  - phylink learns the link from phylib, which polls the PHY once a
    second in series 1. Series 5 replaces that with `PHY_MAC_INTERRUPT`
    and `phy_mac_interrupt()` from the `switch` interrupt.
  - The polling engine and phylib both talk to the PHY; the MDIO driver
    already handles that, and C45 PHYs on a C45 bus are what this board
    has.
- **Ports without a PHY** (SFP, fixed link), from series 2:
  - **OpenWrt's way:** leave a SerDes port unforced, with
    `SMI_MAC_TYPE_CTRL` set to the SerDes, and keep polling on, so the
    MAC takes the link from the PCS. It forces `MAC_FORCE_MODE_CTRL`
    only for the CPU port, fixed links and PHYs in SFP modules (which
    aren't on the polled MDIO bus), pulsing FORCE_EN so that the MAC
    latches the values.
  - **Preferred:** do the same, which needs polling for SerDes ports
    (the polling question below).
  - **Fallback:** force SerDes ports from phylink's resolved state. That
    is a departure from OpenWrt and has to be tested.
  - **How the choice is made:** at probe, from the DT (`phy-handle` or
    not), as Markus asked for on openwrt/openwrt#25995, and never from
    `phydev` in an interrupt handler.
- **The SFP+ port needs series 2.** With `managed = "in-band-status"`,
  phylink takes the link from a `phylink_pcs` and reports it down
  without one. The mainline MDIO driver also turns polling off at probe
  for every port and never turns it on for a port without a PHY, so
  `MAC_LINK_STS` of the SFP port isn't fed either. Series 1 therefore
  creates no netdev for a port that needs a PCS, and says so in the log.
  The PCS of series 2 and the polling question below decide how that
  port gets its link.
- **The gap: polling setup.** Mainline's MDIO driver toggles only the
  per-port poll bit. The rest is set by OpenWrt's MDIO driver and, on
  this board, by U-Boot:
  - the MAC type;
  - the per-bus resolution-register select (`POLL_SEL`) that the
    RTL8261 and RTL8264B need;
  - the private polling bit;
  - polling for SerDes ports.

  OpenWrt picks these by PHY model, and Russell King has rejected per-PHY
  register tables in the MDIO driver as not scaling. Markus said in the
  MDIO review that the mainline driver has no place yet to set up polling
  for SerDes and SFP ports. Reviewers have pulled both ways:
  - **Andrew Lunn, 2025** (1719bd97-e160-488b-bfda-f2a8a639353b@lunn.ch,
    Chris's MDIO v11): configuring the polling "has nothing to do with the MDIO
    bus driver. It is a switch thing. So logically it should be in the
    switch driver. Now the address spaces might not allow that..."
  - **Andrew, 2026:** workarounds for the polling stay inside the MDIO
    bus driver, and PHY drivers must not know about them.

  The two fit together:
  - **The MDIO driver** keeps what concerns PHY access and the per-PHY
    poll bit.
  - **The switch driver** sets what describes its ports: the MAC type in
    `SMI_MAC_TYPE_CTRL`, and polling for SerDes ports, which it knows
    from `phy-mode` and the lane.

  `POLL_SEL`, the private polling bit and the 10G polling registers are
  about which PHY registers get read, so they sit with the MDIO driver,
  in whatever form satisfies Russell's objection. Agree this with Markus
  before writing either side.

  Until then, series 1 works only where the boot loader has set these,
  and the cover letter says so. The mainline MDIO driver's own comment
  says "an unpopulated MAC_LINK_STS register cancels operations to other
  MAC registers", so a forced SFP port without polling is untested
  ground. That is the first question to Markus.
- **MAC capabilities and interfaces** come from the variant data and the
  port's SerDes (section 10), not the "everything on every port" list
  OpenWrt has as a TODO.

## 7. SerDes and PCS

The known blocker is that OpenWrt's PCS is a separate provider device
that needs the fwnode PCS framework. The plan sidesteps it.

- **Where:** inside the switch driver. The SerDes registers (the
  indirect access at `0x03b0`, the mode selects at `0x0194` and friends)
  are in the switch block, and the switch driver owns the port-to-lane
  map. The driver embeds one `phylink_pcs` per lane and returns it from
  `mac_select_pcs`, as sparx5, lan966x, mvpp2 and others do. No separate
  device, no `#pcs-cells`, and no dependency on an unmerged framework.
- **The framework's state:**
  - **Christian Marangi**, "net: pcs: Introduce support for fwnode PCS":
    v18 (12 patches, 9 October 2026, patchwork series 1182638) is in
    "changes requested", and its CI found it conflicting with pending net
    patches. Andrew Lunn likes the direction.
  - **Russell King's objections:** published PCS devices can go away
    while in use; `mac_select_pcs()` must never return
    `-EPROBE_DEFER`; he refuses new separate PCS drivers until that
    lifetime problem is solved.
  - **Sean Anderson's** competing "PCS subsystem" stopped at v6 (June
    2025). Marangi's v18 cover says Sean agreed, with conditions, to let
    Marangi's go in instead.
  - OpenWrt carries Marangi's v14.
- **Later:** if the framework lands, the `serdes@N` nodes already have
  the shape it needs, and a provider can be split out then. OpenWrt
  could keep its separate PCS until then.
- **Markus's generic PHY driver for the SerDes** (2024-2025, v7, never
  merged) is the other shape: lanes as a `drivers/phy` provider, and the
  switch driver calling `phy_set_mode_ext()`, as sparx5 does. If Markus
  wants to revive it, the switch driver can use it and keep only the
  `phylink_pcs` part. Ask him first.
- **Newest precedent for an embedded PCS:** motorcomm yt921x's SerDes PCS
  (`4b8fbb875a5`, September 2026, reviewed by Andrew Lunn), with
  `pcs_inband_caps`. sparx5 drives its lanes through the generic PHY
  framework (`phys = <&serdes N>`) with the PCS in the switch driver.
  That is the other option, if a `drivers/phy` lane driver is wanted.
- **Modes, first:** USXGMII single-port (this board's PHYs), 10GBASE-R,
  1000BASE-X, SGMII, 2500BASE-X (SFP).
- **Modes, later:** USXGMII-QX for the RTL8224 boards (RTL9302B/C),
  XSGMII and QSGMII for the RTL9301.
- **What the code has to do** (from `pcs-rtl-otto.c`, which follows the
  vendor SDK):
  - mode and submode selection;
  - the shared clock unit of each lane pair and its two PLLs, including
    moving the neighbour lane;
  - the analog tables;
  - TX amplitude by speed and attachment;
  - polarity, from `rx-polarity`/`tx-polarity`, read with the
    `phy_get_*_polarity()` helpers already in net-next;
  - clock-ready checks, the 10GBASE-R state machine reset;
  - RX calibration as delayed work;
  - `pcs_get_state` from the fibre page for C37 modes and from the MAC
    status registers for USXGMII and 10GBASE-R.
- **Size:** about 1,500 lines in OpenWrt for RTL930x.
- **Origin:** the analog values are magic numbers from the SDK; the
  commit messages must say so.
- **Series 1 doesn't program the SerDes,** and uses the lanes as the boot
  loader set them up, for the interface modes it accepts (SGMII, QSGMII,
  USXGMII, 10G-QXGMII). A check of each lane's mode needs the per-variant
  lane map, which is series 2's, so series 1 doesn't check it; the cover
  letter says what it relies on.

## 8. Port LEDs

Andrew Lunn asked for the port LEDs in the switch driver's port nodes,
all of them with on/off control first, and hardware offload as an
optional extension (withdrawn `leds` series, 18 October 2026). The
binding already allows that: `leds { led@N { ... } }` under a port, from
`ethernet-controller.yaml` (`57b6c752c5c0`).

- **Order:** the LEDs are registered after `register_netdev()` and
  removed before `unregister_netdev()`, as r8169 does, so that
  `hw_control_get_device()` has a netdev to return from the start. That
  means `led_classdev_register_ext()` with an explicit unregister, not
  the devm variant, which would be released only after `remove()` has
  unregistered the netdev.
- **Shared functions have precedent:** qca8k has one rule per LED index
  for all its PHY ports, so setting one changes four.
- **Registration:** for each LED node, with the port's fwnode, as qca8k,
  mv88e6xxx and yt921x do for DSA ports.
  - `brightness_set` uses the software control registers (`0xcc48`,
    `0xcc4c + 4 * (port / 8)`, `0xcc5c + 4 * port`). This repository's
    withdrawn `leds-rtl9300-port.c` already drives them.
  - Hardware offload of the netdev trigger uses `hw_control_is_supported`,
    `hw_control_set`, `hw_control_get` and `hw_control_get_device`
    (returning the port netdev's device).
- **Constraint: shared LED sets.** The hardware has four LED "sets"
  (`0xcc28 - 8 * set`), each defining the functions of LEDs 0-3
  together. Each port selects one set for copper and one for fibre, so a
  set fixes all of a port's LEDs at once.
  - Offloading one LED therefore means finding or freeing a set whose
    functions match the port's other LEDs as they are, and moving the
    port to it.
  - When no set fits, `hw_control_is_supported` or `hw_control_set`
    returns `-EOPNOTSUPP` and the LED core blinks in software.
  - The board's default, from the boot loader or the DT, is one set per
    kind of port.
- **Board properties** (binding addition on the switch node): the scan
  mode, the LED clock and the polarity. The number of LEDs per port
  follows from the LED nodes. OpenWrt's polarity bit is named the
  opposite of what it does (it calls bit 22 `ACTIVE_LOW` and sets it
  when the DT doesn't say `active-low`), so check it on hardware.
- **The LED unit depends on the polling:** Chris noted in his MDIO v6
  that with `SMI_POLL_CTRL` off the LED unit stops updating. A port whose
  PHY isn't attached, or a SerDes port that isn't polled, has hardware
  LEDs that don't follow the link. Attaching PHYs at probe keeps polling
  on (a down port's suspended PHY simply shows no link), and the SerDes
  ports tie the LED work to the polling question (section 6).
- **PoE LED:** the third LED of each copper port is an ordinary port LED
  under software control. Its trigger comes from the PSE LED trigger
  series when that lands.
- **Merge path:** binding (`bindings/net`, refs `leds/common.yaml` without
  changing it) and driver together through net-next, with linux-leds and
  Lee Jones on Cc for review. The cover letter says this, as Krzysztof
  Kozlowski asked on the withdrawn series.

## 9. PSE

- **The lookup:** ethtool PSE finds the controller through
  `ethnl_req_get_phydev()`, which returns `dev->phydev` or a PHY from the
  link topology, then `phydev->psec`. It doesn't check whether the netdev
  is running (verified in `net/ethtool/netlink.c` and `pse-pd.c`).
  `psec` comes from the PHY node's `pses` when the MDIO bus registers the
  PHY, independently of the switch driver.
- **The switch driver's part:** attach the PHY to the port netdev at
  probe and keep it attached while the netdev is down, which section 6
  already does. Then `ethtool --set-pse lanN` works on a port that is
  down.
- **Not in the switch driver:** the RTL8239 driver and the PSE poll and
  LED trigger series stay as planned (docs/08-upstreaming.md). Hardware
  tests of PSE need them out of tree until they land.

## 10. Variants

None of OpenWrt's drivers tell the variants apart in code; the board DT
says which ports and lanes exist. Mainline's compatibles do distinguish
them, so the driver keeps a small per-variant table:

| | RTL9301 | RTL9302B | RTL9302C | RTL9303 |
|---|---|---|---|---|
| ports on the example board | 24 × 1G + 4 × 10G SFP+ | 16 × 1G + 8 × 2.5G + 2 × 10G SFP+ | 8 × 2.5G + 2 × 10G | 8 × 10G |
| lanes in use there | 0-1 QSGMII, 2-3 XSGMII, 4/6/8/9 10G | 2-3 XSGMII, 4-5 USXGMII-QX, 6-7 10G | 2-3 USXGMII-QX (two RTL8224), two 10G lanes | 2-9 10G |
| example board | D-Link DGS-1250-28X | Ubiquiti USW-Pro-Max-24-PoE (OpenWrt's only RTL9302B board) | Cameo RTL9302C (mainline), Zyxel XMG1915-10E | this board, Ubiquiti USW-Aggregation |

The table holds, per variant:

- the model ID in `MODEL_NAME_INFO` bits 31:16 and the allowed suffixes;
- the valid MAC ports;
- for each port, the lane and the link index on that lane in each lane
  mode, so the driver can work out the lane from `phy-mode`;
- the modes each lane supports.

Unverified: the port layouts above are read off OpenWrt board DTS files,
not a datasheet, and the difference between the RTL9302B and RTL9302C
doesn't show in any code. The coordination email asks.

## 11. Name and place

- **Avoid names that clash with the Realtek DSA drivers:** `realtek` on
  its own, `rtl83xx` (mainline's `drivers/net/dsa/realtek/rtl83xx.c` is
  shared code for external switches), `rtl8365mb`, `rtl8366`, and the
  `NET_DSA_REALTEK*` symbols.
- **Luiz's warning** (MDIO v2, December 2024, two messages:
  CAJq09z49uBPPZqDyc3O+4nVppKoKdrJunQnQKBUfQmwzdV+ZFQ@mail.gmail.com and
  CAJq09z7GDOmKqmbD5evafSFaQswgSJPU-G+44DtwfidmZMS6sA@mail.gmail.com):
  - `realtek_mdio_*` collides with `drivers/net/dsa/realtek/realtek-mdio.c`;
  - a bare `rtl` only means "Realtek";
  - name things after the most significant model. He preferred
    `mdio-realtek-rtl9300.c` with an `rtl9300_` prefix.

  In 2026 Andrew asked for "otto" in that driver's prefix, which became
  `otto_emdio_`.
- **What series 1 uses:** `drivers/net/ethernet/realtek/rtl9300/`, module
  `rtl9300-switch`, Kconfig `RTL9300_SWITCH`, function prefix `rtl9300_`,
  as Luiz preferred and matching `mdio-realtek-rtl9300` and
  `i2c-rtl9300`. The driver supports only RTL930x, so the model name
  fits; Andrew's "otto" was for an MDIO driver that covers all Otto
  families.
- **Alternative:** `otto` in the path and prefix, if the driver is meant
  to grow to RTL931x (Chris's RTL9311B). Markus's view decides this, and
  a rename before posting is mechanical.
- **Netdev names:** not from DT `label`. Vladimir called it deprecated in
  favour of udev naming on IPQESS. Each port registers a devlink port, so
  `phys_port_name` gives udev stable names.
- **Kconfig:** `NET_VENDOR_REALTEK` depends on PCI today and needs
  `|| MACH_REALTEK_RTL || COMPILE_TEST`.

## 12. Series plan

Rules for every patch:

- **Size and order:** each series at most 15 patches (maintainer-netdev.rst).
  Each patch builds and works on its own.
- **Style:**
  - **Andrew Lunn** (da228153-65b0-4b21-b62c-1562394ca95a@lunn.ch, May
    2026): many small patches, no `inline` in .c files (NIPA's
    `source_inline` check flags them), no `guard()`. `scoped_guard()`
    on a short section passed review in the MDIO driver.
  - **Jakub Kicinski:** no `__free()` and no declarations in the middle
    of a function.
  - Reverse Christmas tree; `of_` APIs.
- **Checks before posting:** `checkpatch --strict` and a W=1 build, with
  sparse, and our own Sashiko-style review.
- **After posting:**
  - Netdev's Sashiko instances post findings from
    `netdev-bot+sashiko@kernel.org` about a day later, and a
    `sashiko-nipa` check shows on patchwork. Each finding gets an answer
    on the list.
  - Repost at most twice a week (Jakub, August 2026) and never within 24
    hours.
- **Cover letters:**
  - a block diagram of the hardware in Linux terms (Vladimir asked for
    one on MT7628);
  - the base tree and the dependencies;
  - which patches a maintainer can apply on their own (Krzysztof
    Kozlowski on the withdrawn LED series).

### Series 1: CPU NIC and standalone ports (net-next)

As written (`upstream/linux/rtl930x-switch/01-*` to `09-*`, cover letter
`cover.txt`):

1. net: ethernet: realtek: offer the vendor menu on Realtek switch SoCs
2. net: ethernet: realtek: add an RTL930x switch driver
   - binds the four compatibles, the syscon regmap, chip model check,
     MAINTAINERS
3. net: ethernet: realtek: rtl9300: set the switch up for standalone ports
   - isolation, MSTI 0 forwarding, no learning, L2 flush, every VLAN
     with every port and no filtering, tags kept, link-local frames
     trapped, MACs off
4. net: ethernet: realtek: rtl9300: add the DMA engine and receive frames
   - reset, RX ring 0 of 512 page pool fragments, the frame counter, NAPI
     on a dummy netdev, the interrupt, and the engine stopped at shutdown
     (kexec)
5. net: ethernet: realtek: rtl9300: transmit frames
   - TX ring 0 of 64, the CPU tag, the FCS from the tailroom, the TX done
     interrupt (unverified) and the TX timer
6. net: ethernet: realtek: rtl9300: add the ports
   - netdevs from ethernet-ports, phylink with PHYs attached at probe,
     the MAC state check and forced pause in mac_link_up, deferral with
     a timeout, the open-port mask, queue stop and wake across ports,
     ethtool link operations
   - the forced MAC for PHY-less ports comes with series 2, where there
     is a port to run it on
7. net: ethernet: realtek: rtl9300: support jumbo frames
8. net: ethernet: realtek: rtl9300: report the MIB counters
   - the ethtool eth-mac, eth-ctrl, pause and RMON groups, with the
     32-bit counters added up once a minute and the eth-mac frame and
     octet counts derived as RFC 3635 has it (as rtl8365mb does); the
     interface statistics stay what passed through the CPU, with the
     frames lost on the way as rx_dropped
9. net: ethernet: realtek: rtl9300: add devlink ports
   - phys_port_name for udev, and the switch ID bridge offload will need

- **Merge path:** all of it through net-next; no binding change, no
  other tree.
- **The cover letter states:**
  - the boot loader dependency for SerDes and polling (section 6);
  - that the board DTS goes to the MIPS tree separately;
  - exactly what was tested and on which board.

### Series 2: SerDes and PCS (net-next)

- binding: `serdes@N` children with polarity, `pcs-handle` on ports;
- driver: lane access, CMU and PLL handling, USXGMII, 10GBASE-R,
  1000BASE-X, SGMII, 2500BASE-X, RX calibration, `phylink_pcs`;
- SFP support through phylink.

### Series 3 onward (net-next)

3. Bridge offload: the switchdev and netdev notifiers, bridge join and
   leave, isolation by bridge, learning and ageing, the L2 table and FDB,
   flood flags, STP, `offload_fwd_mark` from the RX trap reason.
4. VLAN filtering: VLAN and untag tables, PVID.
5. Link interrupt (`PHY_MAC_INTERRUPT`) and EEE.
6. Port LEDs: binding and driver (section 8).
7. LAG, MDB and IGMP snooping, mirroring.

### Other trees and other people's work

- **MIPS tree:** `realtek,rtl9303-soc`, `rtl9303.dtsi` and the MokerLink
  board. It can go in parallel with series 1, since the binding it needs
  exists (subject to the `ethernet-phy-package` check in section 4). Its
  PSE and LED nodes follow once their bindings are merged.
- **PHYs:** RTL8261BE and RTL8264B support in `drivers/net/phy/realtek`.
  - **Without it:** mainline binds the generic C45 driver to these PHYs.
    That may be enough for series 1 on this board, since U-Boot has
    initialised the PHYs and their polarity. Whether it is, is the first
    thing test 2 finds out. If not, the tests carry OpenWrt's patch out
    of tree.
  - **For real use:** it is needed for anything to work without U-Boot's
    help.
  - OpenWrt's patch needs firmware blobs; ask its author and Markus
    before taking it on.
- **MDIO:** the polling setup (section 6), Markus's driver.
- **PSE:** the RTL8239 series as planned.

## 13. Reuse and attribution

| Source | Licence | What to take | Attribution |
|---|---|---|---|
| OpenWrt `rtl83xx`, `rtl838x_eth.c` | GPL-2.0-only | register map, table layouts, init order, quirks | new code; the commit messages say which OpenWrt file each sequence comes from and name its authors. Where code is adapted closely, Co-developed-by with the author's own Signed-off-by, only with their agreement |
| OpenWrt `pcs-rtl-otto.c` | GPL-2.0-or-later | the RTL930x SerDes sequences and SDK tables | as above. Jonas Jelonek wrote most of it, with Markus Stockhausen; ask them how they want it credited. For `rtl83xx`, also ask Gennaro Cimmino, its most active recent contributor |
| OpenWrt `mdio-realtek-otto.c` | GPL-2.0-only | the polling setup, if Markus wants it in the mainline MDIO driver | his to send, or with his agreement |
| this repository's `leds-rtl9300-port.c` | GPL-2.0-only, Ross Cawston | LED software control | ours |
| the vendor SDK (through OpenWrt) | | analog magic values | say so in the commit message |

- **Rules:** a `Co-developed-by:` must be followed by that person's
  `Signed-off-by:`, which only they can give. Credit without code taken
  goes in the commit message. No `Signed-off-by` is added for anyone,
  Ross Cawston's included: each person adds their own.
- **Existing copyright lines:** Birger Koblitz ("Copyright (C) 2020"),
  and the MODULE_AUTHORs of the OpenWrt files, are kept in any file that
  carries their code.

## 14. Test plan on this board

What the user has to provide, to be confirmed with them:

- the serial console and TFTP setup already used for RAM boots;
- a Linux host with a 10G NIC (or a multi-gig one) and a second port or
  a second host;
- copper cables;
- an SFP+ module (10G, ideally also a 1G one);
- a PoE device, for the PSE checks.

**Build:** net-next with the series, plus a test-only board DTS made from
the OpenWrt one in mainline-binding form.

- **PHY driver:** OpenWrt's RTL8261BE/RTL8264B patch, or the generic C45
  driver. Patch 742 reprograms lane polarity from the DT, so with it the
  DTS must keep the `&phyN` polarity lines.
- **PSE:** two builds. Tests 1-6 and 8-10 without `CONFIG_PSE_CONTROLLER`
  (or without `pses` in the DTS); otherwise the MDIO bus waits for a PSE
  driver and the switch never probes (section 6). Test 7 with the RTL8239
  driver, the PSE poll series and `pses`.

Kernel options: `CONFIG_PROVE_LOCKING`, `DEBUG_ATOMIC_SLEEP`,
`DMA_API_DEBUG`, `PAGE_POOL_STATS`. The image is a uImage with magic
`0x83800000` (`scripts/uimage-remagic.py`).

**As built:** the `rtl930x-switch` workflow builds the image on every
push of the series (`scripts/build-rtl930x-test-image.sh`): the kernel
with `test/config` and `test/mokerlink-poe-10g07110gsm.dts` appended, and
an initramfs with a static busybox, ethtool and iperf3 and the guided
script `test/rtl9300-switch-test`, which covers tests 1-7, 9 and 10 below
with PASS/FAIL per step. `test/README.md` has the boot and test steps.
Without the PSE driver, test 7 is reduced to "ethtool's PSE lookup finds
the PHY of a down port"; the full PSE test needs a build with the RTL8239
driver and `-DWITH_PSE`.

The tests call the ports `lan1`-`lan7` by front port. The driver doesn't
name them from the DT, so the test image renames them at boot from
`phys_port_name`.

**Boot:** `rtk network on`, `tftpboot 0x84f00000 <image>`, `bootm
0x84f00000`, so U-Boot has set up the SerDes, PHYs and polling. Record
U-Boot's register state first with `scripts/uboot-collect.py`, so the
test report can say exactly what the boot loader left set.

| # | Test | Pass |
|---|---|---|
| 1 | Boot | the driver probes once (no endless deferral); 7 netdevs for the copper ports (the SFP port waits for series 2), with `phys_port_name` set; no lockdep, DMA-API or other warnings in `dmesg` |
| 2 | Link | first, which PHY driver bound (`ethtool -i`, `dmesg`) and whether the generic C45 driver links at all; then each copper port links at 10G and, by setting the partner, at 5G, 2.5G, 1G and 100M; `ethtool lanN` matches the partner; carrier follows a cable pull within 2 s |
| 3 | Traffic | ping and `iperf3` TCP and UDP both ways on each port, then on all ports at once (shared TX ring); no TX timeouts, no stuck queues, counters match the host's |
| 4 | Standalone isolation | two ports to one host on two subnets; nothing crosses between switch ports (tcpdump on the host); VLAN-tagged frames arrive at the CPU unchanged and go out unchanged |
| 5 | MTU | `ip link set lanN mtu 9000`; ping with DF and 8972 bytes both ways; the largest MTU the driver offers works |
| 6 | Up and down | 100 cycles of `ip link set down/up` under traffic; the link comes back each time; no leaks (`/proc/meminfo`, page_pool stats) |
| 7 | PSE while down | `ip link set lan1 down`, then `ethtool --show-pse lan1` and `--set-pse lan1 c33-pse-admin-control disable/enable` work and the PD powers off and on |
| 8 | SFP+ (series 2) | not part of series 1, which has no PCS (section 6). For series 2: a 10G module links and passes traffic; a 1G module switches the lane to 1000BASE-X; module removal and insertion are handled |
| 9 | Statistics | `ethtool -S` and the standard groups match the host's counters after test 3 |
| 10 | Unbind and bind | the driver unbinds and binds again without warnings, and the ports come back |

What the cover letter may claim afterwards: exactly the tests that
passed, on this board, booted from U-Boot after `rtk network on`, and
"build-tested only" for RTL9301, RTL9302B and RTL9302C.

## 15. Open questions

For Markus, Chris and Jonas:

1. Is anyone working on an upstream Otto switch or NIC driver? Chris
   said he knows of nobody, Markus that it is beyond his time for now;
   Jonas hasn't been asked.
2. Markus: any objection to switchdev?
3. Markus:
   - who sets up the polling for SerDes ports and the MAC type;
   - his SerDes generic PHY driver;
   - credit for the OpenWrt code;
   - `rtl9300` or `otto` naming;
   - the RTL9302B versus RTL9302C difference.
4. The external-CPU mode Chris mentioned: does it use an in-frame CPU
   tag (for a DSA front end later)?

For the user:

1. switchdev or DSA: switchdev (20 October).
2. A Linux build environment: GitHub Actions for now, WSL2 with Ubuntu
   later.
3. What link partners and modules you have for the tests.

## 16. Risks

- **Size and review:** a switchdev driver is a lot of code for netdev
  to review. Mitigation: small series, ports that are only NICs first,
  as lan966x did.
- **Boot loader dependency:** maintainers may refuse a first series that
  relies on the boot loader for SerDes and polling. Mitigation: say it in
  the cover letter, check the lane mode instead of trusting it, and offer
  series 2 promptly. Or reorder, so the SerDes series comes first.
- **Two drivers in one register block:** the MDIO driver and the switch
  driver share `0xca00`-`0xcbbc`. Agree with Markus who owns what before
  posting.
- **Hardware without documentation:** everything comes from OpenWrt and
  the vendor SDK through it; there is no datasheet. Say so in the cover
  letter.
- **OpenWrt diverges:** OpenWrt keeps its DSA driver for years. The
  coordination is about not doing the hardware work twice, not about
  merging the two.
