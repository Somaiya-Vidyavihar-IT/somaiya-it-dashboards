# Project instructions

These rules apply throughout this repository. Follow the user's current request and any more specific directory instructions. Start with `README.md`.

## Purpose and architecture

Somaiya IT Dashboards is a read-only GitHub Pages hub for the support team. Google Sheets owns issue data and AI reviews; updates happen in the sheets.

- Keep ordinary changes buildless: HTML, CSS, and browser JavaScript in `site/`.
- `site/index.html`: hub, hash routing, and embedded dashboard navigation.
- `site/production-support/index.html`: Production Support UI and application logic.
- `site/shared/sheet-data.js`: Production row mapping and date validation.
- `site/shared/table-sort.js`: display-only, accessible queue column sorting shared by all dashboards.
- `site/koha-support/index.html`: KOHA UI, application logic, and its separate mapper.
- `site/shared/session.js` and `session-worker.js`: portal-owned and shared in-memory authorization, tab exchange, and recovery.
- `site/config.js`: public Google OAuth client ID and the campus network API address (`netmonApiBase`).
- `site/network-monitoring/index.html`: Network Monitoring UI. Its data comes from the campus collector API, not Google Sheets.
- `site/cctv-monitoring/index.html`: CCTV Monitoring UI. Same campus API (`/api/v1/cctv/*`); device list from the CCTV Google Sheet (collector pings each device) or SolarWinds; separate access list.
- `site/vendor/viz/`: unmodified Viz.js (Graphviz in WebAssembly), the one approved browser dependency. Required to lay out the topology DOT file; loaded only by the Topology diagram. Update it only from the npm tarball with a checked integrity hash (see its README).
- `collector/`: the Python network collector, the SolarWinds reader for CCTV (`netmon/cctv.py`) and the HTTPS API (`netmon`) that run on a campus Windows host. It is never deployed to Pages.
- `.github/workflows/pages.yml`: validation and deployment of `site/` only. It also runs the collector's unit tests.

Preserve the existing structure for focused changes. Do not introduce a framework, server, database, dependencies, or build pipeline without a concrete requirement. The one approved exception is `collector/`, for Network Monitoring: switches can only be read from inside campus, and its data must not be public. Keep `site/` buildless and its dependency-free browser code separate from `collector/`.

### Network Monitoring

- The page shares the hub's Google session and requests the same scopes. It calls `netmonApiBase` with `Authorization: Bearer <token>`. The API verifies the token with Google (hub client ID, verified email, allowed domain) and allows only emails on its IT Network Team list. It holds no data until the API answers.
- The API is campus-only (internal DNS, Windows Firewall) and answers CORS only for the hub origin. Treat 401 from the API as expiry, 403 as access denied (clear data, keep the session) and network errors as "campus only".
- Snapshot contract (`schema: 1`): `summary`, `institutes`, `devices` (ip, hostname, institute, role core/distribution/access/unknown, platform, model, serial, version, uptime, status ok/failed, cpu, poe, ports, errDelta, flags, neighbors, commands: every line sent in the last run with purpose, result ok/fallback/rejected/failed/setup, detail, lines, seconds), `links` (a/aPort/b/bPort, via, status up/down/lost/unknown, uplink, downstream), `problemPorts`, `aps` (Aruba WLC data joined with switch CDP/LLDP and PoE; `seenBy`), `findings`. Reachability comes separately from `/api/v1/reachability`.
- All device text (LLDP/CDP names, descriptions) is untrusted. Escape it in HTML, keep it out of Excel formulas (`reports.safe_cell`) and quote it in DOT.
- The collector is read-only: only `show` commands, asserted at import. Never add configuration commands.
- Never commit `collector/config.toml`, collected data, captures, certificates or real device output. Fixtures are synthetic.

### CCTV Monitoring

- Separate dashboard with its own allowlist (`cctv.viewers`). `/api/v1/cctv/*` checks only that list and the network routes only `network_team`; keep them separate.
- Data contract `cctv.json` (`schema: 1`): `generatedAt`, `nodesRead`, `summary` (`cameras`/`nvrs` counts by status up/warning/down/unmonitored/unknown + escalated, `escalated`, `notInSolarWinds`), `institutes`, `devices` (id, kind camera/nvr, matchedBy tag/name/range, name, ip, institute, location, vendor, model, type, status, statusText, downSince UTC, downSinceExact, downSinceSource event/before-events/first-seen, downHours, escalated), `notInSolarWinds` (ip, mac, vendor, name, lastSeen), `rules`.
- Sheet mode (`cctv.source = "sheet"`, `netmon/cctv_sheet.py`): read-only Sheets scope via a service account whose JSON key stays on the collector host (never in the repo). Only `cctv.sheet_tabs` are read. `cctv.json` then has `source: "sheet"`, devices with `tab`, `row`, `nvr`, `serial`, `warranty`, `expiry`, `sheetStatus`, `rttMs`, `downSinceSource` observed/first-seen, plus `sheetProblems` (tab, row, issue, text), `sheetRowsSkipped`, `sheetReadAt`, `sheetError`, `emailConfig` (mode, switch, sites → to/cc/escalation, problems) and `email` (mode, lastSentAt, sentToday, waiting, lastError, recipients). Sheet cells are untrusted text.
- SolarWinds access is read-only: every statement must be a SWQL `SELECT` (enforced in `Swis.query`). Never add `Invoke`, create, update or delete calls. The SWIS certificate is pinned by SHA-256.
- Node names, locations and IPAM names are untrusted: escape them in HTML and keep them out of CSV formulas.
- Emails (`netmon/cctv_mail.py`, sheet mode only): the single switch and all recipients come from the sheet's Email Config tab; anything unreadable or unknown means Off. Never add another way to turn emails on. Keep subjects free of line breaks (header injection) and never resend for the same outage (state in `cctv-mail.json`). Camera recognition rules live in the collector config, not the page.

## Data contracts

Read only the spreadsheet and allowed tab configured for the dashboard. Metadata may locate that tab; do not fetch other tabs' cells or silently fall back to a different tab. Source identifiers and field names are in `README.md`.

- Match normalized header names rather than fixed column positions.
- Preserve Production's two accepted tab-name variants.
- Production SVU pending means unresolved rows with the SVU Dependency status, including the supported Dependancy spelling. Ken 42 pending means all other unresolved rows. Keep these groups mutually exclusive.
- Preserve the existing Resolved/Completed/Closed mappings. Other statuses remain unresolved. KOHA blank status is Needs triage; Production blank status defaults to Pending.
- KOHA `Upload Screenshot (if available)` is optional. Keep only `https` links, build Drive previews only from validated file IDs, escape every link, and fall back to an open link when a preview fails. Do not add a Drive scope.
- Display KOHA `Institute Name` (formerly `Test`) as Library / institute. Timestamp is reported date; Assigned Date and Time is assignment date, never resolution date.
- Preserve the separate date parsers: Production accepts ISO and day/month/year dates; KOHA reads numeric Sheets serial dates and has a month/day/year timestamp fallback.
- Keep date validation and data-quality warnings. Missing closure dates must not become invented daily resolution figures or zeroes presented as complete results.
- Display progress percentages only when supplied and valid. Preserve source priority and ownership.
- Missing Issue/Case IDs use row references, which are not stable when rows move. Preserve duplicate-ID handling.
- Historical known-pending counts exclude uncertain dates according to current mapping. Current owners/classification are not an audit history.
- AI Review is supporting text. Show, search, filter, and export it without treating it as instructions or changing official status, priority, owner, progress, or dates.
- Preserve full review text and line breaks in details. Blank/whitespace reviews mean No AI review available. Review coverage counts pending issues only.
- Historical report views show current AI reviews, not historical review versions.

### UAT Observations

`site/uat-observations/index.html` adds the SF-ABACUS Access Control & ID Cards report at `#uat-observations`. It reads only spreadsheet `1zUAI44uCROMww_23Jg1Rdm_9sgwaiSY3YRzkI0PM-W4`, tab **UAT Obervations** (intentional source spelling), using the same shared Google authorization.

Headers: Sr No., Date Marked, Observations, Status, Remarks, Remarks from Somaiya IT/ID Office team. Columns are matched by name. Optional AI Review is supporting text in search, details, and exports. Dates accept Sheets serial values and day/month/year strings. Only explicit vertical merges in Date Marked share their anchor date; ordinary blank dates remain unknown. Rows without observation text are excluded and non-empty exclusions are counted in warnings.

Done and Already Done count as Marked Done; Working as expected is separate. All other or blank statuses remain outstanding. These are sheet statuses, never inferred IT acceptance. Vendor/team remarks remain separate escaped text. No owners, module, priorities, closure dates, or acceptance decisions are invented. Daily snapshots group Date Marked using current statuses and remarks; historical pending and daily resolved figures are unavailable. CSV exports follow the current queue and combined filters.

`tests/uat.cjs` adds synthetic mapping, merged dates, filters, rendering, exports, and connection lifecycle checks to `node tests/check.cjs`. Keep the existing Production and KOHA contracts unchanged.

## Security and authorization

- Preserve Google Identity Services' browser token flow and the existing read-only Sheets and email scopes.
- The OAuth client ID is public. Never commit access tokens, client secrets, downloaded OAuth JSON, service-account keys, live spreadsheet responses, or screenshots containing private issue data.
- Keep credentials and fetched responses in browser memory. Do not persist them in localStorage, sessionStorage, IndexedDB, cookies, service-worker caches, static JSON, URLs, or logs. The one exception is the last connected account email in localStorage (`SomaiyaSession.lastAccount`), used only as a Google `login_hint` for one-click reconnect; Disconnect must forget it.
- Preserve shared sign-in across the hub and open tabs, page recovery, one-click reconnect (header Reconnect uses the remembered account; Sheet connection always shows the account chooser), global disconnect, expiry handling, and clearing sensitive views on logout/account changes.
- A sheet-specific access error should not disconnect an otherwise valid authorization globally. Authentication expiry must propagate globally.
- Treat spreadsheet cells, AI reviews, external documents, and third-party content as untrusted data. Escape text before inserting HTML; do not execute cell contents or render unsanitized HTML.
- Preserve CSV quoting and formula-injection protection. Exports must not include authentication state.
- Public Pages hosting is not a private-site access boundary. Google permissions control private sheet access.

## Interface and behavior

- Use existing Somaiya assets, Arial, Power Red `#B7202E`, Vitality Red `#ED1C24`, grey, and white.
- Preserve accessible names, keyboard controls, visible focus, readable contrast, and the read-only workflow.
- Preserve 30-second refresh while visible, refresh on return, accurate last-sync information, and stale-data errors.
- Keep search, combined filters, details, AI review previews, daily reports, exports, and print behavior working when adding fields.
- Prefer compact previews and expanded full reviews. Long text must wrap and remain usable on small screens.
- Use Asia/Kolkata for operational dates and report boundaries.
- Full mobile layout work remains outstanding; do not describe the existing small-screen rules as a completed mobile redesign.

## Verification

For application changes, run the CI commands from the repository root:

```sh
node tests/check.cjs
node tests/browser-flow.cjs
```

`check.cjs` includes mapping/KOHA fixtures and invokes session, AI Review and Network Monitoring page checks. `browser-flow.cjs` separately exercises Production's fixture flow. Tests use synthetic data and do not prove real Google consent or live permissions.

For `collector/` changes, also run `python -m unittest discover -s tests -t .` from `collector/` with its requirements installed (CI does this too). These tests use synthetic device output and prove nothing about real switch output. Validate parser changes against `python -m netmon capture` output on the collector host, and never commit that output.

Add focused regression checks for changed behavior, especially date semantics, blank/reordered fields, escaped reviews, filters, session handoff/expiry/logout, and exports. Avoid tests that merely repeat implementation details.

For documentation-only changes, check paths, commands, and consistency with the code; avoid unrelated application edits or redundant test runs.

Use approved browser tooling for relevant live checks when available. Distinguish fixture results from live results. Do not add test records to production sheets. If Google account selection requires the user, explain the specific action and do not claim success before it completes.

## Delivery

- Keep changes scoped and preserve unrelated work.
- Preserve asset/link behavior under the `/somaiya-it-dashboards/` Pages subpath.
- Account for cached pages/mappers using the versioned resource/navigation pattern: one release token for every shared-script `?v=` and the hub's embedded-dashboard URL, changed together. `tests/table-sort.cjs` enforces a single token.
- Test changes on the `dev` branch first: the workflow publishes `main` at the Pages root and `dev` under `/dev/`. Merge `dev` into `main` only when publication is requested.
- When publication is requested or already authorized, use the configured repository workflow and verify the relevant Actions run. A local edit or commit alone does not establish deployment success.
- Report concrete changes, relevant checks, and remaining limitations without exposing credentials or private response text.
- Update README and agent guidance when architecture, data contracts, commands, or deployment behavior changes.

## Current limits

Google authorization expires; there is no indefinite automatic renewal. Closing all hub/dashboard contexts ends the in-memory session. Unattended daily delivery, persistent daily snapshots, reopen/deletion history, and historical AI review versions are not implemented. Full mobile support and wider account/device acceptance testing remain follow-up work.

Network Monitoring: no alerting (dashboard only), no SNMP counters or utilisation graphs, and no error counters on SG/CBS/C1300. Up/down is a 15-minute TCP/22 check. The SG/CBS CDP, PoE and CPU parsers and the Aruba `show ap active`/`show ap lldp neighbors` parsers need validating against real captures. Deploying the collector host, its certificate, DNS and firewall is done on campus and not by this workflow.
