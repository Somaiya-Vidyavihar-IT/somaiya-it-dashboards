"""LLD outputs: Excel workbook (inventory, connectivity matrix, APs, ports, findings) and Graphviz DOT."""
import re

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Device text (LLDP names, descriptions) is untrusted: never let it become a spreadsheet formula."""
    if isinstance(value, str):
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)  # openpyxl rejects control characters
        return "'" + value if value.startswith(FORMULA_START) else value
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return value


def write_excel(snapshot, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    sheets = [
        ("Devices", ["Institute", "Hostname", "Mgmt IP", "Role", "Platform", "Model", "Serial", "OS version",
                     "Uptime", "Status", "CPU %", "PoE used W", "PoE available W", "Ports connected", "Err-disabled",
                     "Flags", "Error"],
         [[d["institute"], d["hostname"], d["ip"], d["role"], d["platform"], d["model"], d["serial"], d["version"],
           d["uptime"], d["status"], d["cpu"], (d["poe"] or {}).get("usedW"), (d["poe"] or {}).get("availableW"),
           (d["ports"] or {}).get("connected"), (d["ports"] or {}).get("errDisabled"), d["flags"], d["error"]]
          for d in snapshot["devices"]]),
        ("Connectivity Matrix", ["Device A", "IP A", "Port A", "Device B", "IP B", "Port B", "Protocols", "Seen both sides",
                                 "Uplink", "Status", "First seen", "Last seen"],
         [[l.get("aName"), l["a"], l["aPort"], l.get("bName"), l["b"], l["bPort"], l.get("via"), l.get("both"),
           l.get("uplink"), l["status"], l.get("firstSeen"), l.get("lastSeen")] for l in snapshot["links"]]),
        ("Access Points", ["Institute", "AP name", "Model", "Serial", "MAC", "IP", "Group", "WLC status", "Clients",
                           "WLC", "Switch", "Switch IP", "Port", "PoE W", "PoE class", "Seen by"],
         [[a["institute"], a["name"], a["model"], a["serial"], a["mac"], a["ip"], a["group"], a["status"], a["clients"],
           a["wlc"], a["switch"], a["switchIp"], a["port"], a["poeW"], a["poeClass"], a["seenBy"]] for a in snapshot["aps"]]),
        ("Problem Ports", ["Institute", "Hostname", "IP", "Port", "Issue", "Detail", "Neighbour"],
         [[p["institute"], p["hostname"], p["ip"], p["port"], p["issue"], p["detail"], p["neighbor"]]
          for p in snapshot["problemPorts"]]),
        ("Findings", ["Severity", "Type", "Hostname", "IP", "Message"],
         [[f["severity"], f["type"], f["hostname"], f["ip"], f["message"]] for f in snapshot["findings"]]),
        ("Neighbours", ["Hostname", "IP", "Local port", "Neighbour", "Neighbour IP", "Remote port", "Platform", "Kind", "Protocols"],
         [[d["hostname"], d["ip"], n["localPort"], n["name"], n["ip"], n["remotePort"], n["platform"], n["kind"], n["via"]]
          for d in snapshot["devices"] for n in d["neighbors"]]),
    ]
    wb = Workbook()
    wb.remove(wb.active)
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="B7202E")
    for title, heads, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append(heads)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for row in rows:
            ws.append([safe_cell(v) for v in row])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, h in enumerate(heads, start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = max(12, min(40, len(h) + 4))
    wb.properties.title = f"Somaiya campus network inventory {snapshot['generatedAt']}"
    wb.save(path)


def _clean(text):
    # Device text is untrusted: no quotes, backslashes or line breaks inside a DOT string.
    return re.sub(r'["\\\n\r]', " ", str(text or ""))


def _q(text):
    return '"' + _clean(text) + '"'


def write_dot(snapshot, path):
    lines = ["graph campus {", '  graph [rankdir=TB, fontname="Arial", splines=true];',
             '  node [shape=box, style="rounded,filled", fillcolor="#ffffff", fontname="Arial", fontsize=10];',
             '  edge [fontname="Arial", fontsize=8];']
    by_inst = {}
    for d in snapshot["devices"]:
        by_inst.setdefault(d["institute"], []).append(d)
    for i, (inst, devs) in enumerate(sorted(by_inst.items())):
        lines.append(f"  subgraph cluster_{i} {{ label={_q(inst)}; color=\"#b7202e\";")
        for d in devs:
            color = "#f7e9ec" if d["status"] != "ok" or d["flags"] else "#ffffff"
            label = "\\n".join(_clean(v) for v in (d["hostname"] or d["ip"], d["model"], d["ip"]))
            lines.append(f"    {_q(d['ip'])} [label=\"{label}\", fillcolor=\"{color}\"];")
        lines.append("  }")
    for l in snapshot["links"]:
        style = {"lost": 'color="#ed1c24", style=dashed', "down": 'color="#ed1c24"', "unknown": 'color="#999999", style=dotted'}.get(l["status"], 'color="#58595b"')
        lines.append(f"  {_q(l['a'])} -- {_q(l['b'])} [taillabel={_q(l['aPort'])}, headlabel={_q(l['bPort'])}, {style}];")
    lines.append("}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
