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

Run these as an administrator unless noted. `<…>` values are placeholders.

1. **Service account.** Create a local or domain account (e.g. `svc-netmon`) with no interactive logon rights beyond "Log on as a batch job". Everything below runs as this account.
2. **Python 3.13 (64-bit)** from python.org, installed for all users. Then:
   ```powershell
   mkdir C:\netmon\app, C:\netmon\tls
   # copy this repository's collector\ folder to C:\netmon\app\collector
   py -3.13 -m venv C:\netmon\venv
   C:\netmon\venv\Scripts\pip install -r C:\netmon\app\collector\requirements.txt
   ```
3. **Config.** Copy `config.example.toml` to `C:\netmon\config.toml` and fill in the subnets (each tagged with its institute), WLC IPs, `switch_username` / `wlc_username`, the `network_team` emails and the certificate paths. Keep the file out of git.
4. **Passwords** go into Windows Credential Manager. Run these as the service account (`runas /user:<SERVICE_ACCOUNT> powershell`):
   ```powershell
   cd C:\netmon\app\collector
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml set-password switch
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml set-password wlc
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml check-config
   ```
5. **Certificate.** Convert the wildcard `.pfx` into the PEM files named in the config, then restrict the key to the service account:
   ```powershell
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml import-pfx <PATH_TO_WILDCARD>.pfx
   icacls C:\netmon\tls\key.pem /inheritance:r /grant:r "<SERVICE_ACCOUNT>:R" "Administrators:F"
   ```
6. **DNS:** an internal record such as `netmon.<wildcard-domain>` pointing to this host. The name must be covered by the wildcard certificate.
7. **Firewall.** Allow the API port only from IT staff subnets:
   ```powershell
   New-NetFirewallRule -DisplayName "netmon API" -Direction Inbound -Protocol TCP -LocalPort 8443 -RemoteAddress <IT_STAFF_SUBNETS> -Action Allow
   ```
8. **Validate parsers on real gear before go-live.** For one device of each model, run a capture. It is read-only and saves raw output under `data\captures`, which must stay out of git:
   ```powershell
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml capture --ip <SWITCH_IP>
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml capture --wlc <WLC_IP>
   ```
   Check the parsed summary it prints: hostname, model, serial, neighbours, PoE and CPU. The SG/CBS/C1300 CDP, PoE and CPU parsers and the Aruba `show ap active` / `show ap lldp neighbors` parsers are tolerant custom parsers. Confirm them first.
9. **First run and tasks.**
   ```powershell
   C:\netmon\venv\Scripts\python -m netmon --config C:\netmon\config.toml run
   .\deploy\install-tasks.ps1 -ServiceAccount '<SERVICE_ACCOUNT>'
   Start-ScheduledTask -TaskName netmon-api
   ```
10. **Hub.** Set `netmonApiBase` in `site/config.js` to `https://netmon.<wildcard-domain>:8443` and publish the hub as usual (`dev` first).

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
