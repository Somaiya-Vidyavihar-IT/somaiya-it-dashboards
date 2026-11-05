# netmon: campus network collector and API

Backend for the hub's **Network Monitoring** dashboard (`site/network-monitoring/`). It runs on a campus Windows host, never on GitHub Pages.

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
10. **First run and scheduled tasks.**
    ```powershell
    C:\netmon\netmon run
    .\install-tasks.ps1 -ServiceAccount '<VM_NAME>\svc-netmon'
    Start-ScheduledTask -TaskName netmon-api
    ```
11. **Hub.** Set `netmonApiBase` in `site/config.js` to `https://netmon.somaiya.edu:8443` and publish (`dev` first).

### Credential sets

Switches can use several read-only accounts. `switch_credential_sets` lists them in the order to try. For each switch the collector tries the set that worked last time first, then the others, and remembers the winner in `data\state\credentials.json` (set names only). A switch where every set fails is shown as "SSH authentication failed with all N credential sets". A new switch costs at most N−1 failed logins, once.

## Access control

The page sends the hub's shared Google access token as `Authorization: Bearer`. The API:

1. checks it with Google `tokeninfo` (sent in the POST body). It must be issued to the hub's OAuth client ID, have a verified email, an allowed domain, and time left;
2. requires the email to be in `server.network_team`;
3. caches a successful check for at most 5 minutes, keyed by a SHA-256 hash of the token. Tokens are never logged or written to disk;
4. answers CORS only for `server.allowed_origins` (the hub origin), including the Private Network Access preflight.

The token also carries the hub's read-only Sheets scope, because the hub shares one session. The API only forwards it to Google `tokeninfo`. `audit.log` records every Run now request with the requester's email.

## Data handling

`data_dir` holds switch names, IPs, serials and topology: treat it as internal. Snapshots, reports and captures are pruned after `keep_days`. Lost links stay flagged until they age out. Back up `config.toml`; `data_dir` can be rebuilt by running the collector.

## Development

```sh
cd collector
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
python -m unittest discover -s tests -t .
python -m netmon --config <test-config.toml> serve --dev-http        # plain HTTP on 127.0.0.1 only
```

Tests use only synthetic fixtures (`tests/fixtures.py`). Never paste real captures into the repository; it is public.
