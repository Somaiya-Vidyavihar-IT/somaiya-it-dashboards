# Somaiya IT Dashboards

A read-only support dashboard hub for Somaiya Vidyavihar IT, hosted on GitHub Pages. Issue updates and AI reviews are maintained in Google Sheets and read directly after Google authorization.

- [Live dashboard hub](https://somaiya-vidyavihar-it.github.io/somaiya-it-dashboards/)
- [Repository](https://github.com/Somaiya-Vidyavihar-IT/somaiya-it-dashboards)
- [Project/agent rules](AGENTS.md)
- [Claude working notes](claude.md)

The site uses static HTML, CSS, and browser JavaScript. There is no application server, database, dependency installation, or build step.

## Features

### Production Support

Tracks pending/resolved issues, module, priority, owner, supplied progress, reported dates, target dates, and recorded closure dates. Includes critical/urgent issues, overdue targets, workload, queue health, searchable details, and daily reports.

- **SVU Dependency pending:** unresolved rows with the SVU Dependency status, including the supported Dependancy spelling.
- **Ken 42 pending:** all other unresolved rows, including Pending, In Progress, and To Do. This grouping follows status; it does not read a separate dependency field.

### KOHA Library Support

Tracks library software issues by library/institute, category, request type, status, assigned team, reported date, and assignment date. Includes recurring issues, unassigned pending issues, queue aging, workload, and daily reports.

### AI Review in both dashboards

The optional **AI Review** column supplies supporting analysis:

- Expandable queue previews and the full review in issue details.
- Review text search, review-available/no-review filters, and pending-review coverage.
- A shortcut to pending issues without a review.
- Current reviews in daily reports, CSV exports, text digests, and the print view.

Blank or whitespace-only cells display **No AI review available**. Reviews retain line breaks and are escaped as text. They do not change status, priority, ownership, progress, or dates. Historical report views show current reviews; past review versions are not stored. The dashboard does not generate or write reviews.

## Using the hub

1. Open a dashboard and select **Connect with Google**.
2. Choose an authorized organization account that can read its source sheet.
3. Use **All dashboards** to switch dashboards with the shared connection.
4. Use filters, details, and **Daily reports**. Make updates in Google Sheets.
5. **Disconnect all**, or a dashboard's disconnect action, clears the shared authorization and connected views.

Refresh runs every 30 seconds while visible and on return to the page. Last successful sync and stale-data errors are shown. Network/browser delays can affect timing.

## Source sheets

Only the configured tab's cell values are fetched. Spreadsheet metadata locates the tab. Columns are matched by normalized names rather than fixed positions.

| Dashboard | Spreadsheet | Allowed tab |
| --- | --- | --- |
| Production Support | [KJS Master Tracker](https://docs.google.com/spreadsheets/d/1Slj1cNkj4ZKE1-lXc1Ci-wwaibIEu0-lcRNnxkb7keU/edit) | `All modules Bugs, Issues`; also accepts `All Module Bugs, Issues` |
| KOHA Library Support | [KOHA responses](https://docs.google.com/spreadsheets/d/1zNrZWjO8hSxGbHCbTuf7K7OEWu85V8Jg9DOse7r7HtA/edit?gid=122111389) | `Form Responses 1` |

### Production fields

Primary fields are Description, Module, Status, Type, Priority, Assigned To, ETA, and Date Reported. Optional fields include Issue ID, Resolution Date, Progress, Blocker, Notes, Last Updated, and AI Review. Aliases are defined in `site/shared/sheet-data.js`.

Completed, Resolved, and Closed map to Resolved. Other statuses remain unresolved; blank defaults to Pending. Progress is shown only when supplied and valid. Dates accept ISO `YYYY-MM-DD`, `DD-MM-YYYY`, and `DD/MM/YYYY`; invalid, future reported/resolved dates, and inconsistent closure dates are flagged.

### KOHA fields

| Sheet field | Dashboard use |
| --- | --- |
| Timestamp | Reported date |
| Case ID | Identifier; row reference when missing |
| Test | Library / institute |
| Librarian/Staff Name, Email ID, Contact Number | Reporter details |
| Issue Category | Category |
| Is it a repetitive issue? | Recurring indicator |
| Brief Description of the Issue | Description |
| Type of Request | Request type |
| Assigned To | Assigned team / owner |
| Assigned Date and Time | Assignment date, not resolution date |
| Status | Resolved/Completed/Closed map to Resolved; blank becomes Needs triage |
| Ourlib Comment, Remark | Support notes |
| Resolution Date | Optional recorded closure date |
| AI Review | Optional supporting analysis |

KOHA reads Google Sheets numeric serial dates to avoid display-format ambiguity. Its formatted-date fallback accepts ISO and the source's month/day/year timestamps. It does not use Production's day/month/year interpretation.

### Report accuracy

- Date boundaries use **Asia/Kolkata**.
- Populate **Resolution Date** to measure daily closures. Assignment dates are never substituted for closure dates.
- Missing closure dates make daily resolution counts unavailable or incomplete; warnings explain the limitation.
- Historical known-pending totals exclude uncertain timing and are not a complete audit trail.
- Populate stable **Issue ID / Case ID** values. Row references change when rows move; duplicate IDs are flagged in Production.
- Owners, classification, and AI reviews reflect the current sheet. Reopen/deletion history and past review versions require an audit log or snapshots.

## Google authorization and security

Google Identity Services' browser token flow requests:

```text
https://www.googleapis.com/auth/spreadsheets.readonly
https://www.googleapis.com/auth/userinfo.email
```

`site/config.js` holds the public OAuth client ID. This static version uses no client secret, service-account key, backend callback, or refresh-token store.

Authorization stays in shared browser memory. Embedded dashboards use the portal's session owner; SharedWorker and BroadcastChannel support other active pages and recovery. Reload can recover a connection while another connected hub/tab remains open. Closing all hub/dashboard contexts ends the in-memory session. Google authorization expiry requires reconnecting.

Tokens and fetched responses are not persisted in browser storage, source control, URLs, logs, or static JSON. User-requested report exports contain report data, not credentials. Never commit tokens, credential JSON, private keys, real spreadsheet responses, or screenshots containing private issue data.

The repository and Pages shell are public. Private data is read directly from Google after authorization; sheet permissions and Workspace policies govern access. The public shell does not provide private-site authentication.

### Google Cloud configuration

The existing project is **KJS Master Tracker** (`kjs-master-tracker`). For a replacement client or another origin:

1. Enable **Google Sheets API**.
2. Configure approved organization branding/contact information and the **Internal** audience for intended Workspace staff.
3. Configure the two scopes above.
4. Create an OAuth client of type **Web application**.
5. Add `https://somaiya-vidyavihar-it.github.io` to **Authorized JavaScript origins**, without the repository path. Add `http://127.0.0.1:8765` if authorized local Google testing is needed.
6. Put only the public client ID in `site/config.js`; keep downloaded credentials and secrets out of the project.
7. Ensure each intended account can read the relevant spreadsheet and is allowed by Workspace policies.

This popup flow needs no backend redirect URI. Future custom domains require the corresponding OAuth origin configuration.

## Repository structure

```text
AGENTS.md                         # Shared project/agent working rules
claude.md                         # Claude working notes
README.md                         # Usage, setup, contracts, and limits
site/
  index.html                      # Hub, hash routing, embedded navigation
  config.js                       # Public OAuth client ID
  assets/                         # Somaiya branding
  shared/
    sheet-data.js                 # Production mapping and date validation
    session.js                    # Shared session client and recovery
    session-worker.js             # Shared session ownership
  production-support/index.html   # Production UI and logic
  koha-support/index.html         # KOHA UI, logic, and mapper
tests/
  check.cjs                       # Main checks, including KOHA fixtures
  browser-flow.cjs                 # Production fixture flow
  session.cjs                     # Session handoff, recovery, logout, expiry
  ai-review.cjs                   # Review mapping, escaping, filters, exports
.github/workflows/pages.yml       # Checks and Pages deployment
```

Keep future dashboards in `site/<name>/` with a hub entry. Preserve the Pages subpath, shared session contract, and versioned resource/navigation pattern when addressing stale cached code. Read agent guidance explicitly when using an assistant; `claude.md` is supplied with the requested lowercase filename.

## Local development and checks

Serve the static directory from the repository root:

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory site
```

Open [the local hub](http://127.0.0.1:8765/). Python is needed only for this example server; another static server works. Live local Google authorization requires the origin in the OAuth client.

Run the CI application checks:

```sh
node tests/check.cjs
node tests/browser-flow.cjs
```

These use Node.js built-ins and synthetic fixtures; no dependency installation is required. `check.cjs` also invokes the session and AI Review suites. They cover mapping/date handling, authorization behavior, review safety/filtering, and export generation. They do not perform real Google consent or prove sheet permissions.

For user-visible changes, verify relevant live flows: connection, switching, another tab, reload recovery, refresh, filters/search, details, and affected reports. Never write test records into production sheets. For documentation-only changes, verify accuracy and paths against the repository.

## Publishing

The repository uses **main** and the **Deploy Somaiya dashboard hub** Actions workflow.

1. In repository **Settings → Pages**, choose **GitHub Actions** as the source.
2. Commit authorized changes to `main`, or run the workflow manually.
3. The workflow runs both Node commands above, uploads only `site/`, and deploys the Pages artifact.
4. Confirm the relevant Actions run succeeded, then verify affected deployed behavior. A successful commit alone is not deployment confirmation.

Keep documentation and tests outside `site/`; they are excluded from the Pages artifact but remain viewable in the public repository.

Use the existing Somaiya assets, Arial, Power Red `#B7202E`, Vitality Red `#ED1C24`, grey, and white. See the [Somaiya Brand Centre](https://brand.somaiya.edu.in/en/design-centre/).

## Remaining work and limits

- Comprehensive mobile layout work remains outstanding; existing small-screen CSS does not establish full mobile readiness.
- Unattended daily delivery requires a scheduled service and approved authorization design; it is not implemented on Pages.
- Persistent login after every hub/dashboard context closes is not implemented. Google authorization still expires.
- Daily snapshots, reopen/deletion history, and AI review version history are not stored.
- Wider acceptance checks should cover another support account, an account without sheet access, actual long-running expiry, global disconnect/reconnect, mobile devices, and downloaded CSV/PDF output.

## References

- [Google token model](https://developers.google.com/identity/oauth2/web/guides/use-token-model)
- [Google OAuth client setup](https://developers.google.com/identity/oauth2/web/guides/get-google-api-clientid)
- [GitHub Pages](https://docs.github.com/en/pages)
