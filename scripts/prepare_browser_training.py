"""Package the browser trainer using the pinned, verified sandbox assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from shutil import copyfile
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
PYODIDE_VERSION = "314.0.8"
LOCK_FILE = "pyodide-lock.json"
LOCK_SHA256 = "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010"
VENDOR_ARCHIVE_SHA256 = "c954f978ba048bccc919e766a33d4bda05a6567b5ebae2e2e67a628b270c13a0"
PMDARIMA_WHEEL = "pmdarima-2.1.1-cp314-cp314-pyemscripten_2026_0_wasm32.whl"
RUNTIME_FILES = {
    "pyodide.mjs": "1bb8cc14f9405aaa7cd4e00c12edd0a8539de9cc026618d6ee5a9663f367dc70",
    "pyodide.asm.mjs": "22514b8be6980e28a02c8b1d6387ce8dfe666fc4edf8d547cff5dce29ff4d972",
    "pyodide.asm.wasm": "e14d49ceca9b4a0e274ea918f3a898de2d3880b6e5573a6cfe1a2267ecc70329",
    "python_stdlib.zip": "2bd3394a145bfbbe5bbc9d9d6ab9136abbb986c6fef6c142b3a2962227aa5c28",
    PMDARIMA_WHEEL: "5bc5d6a3481147dbf7bfbca4da2c92b1add3035d0574c1a36f7bc080a180d3f0",
}
PACKAGES = (
    "statsmodels", "matplotlib", "xarray", "tzdata", "pyodide-unix-timezones",
    "toolz", "cloudpickle", "pyyaml", "fsspec", "scikit-learn", "urllib3", "setuptools", "joblib",
)
PYTHON_FILES = (
    "core/__init__.py", "core/config.py", "core/exogenous.py", "services/__init__.py",
    "services/booking_analytics.py", "services/forecast_publication_views.py",
    "services/pipeline/__init__.py", "services/pipeline/browser_training.py",
    "services/pipeline/step1_ingestion.py", "services/pipeline/step2_correlations.py",
    "services/pipeline/step3_stationarity.py", "services/pipeline/step4_decomposition.py",
    "services/pipeline/step5_training.py", "services/pipeline/step6_evaluation.py",
)
VENDOR_PACKAGES = {
    "dask", "dask-2026.3.0.dist-info", "locket", "locket-1.0.0.dist-info",
    "partd", "partd-1.4.2.dist-info", "properscoring", "properscoring-0.1.dist-info",
    "seaborn", "seaborn-0.13.2.dist-info", "xhistogram", "xhistogram-0.3.2.dist-info",
    "xskillscore", "xskillscore-0.0.29.dist-info",
}


def _verify(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum mismatch for {path.name}.")


def _locked_wheels(lock_path: Path) -> dict[str, str]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))["packages"]
    required = set(PACKAGES)
    queue = list(PACKAGES)
    while queue:
        name = queue.pop()
        dependencies = set(lock[name]["depends"]) - required
        required.update(dependencies)
        queue.extend(dependencies)
    return {lock[name]["file_name"]: lock[name]["sha256"] for name in sorted(required)}


def _write_source_archive(vendor_archive: ZipFile, path: Path) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for source in PYTHON_FILES:
            archive.writestr(ZipInfo(source), (ROOT / source).read_bytes(), ZIP_DEFLATED)
        for filename in sorted(vendor_archive.namelist()):
            parts = PurePosixPath(filename).parts
            if len(parts) > 2 and parts[0] == "vendor" and parts[1] in VENDOR_PACKAGES:
                if not filename.endswith("/") and ".." not in parts and "tests" not in parts:
                    archive.writestr(ZipInfo(filename), vendor_archive.read(filename), ZIP_DEFLATED)


def prepare_browser_training(sandbox: Path, output: Path) -> None:
    """Create an allowlisted release folder without copying workspace data."""
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Choose a new output directory outside the backend repository.")

    runtime = sandbox / "runtime"
    lock_path = runtime / LOCK_FILE
    vendor_path = sandbox / "site" / "source.zip"
    _verify(lock_path, LOCK_SHA256)
    _verify(vendor_path, VENDOR_ARCHIVE_SHA256)
    files = {LOCK_FILE: LOCK_SHA256, **RUNTIME_FILES, **_locked_wheels(lock_path)}
    for filename, digest in files.items():
        _verify(runtime / filename, digest)

    with TemporaryDirectory(dir=output.parent) as scratch:
        release = Path(scratch) / "training"
        release_runtime = release / "runtime"
        release_runtime.mkdir(parents=True)
        for filename in files:
            copyfile(runtime / filename, release_runtime / filename)
        with ZipFile(vendor_path) as vendor_archive:
            _write_source_archive(vendor_archive, release / "source.zip")
        copyfile(ROOT / "scripts" / "browser_training_worker.mjs", release / "worker.mjs")
        manifest = {
            "schema_version": 1,
            "pyodide_version": PYODIDE_VERSION,
            "packages": PACKAGES,
            "pmdarima_wheel": PMDARIMA_WHEEL,
        }
        (release / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        release.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sandbox", type=Path, help="Verified xoco-browser-sarimax directory")
    parser.add_argument("output", type=Path, help="New directory for the frontend training assets")
    args = parser.parse_args()
    try:
        prepare_browser_training(args.sandbox, args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
