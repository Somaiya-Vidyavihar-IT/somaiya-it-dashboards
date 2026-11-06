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

# ---- SG/CBS/C1300 layouts as seen live on 2026-11-05 (invented names, addresses and serials) ----

SG300_VERSION = """
SW version    1.4.11.5 ( date  08-Apr-2020 time  13:49:34 )
Boot version    1.3.5.06 ( date  21-Jul-2013 time  15:12:10 )
HW version    V02
"""

CBS_VERSION = """
Active-image: flash://system/images/image1.bin
  Version: 3.2.1.1
  MD5 Digest: 00000000000000000000000000000000
  Date: 13-Feb-2023
Inactive-image: flash://system/images/_image1.bin
  Version: 3.1.0.57
"""

CBS_SYSTEM = """
System Description:                       CBS350-24FP-4G 24-Port Gigabit PoE Managed Switch
System Up Time (days,hour:min:sec):       86,05:32:10
System Contact:
System Name:                              asw-test-cbs
System Location:
System MAC Address:                       40:14:82:00:00:78
System Object ID:                         1.3.6.1.4.1.9.6.1.1004.28.6

Unit          Type
---- ----------------------
 1       CBS350-24FP-4G
"""

SG300_SYSTEM_ID = """
Serial number : DNITEST0001
"""

CBS_SYSTEM_ID = """
       Unit            Serial number
------------------- -------------------
         1              FOCTEST0002
"""

SX_CDP_DETAIL_REAL = """
---------------------------------------------
Device-ID: 007e95000001
Advertisement version: 2
Platform: Cisco SG350-28P (PID:SG350-28P-K9)-VSD
Capabilities: Router Switch IGMP
Interface: gi21, Port ID (outgoing port): gi26
Holdtime: 130
Version: 2.5.9.54
Duplex: full
Native VLAN: 1
SysName: switch000001
SysObjectID: 0.0
Addresses:
          IP 10.0.19.85
          IPv6 fe80::27e:95ff:fe00:1 (link-local)
---------------------------------------------
Device-ID: dsw-test-c1000.example.local
Advertisement version: 2
Platform: cisco C1000-24FP-4X-L
Capabilities: Switch IGMP
Interface: gi23, Port ID (outgoing port): GigabitEthernet1/0/10
Holdtime: 161
Version: Cisco IOS Software, C1000 Software (C1000-UNIVERSALK9-M), Version 15.2(7)E6, RELEASE SOFTWARE (fc2)
Technical Support: http://www.cisco.com/techsupport
Copyri
Duplex: full
Native VLAN: 19
SysObjectID: 0.0
Primary Management Address: IP 10.0.19.5,
Addresses:
          IP 10.0.19.5
"""

SX_LLDP_SUMMARY_REAL = """
System capability legend:
B - Bridge; R - Router; W - Wlan Access Point; T - telephone;
D - DOCSIS Cable Device; H - Host; r - Repeater;
TP - Two Ports MAC Relay; S - S-VLAN; C - C-VLAN; O - Other

  Port        Device ID        Port ID       System Name    Capabilities  TTL
--------- ----------------- ------------- ----------------- ------------ -----
gi16      d8:cb:8a:00:12:10 d8:cb:8a:00:1                        O       2791
                            2:10
gi21      00:7e:95:00:00:01     gi26        switch000001        B, R      99
gi24      f0:5c:19:00:b0:fc f0:5c:19:00:b LAB_2ndFloor_Staf      W        117
                            0:fc          fRoom
gi25      00:8e:73:00:a0:ab     gi25      asw-test-2ND-CLS1     B, R      119
                                          -AS02-SG350
gi27      b0:00:b4:00:00:29     gi49        switch000029         B        100

"""

CBS_POE = """

Power-limit mode: Class based
Usage threshold: 95%

Unit Module           Nominal   Consumed     Temp     SW            PSE chipset
                      Power (W) Power (W)    (C)      Version       HW Revision
---- ---------------- --------- ------------ -------- ------------- -----------------
1    CBS350-24FP-4G   370       183 (49%)    64       0.2.0.34       TPS2388 - 0x40C4
                                                                     TPS2388 - 0x40C4


Interface    Admin       Oper         Power (W)     Class     Device     Priority
---------- ---------- ----------- ----------------- ----- -------------- --------
gi1        Auto       Fault       0.0               -                    low
gi3        Auto       On          10.138 (25.5)     4                    low
gi12       Auto       On          0.798             0                    low
gi14       Auto       Searching   0.0               -                    low
"""

# 'show lldp neighbors <port>' on SG/CBS: the two documented layouts (no live capture yet).
SX_LLDP_PORT_SIMPLE = """
Device ID: 00:11:22:00:00:01
Port ID: gi24
Capabilities: Bridge, Router
System Name: asw-test-hpc-01
System description: CBS350-24P-4G 24-Port Gigabit PoE Managed Switch
Port description:
Management address: 10.0.28.21
Time To Live: 107 seconds
"""

SX_LLDP_PORT_BLOCK = """
Device ID: 00:11:22:00:00:02
Port ID: gi24
System Name: asw-test-hpc-02
Capabilities: B, R
System description: SG350-28 28-Port Gigabit Managed Switch

Management Address Information
  Address Subtype: IPv6
  Address: fe80::211:22ff:fe00:2
  Address Subtype: IPv4
  Address: 10.0.28.22
  Interface Subtype: ifIndex
  Interface Number: 100000
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
