# netmon: campus network collector and API

Backend for the hub's **Network Monitoring** dashboard (`site/network-monitoring/`) and **CCTV Monitoring** dashboard (`site/cctv-monitoring/`, see [CCTV dashboard](#cctv-dashboard)). It runs on a campus Windows host, never on GitHub Pages.

- **Collector:** logs in over SSH with a read-only account and sends only `show` commands to the Nexus 9K core, the Catalyst / SG / CBS / C1300 distribution and access switches, and the Aruba WLCs. The workflow follows the network team's LLD: sweep the configured subnets for TCP/22, log in, `show version`, CDP + LLDP neighbours, crawl new neighbours inside the configured subnets, then write reports.
- **Outputs** (in `data_dir`): `latest.json` (the dashboard snapshot), timestamped snapshots, `inventory-latest.xlsx` (Devices, Connectivity Matrix, Access Points, Problem Ports, Findings, Neighbours) and `topology-latest.dot` (Graphviz).
- **API:** a small HTTPS server that serves those files only to Google accounts on the IT Network Team allowlist, and lets the same team start a run.

Nothing here writes to devices. Every command is asserted at import time to start with `show `.

## Schedule

| Task | When | Command |
|---|---|---|
| `netmon-full-run` | 08:00 and 16:30 IST | `python -m netmon run` |
| `netmon-reach` | every 15 minutes | `python -m netmon reach` (TCP/22 to known switches and WLCs, no login) |
| `netmon-api` | at startup, auto-restart | `python -m netmon serve` |
| Run now (dashboard) | on demand, IT Network Team | same as the full run; one run at a time |
| CCTV: SolarWinds read | every `cctv.poll_minutes` (5) inside `netmon-api`, and on demand | `python -m netmon cctv` runs one read by hand |

**Progress per switch.** Every full run (scheduled or Run now) writes `data\run-progress.json`: one item per switch and WLC with its state (waiting, running, ok, failed, no-ssh), time taken, how it was found (core seed, last run, neighbour of X port) and any error. The API serves it at `/api/v1/progress`, and the page shows it as a live list under the run status, polled every 5 seconds while a run is going.
| Refresh switch / Reconnect (dashboard, switch details) | on demand, IT Network Team | `python -m netmon refresh --ip <IP>` |

**Single-switch refresh.** Logs in to that one switch only (every credential set again, remembered one first) and re-runs its `show` commands. The snapshot is then rebuilt from the last full run's raw data (`data\state\last-run.json`) with that switch replaced, so links, roles, findings and APs stay consistent; no other switch or WLC is contacted, and new neighbours it reveals wait for the next full run. Only switches already in the last full run can be refreshed, one at a time (`state\refresh.lock`), and not while a full run is going. It needs one full run with this version first (that run writes `last-run.json`).

**IP phones that look like switches.** Some phones advertise the LLDP "bridge" capability, so they look like switches. Neighbours named or described as one of `collector.phone_models` (default `["X1SG", "X301G", "X3SG"]`, Fanvil models seen on campus) are treated as phones: never probed on tcp/22, never logged in to, never listed as switches. Add a model to the list in `config.toml` when another one turns up.

**No SSH from the collector.** A neighbour switch whose management IP is in a configured subnet but does not answer tcp/22 from the collector is listed as its own switch (state "No SSH from collector", linked under the switch that sees it, with a `no-ssh` warning finding) instead of only an info finding. The 15-minute check skips it. Reconnect on it checks tcp/22 first and logs in only if it now answers; the switches behind it are crawled by the next full run. Output goes to `data\refresh.log`; the result to `data\refresh-status.json`.

## Install on the collector host (Windows 10/11 or Windows Server)

A plain-language, step-by-step guide for the network team is kept in the network project (`network/runbooks/netmon-vm-setup.md`). Short version, in an elevated PowerShell. `<…>` values are placeholders.

1. **Service account.** Create a local account such as `svc-netmon` (Computer Management → Local Users). It runs the scheduled tasks and owns the stored passwords.
2. **Python 3.13 (64-bit)** from python.org, installed for all users.
3. **Install the collector.** Download this repository, then run from `collector\deploy`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup-host.ps1 -ServiceAccount '<VM_NAME>\svc-netmon'
   ```
   This creates `C:\netmon` (code, Python packages, `config.toml` if missing, and the `C:\netmon\netmon` command) and restricts the folder to Administrators, SYSTEM and the service account.
4. **Config.** Edit `C:\netmon\config.toml`: subnets with institutes (`sweep = false` for whole institute ranges, plus the Nexus core IPs as `seeds`), switch credential set names and usernames, WLC IPs and username, and the `network_team` emails. Keep Windows paths in single quotes.
5. **Passwords** go into Windows Credential Manager under the service account (`runas /user:<VM_NAME>\svc-netmon powershell`):
   ```powershell
   C:\netmon\netmon set-password switch --set set1     # repeat for set2, set3
   C:\netmon\netmon set-password wlc
   C:\netmon\netmon check-config
   ```
6. **Certificate.** Either a self-signed certificate (each team member accepts a browser warning once):
   ```powershell
   C:\netmon\netmon make-cert --host netmon.somaiya.edu
   ```
   or a trusted wildcard certificate later: `C:\netmon\netmon import-pfx <PATH>.pfx`. Restart `netmon-api` after changing it.
7. **DNS:** an internal A record `netmon.somaiya.edu` → this VM.
8. **Firewall.** Allow the API port only from IT staff subnets:
   ```powershell
   New-NetFirewallRule -DisplayName "netmon API" -Direction Inbound -Protocol TCP -LocalPort 8443 -RemoteAddress <IT_STAFF_SUBNETS> -Action Allow
   ```
9. **Validate the parsers on real gear before go-live.** Capture one device of each model and each WLC. This is read-only and the output is saved under `data\captures` (never commit it):
   ```powershell
   C:\netmon\netmon capture --ip <SWITCH_IP>
   C:\netmon\netmon capture --wlc <WLC_IP>
   ```
   Check the printed summary: hostname, model, serial, neighbours, PoE and CPU. The SG/CBS/C1300 CDP, PoE and CPU parsers and the Aruba `show ap active` / `show ap lldp neighbors` parsers are tolerant custom parsers, so confirm them first.
10. **First run and scheduled tasks.** The service account needs the Windows right **Log on as a batch job**, or the tasks are registered but never start (`LastTaskResult 267011`). Grant it in `secpol.msc` (started from the elevated window) → Local Policies → User Rights Assignment. On a domain-joined host, pick the computer under *Locations…* to find a local account. `install-tasks.ps1` checks the right, starts `netmon-api`, and reports whether Windows actually launched it.
    ```powershell
    C:\netmon\netmon run
    .\install-tasks.ps1 -ServiceAccount '<VM_NAME>\svc-netmon'
    curl.exe -k https://localhost:8443/api/v1/health
    ```
11. **Hub.** Set `netmonApiBase` in `site/config.js` to `https://netmon.somaiya.edu:8443` and publish (`dev` first).

### Credential sets

Switches can use several read-only accounts. `switch_credential_sets` lists them in the order to try. For each switch the collector tries the set that worked last time first, then the others, and remembers the winner in `data\state\credentials.json` (set names only). A switch where every set fails is shown as "SSH authentication failed with all N credential sets". A new switch costs at most N−1 failed logins, once.

## CCTV dashboard

The CCTV page shows cameras and NVRs up or down, the NVR inventory, and everything down longer than `cctv.escalate_hours` (48). It runs inside `netmon-api` as a background thread (no extra scheduled task), every `poll_minutes`; **Check now** on the page wakes it early (one round at a time; requests are written to `audit.log`). `C:\netmon\netmon cctv` does one round by hand and prints a summary. Outputs: `data\cctv.json` (the page snapshot) and `data\cctv-status.json`. `cctv.source` picks where the device list comes from.

### Sheet mode (`source = "sheet"`): the CCTV Google Sheet is the master list

`netmon/cctv_sheet.py` reads the tabs in `sheet_tabs` (only finished tabs) of the sheet `sheet_id` and checks every device itself.

- **Reading the sheet:** a Google **service account** with the read-only Sheets scope (JWT bearer, no extra packages). Its JSON key is only on the collector host (`service_account_file`, default `C:\netmon\secrets\cctv-sheet.json`, readable by Administrators and the service account); the sheet is shared with the account's email as **Viewer**, and the Google Sheets API is enabled in its project. The sheet is re-read at most every `sheet_minutes` (15); if a read fails, the last good copy is used and the page says so.
- **Rows:** in each tab the header row is the first row with an `IP` cell. Columns: `Location`, `Device Type` or `Particulars`, `IP`, and when present `Model`, `Serial No.`, `Warranty`, `Date of Expiry`, `Status`. A type containing "NVR" is an NVR; camera/CCTV/dome/bullet/PTZ is a camera; HDD rows are skipped. A camera is linked to the nearest NVR row above it in its tab. The tab name is the site. Rows with a bad or duplicate IP, a blank or unknown type are not monitored and are listed on the page under **Sheet problems** with tab and row.
- **Up/down:** ICMP echo from the collector (`check = "ping"`; Windows `IcmpSendEcho`, no admin rights needed) or a TCP connect to `tcp_ports` (`check = "tcp"`, default 80 and 554), `check_tries` attempts with `check_timeout_ms` each, `check_workers` in parallel. The collector host needs ICMP (or those ports) to the camera and NVR subnets.
- **Down since:** the collector's own history (`data\state\cctv-reach.json`): "observed" (exact to one round) when a device that answered earlier stops answering; "at least since" when it was already down the first time it was checked.
- **Setup:** copy the key to `service_account_file`, set `source`, `sheet_id`, `sheet_tabs` and `viewers` in `[cctv]`, run `C:\netmon\netmon check-config` and `C:\netmon\netmon cctv`, then restart `netmon-api`.

### Emails (sheet mode, `netmon/cctv_mail.py`)

Sent as `mail_from` (itsurveillance@somaiya.edu). **The one switch and all recipients are in the sheet's `email_config_tab`** ("Email Config"), read separately from the camera tabs, so a missing or broken email tab never stops monitoring (emails just count as Off):

```text
A1 Emails | B1 Off / Test / On            <- the switch; anything else (blank, typo, unreadable tab) = Off
Institute Name | IT SPOC | CC | CC | Escalation   <- header row; labels may repeat; one address per cell
VTI            | spoc@...| cc@...| partner@... | head@...
```

`Institute Name` is the monitored tab's name. **IT SPOC** cells (any header containing "SPOC") are To, **CC**, **Vendor** or **Partner** cells Cc, **Escalation** cells are added only to escalations; `email_team` (default `server.network_team`) is copied on urgent mails and escalations. **Test** sends every mail only to `email_team`, with "[TEST]" and the would-be recipients in the text. Cells that are not one valid address, columns without a known header, duplicate sites and monitored sites without an IT SPOC are listed as sheet problems (tab "Email Config").

| Email | When | To |
|---|---|---|
| NVR down / back up | an NVR fails `nvr_down_checks` (2) rounds in a row; back up when it answers again | site + team |
| Cameras down at once / back up | ≥ `mass_down_min` (5) cameras, or ≥ `mass_down_fraction` (half) of a site, go down in one round; back up when ≥ 80 % answer | site + team |
| Daily site report | at `digest_time` (09:00 IST), per site, only if something is down or changed since the last report; single camera outages are only reported here | site |
| Escalation | once when a device passes `escalate_hours` (48 h) down, once more after `escalate_again_days` (7) | site + Escalation + team |
| Monitoring problems | with the daily reports, if the sheet could not be re-read or has problems; and once a day after an hour of failed rounds | team |

- **No floods, no repeats:** what was mailed per outage is kept in `data\state\cctv-mail.json`, so restarts never resend. Devices already down when a site is first seen or emails are first evaluated, and anything that happened while the switch was Off, never produce urgent mails; they appear in the next daily report. More than `urgent_per_site_hour` (4) urgent mails for one site in an hour are held for the daily report.
- **Sending:** SMTP with STARTTLS to `smtp_host:smtp_port`, as `smtp_username` with its password from Credential Manager (`C:\netmon\netmon set-password smtp`, e.g. a Google app password for itsurveillance@somaiya.edu), or without login through a relay that allows this host (`smtp-relay.gmail.com`). Mails that cannot be sent wait in an outbox and are retried each round for 24 hours; the page's Connection tab shows the switch, last sent, waiting mails, the last error and the recipients per site. Switching Off drops anything waiting.
- **Try the mail route** before turning the switch on: `C:\netmon\netmon cctv-mail-test --to you@somaiya.edu` (ignores the switch).
- Mails are plain text: site, location, device type, NVR, IP, since when, and the dashboard link.

### SolarWinds mode (`source = "solarwinds"`)

SolarWinds already polls these devices; `netmon/cctv.py` only reads its results through the SolarWinds Information Service (`https://<host>:17774/SolarWinds/InformationService/v3/Json/Query`) with a **read-only** SolarWinds account. Every statement is checked to be a SWQL `SELECT` before it is sent.

- **Which nodes are cameras/NVRs** (SolarWinds has no built-in camera type): the node custom property `type_property` (`Device_Type`) in `nvr_types` / `camera_types`; else, untagged, a name matching `nvr_name_pattern` is an NVR; else, untagged, an IP in `camera_ranges` is a camera. A node tagged as anything else (e.g. `SWITCH`) is never a camera. Institute: the `institute_property` (`InstituteName`) or the matching `[[subnets]]` institute.
- **Down since:** the last SolarWinds node-down event (`Orion.Events`, EventType 1, UTC). SolarWinds keeps about 30 days of events; older outages show as "before <oldest event>" and count as escalated. If events cannot be read, the first read that saw it down is used ("at least since"). Kept in `data\state\cctv-state.json`.
- **Not in SolarWinds:** with `ipam_ranges` set, live IPs that SolarWinds IPAM sees in those CCTV subnets but that are not SolarWinds nodes (checked at most hourly).
- **Certificate:** SWIS usually has a self-signed certificate. Its SHA-256 fingerprint is pinned: `solarwinds_cert_sha256`, or else the one seen first (`data\state\solarwinds-cert.json`; delete it after SolarWinds renews its certificate).
- **Setup:** fill `[cctv]` in `config.toml` (see `config.example.toml`), then as the service account `C:\netmon\netmon set-password solarwinds`, `C:\netmon\netmon cctv` (test read), and restart `netmon-api`. The collector host needs TCP 17774 to the SolarWinds main polling engine.
**Either mode:** no check that video was recorded: ping and SolarWinds only show that a device answers. Emails exist only in sheet mode.

## Access control

The page sends the hub's shared Google access token as `Authorization: Bearer`. The API:

1. checks it with Google `tokeninfo` (sent in the POST body). It must be issued to the hub's OAuth client ID, have a verified email, an allowed domain, and time left;
2. requires the email to be in `server.network_team` for the network routes (`/api/v1/*`), or in `cctv.viewers` for the CCTV routes (`/api/v1/cctv/me`, `status`, `snapshot`, `POST refresh`). The two lists are separate: one gives no access to the other dashboard;
3. caches a successful check for at most 5 minutes, keyed by a SHA-256 hash of the token. Tokens are never logged or written to disk;
4. answers CORS only for `server.allowed_origins` (the hub origin), including the Private Network Access preflight.

The token also carries the hub's read-only Sheets scope, because the hub shares one session. The API only forwards it to Google `tokeninfo`. `audit.log` records every Run now, switch refresh and CCTV read request with the requester's email.

## Data handling

`data_dir` holds switch names, IPs, serials and topology, and camera/NVR names and IPs: treat it as internal. Snapshots, reports and captures are pruned after `keep_days`. Lost links stay flagged until they age out. Back up `config.toml`; `data_dir` can be rebuilt by running the collector.

## Development

```sh
cd collector
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
python -m unittest discover -s tests -t .
python -m netmon --config <test-config.toml> serve --dev-http        # plain HTTP on 127.0.0.1 only
```

Tests use only synthetic fixtures (`tests/fixtures.py`). Never paste real captures into the repository; it is public.
