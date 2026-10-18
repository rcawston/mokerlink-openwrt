# Using the switch

## Defaults

| Setting | Default | Where to change it |
|---|---|---|
| Ports | lan1-lan7 copper, lan8 SFP+; all in VLAN 1, untagged | *Network → Interfaces → Devices → switch* |
| Management address | DHCP client on VLAN 1 | *Network → Interfaces → lan* |
| Fallback address | `192.168.2.1/24`, only if no DHCP lease after 60 s | interface `mgmt`; delay in `/etc/config/mokerlink` |
| Port MTU | 9200 (jumbo frames forwarded) | *Network → Interfaces → Devices → lanN* |
| Management MTU | 1500 | interface `lan` |
| EEE (802.3az) | off | `/etc/config/mokerlink`, then `service mokerlink reload` |
| Fans | 50 %, full speed from 66 °C (hottest fan controller sensor), run by the fan controller itself | `/etc/init.d/hwmon_fancontrol` |
| PoE | all ports on; only valid PoE devices get power, within a 260 W budget by class; green PoE LED per port. Needs the RTL8239 firmware in `/lib/firmware/realtek/` (kept by `sysupgrade`, removed by a factory reset; [05-porting.md](05-porting.md#poe)) | device options `pse` and `pse_priority` in `/etc/config/network` ([below](#poe)) |
| Spanning tree | off | *Devices → switch → Bridge options* |
| DHCP server, routing, firewall | not installed | |
| Web UI | `http://<address>/` (LuCI) | |
| SSH | `root@<address>` | |
| Password | **none**: set one at first login | *System → Administration* or `passwd` |

Forwarding between ports happens in the switch chip. The CPU only handles
management traffic, so the web UI's speed has no effect on throughput.

## When traffic doesn't forward

These are the usual causes on a 10G switch, roughly in order of likelihood:

1. **Tagged VLAN traffic.** Like most managed switches, the default only
   carries VLAN 1 untagged and **drops frames tagged with other VLANs**. If
   you run VLANs (hypervisor trunks, access points with several SSIDs,
   VoIP), add them: *Network → Interfaces → Devices → switch → Configure →
   Bridge VLAN filtering*. Add each VLAN ID and mark it tagged (`t`) on
   trunk ports and untagged (`u`, plus `*` for the PVID) on access ports.
2. **Jumbo frames.** Ports accept frames up to MTU 9200, so jumbo traffic
   between hosts at MTU 9000 passes. If you lowered the port MTU, frames
   larger than it are dropped silently. Test with `ping -M do -s 8972 <host>`.
   Switches first set up with an older firmware have MTU 9216 on their
   ports, which LuCI's device settings don't accept (they allow up to 9200),
   so a port's settings can't be saved there until its MTU is lowered. To
   lower all of them:

   ```sh
   for s in $(uci show network | sed -n "s/^network\.\([^.]*\)\.mtu='9216'$/\1/p"); do
   	uci set network.$s.mtu=9200
   done
   uci commit network; service network reload
   ```
3. **A port with link but no traffic** means wrong SerDes lane polarity in
   the device tree. Run `mokerlink-polarity-scan` (see
   [05-porting.md](05-porting.md#serdes-lane-polarity)).
4. **Link drops under load**, often caused by EEE on multi-gig links. It is
   off by default; check with `ethtool --show-eee lanN`.
5. **Loops.** With two cables between the same switches, or a loop through
   other switches, the network floods. Remove the loop, or enable spanning
   tree on the `switch` bridge (STP adds about 30 s of delay before a port
   forwards after link-up).
6. **Errors on one port** (cable, module, heat): `ethtool -S lanN` shows
   error and drop counters, `dmesg` shows link changes, and
   `mokerlink-probe` shows temperatures.

## Useful commands

```sh
mokerlink-probe              # port, PHY, link and temperature summary
mokerlink-fan                # fan controller temperatures, fan RPM, PWM
ethtool --show-pse lan3      # PoE state, class, voltage and power
ethtool lan3                 # link speed and duplex
ethtool -S lan3              # per-port counters
bridge fdb show              # learned MAC addresses
bridge vlan show             # VLAN membership per port
logread -f                   # system log
```

## PoE

Each RJ45 port has a `config device` section in `/etc/config/network` with
`option name 'lanN'`. Its `pse` option switches PoE on (`1`, the default)
or off (`0`), and `pse_priority` sets the port's priority, `0` (the
default) being the highest. Apply changes with `service network reload`.

The ports share a 260 W budget. A PD gets the power its class asks for
(up to 90 W for class 8) if that still fits. If it doesn't, ports with a
lower priority on the same chip (ports 1-3 or ports 4-7) are switched off
to make room; otherwise the new PD waits. `ethtool --show-pse lanN` shows
a port's state, class and power.

## Configuration backup

*System → Backup / Flash Firmware → Generate archive* saves the settings.
`sysupgrade -b /tmp/backup.tar.gz` does the same from the shell.

## Updating

Test new builds from RAM first. That doesn't touch flash, so it is safe to
try anything. Then flash with `sysupgrade -v <file>`, which keeps the
settings ([03-install.md](03-install.md#updating-later)).
