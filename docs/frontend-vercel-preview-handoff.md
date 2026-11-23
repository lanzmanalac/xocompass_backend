# XoCompass frontend handoff for Vercel Preview

The XoCompass backend Preview uses Neon for staff accounts. Its `/health` returned `ok`, and the test Admin received HTTP `200` from `/auth/login`. In this frontend Preview, wire **Train on this PC** and **Publish** together. The analyst reviews the local report before choosing when to publish it.

**Current backend Preview** [xocompass-viewer-preview `/health`](https://xocompass-viewer-preview-4p0kb8mfn-lanzmanalacs-projects.vercel.app/health). An anonymous request returned HTTP `200`, and the Publish route returned `401` without an XoCompass session. Use the rewrite below to test login. End-to-end Publish awaits the frontend Preview receiver and its private Blob store.

**Test account** `xocompass-test@example.com`. Get the password privately from the backend owner.

## Backend owner

1. Share the test Admin password privately. Share the [receiver source](./frontend-preview-receiver/RECEIVER-README.md), [training bundle ZIP](./frontend-preview-assets/xocompass-train-publish-assets.zip), and [mock CSV](./frontend-preview-assets/mock-bookings.csv). To regenerate the training bundle on the backend owner's Mac, run the following command from the backend checkout and choose a new output directory. The packager verifies the sandbox assets and includes the reviewed Python stages, the worker, and the browser runtime.

   ```sh
   .venv/bin/python -B scripts/prepare_browser_training.py \
     /private/tmp/xoco-browser-sarimax /private/tmp/xocompass-browser-training-assets
   ```

   Save the tested asset ZIP with the team’s handoff files. For future rebuilds, keep the verified runtime inputs and package manifest from the sandbox in team-controlled storage. The temporary paths above can be cleared by macOS.

2. Have the frontend project owner copy the receiver to their Preview branch using the [receiver setup guide](./frontend-preview-receiver/RECEIVER-README.md). The entry point is `api/published-forecast.mjs`. Connect a dedicated private Blob store to the test deployment and set `XOCOMPASS_PUBLISH_TOKEN` and `XOCOMPASS_PUBLISH_READ_TOKEN` there as separate server secrets. Test its `/api/published-forecast` URL with the read token, using any Vercel protection credentials server-side. An empty store should return `404` from the receiver. [Private Blob storage](https://vercel.com/docs/vercel-blob/private-storage)
3. Set the matching viewer variables listed below and deploy the updated viewer from a clean, lightweight directory. Run these commands from the backend checkout with a new output path. Verify that the CLI is linked to `xocompass-viewer-preview` before deploying to Preview.

   ```sh
   .venv/bin/python -B scripts/prepare_vercel_viewer.py /private/tmp/xocompass-viewer-with-publish
   cd /private/tmp/xocompass-viewer-with-publish
   npx vercel link --project xocompass-viewer-preview
   npx vercel deploy --environment preview
   ```

4. If Vercel protects the backend Preview, go to **`xocompass-viewer-preview` → Deployment Protection → Exceptions → Add Domain**. Add the exact hostname returned by the updated deployment. Check `/health` at that URL and give the hostname to the frontend developer for the rewrite. The exception makes the hostname publicly reachable. XoCompass checks credentials at login and requires an access token for forecasts. Remove the exception after the test window. [Vercel Deployment Protection Exceptions](https://vercel.com/docs/deployment-protection/methods-to-bypass-deployment-protection/deployment-protection-exceptions)

## Frontend developer

### 1. Update `vercel.json` on a Preview branch

Create a branch such as `codex/vercel-login-preview` from a clean checkout. In the frontend repo's `vercel.json`, replace the existing `/backend/:path*` rewrite with this entry. The example uses the reachable backend Preview URL. If the backend owner redeploys after setting the Publish variables, use the new URL after its health check passes. Keep the rewrite above the SPA fallback and preserve its API exclusions. [Vercel rewrites](https://vercel.com/docs/routing/rewrites)

```json
{
  "source": "/backend/:path*",
  "destination": "https://xocompass-viewer-preview-4p0kb8mfn-lanzmanalacs-projects.vercel.app/:path*"
}
```

The browser calls `/backend/auth/login` on the frontend Preview. Vercel forwards that request to `/auth/login` on the backend Preview. The existing `LoginPage`, auth context, and API client already handle login, session refresh, and sign-out.

### 2. Set the API base and deploy

1. Commit `vercel.json` and push the Preview branch.
2. In the **frontend** Vercel project, go to **Environment Variables**. Set `VITE_API_BASE_URL` to `/backend`. Choose **Preview** and scope it to your branch. [Vercel Preview environment variables](https://vercel.com/docs/environment-variables#preview-environment-variables)
3. Redeploy that branch after saving the variable. In **Deployments**, open the deployment labeled **Preview** for that branch. Use the generated frontend URL for the checks below. [Vercel deployment environments](https://vercel.com/docs/deployments/environments)

### 3. Verify login and redirection

1. Open `/backend/health` on the **frontend Preview**. Expect HTTP `200` and `{"status":"ok","service":"xocompass-staff-viewer"}`.
2. Open `/login` on that same frontend Preview. Open browser **DevTools → Network**, then sign in as `xocompass-test@example.com` using the password from the backend owner.
3. Check that `POST /backend/auth/login` returns `200` and that the app opens `/business-analytics`. Reload the page and check that `POST /backend/auth/refresh` and `GET /backend/auth/me` both return `200`.
4. Sign out, then open `/business-analytics` directly. Expect a redirect to `/login`.
5. Share the frontend Preview URL and the pass or fail result for each check. Keep the password, auth response bodies, and session tokens private.

If `/backend/health` shows a Vercel sign-in page or returns `401`, ask the backend owner to check the exception for the exact hostname. If the browser still calls Cloud Run, check the branch scope of `VITE_API_BASE_URL` and redeploy. If health passes and XoCompass rejects the login, share the HTTP status with the backend owner so they can check the Neon audit log.

### 4. Add Train on this PC

1. In `src/pages/SavesPage.tsx`, add **Train on this PC** for signed-in Analysts and Admins. Use the existing `canTrain` permission, and make the button available before a user has any saved models. Gate it behind a Preview-only feature flag such as `VITE_XOCOMPASS_BROWSER_TRAINING=1`.
2. Put the asset bundle in the frontend at `public/training/`, with `worker.mjs` inside that directory. Add `training(?:/|$)` to the existing SPA fallback exclusions so the assets are served directly. The updated fallback source is `/((?!api/published-forecast(?:/|$)|training(?:/|$)).*)`. Verify that `/training/worker.mjs` returns JavaScript and `/training/runtime/pyodide.asm.wasm` returns WebAssembly in Preview.
3. Keep the selected `File` in the browser and transfer its bytes to the worker. Leave `uploadCSV()` and `triggerRetrain()` on the native workflow.

   ```ts
   const worker = new Worker("/training/worker.mjs", { type: "module" });
   const bytes = await file.arrayBuffer();
   worker.postMessage({ type: "train", bytes }, [bytes]);
   ```

4. Use messages with `type: "progress"` to show the `stage`. An error arrives as `{ type: "error", code, message }`. Allow one active run and provide **Cancel**. Terminate the worker on completion, failure, or cancellation. Preserve the last completed forecast if a new run fails.
5. Read `{ type: "result", result }` and render `result.forecast`. Each row has `week_start`, `week_end`, `forecast_bookings`, `confidence_lower_95`, and `confidence_upper_95`. A completed fit that converged also gives you `result.publication_snapshot` for review and Publish. Show a review state and keep Publish disabled if the snapshot is `null`.
6. In Preview, run the 5,323-row mock CSV supplied with the handoff. Verify all 12 forecasts and their confidence bounds. Check **DevTools → Network** to confirm that the CSV stays in the browser. Rehearse on the KJS office PC before enabling the feature for staff.

### 5. Add Publish to the same Preview branch

The backend source now includes `POST /api/viewer/publish` for signed-in Analysts and Admins. It accepts a complete `PublicationSnapshot` v1 and forwards the validated aggregate results to the private receiver. The backend owner will deploy and configure the route on the viewer Preview. The frontend calls `POST /backend/api/viewer/publish` and receives `{ run_id, published_at }` on success. [Publication schema](../api/schemas.py) · [Hosted endpoint](../api/hosted.py)

1. After training, show **Publish** to the Analyst or Admin when `result.publication_snapshot` is present. Review the report from that snapshot before publishing. It already contains Business Analytics, the forecast graph, outlook, and strategic actions. The UI can pass it directly to the API.
2. Save the snapshot locally under the signed-in `user.id` before the first attempt. Show it only to that same authorized account after a reload, and remove the pending copy after a matching receipt or an explicit discard. Pass only the snapshot to the existing `apiRequest` helper so it uses the XoCompass session and adds `/backend` once.

   ```ts
   const receipt = await apiRequest<{ run_id: string; published_at: string }>(
     "/api/viewer/publish",
     {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify(snapshot),
     },
   );
   ```

3. Show the receipt's `published_at` when its `run_id` matches the snapshot. If publishing fails, retain that snapshot for **Retry publish**. Send the same snapshot and run ID on retry. A `409` means an equal, newer, or conflicting run is already online. Recheck the current published result before selecting another run. Keep the CSV, model files, and storage credentials out of the request.
4. Add a fetch in `src/services/dashboardService.ts` for `GET /api/viewer/published-forecast`. In `src/pages/BusinessAnalyticsPage.tsx`, read `snapshot.business_analytics.overall` and use the other year keys for the year selector. In `src/pages/ForecastActions.tsx`, use `snapshot.forecast_graph`, `snapshot.forecast_outlook`, and `snapshot.strategic_actions`. In this hosted mode, populate both pages from the published snapshot before looking for model data. The viewer backend serves the latest report and does not implement `/api/models` or model-specific routes. Show **No forecast published yet** for a `404` with code `no_published_forecast`.
5. On Preview, train with the mock CSV, publish, and check both dashboard pages from a second browser. Verify that a Viewer can sign in and view the forecast. Check that Publish is allowed for an Analyst or Admin. Simulate a failed publish and confirm the previous report stays online while the analyst retries.

On the backend Preview, set `XOCOMPASS_PUBLISHED_FORECAST_URL` to the HTTPS `/api/published-forecast` receiver, and set `XOCOMPASS_PUBLISH_READ_TOKEN` and `XOCOMPASS_BROWSER_PUBLISH_TOKEN` as server secrets. Set `XOCOMPASS_BROWSER_PUBLISH_TOKEN` to the receiver's `XOCOMPASS_PUBLISH_TOKEN`. Set `XOCOMPASS_VERCEL_AUTOMATION_TOKEN` if the receiver needs Vercel automation access. These variables belong on the server. Publish returns `503` until the receiver URL and write token are configured. Publish permission follows the XoCompass account's role, so an Analyst or Admin can publish from any browser they can sign into.

## Local results to reproduce in Preview

On the test Mac, Chrome trained on 5,323 mock bookings in 7 minutes and 41 seconds. All 12 displayed forecasts and their confidence bounds matched the native reference. The only network requests during training were GETs for the page and training assets. The exact generated report passed through the authenticated API and receiver with a simulated Blob store. A failed write preserved the previous report, and retrying the same report succeeded without training again. The Vercel Preview and the KJS office PC still need the checks above.
