"""Synthetic device output for parser tests. Invented names, serials and addresses only;
never paste real captures here (the repository is public)."""

IOS_VERSION = """Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E8, RELEASE SOFTWARE (fc1)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2023 by Cisco Systems, Inc.
Compiled Fri 21-Apr-23 06:42 by mcpre

ROM: Bootstrap program is C2960X boot loader
BOOTLDR: C2960X Boot Loader (C2960X-HBOOT-M) Version 15.2(3r)E1, RELEASE SOFTWARE (fc1)

asw-test-01 uptime is 1 year, 2 weeks, 3 days, 4 hours, 5 minutes
System returned to ROM by power-on
System image file is "flash:c2960x-universalk9-mz.152-7.E8.bin"
Last reload reason: power-on

cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision B0) with 524288K bytes of memory.
Processor board ID FOC0000TEST
Last reset from power-on
1 Virtual Ethernet interface
52 Gigabit Ethernet interfaces
2 Ten Gigabit Ethernet interfaces

64K bytes of flash-simulated non-volatile configuration memory.
Base ethernet MAC Address       : 00:AA:BB:CC:DD:01
Motherboard assembly number     : 73-15975-04
Model number                    : WS-C2960X-48FPD-L
System serial number            : FOC0000TEST

Switch Ports Model                     SW Version            SW Image
------ ----- -----                     ----------            ----------
*    1 54    WS-C2960X-48FPD-L         15.2(7)E8             C2960X-UNIVERSALK9-M

Configuration register is 0xF
"""

IOS_CDP_DETAIL = """-------------------------
Device ID: dsw-test-01.example.local
Entry address(es):
  IP address: 10.0.1.1
Platform: cisco C9300-48P,  Capabilities: Router Switch IGMP
Interface: TenGigabitEthernet1/0/1,  Port ID (outgoing port): TenGigabitEthernet1/1/4
Holdtime : 150 sec

Version :
Cisco IOS Software [Cupertino], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.9.4a, RELEASE SOFTWARE (fc3)

advertisement version: 2
Native VLAN: 999
Duplex: full
Management address(es):
  IP address: 10.0.1.1

-------------------------
Device ID: SEP00AABBCCDD02
Entry address(es):
  IP address: 10.50.0.20
Platform: Cisco IP Phone 7821,  Capabilities: Host Phone Two-port Mac Relay
Interface: GigabitEthernet1/0/5,  Port ID (outgoing port): Port 1
Holdtime : 160 sec

Version :
sip78xx.14-1-1SR1-2

advertisement version: 2
Duplex: full
"""

SX_CDP_DETAIL = """Device-ID: asw-test-02
Advertisement version: 2
Entry address(es):
  IP Address: 10.0.1.2
Platform: Cisco SG350-28P (PID:SG350-28P-K9)-VSD
Capabilities: Switch IGMP
Interface: gi1/0/1, Port ID (outgoing port): gi1/0/28
Holdtime: 170
Version: 2.5.9.16
Duplex: full
"""

IOS_COUNTERS_ERRORS = """
Port        Align-Err     FCS-Err    Xmit-Err     Rcv-Err  UnderSize  OutDiscards
Gi1/0/1             0           0           0           0          0            0
Gi1/0/2             0          12           0         150          0            0
Te1/1/1             0           0           0           0          0            0

Port      Single-Col  Multi-Col   Late-Col  Excess-Col  Carri-Sen      Runts     Giants
Gi1/0/1            0          0          0           0          0          0          0
Gi1/0/2            0          0          0           0          0          0          0
"""

NXOS_COUNTERS_ERRORS = """
--------------------------------------------------------------------------------
Port          Align-Err    FCS-Err   Xmit-Err    Rcv-Err  UnderSize OutDiscards
--------------------------------------------------------------------------------
mgmt0         --           --        --          --       --        --
Eth1/1        0            7         0           7        0         0
"""

IOS_POE = """Module   Available     Used     Remaining
          (Watts)     (Watts)    (Watts)
------   ---------   --------   ---------
1           740.0       45.8       694.2
2           740.0      600.0       140.0
Interface Admin  Oper       Power   Device              Class Max
                            (Watts)
--------- ------ ---------- ------- ------------------- ----- ----
Gi1/0/1   auto   on         15.4    AP515               4     30.0
Gi1/0/2   auto   off        0.0     n/a                 n/a   30.0
"""

SX_POE_TABLE = """Port Standard: 802.3AT
Unit  Power  Nominal Power  Consumed Power  Usage Threshold  Traps
----  -----  -------------  --------------  ---------------  -------
1     On     375 Watts      300 Watts (80%) 95               Disable

  Interface   Admin    Oper     Power   Class  Device                Priority
                                (W)
 ---------- -------- --------- ------- ------- --------------------- ---------
 gi1/0/1     Auto     On        12.40   class4                        low
 gi1/0/2     Auto     Off       0.00    class0                        low
"""

SG300_POE_KV = """Power: On
Nominal Power: 180 Watts
Consumed Power: 10 Watts (6%)
Usage Threshold: 95%
"""

ARUBA_AP_ACTIVE = """Active AP Table
---------------
Name        Group    IP Address   11g Clients  11g Ch/EIRP/MaxEIRP  11a Clients  11a Ch/EIRP/MaxEIRP  AP Type
----        -----    ----------   -----------  -------------------  -----------  -------------------  -------
ap-lib-01   campus   10.60.0.11   3            1/12.0/24.0          9            36/18.0/24.0         515
ap-lib-02   campus   10.60.0.12   0            6/12.0/24.0          4            40/18.0/24.0         303

Num APs:2
"""

ARUBA_AP_ACTIVE_RADIO = """Active AP Table
---------------
Name        Group    IP Address   Radio 0 Band Ch/EIRP/MaxEIRP/Clients  Radio 1 Band Ch/EIRP/MaxEIRP/Clients
----        -----    ----------   ------------------------------------  ------------------------------------
ap-hall-01  campus   10.60.1.11   AP:5GHz-HE:36/18/24/7                 AP:2.4GHz-HE:1/12/24/2
"""

NXOS_INTERFACE_STATUS = """
--------------------------------------------------------------------------------
Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
mgmt0         --                 connected routed    full    1000    --
Eth1/1        UPLINK TO DSW 01   connected trunk     full    10G     10Gbase-SR
Eth1/37       --                 inactive  30        auto    auto    1000base-T
Eth1/38       --                 err-disab 1         auto    auto    1000base-T
Eth1/39       --                 notconnec 1         auto    auto    1000base-T
Eth1/49       --                 xcvrAbsen 1         auto    auto    --
"""

ARUBA_AP_DATABASE_LONG = """AP Database
-----------
Name        Group    AP Type  IP Address   Status        Flags  Switch IP    Standby IP  Wired MAC Address  Serial #    Port  FQLN  Outer IP  User
----        -----    -------  ----------   ------        -----  ---------    ----------  -----------------  --------    ----  ----  --------  ----
ap-lib-01   campus   515      10.60.0.11   Up 3d:4h:1m   2      10.0.0.5     0.0.0.0     20:4c:03:00:00:01  CNTEST0001  N/A   N/A   N/A
ap-hall-09  campus   303      10.60.0.19   Down          N      10.0.0.5     0.0.0.0     20:4c:03:00:00:09  CNTEST0009  N/A   N/A   N/A

Total APs:2
"""

# Real AOS 8 layout (2026-11-05 capture), invented values: "Neighbor" is an index, the switch is
# "Chassis Name/ID", and "Not received" marks fields the switch did not send.
ARUBA_AP_LLDP = """AP LLDP Neighbors (Updated every 60 seconds)
--------------------------------------------
AP                   Interface  Neighbor  Chassis Name/ID           Port ID  Port Desc     Mgmt. Address  Capabilities
--                   ---------  --------  ---------------           -------  ---------     -------------  ------------
ap-lib-01            bond0      0         asw-test-02               gi1/0/1  Not received  10.0.1.2       BR
ap-hall-07           bond0      0         asw-test-03-SG300         gi22     Not received  Not received   B

Capability codes: (R)Router, (B)Bridge, (A)Access Point, (P)Phone, (O)Other
"""

# AP names with spaces: the ntc template drops these rows (seen live), the column reader keeps them.
ARUBA_AP_DATABASE_SPACES = """AP Database
-----------
Name                 Group     AP Type  IP Address    Status         Flags  Switch IP   Standby IP  Wired MAC Address  Serial #    Port  FQLN  Outer IP  User
----                 -----     -------  ----------    ------         -----  ---------   ----------  -----------------  --------    ----  ----  --------  ----
ap-lib-01            campus    515      10.60.0.11    Up 3d:4h:1m    2      10.0.0.5    0.0.0.0     20:4c:03:00:00:01  CNTEST0001  N/A   N/A   N/A
LAB_ Research Room   campus    315      10.60.0.31    Down           2      10.0.0.5    0.0.0.0     20:4c:03:00:00:31  CNTEST0031        N/A   N/A
LAB_IT LAB           campus    315      10.60.0.32    Down           2      10.0.0.5    0.0.0.0     20:4c:03:00:00:32  CNTEST0032        N/A   N/A

Total APs:3
"""

# Real NX-OS 'show cdp neighbors detail' layout for a neighbour that sends only
# "Interface address(es)" (older Nexus, SG500X). The ntc template returns no IP for it.
NXOS_CDP_INTERFACE_ADDRESS = """
----------------------------------------
Device ID:dsw-test-sg5
System Name: dsw-test-sg5

Interface address(es): 1
    IPv4 Address: 10.0.21.5
Platform: Cisco SG500X-24 (PID:SG500X-24-K9)-VSD, Capabilities: Router Switch IGMP Filtering
Interface: Ethernet1/7, Port ID (outgoing port): te1/1/1
Holdtime: 134 sec

Version:
1.4.11.4

Advertisement Version: 2

Native VLAN: 21
Duplex: full
Local Interface MAC: 70:a9:83:00:00:42
Remote Interface MAC: d4:d7:48:00:00:b0
DC_UUID:
----------------------------------------
Device ID:5017ff000001
System Name: asw-test-sg300

Interface address(es): 1
    IPv4 Address: 10.0.21.9
Platform: Cisco SG300-28 (PID:SRW2024-K9)-VSD, Capabilities: Switch IGMP
Interface: Ethernet1/8, Port ID (outgoing port): gi27
Holdtime: 120 sec
----------------------------------------
Device ID:5017ff000002

Interface address(es): 1
    IPv4 Address: 10.0.21.10
Platform: Cisco SG300-28 (PID:SRW2024-K9)-VSD, Capabilities: Switch IGMP
Interface: Ethernet1/9, Port ID (outgoing port): gi28
Holdtime: 120 sec
"""
