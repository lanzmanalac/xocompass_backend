# XoCompass Publish receiver for the frontend Preview

1. Copy the included `api/`, `server/`, and `tests/` files to the same paths in your frontend Preview branch.
2. From the frontend repo root, run `npm install --save-exact @vercel/blob@2.9.0`. Include the updated package files in your commit.
3. Run `node --test tests/published-forecast.test.mjs tests/published-forecast-blob.test.mjs`.
4. Keep `/api/published-forecast` outside the SPA fallback in `vercel.json`. The frontend handoff also shows the exclusion for the training assets.
5. Connect private Blob storage to the Preview project and set the server-side read and write secrets described in the frontend handoff. The receiver handles storage. The browser calls the authenticated viewer API.
