# Using the switch

## Defaults

| Setting | Default | Where to change it |
|---|---|---|
| Ports | lan1-lan7 copper, lan8 SFP+; all in VLAN 1, untagged | *Network → Interfaces → Devices → switch* |
| Management address | DHCP client on VLAN 1 | *Network → Interfaces → lan* |
| Fallback address | `192.168.2.1/24`, only if no DHCP lease after 60 s | interface `mgmt`; delay in `/etc/config/mokerlink` |
| Port MTU | 9216 (jumbo frames forwarded) | *Network → Interfaces → Devices → lanN* |
| Management MTU | 1500 | interface `lan` |
| EEE (802.3az) | off | `/etc/config/mokerlink`, then `service mokerlink reload` |
| Fans | 50 %, full speed above 65 °C (fan controller sensors) or 95 °C (PHYs) | `/etc/config/mokerlink`, section `fan` |
| PoE | all ports on; only valid PoE devices get power. Needs the RTL8239 firmware ([05-porting.md](05-porting.md#poe)) | `ethtool --set-pse lanN c33-pse-admin-control disable` |
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
2. **Jumbo frames.** Ports accept frames up to MTU 9216, so jumbo traffic
   between hosts passes. If you lowered the port MTU, frames larger than it
   are dropped silently. Test with `ping -M do -s 8972 <host>`.
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
mokerlink-fan status         # fan controller temperatures, fan RPM, PWM
ethtool --show-pse lan3      # PoE state, class, voltage and power
ethtool lan3                 # link speed and duplex
ethtool -S lan3              # per-port counters
bridge fdb show              # learned MAC addresses
bridge vlan show             # VLAN membership per port
logread -f                   # system log
```

## Configuration backup

*System → Backup / Flash Firmware → Generate archive* saves the settings.
`sysupgrade -b /tmp/backup.tar.gz` does the same from the shell.

## Updating

Test new builds from RAM first. That doesn't touch flash, so it is safe to
try anything. Then flash with `sysupgrade -v <file>`, which keeps the
settings ([03-install.md](03-install.md#updating-later)).
