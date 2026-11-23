import { loadPyodide } from "./runtime/pyodide.mjs";

const assets = new URL("./", import.meta.url);
const csvPath = "/xoco/bookings.csv";
let started = false;

function progress(stage) {
  self.postMessage({ type: "progress", stage });
}

function fail(code, message) {
  self.postMessage({ type: "error", code, message });
}

self.onmessage = async ({ data }) => {
  if (started || data?.type !== "train") return;
  if (!(data.bytes instanceof ArrayBuffer) || !data.bytes.byteLength) {
    fail("invalid_csv", "Choose a non-empty booking CSV.");
    return;
  }
  started = true;
  progress("loading");

  let stage = "loading";
  try {
    const [manifestResponse, sourceResponse, py] = await Promise.all([
      fetch(new URL("manifest.json", assets)),
      fetch(new URL("source.zip", assets)),
      loadPyodide({
        indexURL: new URL("runtime/", assets).href,
        stdout: () => {},
        stderr: () => {},
      }),
    ]);
    if (!manifestResponse.ok || !sourceResponse.ok) {
      throw new Error("Training assets unavailable");
    }
    const manifest = await manifestResponse.json();
    await py.loadPackage(manifest.packages);
    await py.loadPackage(new URL(`runtime/${manifest.pmdarima_wheel}`, assets).href);
    py.unpackArchive(await sourceResponse.arrayBuffer(), "zip", { extractDir: "/xoco" });
    py.globals.set("report_progress", (name) => {
      stage = name;
      progress(name);
    });
    py.FS.writeFile(csvPath, new Uint8Array(data.bytes));
    try {
      stage = "ingestion";
      progress(stage);
      const outcome = JSON.parse(py.runPython(`
import json
import sys

sys.path[:0] = ["/xoco", "/xoco/vendor"]
from services.pipeline.browser_training import BrowserTrainingError, train_browser_csv

def run_training():
    try:
        return train_browser_csv("/xoco/bookings.csv", report_progress)
    except BrowserTrainingError as error:
        return json.dumps({"error": {"code": error.code, "message": str(error)}})

run_training()
`));
      if (outcome.error) {
        fail(outcome.error.code, outcome.error.message);
      } else {
        self.postMessage({ type: "result", result: outcome });
      }
    } finally {
      py.FS.unlink(csvPath);
    }
  } catch {
    if (stage === "loading") {
      fail("runtime_unavailable", "The on-device training files could not load. Reload the page and try again.");
    } else {
      fail("training_failed", "Training could not finish on this PC.");
    }
  }
};
