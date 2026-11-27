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

# SG500X stack firmware 1.4 (seen live): per-unit tables and no System Name line.
SG500X_VERSION = """
       Unit             SW version         Boot version         HW version
------------------- ------------------- ------------------- -------------------
         1               1.4.11.2            1.4.0.02               V02
"""

SG500X_SYSTEM = """
Unit          Type
---- ----------------------
 1         SG500X-24

Unit   FAN
       Status
----   ---------
 1        OK

Unit     Up time
---- ---------------
 1     05,20:52:32
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

# Real AOS 8 behaviour (2026-11-06 capture of a 348-AP controller): long lists arrive as repeated
# blocks, each with its own header, column widths and Flags legend; a Down AP may have no flags.
ARUBA_AP_DATABASE_BLOCKS = """
AP Database
-----------
Name               Group       AP Type  IP Address     Status              Flags  Switch IP     Standby IP  Wired MAC Address  Serial #    Port  FQLN  Outer IP  User
----               -----       -------  ----------     ------              -----  ---------     ----------  -----------------  --------    ----  ----  --------  ----
ap-blk1-up         campus      515      10.60.0.11     Up 5d:20h:7m:59s           10.0.0.5      0.0.0.0     20:4c:03:00:01:01  CNTEST0101  N/A   N/A   N/A
ap-blk1-down       campus      655      10.60.0.12     Down                       10.0.0.5      0.0.0.0     20:4c:03:00:01:02  USTEST0102        N/A   N/A

Flags: 1 = 802.1x authenticated AP use EAP-PEAP;   1+ = 802.1x use EST;    1- = 802.1x use factory cert
       2 = Using IKE version 2;                    4 = WiFi Uplink

AP Database
-----------
Name                          Group        AP Type  IP Address     Status             Flags  Switch IP     Standby IP  Wired MAC Address  Serial #    Port  FQLN  Outer IP  User
----                          -----        -------  ----------     ------             -----  ---------     ----------  -----------------  --------    ----  ----  --------  ----
LAB Block Two Corridor AP     campus-west  505      10.60.1.21     Up 14d:15h:32m:37s        10.0.0.5      0.0.0.0     20:4c:03:00:02:01  USTEST0201  N/A   N/A   N/A
ap-blk2-flagged               campus-west  303      10.60.1.22     Down               N      10.0.0.5      0.0.0.0     20:4c:03:00:02:02  CNTEST0202        N/A   N/A

Flags: 1 = 802.1x authenticated AP use EAP-PEAP;   1+ = 802.1x use EST;    1- = 802.1x use factory cert

Total APs:4
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

# ---- uplink load (invented output in the IOS / NX-OS layouts) ----

IOS_SHOW_INTERFACE = """GigabitEthernet1/0/49 is up, line protocol is up (connected)
  Hardware is Gigabit Ethernet, address is 00aa.bb00.0049 (bia 00aa.bb00.0049)
  Description: UPLINK TO DSW-TEST-01
  MTU 1500 bytes, BW 1000000 Kbit/sec, DLY 10 usec,
     reliability 255/255, txload 105/255, rxload 3/255
  Encapsulation ARPA, loopback not set
  Full-duplex, 1000Mb/s, media type is 1000BaseSX SFP
  input flow-control is on, output flow-control is unsupported
  Last input 00:00:01, output 00:00:00, output hang never
  Queueing strategy: fifo
  5 minute input rate 12345000 bits/sec, 2100 packets/sec
  5 minute output rate 412345000 bits/sec, 36000 packets/sec
     123456789 packets input, 98765432100 bytes, 0 no buffer
     0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored
     234567890 packets output, 198765432100 bytes, 0 underruns
"""

NXOS_SHOW_INTERFACE = """Ethernet1/49 is up
admin state is up, Dedicated Interface
  Belongs to Po10
  Hardware: 1000/10000/25000 Ethernet, address: 00aa.bb00.1049 (bia 00aa.bb00.1049)
  Description: TO-DSW-TEST-02
  MTU 1500 bytes, BW 10000000 Kbit , DLY 10 usec
  reliability 255/255, txload 196/255, rxload 1/255
  full-duplex, 10 Gb/s, media type is 10G
  Load-Interval #1: 30 seconds
    30 seconds input rate 4567000 bits/sec, 800 packets/sec
    30 seconds output rate 7700000000 bits/sec, 640000 packets/sec
    input rate 4.57 Mbps, 800 pps; output rate 7.70 Gbps, 640.00 Kpps
  Load-Interval #2: 5 minute (300 seconds)
    300 seconds input rate 3456000 bits/sec, 700 packets/sec
    300 seconds output rate 6600000000 bits/sec, 550000 packets/sec
    input rate 3.46 Mbps, 700 pps; output rate 6.60 Gbps, 550.00 Kpps
"""

NXOS_SHOW_INTERFACE_BPS_ONLY = """Ethernet1/50 is up
  MTU 1500 bytes, BW 10000000 Kbit , DLY 10 usec
  Load-Interval #2: 5 minute (300 seconds)
    input rate 1.20 Mbps, 300 pps; output rate 2.50 Gbps, 210.00 Kpps
"""

IOS_SHOW_INTERFACE_NO_BW = """GigabitEthernet1/0/50 is up, line protocol is up (connected)
  Hardware is Gigabit Ethernet, address is 00aa.bb00.0050 (bia 00aa.bb00.0050)
  5 minute input rate 1000 bits/sec, 1 packets/sec
  5 minute output rate 2000 bits/sec, 2 packets/sec
"""

IOS_ETHERCHANNEL_SUMMARY = """Flags:  D - down        P - bundled in port-channel
        I - stand-alone s - suspended
        H - Hot-standby (LACP only)
        R - Layer3      S - Layer2
        U - in use      f - failed to allocate aggregator

        M - not in use, minimum links not met
        u - unsuitable for bundling
        w - waiting to be aggregated
        d - default port

        A - formed by Auto LAG


Number of channel-groups in use: 2
Number of aggregators:           2

Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi1/0/49(P)     Gi1/0/50(P)
2      Po2(SU)         LACP      Te1/1/1(P)      Te1/1/2(P)      Te1/1/3(D)
                                 Te1/1/4(P)
"""

NXOS_PORT_CHANNEL_SUMMARY = """Flags:  D - Down        P - Up in port-channel (members)
        I - Individual  H - Hot-standby (LACP only)
        s - Suspended   r - Module-removed
        b - BFD Session Wait
        S - Switched    R - Routed
        U - Up (port-channel)
        p - Up in delay-lacp mode (member)
        M - Not in use. Min-links not met
--------------------------------------------------------------------------------
Group Port-       Type     Protocol  Member Ports
      Channel
--------------------------------------------------------------------------------
10    Po10(SU)    Eth      LACP      Eth1/49(P)   Eth1/50(P)
20    Po20(SU)    Eth      LACP      Ethernet1/51(P)   Eth1/52(D)
                                     Eth1/53(P)
"""

# CBS350 'show interfaces counters' (live sample, 2026-11-27): short port names, no wrapped numbers.
SX_COUNTERS_CBS350 = """
Port       InUcastPkts  InMcastPkts  InBcastPkts    InOctets
---------------- ------------ ------------ ------------ ------------
      gi1          2242367        3915         6817      1806044466
      gi2           818132        4970         667       205248611
      gi3          7282236       33873          13       2896947844
      gi4          5871445       33873          13       2850525919
      gi5           851263        2756         4618      711423343
      gi6          8334715       33873          13       4221837605
      gi7          15421389      33872          13      10444998690
      gi8          3460640       33873          13       1288066204
      gi9             0            0            0            0
      gi10         4117042        4285        15155      3060952412
      gi11            0            0            0            0
      gi12            0            0            0            0
      gi13         2870139       33873          13       967532001
      gi14            0            0            0            0
      gi15         11200070      33873          13       9475171099
      gi16            0            0            0            0
      gi17            0            0            0            0
      gi18            0            0            0            0
      gi19            0            0            0            0
      gi20            0            0            0            0
      gi21            0            0            0            0
      gi22            0            0            0            0
      gi23            0            0            0            0
      gi24        362167108    221929674    296921824   380353629057
      gi25            0            0            0            0
      gi26            0            0            0            0
      gi27            0            0            0            0
      gi28            0            0            0            0

      Port       OutUcastPkts OutMcastPkts OutBcastPkts  OutOctets
---------------- ------------ ------------ ------------ ------------
      gi1          5491303      2852128      5352706     7491776663
      gi2          1880263      2016059      4611404     2715969441
      gi3          56351301     12929518     32596750   42587360992
      gi4          40729818     12929517     32596749   34183803196
      gi5          1806082      2388786      5742443     2835391323
      gi6          67917347     12929516     32596742   58064182664
      gi7          83381999     12929519     32596736   77464764000
      gi8          18545779     12929518     32596736   15926545301
      gi9             0            0            0            0
      gi10         6163623      1219064      2969373     7508777492
      gi11            0            0            0            0
      gi12            0            0            0            0
      gi13         20573010     12929516     32596746   15676773186
      gi14            0            0            0            0
      gi15         39753120     12929515     32596748   35951093734
      gi16            0            0            0            0
      gi17            0            0            0            0
      gi18            0            0            0            0
      gi19            0            0            0            0
      gi20            0            0            0            0
      gi21            0            0            0            0
      gi22            0            0            0            0
      gi23            0            0            0            0
      gi24         62475824      63491        26792     37884659241
      gi25            0            0            0            0
      gi26            0            0            0            0
      gi27            0            0            0            0
      gi28            0            0            0            0

       Ch        InUcastPkts  InMcastPkts  InBcastPkts    InOctets
---------------- ------------ ------------ ------------ ------------
      Po1             0            0            0            0
      Po2             0            0            0            0
      Po3             0            0            0            0
      Po4             0            0            0            0
      Po5             0            0            0            0
      Po6             0            0            0            0
      Po7             0            0            0            0
      Po8             0            0            0            0

       Ch        OutUcastPkts OutMcastPkts OutBcastPkts  OutOctets
---------------- ------------ ------------ ------------ ------------
      Po1             0            0            0            0
      Po2             0            0            0            0
      Po3             0            0            0            0
      Po4             0            0            0            0
      Po5             0            0            0            0
      Po6             0            0            0            0
      Po7             0            0            0            0
      Po8             0            0            0            0
"""

# C1300 'show interfaces counters' (live sample, 2026-11-27): numbers wider than the column continue on the next line.
SX_COUNTERS_C1300 = """
Port       InUcastPkts  InMcastPkts  InBcastPkts    InOctets
---------------- ------------ ------------ ------------ ------------
    gi1/0/1       105109579      313845        2055     45461468579
    gi1/0/2         70926         316          118        6201931
    gi1/0/3       9645294409     285386       621803    126047948017
                                                        30
    gi1/0/4       9622006961     285395       622165    125638171460
                                                        68
    gi1/0/5           0            0            0            0
    gi1/0/6           0            0            0            0
    gi1/0/7           0            0            0            0
    gi1/0/8           0            0            0            0
    gi1/0/9        1946247        226           97       185037158
    gi1/0/10       2281312        243           59       211138867
    gi1/0/11       2028670        264          688       188272701
    gi1/0/12       3520391        318         14935      359038077
    gi1/0/13       1919056        382           36       182450788
    gi1/0/14       1914803        887          756       188188145
    gi1/0/15       2664620         0          17903      319895735
    gi1/0/16          0            0            0            0
    gi1/0/17          0            0            0            0
    gi1/0/18         250           0            6          21013
    gi1/0/19          0            0            0            0
    gi1/0/20          0            0            0            0
    gi1/0/21          0            0            0            0
    gi1/0/22          0            0            0            0
    gi1/0/23          0            0            0            0
    gi1/0/24          0            0            0            0
    te1/0/1       5884710192   5378739583   9500091062  377635108543
                                                        8
    te1/0/2      19176193293    1546666      1321780    249266524788
                                                        58
    te1/0/3        51696944      748252       90006      5128000768
    te1/0/4           0            0            0            0

      Port       OutUcastPkts OutMcastPkts OutBcastPkts  OutOctets
---------------- ------------ ------------ ------------ ------------
    gi1/0/1       156904878     17388763     4017243    144471453660
    gi1/0/2         136953        1428         611       185160107
    gi1/0/3       1065156415    33303669     7232053    78710636854
    gi1/0/4       1064961852    33303634     7231687    78696946244
    gi1/0/5           0            0            0            0
    gi1/0/6           0            0            0            0
    gi1/0/7           0            0            0            0
    gi1/0/8           0            0            0            0
    gi1/0/9        2298544      20310164     4799422     3417827863
    gi1/0/10       2610021      20306867     4797904     3439676017
    gi1/0/11       2390448      20309892     4798735     3428939423
    gi1/0/12       3746074      24706043     6063832     4102195994
    gi1/0/13       2248061      20309168     4799031     3414892526
    gi1/0/14       2243859      20311053     4799618     3425470271
    gi1/0/15       4264526      20175147     4740445     2797302469
    gi1/0/16          0            0            0            0
    gi1/0/17          0            0            0            0
    gi1/0/18         255          792          216         113515
    gi1/0/19          0            0            0            0
    gi1/0/20          0            0            0            0
    gi1/0/21          0            0            0            0
    gi1/0/22          0            0            0            0
    gi1/0/23          0            0            0            0
    gi1/0/24          0            0            0            0
    te1/0/1      38616338249    2360415      2615098    501471718860
                                                        56
    te1/0/2       2755663306   3036230803   4367601292  198501099645
                                                        7
    te1/0/3       470316142    2667911895   3389499496  151396006404
                                                        5
    te1/0/4           0            0            0            0

       Ch        InUcastPkts  InMcastPkts  InBcastPkts    InOctets
---------------- ------------ ------------ ------------ ------------
      Po1             0            0            0            0
      Po2             0            0            0            0
      Po3             0            0            0            0
      Po4             0            0            0            0
      Po5             0            0            0            0
      Po6             0            0            0            0
      Po7             0            0            0            0
      Po8             0            0            0            0

       Ch        OutUcastPkts OutMcastPkts OutBcastPkts  OutOctets
---------------- ------------ ------------ ------------ ------------
      Po1             0            0            0            0
      Po2             0            0            0            0
      Po3             0            0            0            0
      Po4             0            0            0            0
      Po5             0            0            0            0
      Po6             0            0            0            0
      Po7             0            0            0            0
      Po8             0            0            0            0
"""

# CBS350 'show interfaces status' (live sample, 2026-11-27): speed in Mbps, '--' when down.
SX_STATUS_CBS350 = """
Flow Link          Back   Mdix
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi2      1G-Copper      --      --     --     --  Down           --     --
gi3      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi4      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi5      1G-Copper      --      --     --     --  Down           --     --
gi6      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi7      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi8      1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi9      1G-Copper      --      --     --     --  Down           --     --
gi10     1G-Copper      --      --     --     --  Down           --     --
gi11     1G-Copper      --      --     --     --  Down           --     --
gi12     1G-Copper      --      --     --     --  Down           --     --
gi13     1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
gi14     1G-Copper      --      --     --     --  Down           --     --
gi15     1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
gi16     1G-Copper      --      --     --     --  Down           --     --
gi17     1G-Copper      --      --     --     --  Down           --     --
gi18     1G-Copper      --      --     --     --  Down           --     --
gi19     1G-Copper      --      --     --     --  Down           --     --
gi20     1G-Copper      --      --     --     --  Down           --     --
gi21     1G-Copper      --      --     --     --  Down           --     --
gi22     1G-Copper      --      --     --     --  Down           --     --
gi23     1G-Copper      --      --     --     --  Down           --     --
gi24     1G-Copper    Full    1000  Enabled  Off  Up          Disabled On
gi25     1G-Fiber       --      --     --     --  Down           --     --
gi26     1G-Fiber       --      --     --     --  Down           --     --
gi27     1G-Fiber       --      --     --     --  Down           --     --
gi28     1G-Fiber       --      --     --     --  Down           --     --

                                          Flow    Link
Ch       Type    Duplex  Speed  Neg      control  State
-------- ------- ------  -----  -------- -------  -----------
Po1         --     --      --      --       --    Not Present
Po2         --     --      --      --       --    Not Present
Po3         --     --      --      --       --    Not Present
Po4         --     --      --      --       --    Not Present
Po5         --     --      --      --       --    Not Present
Po6         --     --      --      --       --    Not Present
Po7         --     --      --      --       --    Not Present
Po8         --     --      --      --       --    Not Present
"""

# C1300 'show interfaces status' (live sample, 2026-11-27): 100/1000/10000 Mbps.
SX_STATUS_C1300 = """
Flow Link          Back   Mdix
Port      Type         Duplex  Speed Neg      ctrl State       Pressure Mode
--------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1/0/1   1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
gi1/0/2   1G-Copper      --      --     --     --  Down           --     --
gi1/0/3   1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/4   1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/5   1G-Copper      --      --     --     --  Down           --     --
gi1/0/6   1G-Copper      --      --     --     --  Down           --     --
gi1/0/7   1G-Copper      --      --     --     --  Down           --     --
gi1/0/8   1G-Copper      --      --     --     --  Down           --     --
gi1/0/9   1G-Copper    Full    100   Enabled  Off  Up          Disabled On
gi1/0/10  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/11  1G-Copper    Full    100   Enabled  Off  Up          Disabled On
gi1/0/12  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/13  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/14  1G-Copper    Full    100   Enabled  Off  Up          Disabled Off
gi1/0/15  1G-Copper    Full    100   Enabled  Off  Up          Disabled On
gi1/0/16  1G-Copper      --      --     --     --  Down           --     --
gi1/0/17  1G-Copper      --      --     --     --  Down           --     --
gi1/0/18  1G-Copper      --      --     --     --  Down           --     --
gi1/0/19  1G-Copper      --      --     --     --  Down           --     --
gi1/0/20  1G-Copper      --      --     --     --  Down           --     --
gi1/0/21  1G-Copper      --      --     --     --  Down           --     --
gi1/0/22  1G-Copper      --      --     --     --  Down           --     --
gi1/0/23  1G-Copper      --      --     --     --  Down           --     --
gi1/0/24  1G-Copper      --      --     --     --  Down           --     --
te1/0/1   10G-Fiber    Full    10000 Disabled Off  Up          Disabled Off
te1/0/2   10G-Fiber    Full    10000 Disabled Off  Up          Disabled Off
te1/0/3   10G-Fiber    Full    10000 Disabled Off  Up          Disabled Off
te1/0/4   10G-Fiber      --      --     --     --  Down           --     --

                                          Flow    Link
Ch       Type    Duplex  Speed  Neg      control  State
-------- ------- ------  -----  -------- -------  -----------
Po1         --     --      --      --       --    Not Present
Po2         --     --      --      --       --    Not Present
Po3         --     --      --      --       --    Not Present
Po4         --     --      --      --       --    Not Present
Po5         --     --      --      --       --    Not Present
Po6         --     --      --      --       --    Not Present
Po7         --     --      --      --       --    Not Present
Po8         --     --      --      --       --    Not Present
"""
