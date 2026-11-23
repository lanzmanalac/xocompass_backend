import { BlobPreconditionFailedError, get, put } from "@vercel/blob";

const PATHNAME = "xocompass/published-forecast.json";

export function createPublishedForecastBlobStore(blob = { get, put }) {
  async function read() {
    const result = await blob.get(PATHNAME, { access: "private", useCache: false });
    if (result === null) return null;
    if (result.statusCode !== 200 || !result.blob.etag) {
      throw new Error("Blob read did not include a current ETag");
    }
    return {
      published: await new Response(result.stream).json(),
      etag: result.blob.etag,
    };
  }

  async function writeIfCurrent(published, expectedEtag) {
    const creating = expectedEtag === null;
    try {
      await blob.put(PATHNAME, JSON.stringify(published), {
        access: "private",
        contentType: "application/json",
        addRandomSuffix: false,
        allowOverwrite: !creating,
        ...(creating ? {} : { ifMatch: expectedEtag }),
      });
      return true;
    } catch (error) {
      if (error instanceof BlobPreconditionFailedError) return false;
      // The SDK has no distinct duplicate-create error. Confirm a winner
      // from origin before treating a failed create as a conflict.
      if (creating && await read() !== null) return false;
      throw error;
    }
  }

  return { read, writeIfCurrent };
}
