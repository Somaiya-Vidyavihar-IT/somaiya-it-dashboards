"""CCTV emails (sheet mode), sent as cctv.mail_from, HTML in the network team's alert layout plus a text part.

Everything about who gets what is in the sheet's "Email Config" tab:

    A1 "Emails"  B1 Off | Test | On      <- the one switch (anything else = Off)
    Institute Name | IT SPOC | CC | CC | Escalation   <- header row; labels may repeat, one address per cell
    VTI            | a@somaiya.edu | b@somaiya.edu | partner@gmail.com | head@somaiya.edu

Institute Name is the monitored tab's name. IT SPOC cells are To, CC cells are Cc, Escalation cells are added
only to the 48 h / 7 day escalations. The IT Network Team (cctv.email_team) is copied on urgent emails and
escalations. Test sends everything only to the team, marked with who would have got it.

Emails (each at most once per outage, remembered in state/cctv-mail.json, so restarts never resend):
  NVR down       an NVR fails cctv.nvr_down_checks rounds in a row; "back up" when it answers again
  Mass outage    >= mass_down_min cameras, or >= mass_down_fraction of a site, go down in one round; "back up"
  Daily report   at digest_time IST, per site, only if something is down or changed since the last report
  Escalation     once when a device passes escalate_hours down, once more after escalate_again_days
  System         once a day to the team: sheet unreadable, sheet/email-config problems, checks failing
Devices already down when a site is first seen (or when emails are first evaluated) never trigger urgent mails;
they appear in the next daily report. Messages that cannot be sent wait in an outbox and are retried for 24 h.
"""
import html
import logging
import math
import smtplib
import ssl
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .config import EMAIL_RE

log = logging.getLogger("netmon.cctv.mail")
IST = timezone(timedelta(hours=5, minutes=30))
STATE_FILE = ("state", "cctv-mail.json")
MODES = ("off", "test", "on")
URGENT = ("nvr-down", "nvr-up", "mass-down", "mass-up")
OUTBOX_HOURS = 24


def _utc(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ist(text):
    dt = _utc(text)
    return dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST") if dt else "unknown time"


def duration(hours):
    if hours is None:
        return "unknown"
    mins = int(round(hours * 60))
    d, rest = divmod(mins, 1440)
    h, m = divmod(rest, 60)
    return f"{d} d {h} h" if d else f"{h} h {m} min" if h else f"{m} min"


# ---------------------------------------------------------------- the Email Config tab

def _role(header):
    h = header.strip().lower()
    if "escalat" in h:
        return "escalation"
    if h.startswith("cc") or h == "copy" or "vendor" in h or "partner" in h:
        return "cc"
    if "spoc" in h or h in ("to", "email", "emails", "recipient", "recipients"):
        return "to"
    return None


def parse_email_config(rows, tab="Email Config", read_error=None, monitored=()):
    """{"mode", "sites": {site: {"to", "cc", "escalation"}}, "problems": [...]} from the tab's rows."""
    out = {"mode": "off", "switch": None, "sites": {}, "problems": [], "readError": read_error}
    prob = lambda row, issue, text="": out["problems"].append({"tab": tab, "row": row, "issue": issue, "text": str(text)[:120]})
    if rows is None:
        prob(None, "Email Config tab could not be read, so emails are Off" + (f": {read_error}" if read_error else ""))
        return out
    heads = None
    for i, r in enumerate(rows, start=1):
        cells = [str(c).strip() if c is not None else "" for c in r]
        if not any(cells):
            continue
        first = cells[0].lower()
        if heads is None and first == "emails":
            raw = cells[1] if len(cells) > 1 else ""
            out["switch"] = raw
            if raw.lower() in MODES:
                out["mode"] = raw.lower()
            else:
                prob(i, "the email switch must be Off, Test or On (treated as Off)", raw)
            continue
        if heads is None and first in ("institute name", "institute", "site", "tab"):
            heads = [c.lower() for c in cells]
            continue
        if heads is None:
            continue
        site = cells[0]
        if not site:
            continue
        if site in out["sites"]:
            prob(i, f"{site} is listed twice; only the first row is used", site)
            continue
        rec = {"to": [], "cc": [], "escalation": []}
        for j, cell in enumerate(cells[1:], start=1):
            if not cell:
                continue
            role = _role(heads[j]) if j < len(heads) else None
            if role is None:
                prob(i, "this column has no IT SPOC, CC or Escalation header", cell)
                continue
            if not EMAIL_RE.fullmatch(cell):
                prob(i, "not one valid email address (one address per cell)", cell)
                continue
            if cell.lower() not in rec[role]:
                rec[role].append(cell.lower())
        out["sites"][site] = rec
    if out["switch"] is None:
        prob(None, 'no "Emails" switch row (A1 "Emails", B1 Off / Test / On); emails are Off')
    if heads is None:
        prob(None, 'no header row starting with "Institute Name"')
    for site in monitored:
        rec = out["sites"].get(site)
        if not rec or not rec["to"]:
            prob(None, f"{site} is monitored but has no IT SPOC address, so it gets no site emails", site)
    return out


# ---------------------------------------------------------------- composing
#
# Every email is built from "sections" and rendered twice: an HTML part in the layout the network team's
# "Alert: Network Device Status Report" emails use (logos, "Dear ...", a device table with a red DOWN /
# green UP status, "Best Wishes," and the red bars), and a plain-text part for clients that show no HTML.
#   ("p", text)                               a paragraph (newlines kept)
#   ("table", title or None, rows, column)    rows = [(device or change event, "DOWN"/"UP", text for column)]
# Everything from the sheet is HTML-escaped. The two logos are the university's public emailer images; the
# device type is a coloured text badge rather than an icon image (the network team's icons are on a bucket that
# now answers 403, and a badge still shows when a mail client blocks images).

LOGO_LEFT = ("https://svv-public-data.somaiya.edu/emailer-images/university-logo.png", "Somaiya Vidyavihar University", 60)
LOGO_RIGHT = ("https://svv-public-data.somaiya.edu/emailer-images/footer-trust.png", "Somaiya Trust", 42)
BADGES = {"nvr": ("NVR", "#b7202e"), "camera": ("CCTV", "#58595b")}
RED, DARK_RED, GREEN = "#ee1c24", "#b7202e", "#1a7f37"
_TD = "padding:10px;border:1px solid #ddd;text-align:left;vertical-align:top"


def _label(d):
    return BADGES.get(d.get("kind"), BADGES["camera"])[0]


def _row_text(d, status, when):
    nvr = f" · NVR: {d['nvr']}" if d.get("nvr") else ""
    return f"- {_label(d)} {d['ip']} · {d['name']}{nvr} · {status}" + (f" · {when}" if when else "")


def render_text(greeting, sections, banner=None):
    out = [banner] if banner else []
    out.append(greeting)
    for s in sections:
        if s[0] == "p":
            out.append(s[1])
        elif s[0] == "table":
            lines = "\n".join(_row_text(d, status, when) for d, status, when in s[2])
            out.append((f"{s[1]}:\n" if s[1] else "") + lines)
    out.append("Best Wishes,\nSomaiya CCTV Monitoring")
    return "\n\n".join(out)


def _p(text, style="font-size:14px;line-height:24px;margin:0 0 10px"):
    return f'<p style="{style}">' + html.escape(str(text)).replace("\n", "<br>") + "</p>"


def _table_html(title, rows, column):
    e = html.escape
    out = []
    if title:
        out.append(f'<p style="font-size:14px;line-height:22px;font-weight:bold;margin:20px 0 6px">{e(title)}</p>')
    out.append('<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-top:'
               + ("0" if title else "20px") + ';font-size:14px"><thead><tr style="background-color:#f2f2f2">'
               + "".join(f'<th style="{_TD}">{e(h)}</th>' for h in ("Device Type", "IP Address", "Location", "Status", column))
               + "</tr></thead><tbody>")
    for d, status, when in rows:
        label, bg = BADGES.get(d.get("kind"), BADGES["camera"])
        nvr = (f'<br><span style="font-size:12px;color:#555">NVR: {e(str(d["nvr"]))}</span>' if d.get("nvr") else "")
        colour = RED if status == "DOWN" else GREEN
        out.append(f'<tr><td style="{_TD}"><span style="display:inline-block;padding:3px 8px;border-radius:3px;background:{bg};'
                   f'color:#fff;font-size:12px;font-weight:bold;letter-spacing:0.5px">{label}</span></td>'
                   f'<td style="{_TD}">{e(str(d["ip"]))}</td><td style="{_TD}">{e(str(d["name"]))}{nvr}</td>'
                   f'<td style="{_TD};color:{colour};font-weight:bold">{status}</td><td style="{_TD}">{e(when or "")}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def render_html(cc, greeting, sections, banner=None):
    e = html.escape
    body = []
    if banner:
        body.append('<div style="background:#fff4ce;border:1px solid #e0c060;padding:10px;margin:0 0 15px;font-size:13px;'
                    'line-height:20px">' + e(banner).replace("\n", "<br>") + "</div>")
    body.append(_p(greeting, "font-size:15px;line-height:24px;font-weight:bold;margin:0 0 10px"))
    for s in sections:
        body.append(_p(s[1]) if s[0] == "p" else _table_html(s[1], s[2], s[3]) if s[0] == "table" else "")
    dash = e(cc["dashboard_url"])
    note = ("Status comes from the CCTV monitoring collector, which pings every NVR and camera in the CCTV assets "
            "sheet every few minutes. Recipients are set in the sheet's Email Config tab. This is an automatic "
            f"message from {e(cc['mail_from'])}.")
    return (
        '<div><table cellpadding="0" cellspacing="0" style="max-width:700px;width:100%;border:1px solid #b7b7b7;'
        'font-family:sans-serif;margin:0 auto">'
        f'<thead><tr><td style="padding:10px;vertical-align:middle"><img src="{LOGO_LEFT[0]}" alt="{LOGO_LEFT[1]}" style="height:{LOGO_LEFT[2]}px;max-width:100%"></td>'
        f'<td style="padding:10px;text-align:right;vertical-align:middle"><img src="{LOGO_RIGHT[0]}" alt="{LOGO_RIGHT[1]}" style="height:{LOGO_RIGHT[2]}px;max-width:100%"></td></tr></thead>'
        '<tbody><tr><td colspan="2" style="padding:20px 10px 10px">' + "".join(body) + '</td></tr>'
        '<tr><td colspan="2" style="padding:10px 10px 10px"><p style="font-size:14px;margin:0 0 5px;color:#000"><b>Best Wishes,</b></p>'
        '<p style="font-size:14px;margin:0 0 10px;color:#000">Somaiya CCTV Monitoring</p>'
        f'<p style="font-size:12px;line-height:18px;color:#777;margin:0">Dashboard: <a href="{dash}" style="color:#777">{dash}</a><br>{note}</p></td></tr>'
        f'<tr><td colspan="2"><div style="height:30px;border-right:60px solid {DARK_RED}"></div></td></tr>'
        f'<tr><td colspan="2"><table cellpadding="0" cellspacing="0" style="width:100%"><tr>'
        f'<td width="20%" style="height:30px;background-color:{RED}"></td><td width="80%" style="height:30px;background-color:{DARK_RED}"></td>'
        '</tr></table></td></tr></tbody></table></div>')


def _footer_text(cc):
    return (f"\n\nDashboard: {cc['dashboard_url']}\n"
            "Status comes from the CCTV monitoring collector, which pings every NVR and camera listed in the "
            "CCTV assets sheet every few minutes. Recipients are set in the sheet's Email Config tab.\n"
            "This is an automatic message from " + cc["mail_from"] + ".")


def _since(d, prefix="since "):
    approx = "" if d.get("downSinceExact") else "at least "
    return f"{prefix}{ist(d.get('downSince'))} ({approx}{duration(d.get('downHours'))})"


def _clean_subject(text):
    return " ".join(str(text).split())[:200]


class Mailer:
    """Turns this round's events into messages for the outbox."""

    def __init__(self, cfg, email_cfg, now, state):
        self.cc, self.email, self.now, self.state = cfg["cctv"], email_cfg, now, state
        self.mode = email_cfg["mode"]
        self.team = list(cfg["cctv"]["email_team"])
        self.messages = []

    def _recipients(self, site, kind):
        rec = self.email["sites"].get(site) or {"to": [], "cc": [], "escalation": []}
        to = list(rec["to"])
        cc = list(rec["cc"])
        if kind.startswith("escalation"):
            cc += rec["escalation"]
        if kind in URGENT or kind.startswith("escalation"):
            cc += self.team
        if kind == "system":
            to, cc = list(self.team), []
        cc = [a for i, a in enumerate(cc) if a not in to and a not in cc[:i]]
        return to, cc

    def add(self, site, kind, subject, sections):
        to, cc = self._recipients(site, kind)
        if not to:
            return False  # no IT SPOC (reported as an Email Config problem)
        if self.mode == "off":
            return False
        if kind in URGENT and not self._rate_ok(site):
            self.state.setdefault("held", {})
            self.state["held"][site] = self.state["held"].get(site, 0) + 1
            return False
        banner = None
        if self.mode == "test":
            if not self.team:
                return False
            banner = (f"TEST MODE: this email went only to the IT Network Team.\nIn On mode it would go to:\n"
                      f"To: {', '.join(to)}\nCc: {', '.join(cc) or '-'}")
            to, cc, subject = list(self.team), [], "[TEST] " + subject
        greeting = "Dear IT Network Team," if kind == "system" else f"Dear {site} IT Team,"
        self.messages.append({"id": make_msgid(domain=self.cc["mail_from"].split("@")[-1]), "kind": kind, "site": site,
                              "subject": _clean_subject(subject),
                              "body": render_text(greeting, sections, banner) + _footer_text(self.cc),
                              "html": render_html(self.cc, greeting, sections, banner),
                              "to": to, "cc": cc, "created": _iso(self.now)})
        if kind in URGENT:
            self.state.setdefault("urgentLog", []).append({"site": site, "at": _iso(self.now)})
        return True

    def _rate_ok(self, site):
        since = self.now - timedelta(hours=1)
        recent = [e for e in self.state.get("urgentLog", []) if e["site"] == site and _utc(e["at"]) > since]
        return len(recent) < self.cc["urgent_per_site_hour"]


# ---------------------------------------------------------------- the rules

def process(cfg, store, snap, now=None, send=None):
    """Run the email rules for this round, queue and send messages. Returns the status for the page."""
    cc = cfg["cctv"]
    now = now or datetime.now(timezone.utc)
    email_cfg = snap.get("emailConfig") or parse_email_config(None, cc["email_config_tab"])
    st = store.read(*STATE_FILE, default=None) or {}
    st.setdefault("devices", {})
    st.setdefault("mass", {})
    st.setdefault("changes", {})
    st.setdefault("outbox", [])
    st["mode"] = email_cfg["mode"]
    m = Mailer(cfg, email_cfg, now, st)
    devs = snap["devices"]
    by_site = defaultdict(list)
    for d in devs:
        by_site[d["tab"]].append(d)

    nvr_down, nvr_up, new_down = [], [], defaultdict(list)
    esc = {"escalation-48h": defaultdict(list), "escalation-7d": defaultdict(list)}
    esc_hours = cc["escalate_hours"]
    again_hours = cc["escalate_again_days"] * 24
    for d in devs:
        down = d["status"] == "down"
        prev = st["devices"].get(d["ip"])
        if prev is None:
            # First sight (first run, or a newly added tab): an existing outage is not news.
            hours = d.get("downHours") or 0
            prev = {"down": down, "since": d.get("downSince"), "nvrMailed": down,
                    "esc": (["48h"] if down and hours >= esc_hours else []) + (["7d"] if down and hours >= again_hours else [])}
        elif down and not prev["down"]:
            prev = {"down": True, "since": d.get("downSince"), "nvrMailed": False, "esc": []}
            new_down[d["tab"]].append(d)
            _change(st, d["tab"], {"type": "down", "name": d["name"], "ip": d["ip"], "kind": d["kind"],
                                   "nvr": d.get("nvr"), "at": d.get("downSince") or _iso(now)})
        elif not down and prev["down"]:
            since = _utc(prev.get("since"))
            hours = (now - since).total_seconds() / 3600 if since else None
            _change(st, d["tab"], {"type": "up", "name": d["name"], "ip": d["ip"], "kind": d["kind"],
                                   "nvr": d.get("nvr"), "at": _iso(now), "hours": hours})
            if d["kind"] == "nvr" and prev.get("alerted"):
                nvr_up.append((d, hours))
            prev = {"down": False, "since": None, "nvrMailed": False, "esc": []}
        if down:
            if d["kind"] == "nvr" and not prev["nvrMailed"] and d.get("failedChecks", 1) >= cc["nvr_down_checks"]:
                nvr_down.append(d)
                prev["nvrMailed"] = prev["alerted"] = True
            hours = d.get("downHours") or 0
            if hours >= esc_hours and "48h" not in prev["esc"]:
                esc["escalation-48h"][d["tab"]].append(d)
                prev["esc"].append("48h")
            if hours >= again_hours and "7d" not in prev["esc"]:
                esc["escalation-7d"][d["tab"]].append(d)
                prev["esc"].append("7d")
        st["devices"][d["ip"]] = prev
    current = {d["ip"] for d in devs}
    st["devices"] = {k: v for k, v in st["devices"].items() if k in current}

    for d in nvr_down:
        cams = [c for c in by_site[d["tab"]] if c.get("nvr") and c["nvr"].endswith(f"({d['ip']})")]
        m.add(d["tab"], "nvr-down", f"[CCTV] {d['tab']}: NVR down - {d['name']} ({d['ip']})", [
            ("p", f"The following NVR at {d['tab']} is currently down ({d.get('failedChecks')} checks in a row). "
                  f"Cameras recording to it are probably not being recorded ({len(cams)} listed under it in the sheet). "
                  "Please check its power, network cable and switch port."),
            ("table", None, [(d, "DOWN", _since(d, ""))], "Down Since")])
    for d, hours in nvr_up:
        m.add(d["tab"], "nvr-up", f"[CCTV] {d['tab']}: NVR back up after {duration(hours)} - {d['name']}", [
            ("p", f"The following NVR at {d['tab']} answers again, after {duration(hours)} down. No action is needed."),
            ("table", None, [(d, "UP", f"{ist(_iso(now))} (down {duration(hours)})")], "Back Up At")])

    for site, ds in new_down.items():
        cams = [d for d in ds if d["kind"] == "camera"]
        total = sum(1 for d in by_site[site] if d["kind"] == "camera")
        need = min(cc["mass_down_min"], max(2, math.ceil(cc["mass_down_fraction"] * total)))
        if cams and len(cams) >= need and site not in st["mass"]:
            st["mass"][site] = {"since": _iso(now), "ips": [d["ip"] for d in cams]}
            m.add(site, "mass-down", f"[CCTV] {site}: {len(cams)} cameras down at once", [
                ("p", f"{len(cams)} of {total} cameras at {site} stopped answering at {ist(_iso(now))}. When many go down "
                      "together it is usually a network switch, power or uplink fault rather than the cameras. "
                      "Please take necessary action to resolve the issue."),
                ("table", None, [(d, "DOWN", _since(d, "")) for d in cams], "Down Since")])
    by_ip = {d["ip"]: d for d in devs}
    for site, mass in list(st["mass"].items()):
        still = [ip for ip in mass["ips"] if by_ip.get(ip, {}).get("status") == "down"]
        if len(still) <= len(mass["ips"]) * 0.2:
            since = _utc(mass["since"])
            hours = (now - since).total_seconds() / 3600 if since else None
            m.add(site, "mass-up", f"[CCTV] {site}: cameras back up after {duration(hours)}", [
                ("p", f"Most of the {len(mass['ips'])} cameras that went down together at {site} answer again "
                      f"(after {duration(hours)})." + (" The ones below are still down." if still else " No action is needed."))]
                + ([("table", "Still down", [(by_ip[ip], "DOWN", _since(by_ip[ip], "")) for ip in still], "Down Since")]
                   if still else []))
            del st["mass"][site]

    for kind, sites in esc.items():
        if kind == "escalation-48h":
            days = f"{esc_hours // 24} days" if esc_hours % 24 == 0 else f"{esc_hours} hours"
        else:
            days = f"{cc['escalate_again_days']} days"
        for site, ds in sites.items():
            ds = sorted(ds, key=lambda d: -(d.get("downHours") or 0))
            m.add(site, kind, f"[CCTV] {site} escalation: {len(ds)} device{'s' if len(ds) > 1 else ''} down more than {days}", [
                ("p", f"The following CCTV devices at {site} have been down for more than {days} and still do not answer. "
                      "Please arrange the repair or tell IT what is blocking it."),
                ("table", None, [(d, "DOWN", _since(d, "")) for d in ds], "Down Since")])

    _daily(cc, st, m, by_site, now, email_cfg, snap)
    st["messagesQueued"] = len(m.messages)
    st["outbox"] = [x for x in st["outbox"] if _utc(x["created"]) > now - timedelta(hours=OUTBOX_HOURS)] + m.messages
    if email_cfg["mode"] == "off":
        st["outbox"] = []  # Off means nothing goes out, including anything still waiting
    status = _flush(cfg, st, now, send)
    store.write(st, *STATE_FILE)
    status.update(mode=email_cfg["mode"], switch=email_cfg.get("switch"),
                  sites={s: {k: len(v) for k, v in r.items()} for s, r in email_cfg["sites"].items()},
                  recipients=email_cfg["sites"], problems=len(email_cfg["problems"]), mailFrom=cc["mail_from"],
                  digestTime=cc["digest_time"])
    return status


def _change(st, site, event):
    st["changes"].setdefault(site, []).append(event)
    st["changes"][site] = st["changes"][site][-300:]


def _daily(cc, st, m, by_site, now, email_cfg, snap):
    local = now.astimezone(IST)
    hh, mm = (int(x) for x in cc["digest_time"].split(":"))
    today = local.strftime("%Y-%m-%d")
    due = (local.hour, local.minute) >= (hh, mm)
    if st.get("digestLast") is None:
        st["digestLast"] = today if due else (local - timedelta(days=1)).strftime("%Y-%m-%d")  # first report: next due time
        return
    if not due or st["digestLast"] == today:
        return
    for site, devs in sorted(by_site.items()):
        down = sorted((d for d in devs if d["status"] == "down"), key=lambda d: d.get("downSince") or "")
        changes = st["changes"].get(site, [])
        held = (st.get("held") or {}).get(site, 0)
        if not down and not changes and not held:
            continue
        went = [c for c in changes if c["type"] == "down"]
        back = [c for c in changes if c["type"] == "up"]
        parts = [("p", f"{site}: {len(down)} down now, {len(went)} went down and {len(back)} came back since the last report."
                       + (" The following CCTV devices are currently down. Please take necessary action to resolve the issues."
                          if down else " Nothing is down now."))]
        if down:
            parts.append(("table", "Down now (oldest first)", [(d, "DOWN", _since(d, "")) for d in down], "Down Since"))
        if went:
            parts.append(("table", "Went down since the last report", [(c, "DOWN", ist(c["at"])) for c in went], "Went Down At"))
        if back:
            parts.append(("table", "Came back since the last report",
                          [(c, "UP", f"{ist(c['at'])} (down {duration(c.get('hours'))})") for c in back], "Back Up At"))
        if held:
            parts.append(("p", f"{held} urgent email(s) were held back because of the hourly limit; the details are above."))
        m.add(site, "daily", f"[CCTV] {site} daily report - {len(down)} down", parts)
    st["changes"], st["held"] = {}, {}
    st["digestLast"] = today
    # System email to the team: once a day, with the reports.
    issues = []
    if snap.get("sheetError"):
        issues.append(f"The CCTV sheet could not be re-read: {snap['sheetError']} (the copy from {ist(snap.get('sheetReadAt'))} is in use).")
    probs = snap.get("sheetProblems") or []
    if probs:
        issues.append(f"{len(probs)} sheet problem(s) (rows not monitored or email settings ignored):\n"
                      + "\n".join(f"- {p['tab']} row {p['row'] or '-'}: {p['issue']}" + (f" ({p['text']})" if p.get("text") else "")
                                  for p in probs[:50]))
    if issues:
        m.add("", "system", "[CCTV] Monitoring problems to fix", [("p", x) for x in issues])


def on_failure(cfg, store, error, now=None, send=None):
    """Checks failed this round: tell the team once a day after an hour of failures (if emails are Test/On)."""
    now = now or datetime.now(timezone.utc)
    st = store.read(*STATE_FILE, default=None) or {}
    st.setdefault("outbox", [])
    first = _utc(st.get("failingSince"))
    if first is None:
        st["failingSince"] = _iso(now)
    elif now - first >= timedelta(hours=1) and st.get("failureMailDay") != now.astimezone(IST).strftime("%Y-%m-%d") \
            and st.get("mode") in ("test", "on"):
        m = Mailer(cfg, {"mode": st["mode"], "sites": {}}, now, st)
        m.add("", "system", "[CCTV] Monitoring has stopped checking", [
            ("p", f"The CCTV collector has failed every round since {ist(_iso(first))}:"), ("p", str(error)),
            ("p", "The dashboard shows old data until this is fixed. See C:\\netmon\\data\\logs\\netmon-serve.log on the "
                  "collector host.")])
        st["outbox"] += m.messages
        st["failureMailDay"] = now.astimezone(IST).strftime("%Y-%m-%d")
    _flush(cfg, st, now, send)
    store.write(st, *STATE_FILE)


def clear_failure(store):
    st = store.read(*STATE_FILE, default=None)
    if st and st.get("failingSince"):
        st.pop("failingSince", None)
        store.write(st, *STATE_FILE)


# ---------------------------------------------------------------- sending

def build_message(cc, item):
    msg = EmailMessage()
    msg["From"] = formataddr((cc["mail_from_name"], cc["mail_from"]))
    msg["To"] = ", ".join(item["to"])
    if item["cc"]:
        msg["Cc"] = ", ".join(item["cc"])
    msg["Subject"] = _clean_subject(item["subject"])
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = item["id"]
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Netmon-Kind"] = item["kind"]
    msg.set_content(item["body"])
    if item.get("html"):  # messages queued before the HTML layout have text only
        msg.add_alternative(item["html"], subtype="html")
    return msg


def smtp_sender(cfg):
    """send(list of EmailMessage) over one SMTP session. Password for cctv.smtp_username from Credential Manager."""
    cc = cfg["cctv"]

    def send(msgs):
        password = None
        if cc["smtp_username"]:
            from . import creds
            password = creds.get(cfg, "smtp")[1]
        with smtplib.SMTP(cc["smtp_host"], cc["smtp_port"], timeout=30) as s:
            s.ehlo()
            if cc["smtp_starttls"]:
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if password:
                s.login(cc["smtp_username"], password)
            for msg in msgs:
                s.send_message(msg)
    return send


def _flush(cfg, st, now, send=None):
    cc = cfg["cctv"]
    pending = st.get("outbox", [])
    if pending:
        try:
            (send or smtp_sender(cfg))([build_message(cc, x) for x in pending])
            st["lastSentAt"] = _iso(now)
            st["sentLog"] = (st.get("sentLog", []) + [{"at": _iso(now), "kind": x["kind"], "site": x["site"]} for x in pending])[-500:]
            st["outbox"], st["lastError"] = [], None
        except Exception as exc:  # keep them for the next round
            st["lastError"] = f"{exc.__class__.__name__}: {exc}"[:300]
            log.warning("CCTV email send failed (%d waiting): %s", len(pending), st["lastError"])
    today = now.astimezone(IST).strftime("%Y-%m-%d")
    sent_today = sum(1 for e in st.get("sentLog", []) if _utc(e["at"]).astimezone(IST).strftime("%Y-%m-%d") == today)
    return {"lastSentAt": st.get("lastSentAt"), "sentToday": sent_today, "waiting": len(st.get("outbox", [])),
            "lastError": st.get("lastError")}
