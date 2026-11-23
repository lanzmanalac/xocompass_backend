# XoCompass frontend handoff for Vercel Preview

## Task

Connect the existing frontend Preview to the new backend Preview. Verify login, local training, Publish, and staff viewing in one test. Work on the frontend Preview branch. The frontend owner can review the results before deciding on the live release.

- Frontend repository and branch [`leapziggy-debug/xocompass` on `codex/vercel-login-preview`](https://github.com/leapziggy-debug/xocompass/tree/codex/vercel-login-preview)
- Backend Preview [health endpoint](https://xocompass-viewer-preview-lwriovrct-lanzmanalacs-projects.vercel.app/health)
- Test input [5,323-row mock CSV](https://github.com/lanzmanalac/xocompass_backend/blob/5d0b2f4a395f2c5dd0b852982d1615986eda019c/docs/frontend-preview-assets/mock-bookings.csv)
- Runtime files, if missing [reviewed training bundle ZIP](https://github.com/lanzmanalac/xocompass_backend/blob/5d0b2f4a395f2c5dd0b852982d1615986eda019c/docs/frontend-preview-assets/xocompass-train-publish-assets.zip)

The backend owner applied Neon migration `e2a4b6c8d0f1` to Preview and deployed the updated viewer. Its health check returned `200`. The report routes require a signed-in XoCompass account. The viewer saves the latest report directly to Neon using its existing database connection. Frontend setup is the rewrite and the two settings below. This supersedes the earlier Blob receiver and token setup.

## 1. Point the frontend to the new viewer

Replace the existing `/backend/:path*` entry in the frontend's `vercel.json`. Keep the other rewrites in place. The training files must still resolve directly under `/training/`.

```json
{
  "source": "/backend/:path*",
  "destination": "https://xocompass-viewer-preview-lwriovrct-lanzmanalacs-projects.vercel.app/:path*"
}
```

Set these values in the **frontend** Vercel project. Scope them to **Preview** and the `codex/vercel-login-preview` branch.

| Name | Value |
| --- | --- |
| `VITE_API_BASE_URL` | `/backend` |
| `VITE_XOCOMPASS_BROWSER_TRAINING` | `1` |

The branch already has the training screen in `src/components/BrowserTraining.tsx` and the report loader in `src/components/dashboard/PublishedReport.tsx`. Reuse them. The Vite config checks `VERCEL_ENV=preview` to select the hosted screens. The live production build currently selects the older upload workflow.

Build the branch using the existing scripts in `package.json`. Redeploy it after setting the variables. Open the generated deployment labeled **Preview**.

**Pass:** `GET /backend/health` on that frontend Preview returns `200` with `{"status":"ok","service":"xocompass-staff-viewer"}`. Both `/training/worker.mjs` and `/training/runtime/pyodide.asm.wasm` load as their expected file types. If those files are missing in the branch, extract the ZIP's `training/` folder into `public/`.

## 2. Run the login, training, and Publish test

Use approved XoCompass test accounts from the backend owner. Use an Analyst or Admin for training and a Viewer for read access. Keep passwords, tokens, and private KJS data out of chat, commits, screenshots, and logs. Run the Publish test against the agreed test database using the linked mock CSV.

1. Open `/login` on the frontend Preview. Sign in as the Analyst or Admin. In DevTools → Network, confirm that `POST /backend/auth/login` returns `200`. Confirm that the app opens `/business-analytics`.
2. Open `/saves` and select the mock CSV in **Train on this PC**. Start training. Check that the file stays in the browser and the run shows a 12-week forecast. On the reference Mac, this fixture took about 7 minutes and 41 seconds.
3. Review the report and click **Publish**. Confirm that `POST /backend/api/viewer/publish` returns `200` with `run_id` and `published_at`. Record the run ID or published time. The request contains the finished report, while the raw CSV stays on the PC.
4. Sign in as a Viewer in a separate browser session. Open **Business Analytics** and **Forecast Actions**. Confirm that `GET /backend/api/viewer/published-forecast` returns `200` and that both pages show the published run. Check that the Viewer has read access only.
5. If a second mock report is available, block its Publish request in the Analyst's browser. Confirm that the Viewer can still read the first report. Unblock the request and retry the same draft. Confirm that the Viewer can read the second report.

**Pass:** An Analyst or Admin can sign in, train, review, and publish. A Viewer can sign in separately and see the same report. Report the retry check separately if a second mock report is unavailable. An empty test database can show **No forecast published yet** before the first successful Publish.

## Diagnose failures

| What you see | What to check |
| --- | --- |
| The login request points to Cloud Run | Check the frontend Preview value of `VITE_API_BASE_URL` and rebuild. The expected URL ends in `/backend/auth/login` on the frontend hostname. |
| `/backend/health` redirects to Vercel sign-in or says **Protected deployment** | Check access to the frontend Preview, then check the exact rewrite destination. The backend health link above returns XoCompass JSON when reachable. |
| **Saves** shows **New session** or calls `/backend/api/upload` | Check that the deployment is labeled **Preview** and that both frontend variables were set before its build. Check that Vite sees `VERCEL_ENV=preview`. |
| Publish returns `404 not_found` | Check the rewrite destination. The older viewer builds do not expose Publish. |
| Publish returns `503 publish_failed` | Save the pending report in the browser and give the backend owner the status and error code. Retry the same draft when the backend is ready. |
| Publish returns `409 publish_conflict` | Read the current report first. An older or conflicting run cannot replace it. |

Return the frontend commit, the new Preview URL, and the result of each check. If account access or approval is needed, report the exact step that is waiting. Keep credentials and authenticated response bodies out of the handoff.
