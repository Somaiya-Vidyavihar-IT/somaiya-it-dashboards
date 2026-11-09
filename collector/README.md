# netmon: campus network collector and API

Backend for the hub's **Network Monitoring** dashboard (`site/network-monitoring/`) and **CCTV Monitoring** dashboard (`site/cctv-monitoring/`, see [CCTV dashboard](#cctv-dashboard-solarwinds)). It runs on a campus Windows host, never on GitHub Pages.

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

## CCTV dashboard (SolarWinds)

The CCTV page shows cameras and NVRs up or down, the NVR inventory, and everything down longer than `cctv.escalate_hours` (48). SolarWinds already polls these devices; `netmon/cctv.py` only reads its results through the SolarWinds Information Service (`https://<host>:17774/SolarWinds/InformationService/v3/Json/Query`) with a **read-only** SolarWinds account. Every statement is checked to be a SWQL `SELECT` before it is sent.

- **Where it runs:** inside `netmon-api` as a background thread (no extra scheduled task). It reads every `poll_minutes`, and **Read SolarWinds now** on the page wakes it early (one read at a time; requests are written to `audit.log`). `C:\netmon\netmon cctv` does one read by hand and prints a summary.
- **Which nodes are cameras/NVRs** (SolarWinds has no built-in camera type): the node custom property `type_property` (`Device_Type`) in `nvr_types` / `camera_types`; else, untagged, a name matching `nvr_name_pattern` is an NVR; else, untagged, an IP in `camera_ranges` is a camera. A node tagged as anything else (e.g. `SWITCH`) is never a camera. Institute: the `institute_property` (`InstituteName`) or the matching `[[subnets]]` institute.
- **Down since:** the last SolarWinds node-down event (`Orion.Events`, EventType 1, UTC). SolarWinds keeps about 30 days of events; older outages show as "before <oldest event>" and count as escalated. If events cannot be read, the first read that saw it down is used ("at least since"). Kept in `data\state\cctv-state.json`.
- **Not in SolarWinds:** with `ipam_ranges` set, live IPs that SolarWinds IPAM sees in those CCTV subnets but that are not SolarWinds nodes (checked at most hourly).
- **Certificate:** SWIS usually has a self-signed certificate. Its SHA-256 fingerprint is pinned: `solarwinds_cert_sha256`, or else the one seen first (`data\state\solarwinds-cert.json`; delete it after SolarWinds renews its certificate).
- **Outputs:** `data\cctv.json` (the page snapshot) and `data\cctv-status.json`.
- **Setup:** fill `[cctv]` in `config.toml` (see `config.example.toml`), then as the service account `C:\netmon\netmon set-password solarwinds`, `C:\netmon\netmon cctv` (test read), and restart `netmon-api`. The collector host needs TCP 17774 to the SolarWinds main polling engine.
- **No alerts or emails yet** (planned for phase 2), and no check that video was recorded: SolarWinds only knows ping/SNMP.

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
