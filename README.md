# Somaiya IT Dashboards

A shared GitHub Pages hub for Somaiya Vidyavihar IT. The first dashboard is Production Support, a read-only view of the selected Google Sheets tab.

## Publish from your organization

1. Create a repository named `somaiya-it-dashboards` in `Somaiya-Vidyavihar-IT`, using the visibility your organization approves. Private-repository Pages availability depends on your GitHub plan. A private repository does not automatically make its Pages website private.
2. Upload the contents of this project into the repository root on branch `main`. Include the hidden `.github` folder and `.gitignore`. This package contains no OAuth secrets, tokens, or issue data.
3. Open **Settings → Pages → Build and deployment → Source → GitHub Actions**.
4. Run the **Deploy Somaiya dashboard hub** workflow, or push to `main`. The workflow deploys only `site/`.
5. Copy the actual site URL from **Settings → Pages**. A standard public project site is expected at `https://somaiya-vidyavihar-it.github.io/somaiya-it-dashboards/`; privately published Enterprise sites use a different hostname. Use the actual deployed URL for Google configuration.

## Google Console setup

Project from the supplied screenshot: **KJS Master Tracker** (`kjs-master-tracker`). Its audience is already **Internal**; keep that for your Google Workspace staff.

1. Enable **Google Sheets API** under **APIs & Services → Library**.
2. In **Google Auth Platform → Branding**, use the deployed dashboard hub URL as the Application home page. Use your organization's approved privacy policy and terms URLs where applicable; do not invent them. Register the site's actual domain as needed by the console.
3. Under **Audience**, retain **Internal**. People signing in must belong to that Workspace organization and have access to the source spreadsheet. Google Workspace admin restrictions can still apply.
4. Under **Data Access**, configure `https://www.googleapis.com/auth/spreadsheets.readonly` and `https://www.googleapis.com/auth/userinfo.email`. The browser requests these scopes; Google API scopes can cover accessible spreadsheets, even though this app reads only the fixed spreadsheet tab.
5. Under **Clients → Create client**, select **Web application**. Name it **Somaiya IT Dashboard Hub**.
6. Add the deployed site's **origin** under **Authorized JavaScript origins**, without its repository path. For a standard Pages project site that is `https://somaiya-vidyavihar-it.github.io`. For a private Enterprise Pages site or a custom domain, use its actual origin. You can also register `http://127.0.0.1:8765` for local testing.
7. This browser popup flow does **not** use Authorized redirect URIs. The earlier localhost `/auth/google/callback` instructions applied to the local backend version and are not required by this Pages version.
8. Create the client, then copy its **Client ID** into `site/config.js`. It ends with `.apps.googleusercontent.com`. The Client ID is public configuration. Do not upload the client secret or downloaded client JSON.
9. Commit the configuration. After the deployment finishes, open Production Support and click **Connect with Google**. Approve read-only access using an account that can read the source sheet.

## Structure

```text
site/
  index.html                  # Dashboard hub with persistent navigation
  config.js                   # Public OAuth Client ID
  assets/                     # Somaiya branding assets
  shared/sheet-data.js         # Production field mapping and date parsing
  shared/session.js           # Global in-memory session client
  shared/session-worker.js    # Shared session owner
  production-support/
    index.html                # Production Support
  koha-support/
    index.html                # KOHA Library Support
.github/workflows/pages.yml   # Static deployment workflow
tests/check.cjs               # Source and data checks
```

Add each future dashboard in its own `site/<name>/` folder and add a card in the hub. Dashboards on the same origin can use the same Google client ID. Keep shared data utilities and design assets in `site/shared/` and `site/assets/`.

## Production Support data

The app reads only the spreadsheet `1Slj1cNkj4ZKE1-lXc1Ci-wwaibIEu0-lcRNnxkb7keU` and the tab **All modules Bugs, Issues**, also accepting the singular label **All Module Bugs, Issues** shown in the screenshot. No other tab's cell values are read.

Supported columns: Description, Module, Status, Type, Priority, Assigned To, ETA, Date Reported. Add **Resolution Date** for daily closures and **Issue ID** for stable references. Progress, Blocker, Notes, and Last Updated are optional. Dates accept YYYY-MM-DD or DD-MM-YYYY / DD/MM/YYYY.

## KOHA Library Support data

Reads only **Form Responses 1** in spreadsheet `1zNrZWjO8hSxGbHCbTuf7K7OEWu85V8Jg9DOse7r7HtA`. The **Test** column is displayed as Library / institute. Timestamp is the reported date, and Assigned Date and Time is the assignment date. Blank status counts as Needs triage. Add **Resolution Date** for daily closure counts; assignment dates are never used as resolution dates. Missing Case IDs use row references. Library/category/owner filters, recurring issue counts, queue aging, and daily reports are available.

## Connection and reports

- Google authorization uses the Google Identity Services token model. Access tokens stay in shared browser memory. Connect once and use the hub to switch dashboards. Other open dashboard tabs can adopt the connection, including after reload while another connected hub/tab remains open. Disconnect clears authorization and sheet data across all connected dashboards. Closing every hub/dashboard tab ends the in-memory session. They are not written to browser storage, the repository, or the URL.
- The dashboard refreshes every 30 seconds while visible and when returning to the window. Browser/network delays can affect refresh timing.
- When a Google access token expires, users must click **Connect with Google** again. This Pages version has no server-side refresh token store and does not automatically renew authorization indefinitely.
- Reports recalculate from the current sheet. Automatic daily delivery while nobody has the page open needs a separate scheduled service. Reopen/deletion history needs saved snapshots or an audit log.
- On a public Pages site, the UI and source can be viewed publicly; private issue data is retrieved directly from Google only after Google authorization. Site-level private access requires a supported GitHub Enterprise Pages setup or another hosting arrangement.

## Local preview and checks

Run `node tests/check.cjs` from the repository root. No dependencies or build step are needed. Serve `site/` with any local static server, for example `python -m http.server 8765 --bind 127.0.0.1 --directory site`, then open `http://127.0.0.1:8765/`.

Official references: [GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages), [Google token model](https://developers.google.com/identity/oauth2/web/guides/use-token-model), [Google client setup](https://developers.google.com/identity/oauth2/web/guides/get-google-api-clientid), [Somaiya Brand Centre](https://brand.somaiya.edu.in/en/design-centre/).
