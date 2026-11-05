"""Pure parsing and normalising helpers. No device access here, so everything is unit-testable.

TextFSM (ntc-templates) output arrives as lists of dicts with upper-case keys; custom parsers
cover commands that have no template for a platform. Device text is untrusted input.
"""
import re

IFACE_PREFIXES = [
    ("hundredgigabitethernet", "Hu"), ("hundredgige", "Hu"), ("fortygigabitethernet", "Fo"),
    ("twentyfivegige", "Twe"), ("tengigabitethernet", "Te"), ("gigabitethernet", "Gi"),
    ("fastethernet", "Fa"), ("ethernet", "Eth"), ("port-channel", "Po"), ("twe", "Twe"),
    ("eth", "Eth"), ("gi", "Gi"), ("ge", "Gi"), ("te", "Te"), ("xg", "Te"), ("xe", "Te"),
    ("fa", "Fa"), ("fe", "Fa"), ("fo", "Fo"), ("hu", "Hu"), ("po", "Po"), ("mgmt", "mgmt"),
]


def short_if(name):
    """Canonical short interface name so CDP, LLDP, status and PoE rows can be joined."""
    n = str(name or "").strip()
    low = n.lower()
    for long, short in IFACE_PREFIXES:
        if low.startswith(long) and len(low) > len(long) and (low[len(long)].isdigit() or low[len(long)] == "/"):
            return short + n[len(long):]
    return n


def norm_mac(value):
    hexes = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    if len(hexes) != 12:
        return ""
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2)).lower()


def base_name(name):
    """Hostname without domain or NX-OS '(SERIAL)' suffix, lower-case."""
    n = re.sub(r"\(.*?\)$", "", str(name or "").strip())
    return n.split(".")[0].lower()


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _up(rows):
    """TextFSM rows with upper-case keys (netmiko and ntc-templates return lower-case)."""
    return [{str(k).upper(): v for k, v in r.items()} for r in (rows or []) if isinstance(r, dict)]


def first(value):
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def is_ip(value):
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", str(value or "").strip()))


# ---------- identity ----------

def version_ios(rows):
    rows = _up(rows)
    r = rows[0] if rows else {}
    return {
        "hostname": r.get("HOSTNAME", ""), "version": r.get("VERSION", ""),
        "model": first(r.get("HARDWARE")), "serial": first(r.get("SERIAL")),
        "uptime": r.get("UPTIME", ""), "mac": norm_mac(first(r.get("MAC_ADDRESS"))),
    }


def version_nxos(rows):
    rows = _up(rows)
    r = rows[0] if rows else {}
    return {
        "hostname": r.get("HOSTNAME", ""), "version": r.get("OS", ""),
        "model": r.get("PLATFORM", ""), "serial": r.get("SERIAL", ""),
        "uptime": r.get("UPTIME", "").strip(), "mac": "",
    }


def version_sx(version_rows, system_rows, system_id_rows):
    version_rows, system_rows, system_id_rows = _up(version_rows), _up(system_rows), _up(system_id_rows)
    v = version_rows[0] if version_rows else {}
    s = system_rows[0] if system_rows else {}
    sid = system_id_rows[0] if system_id_rows else {}
    desc = s.get("DESCRIPTION", "")
    return {
        "hostname": s.get("HOSTNAME", ""), "version": v.get("SW_VERSION", ""),
        "model": desc.split()[0] if desc else "", "serial": sid.get("SERIAL_NUMBER", ""),
        "uptime": s.get("UP_TIME", ""), "mac": norm_mac(s.get("MAC_ADDRESS", "")),
    }


def platform_hint(text):
    """Driver guess from a neighbour's CDP/LLDP platform string, e.g. 'Cisco CBS350-24P-4G (PID:…)'."""
    t = text or ""
    if re.search(r"\b(SG\d{3}|SF\d{3}|CBS\d{3}|C1300|SRW\d{3,4}|SX\d{3})", t, re.I):
        return "cisco_s300"
    if re.search(r"\bN\dK|Nexus|NX-OS", t, re.I):
        return "cisco_nxos"
    if re.search(r"WS-C|\bC9\d{3}|\bC1000|\bC3[68]50|\bIE-\d|\bISR|\bASR", t, re.I):
        return "cisco_ios"
    return ""


def classify_show_version(text):
    """Which netmiko driver fits, from 'show version' text read over a cisco_ios session."""
    t = text or ""
    if re.search(r"NX-OS|Nexus Operating System", t):
        return "cisco_nxos"
    if re.search(r"Cisco IOS|IOS-XE|IOS XE", t):
        return "cisco_ios"
    if re.search(r"SW version|Boot version|HW version|Active-image|Unrecognized command", t, re.I):
        return "cisco_s300"
    return ""


# ---------- neighbours ----------

MAC_TEXT = re.compile(r"[0-9a-fA-F]{12}|([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}|([0-9a-fA-F]{4}\.){2}[0-9a-fA-F]{4}")


def _nbr(local, name, ip, remote, platform="", caps="", desc="", chassis=""):
    name = str(name or "").strip()
    # SG/CBS switches advertise their MAC as the CDP Device-ID by default; keep it as the chassis MAC
    # so the link can still be matched to the switch's own 'show system' MAC.
    chassis = norm_mac(chassis) or (norm_mac(name) if MAC_TEXT.fullmatch(name) else "")
    return {
        "localPort": short_if(local), "name": name, "ip": ip if is_ip(ip) else "",
        "remotePort": short_if(remote), "platform": str(platform or "").strip(),
        "caps": str(caps or "").strip(), "desc": str(desc or "").strip()[:300],
        "chassis": chassis,
    }


def fill_missing_ips(primary, secondary):
    """Merge a second parse of the same output into the first: copy missing management IPs and add
    neighbours the first parse skipped. The ntc NX-OS CDP template drops the IP when a neighbour only
    sends 'Interface address(es)' (seen live: 15 of 33 kept) and can skip entries altogether."""
    key = lambda n: (n["localPort"], base_name(n["name"]))
    have = {key(n): n for n in primary}
    for n in secondary:
        if not n["name"]:  # the NX-OS template returns some entries with no name; never add those
            continue
        cur = have.get(key(n))
        if cur is None and n["ip"]:  # same port and IP under another name (MAC vs hostname) = same neighbour
            cur = next((p for p in primary if p["localPort"] == n["localPort"] and p["ip"] == n["ip"]), None)
        if cur is None:
            primary.append(n)
            have[key(n)] = n
        elif not cur["ip"] and n["ip"]:
            cur["ip"] = n["ip"]
    return primary


def cdp_textfsm(rows):
    rows = _up(rows)
    return [_nbr(r.get("LOCAL_INTERFACE"), r.get("NEIGHBOR_NAME"), r.get("MGMT_ADDRESS") or r.get("INTERFACE_IP"),
                 r.get("NEIGHBOR_INTERFACE"), r.get("PLATFORM"), r.get("CAPABILITIES"),
                 r.get("NEIGHBOR_DESCRIPTION")) for r in rows]


def lldp_textfsm(rows):
    rows = _up(rows)
    out = []
    for r in rows:
        name = first(r.get("NEIGHBOR_NAME")) if not isinstance(r.get("NEIGHBOR_NAME"), list) else "".join(r["NEIGHBOR_NAME"])
        remote = r.get("NEIGHBOR_PORT_ID") or r.get("NEIGHBOR_INTERFACE")
        if isinstance(remote, list):
            remote = "".join(remote)
        out.append(_nbr(r.get("LOCAL_INTERFACE"), name, r.get("MGMT_ADDRESS"), remote,
                        r.get("PLATFORM"), r.get("CAPABILITIES"), r.get("NEIGHBOR_DESCRIPTION"),
                        r.get("CHASSIS_ID") or r.get("MAC_ADDRESS")))
    return out


def cdp_detail_text(text):
    """Tolerant 'show cdp neighbors detail' parser (SG/CBS/C1300 and fallback for IOS)."""
    out = []
    blocks = re.split(r"(?m)^(?=\s*Device[- ]ID\s*:)", text or "")
    for b in blocks:
        m = re.search(r"Device[- ]ID\s*:\s*(\S+)", b)
        if not m:
            continue
        ip = re.search(r"IP(?:v4)?\s+[Aa]ddress\s*:\s*(\d{1,3}(?:\.\d{1,3}){3})", b)
        plat = re.search(r"Platform\s*:\s*([^,\n]+)", b)
        ports = re.search(r"Interface\s*:\s*([^,\n]+),\s*Port ID \(outgoing port\)\s*:\s*([^\n]+?)\s*$", b, re.M)
        caps = re.search(r"Capabilities\s*:\s*(.+)", b)
        ver = re.search(r"Version\s*:?\s*\n?\s*(.+)", b)
        if not ports:
            continue
        name, chassis = m.group(1), ""
        sysname = re.search(r"System Name\s*:\s*(\S+)", b)
        if sysname and MAC_TEXT.fullmatch(name):  # Device-ID is the MAC; NX-OS also prints the real name
            name, chassis = sysname.group(1), name
        out.append(_nbr(ports.group(1), name, ip.group(1) if ip else "", ports.group(2),
                        plat.group(1) if plat else "", caps.group(1) if caps else "", ver.group(1) if ver else "",
                        chassis))
    return out


def merge_neighbors(cdp, lldp):
    """One entry per (local port, neighbour); 'via' records which protocols saw it."""
    merged = {}
    for proto, rows in (("cdp", cdp), ("lldp", lldp)):
        for n in rows:
            key = (n["localPort"], base_name(n["name"]) or n["chassis"] or n["ip"])
            cur = merged.get(key)
            if cur is None:
                merged[key] = dict(n, via=[proto])
            else:
                for f in ("ip", "platform", "caps", "desc", "chassis", "remotePort"):
                    if not cur.get(f) and n.get(f):
                        cur[f] = n[f]
                if proto not in cur["via"]:
                    cur["via"].append(proto)
    return list(merged.values())


# ---------- ports, errors, PoE, CPU ----------

def ports_textfsm(rows, platform):
    rows = _up(rows)
    out = []
    for r in rows:
        if platform == "cisco_s300":
            status = "connected" if str(r.get("LINKSTATE", "")).lower() == "up" else "notconnect"
            out.append({"port": short_if(r.get("PORT")), "status": status, "vlan": "",
                        "speed": r.get("SPEED", ""), "duplex": r.get("DUPLEX", ""), "name": ""})
        else:
            status = str(r.get("STATUS", "")).lower()
            status = {"notconnec": "notconnect", "xcvrabsen": "sfpAbsent"}.get(status, status)
            out.append({"port": short_if(r.get("PORT")), "status": status, "vlan": r.get("VLAN_ID", ""),
                        "speed": r.get("SPEED", ""), "duplex": r.get("DUPLEX", ""),
                        "name": str(r.get("NAME", "")).strip()[:120]})
    return out


PORT_STATUSES = ("connected", "notconnect", "notconnec", "err-disabled", "disabled", "inactive", "sfpAbsent",
                 "xcvrAbsen", "noOperMem", "channelDo", "suspended", "suspnd", "monitoring", "down", "up", "linkFlapE",
                 "err-disab")


def ports_status_text(text):
    """Fallback for 'show interface(s) status' (IOS / NX-OS) when the TextFSM template fails.

    Columns are sliced at the header's word positions (Port, Name, Status, Vlan, Duplex, Speed, Type),
    so descriptions with spaces are fine. Any status word is accepted, including NX-OS 'inactive'.
    """
    lines = (text or "").splitlines()
    out = []
    for i, line in enumerate(lines):
        if not (line.startswith("Port") and "Status" in line and "Vlan" in line):
            continue
        heads = [(m.group(0), m.start()) for m in re.finditer(r"\S+", line)]
        starts = [s for _, s in heads]
        names = [h for h, _ in heads]
        for row in lines[i + 1:]:
            if not re.match(r"[A-Za-z]+[\d/]", row):
                continue
            vals = {}
            for k, name in enumerate(names):
                end = starts[k + 1] if k + 1 < len(starts) else len(row)
                vals[name] = row[starts[k]:end].strip()
            status = vals.get("Status", "")
            # Long descriptions can push Status right; recover it from the tokens if the slice missed.
            if status not in PORT_STATUSES:
                found = next((t for t in row.split()[1:] if t in PORT_STATUSES), "")
                status = found or status
            status = {"notconnec": "notconnect", "xcvrAbsen": "sfpAbsent", "suspnd": "suspended",
                      "err-disab": "err-disabled"}.get(status, status)
            out.append({"port": short_if(row.split()[0]), "status": status, "vlan": vals.get("Vlan", ""),
                        "speed": vals.get("Speed", ""), "duplex": vals.get("Duplex", ""),
                        "name": "" if vals.get("Name", "") == "--" else vals.get("Name", "")[:120]})
        break
    return out


def error_counters(text):
    """Header-driven parse of 'show interface(s) counters errors' (IOS and NX-OS).

    Returns {port: receive errors}. Uses Rcv-Err (all receive errors) or FCS-Err when absent.
    """
    out, heads = {}, None
    for line in (text or "").splitlines():
        cols = line.split()
        if not cols:
            continue
        if cols[0] == "Port":
            heads = cols
            continue
        if not heads or set(line.strip()) <= set("- "):
            continue
        if len(cols) != len(heads) or not re.match(r"[A-Za-z]+[\d/]", cols[0]):
            continue
        row = dict(zip(heads, cols))
        for key in ("Rcv-Err", "FCS-Err"):
            if key in row:
                val = to_int(row[key])
                if val is not None:
                    port = short_if(cols[0])
                    out[port] = max(out.get(port, 0), val)
                break
    return out


def poe_ports_ios(rows):
    rows = _up(rows)
    return [{"port": short_if(r.get("INTERFACE")), "oper": str(r.get("OPERATIONAL_STATUS", "")).lower(),
             "powerW": to_float(r.get("POWER")) or 0.0, "device": str(r.get("DEVICE", "")).strip()[:80],
             "class": str(r.get("CLASS", ""))} for r in rows]


def poe_totals(text):
    """(available W, used W) summed over stack members, or None if no PoE summary was found."""
    t = text or ""
    avail = used = 0.0
    found = False
    # IOS / IOS-XE: "Module   Available     Used     Remaining" then "1  740.0  45.8  694.2"
    if re.search(r"Available\s+Used\s+Remaining", t):
        for m in re.finditer(r"(?m)^\s*\d+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", t):
            avail += float(m.group(1))
            used += float(m.group(2))
            found = True
    # SG/CBS/C1300 table: "1   On   375 Watts   18 Watts (5%)"
    if not found:
        for m in re.finditer(r"(?m)^\s*\d+\s+On\s+(\d+(?:\.\d+)?)\s*Watts\s+(\d+(?:\.\d+)?)\s*Watts", t):
            avail += float(m.group(1))
            used += float(m.group(2))
            found = True
    # SG300 key/value form: "Nominal Power: 180 Watts" / "Consumed Power: 10 Watts (6%)"
    if not found:
        noms = re.findall(r"Nominal Power\s*:\s*(\d+(?:\.\d+)?)\s*Watts", t)
        cons = re.findall(r"Consumed Power\s*:\s*(\d+(?:\.\d+)?)\s*Watts", t)
        if noms and cons:
            avail, used, found = sum(map(float, noms)), sum(map(float, cons)), True
    return (round(avail, 1), round(used, 1)) if found else None


def poe_ports_sx(text):
    """Tolerant per-port PoE rows for SG/CBS/C1300. Shortcut: column order differs by firmware,
    so only port, oper state and the first decimal watt value are read; verify with captures."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*((?:gi|fa|te|ge|xg|fe)\d\S*)\s+(.*)$", line, re.I)
        if not m:
            continue
        rest = m.group(2)
        oper = re.search(r"\b(On|Off|Searching|Fault|Disabled)\b", rest)
        watts = re.search(r"\b(\d+\.\d+)\b", rest)
        cls = re.search(r"\b[Cc]lass\s?(\d)\b", rest)
        out.append({"port": short_if(m.group(1)), "oper": oper.group(1).lower() if oper else "",
                    "powerW": float(watts.group(1)) if watts else 0.0, "device": "",
                    "class": cls.group(1) if cls else ""})
    return out


def cpu_percent(text):
    """1-minute CPU % from IOS / SG-CBS output, or 100 - idle from NX-OS 'show system resources'."""
    t = text or ""
    m = re.search(r"one minute\s*:\s*(\d+)\s*%", t, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"CPU states\s*:.*?([\d.]+)%\s*idle", t, re.I | re.S)
    if m:
        return round(100 - float(m.group(1)))
    return None


# ---------- Aruba WLC ----------

def fixed_table(text):
    """Parse Aruba-style fixed-width tables whose header is underlined with dashes."""
    lines = (text or "").splitlines()
    rows = []
    for i in range(1, len(lines)):
        dash = lines[i]
        if not re.fullmatch(r"\s*-+(\s+-+)+\s*", dash):
            continue
        header = lines[i - 1]
        spans = [(m.start(), m.end()) for m in re.finditer(r"-+", dash)]
        starts = [s for s, _ in spans]
        names = []
        for k, start in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(header)
            names.append(header[start:end].strip())
        for line in lines[i + 1:]:
            if not line.strip() or re.match(r"\s*(Total|Num|Flags|-)", line):
                break
            values = []
            for k, start in enumerate(starts):
                end = starts[k + 1] if k + 1 < len(starts) else len(line)
                values.append(line[start:end].strip())
            rows.append(dict(zip(names, values)))
        break
    return rows


def ap_database(rows):
    rows = _up(rows)
    return [{"name": r.get("AP_NAME", ""), "group": r.get("GROUP", ""), "model": r.get("AP_MODEL", ""),
             "ip": r.get("IP_ADDRESS", ""), "status": r.get("STATUS", ""), "mac": norm_mac(r.get("MAC_ADDRESS")),
             "serial": r.get("SERIAL", ""), "flags": r.get("FLAGS", "")} for r in rows if r.get("AP_NAME")]


def ap_database_table(rows):
    """Fallback for 'show ap database long' read with fixed_table (Aruba column headings)."""
    get = lambda r, *keys: next((r[k] for k in keys if r.get(k)), "")
    out = []
    for r in rows:
        name = get(r, "Name", "AP Name")
        if not name:
            continue
        out.append({"name": name, "group": get(r, "Group"), "model": get(r, "AP Type", "Model"),
                    "ip": get(r, "IP Address"), "status": get(r, "Status"),
                    "mac": norm_mac(get(r, "Wired MAC Address", "Wired MAC", "MAC Address")),
                    "serial": get(r, "Serial #", "Serial"), "flags": get(r, "Flags")})
    return out


def ap_database_best(rows, text):
    """The ntc template drops APs whose names contain spaces (seen live: 16 of 20 rows); the column
    reader keeps them. Use whichever found more."""
    by_template, by_columns = ap_database(rows), ap_database_table(fixed_table(text))
    return by_columns if len(by_columns) > len(by_template) else (by_template or by_columns)


def ap_clients(rows):
    """{ap name: client count} from 'show ap active'. Handles '11g Clients' columns and the newer
    'Radio N Band Ch/EIRP/MaxEIRP/Clients' values ending in '/<clients>'."""
    out = {}
    for r in rows:
        name = r.get("Name") or r.get("AP Name") or ""
        if not name:
            continue
        total = 0
        for key, val in r.items():
            if "Clients" not in key:
                continue
            m = re.search(r"/(\d+)$", val) or re.fullmatch(r"(\d+)", val)
            if m:
                total += int(m.group(1))
        out[name] = total
    return out


def ap_lldp(rows):
    """{ap name: {switch, switchIp, port}} from 'show ap lldp neighbors'.

    AOS 8 columns (seen live): AP | Interface | Neighbor (an index, e.g. "0") | Chassis Name/ID |
    Port ID | Port Desc | Mgmt. Address | Capabilities. "Not received" means the switch sent nothing."""
    out = {}
    clean = lambda v: "" if str(v or "").strip().lower() in ("not received", "n/a", "-") else str(v or "").strip()
    for r in rows:
        name = r.get("AP") or r.get("AP Name") or ""
        if not name or name in out:
            continue
        neighbor = r.get("Neighbor", "")
        switch = clean(r.get("Chassis Name/ID") or r.get("Neighbor Name") or ("" if neighbor.isdigit() else neighbor))
        ip = clean(r.get("Mgmt. Address") or r.get("Mgmt Address") or r.get("Neighbor IP"))
        port = clean(r.get("Port ID") or r.get("Neighbor Port") or r.get("Port"))
        if switch or ip:
            out[name] = {"switch": switch, "switchIp": ip if is_ip(ip) else "", "port": short_if(port)}
    return out
