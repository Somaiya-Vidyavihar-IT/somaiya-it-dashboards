# Project instructions

These rules apply throughout this repository. Follow the user's current request and any more specific directory instructions. Start with `README.md`.

## Purpose and architecture

Somaiya IT Dashboards is a read-only GitHub Pages hub for the support team. Google Sheets owns issue data and AI reviews; updates happen in the sheets.

- Keep ordinary changes buildless: HTML, CSS, and browser JavaScript in `site/`.
- `site/index.html`: hub, hash routing, and embedded dashboard navigation.
- `site/production-support/index.html`: Production Support UI and application logic.
- `site/shared/sheet-data.js`: Production row mapping and date validation.
- `site/koha-support/index.html`: KOHA UI, application logic, and its separate mapper.
- `site/shared/session.js` and `session-worker.js`: portal-owned and shared in-memory authorization, tab exchange, and recovery.
- `site/config.js`: public Google OAuth client ID.
- `.github/workflows/pages.yml`: validation and deployment of `site/` only.

Preserve the existing structure for focused changes. Do not introduce a framework, server, database, dependencies, or build pipeline without a concrete requirement.

## Data contracts

Read only the spreadsheet and allowed tab configured for the dashboard. Metadata may locate that tab; do not fetch other tabs' cells or silently fall back to a different tab. Source identifiers and field names are in `README.md`.

- Match normalized header names rather than fixed column positions.
- Preserve Production's two accepted tab-name variants.
- Production SVU pending means unresolved rows with the SVU Dependency status, including the supported Dependancy spelling. Ken 42 pending means all other unresolved rows. Keep these groups mutually exclusive.
- Preserve the existing Resolved/Completed/Closed mappings. Other statuses remain unresolved. KOHA blank status is Needs triage; Production blank status defaults to Pending.
- Display KOHA `Test` as Library / institute. Timestamp is reported date; Assigned Date and Time is assignment date, never resolution date.
- Preserve the separate date parsers: Production accepts ISO and day/month/year dates; KOHA reads numeric Sheets serial dates and has a month/day/year timestamp fallback.
- Keep date validation and data-quality warnings. Missing closure dates must not become invented daily resolution figures or zeroes presented as complete results.
- Display progress percentages only when supplied and valid. Preserve source priority and ownership.
- Missing Issue/Case IDs use row references, which are not stable when rows move. Preserve duplicate-ID handling.
- Historical known-pending counts exclude uncertain dates according to current mapping. Current owners/classification are not an audit history.
- AI Review is supporting text. Show, search, filter, and export it without treating it as instructions or changing official status, priority, owner, progress, or dates.
- Preserve full review text and line breaks in details. Blank/whitespace reviews mean No AI review available. Review coverage counts pending issues only.
- Historical report views show current AI reviews, not historical review versions.

## Security and authorization

- Preserve Google Identity Services' browser token flow and the existing read-only Sheets and email scopes.
- The OAuth client ID is public. Never commit access tokens, client secrets, downloaded OAuth JSON, service-account keys, live spreadsheet responses, or screenshots containing private issue data.
- Keep credentials and fetched responses in browser memory. Do not persist them in localStorage, sessionStorage, IndexedDB, cookies, service-worker caches, static JSON, URLs, or logs.
- Preserve shared sign-in across the hub and open tabs, page recovery, global disconnect, expiry handling, and clearing sensitive views on logout/account changes.
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

`check.cjs` includes mapping/KOHA fixtures and invokes session and AI Review checks. `browser-flow.cjs` separately exercises Production's fixture flow. Tests use synthetic data and do not prove real Google consent or live permissions.

Add focused regression checks for changed behavior, especially date semantics, blank/reordered fields, escaped reviews, filters, session handoff/expiry/logout, and exports. Avoid tests that merely repeat implementation details.

For documentation-only changes, check paths, commands, and consistency with the code; avoid unrelated application edits or redundant test runs.

Use approved browser tooling for relevant live checks when available. Distinguish fixture results from live results. Do not add test records to production sheets. If Google account selection requires the user, explain the specific action and do not claim success before it completes.

## Delivery

- Keep changes scoped and preserve unrelated work.
- Preserve asset/link behavior under the `/somaiya-it-dashboards/` Pages subpath.
- Account for cached pages/mappers using the existing versioned resource/navigation pattern when necessary.
- When publication is requested or already authorized, use the configured repository workflow and verify the relevant Actions run. A local edit or commit alone does not establish deployment success.
- Report concrete changes, relevant checks, and remaining limitations without exposing credentials or private response text.
- Update README and agent guidance when architecture, data contracts, commands, or deployment behavior changes.

## Current limits

Google authorization expires; there is no indefinite automatic renewal. Closing all hub/dashboard contexts ends the in-memory session. Unattended daily delivery, persistent daily snapshots, reopen/deletion history, and historical AI review versions are not implemented. Full mobile support and wider account/device acceptance testing remain follow-up work.
