import { createPublishedForecastBlobStore } from "../server/published-forecast-blob.mjs";
import { createPublishedForecastHandler } from "../server/published-forecast.mjs";

export default {
  fetch: createPublishedForecastHandler({
    store: createPublishedForecastBlobStore(),
    publishToken: process.env.XOCOMPASS_PUBLISH_TOKEN,
    readToken: process.env.XOCOMPASS_PUBLISH_READ_TOKEN,
  }),
};
