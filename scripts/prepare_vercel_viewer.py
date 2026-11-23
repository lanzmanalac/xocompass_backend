"""Build a Vercel upload directory containing only the staff viewer."""

import argparse
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parents[1]
VIEWER_SOURCES = (
    "api/dependencies/auth.py",
    "api/routers/auth.py",
    "api/schemas.py",
    "core/rate_limit.py",
    "core/security.py",
    "domain/auth_models.py",
    "domain/models.py",
    "repository/auth_repository.py",
    "repository/model_repository.py",
    "services/audit_service.py",
    "services/audit_vocab.py",
    "services/auth_service.py",
    "services/forecast_publication_views.py",
    "services/publication_delivery.py",
    "services/published_forecast_service.py",
)
VIEWER_DEPENDENCIES = (
    "fastapi",
    "starlette",
    "pydantic",
    "email-validator",
    "uvicorn",
    "SQLAlchemy",
    "psycopg2-binary",
    "python-dotenv",
    "python-jose[cryptography]",
    "passlib[argon2]",
    "argon2-cffi",
    "requests",
    "slowapi",
    "tzdata",
)


def prepare_viewer(destination: Path) -> None:
    """Copy source and dependency pins into a new directory outside the checkout."""
    destination = destination.resolve()
    if destination.is_relative_to(ROOT) or destination.exists():
        raise ValueError("Choose a new directory outside the repository.")

    pins = dict(
        line.strip().split("==", maxsplit=1)
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    requirements = "".join(f"{name}=={pins[name]}\n" for name in VIEWER_DEPENDENCIES)
    destination.mkdir()
    for source in VIEWER_SOURCES:
        target = destination / source
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(ROOT / source, target)

    copyfile(ROOT / "api/hosted.py", destination / "app.py")
    (destination / "requirements.txt").write_text(requirements)
    (destination / ".python-version").write_text("3.12\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New path outside the repository")
    args = parser.parse_args()
    try:
        prepare_viewer(args.destination)
    except ValueError as exc:
        parser.error(str(exc))
    print(args.destination.resolve())


if __name__ == "__main__":
    main()
