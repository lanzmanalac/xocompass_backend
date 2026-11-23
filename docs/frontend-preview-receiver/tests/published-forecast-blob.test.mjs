import assert from "node:assert/strict";
import test from "node:test";
import { BlobPreconditionFailedError } from "@vercel/blob";

import { createPublishedForecastBlobStore } from "../server/published-forecast-blob.mjs";

test("the Blob adapter requests consistent private reads and uses the read ETag to replace", async () => {
  const requests = [];
  const published = { snapshot: { run_id: "synthetic" }, published_at: "2026-11-21T12:00:00.000Z" };
  const store = createPublishedForecastBlobStore({
    async get(pathname, options) {
      requests.push({ pathname, options });
      return {
        statusCode: 200,
        stream: new Response(JSON.stringify(published)).body,
        blob: { etag: '"previous"' },
      };
    },
    async put(pathname, body, options) {
      requests.push({ pathname, body, options });
    },
  });

  const current = await store.read();
  assert.deepEqual(current, { published, etag: '"previous"' });
  assert.equal(await store.writeIfCurrent(published, current.etag), true);
  assert.deepEqual(requests, [
    {
      pathname: "xocompass/published-forecast.json",
      options: { access: "private", useCache: false },
    },
    {
      pathname: "xocompass/published-forecast.json",
      body: JSON.stringify(published),
      options: {
        access: "private",
        contentType: "application/json",
        addRandomSuffix: false,
        allowOverwrite: true,
        ifMatch: '"previous"',
      },
    },
  ]);
});

test("a first publication uses create-only and discovers a concurrent create after an SDK error", async () => {
  let firstWrite;
  const store = createPublishedForecastBlobStore({
    async get() {
      return {
        statusCode: 200,
        stream: new Response("{}").body,
        blob: { etag: '"winner"' },
      };
    },
    async put(_pathname, _body, options) {
      firstWrite = options;
      throw new Error("This blob already exists");
    },
  });

  assert.equal(await store.writeIfCurrent({}, null), false);
  assert.equal(firstWrite.access, "private");
  assert.equal(firstWrite.addRandomSuffix, false);
  assert.equal(firstWrite.allowOverwrite, false);
  assert.equal(Object.hasOwn(firstWrite, "ifMatch"), false);
});

test("an ETag conflict is retryable and an ordinary write failure is surfaced", async () => {
  const conflict = createPublishedForecastBlobStore({
    async put() {
      throw new BlobPreconditionFailedError();
    },
  });
  assert.equal(await conflict.writeIfCurrent({}, '"expected"'), false);

  const offline = new Error("storage unavailable");
  const unreachable = createPublishedForecastBlobStore({
    async get() {
      return null;
    },
    async put() {
      throw offline;
    },
  });
  await assert.rejects(unreachable.writeIfCurrent({}, null), offline);
  await assert.rejects(unreachable.writeIfCurrent({}, '"expected"'), offline);
});
