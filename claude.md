# Claude project notes

Read `AGENTS.md` for shared repository rules and `README.md` for usage, setup, source sheets, and deployment. Keep shared rules there rather than creating conflicting copies here. Follow the user's current request and more specific directory instructions.

## Working context

This is Somaiya Vidyavihar IT's buildless GitHub Pages hub. Production Support, KOHA Library Support, and UAT Observations read selected private Google Sheets tabs after Google authorization. Staff update the sheets; the dashboard does not edit them or generate AI reviews.

Start with the relevant files:

- Hub: `site/index.html`.
- Production UI: `site/production-support/index.html`.
- Production mapper: `site/shared/sheet-data.js`.
- KOHA UI and mapper: `site/koha-support/index.html`.
- UAT Observations UI and mapper: `site/uat-observations/index.html` (contract in `AGENTS.md` → Data contracts → UAT Observations).
- Queue sorting: `site/shared/table-sort.js`.
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

Push work to `dev` for the `/dev/` preview; merging into `main` publishes live. When deployment is authorized, verify the Actions result and affected deployed behavior. State which checks were fixtures and which were live. Do not claim permanent Google login, unattended reports, historical review storage, or completed mobile support.
