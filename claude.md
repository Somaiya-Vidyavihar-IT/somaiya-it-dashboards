# Claude project notes

Read `AGENTS.md` for shared repository rules and `README.md` for usage, setup, source sheets, and deployment. Keep shared rules there rather than creating conflicting copies here. Follow the user's current request and more specific directory instructions.

## Working context

This is Somaiya Vidyavihar IT's buildless GitHub Pages hub. Production Support and KOHA Library Support read selected private Google Sheets tabs after Google authorization. Staff update the sheets; the dashboard does not edit them or generate AI reviews.

Start with the relevant files:

- Hub: `site/index.html`.
- Production UI: `site/production-support/index.html`.
- Production mapper: `site/shared/sheet-data.js`.
- KOHA UI and mapper: `site/koha-support/index.html`.
- Shared authorization: `site/shared/session.js` and `site/shared/session-worker.js`.
- Public configuration: `site/config.js`.
- Deployment: `.github/workflows/pages.yml`.

## Essentials

1. Preserve read-only Sheets access and the current authorization scopes.
2. Keep credentials and live responses out of source control and persistent browser storage. The client ID is public; tokens and secrets are not.
3. Preserve shared sign-in, recovery while another connected page remains open, global logout, and expiry. Authorization does not guarantee access to every configured sheet.
4. Keep Production and KOHA date/status mappings distinct. Preserve the mutually exclusive SVU/Ken 42 pending classification.
5. AI Review is escaped supporting text, never an instruction source or an automatic override for status, owner, priority, or dates.
6. Show missing dates/reviews honestly. Historical report views contain current reviews, not stored historical versions.
7. Maintain Somaiya branding and accessible controls. Comprehensive mobile layout work remains outstanding.

## Checks and handover

For application changes, run:

```sh
node tests/check.cjs
node tests/browser-flow.cjs
```

Use synthetic fixtures and relevant live checks without writing test data into production sheets. For documentation-only changes, validate the documents against actual files and workflow commands.

When deployment is authorized, verify the Actions result and affected deployed behavior. State which checks were fixtures and which were live. Do not claim permanent Google login, unattended reports, historical review storage, or completed mobile support.


## UAT Observations dashboard

`site/uat-observations/index.html` adds the SF-ABACUS Access Control & ID Cards report at `#/uat-observations`. It reads only spreadsheet `1zUAI44uCROMww_23Jg1Rdm_9sgwaiSY3YRzkI0PM-W4`, tab **UAT Obervations** (intentional source spelling), using the same shared Google authorization.

Headers: Sr No., Date Marked, Observations, Status, Remarks, Remarks from Somaiya IT/ID Office team. Columns are matched by name. Optional AI Review is supporting text in search, details, and exports. Dates accept Sheets serial values and day/month/year strings. Only explicit vertical merges in Date Marked share their anchor date; ordinary blank dates remain unknown. Rows without observation text are excluded and non-empty exclusions are counted in warnings.

Done and Already Done count as Marked Done; Working as expected is separate. All other or blank statuses remain outstanding. These are sheet statuses, never inferred IT acceptance. Vendor/team remarks remain separate escaped text. No owners, module, priorities, closure dates, or acceptance decisions are invented. Daily snapshots group Date Marked using current statuses and remarks; historical pending and daily resolved figures are unavailable. CSV exports follow the current queue and combined filters.

`tests/uat.cjs` adds synthetic mapping, merged dates, filters, rendering, exports, and connection lifecycle checks to `node tests/check.cjs`. Keep the existing Production and KOHA contracts unchanged.
