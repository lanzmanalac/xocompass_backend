import { createHash, timingSafeEqual } from "node:crypto";
import { isDeepStrictEqual } from "node:util";

import { isForecastSnapshot, isPublishedForecast, utcOrderKey } from "./published-forecast-schema.mjs";

const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MAX_WRITE_ATTEMPTS = 3;

function response(body, status = 200, headers = {}) {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "private, no-store", ...headers },
  });
}

function reject(status, error, headers) {
  return response({ error }, status, headers);
}

function tokenDigest(value) {
  return createHash("sha256").update(value).digest();
}

async function readSnapshot(request) {
  const mediaType = request.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
  if (mediaType !== "application/json") return reject(415, "expected_json");

  const contentLength = Number(request.headers.get("Content-Length"));
  if (contentLength > MAX_BODY_BYTES) return reject(413, "snapshot_too_large");
  if (!request.body) return reject(400, "invalid_snapshot");

  const reader = request.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) return reject(413, "snapshot_too_large");
      text += decoder.decode(value, { stream: true });
    }
    // JSON storage serializes negative zero as zero. Compare retries in that form.
    const snapshot = JSON.parse(text + decoder.decode(), (_key, value) => Object.is(value, -0) ? 0 : value);
    return isForecastSnapshot(snapshot) ? snapshot : reject(400, "invalid_snapshot");
  } catch {
    return reject(400, "invalid_snapshot");
  } finally {
    void reader.cancel().catch(() => {});
  }
}

function acknowledgement(published) {
  return response({
    run_id: published.snapshot.run_id,
    published_at: published.published_at,
  });
}

export function createPublishedForecastHandler({ store, publishToken, readToken, now = () => new Date() }) {
  const validToken = (token) => typeof token === "string" && /^[^\s,]+$/.test(token);
  const postSecret = validToken(publishToken) ? tokenDigest(publishToken) : null;
  const getSecret = validToken(readToken) ? tokenDigest(readToken) : null;
  const sharedSecret = postSecret && getSecret && timingSafeEqual(postSecret, getSecret);

  async function readCurrent() {
    const current = await store.read();
    if (current !== null && !isPublishedForecast(current.published)) {
      throw new Error("Invalid stored forecast");
    }
    return current;
  }

  async function publish(snapshot) {
    const incomingOrder = utcOrderKey(snapshot.run_created_at);
    let current = await readCurrent();

    for (let attempt = 0; attempt <= MAX_WRITE_ATTEMPTS; attempt++) {
      if (current) {
        const previous = current.published.snapshot;
        if (previous.run_id.toLowerCase() === snapshot.run_id.toLowerCase()) {
          return isDeepStrictEqual(previous, snapshot)
            ? acknowledgement(current.published)
            : reject(409, "run_id_conflict");
        }
        if (incomingOrder <= utcOrderKey(previous.run_created_at)) {
          return reject(409, "stale_snapshot");
        }
      }
      if (attempt === MAX_WRITE_ATTEMPTS) break;

      const published = { snapshot, published_at: now().toISOString() };
      if (await store.writeIfCurrent(published, current?.etag ?? null)) {
        return acknowledgement(published);
      }

      // Re-read and re-check ordering after a concurrent writer wins.
      current = await readCurrent();
    }

    return reject(503, "publication_contended");
  }

  return async function handler(request) {
    if (request.method !== "GET" && request.method !== "POST") {
      return reject(405, "method_not_allowed", { Allow: "GET, POST" });
    }

    const secret = request.method === "GET" ? getSecret : postSecret;
    if (!secret || sharedSecret) return reject(503, "publication_unavailable");

    const supplied = /^Bearer ([^\s,]+)$/i.exec(request.headers.get("Authorization") ?? "")?.[1];
    if (!supplied || !timingSafeEqual(tokenDigest(supplied), secret)) {
      return reject(401, "unauthorized", { "WWW-Authenticate": "Bearer" });
    }

    try {
      if (request.method === "GET") {
        const current = await readCurrent();
        return current ? response(current.published) : reject(404, "no_published_forecast");
      }

      const snapshot = await readSnapshot(request);
      return snapshot instanceof Response ? snapshot : await publish(snapshot);
    } catch {
      return reject(503, "publication_unavailable");
    }
  };
}
