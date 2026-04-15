from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 34
GAP = 12

BG = colors.HexColor("#F6F7FB")
CARD = colors.white
ACCENT = colors.HexColor("#0F4C5C")
ACCENT_SOFT = colors.HexColor("#DDECEF")
TEXT = colors.HexColor("#14213D")
MUTED = colors.HexColor("#5E6472")
BORDER = colors.HexColor("#D9DEE8")


def wrap_text(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = word
        else:
            lines.append(word)
            current = ""

    if current:
        lines.append(current)

    return lines


def draw_card(
    c: canvas.Canvas,
    x: float,
    y_top: float,
    w: float,
    h: float,
    title: str,
) -> float:
    c.setFillColor(CARD)
    c.setStrokeColor(BORDER)
    c.roundRect(x, y_top - h, w, h, 12, stroke=1, fill=1)

    c.setFillColor(ACCENT_SOFT)
    c.roundRect(x + 14, y_top - 28, 86, 16, 8, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + 22, y_top - 24, title.upper())
    return y_top - 40


def draw_paragraph(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    text: str,
    font_name: str = "Helvetica",
    font_size: float = 9.6,
    leading: float = 12.0,
    color=TEXT,
) -> float:
    c.setFillColor(color)
    c.setFont(font_name, font_size)
    for line in wrap_text(text, font_name, font_size, w):
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_bullets(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    items: list[str],
    font_size: float = 9.0,
    leading: float = 11.2,
) -> float:
    bullet_indent = 10
    text_width = w - bullet_indent - 4
    c.setFont("Helvetica", font_size)
    c.setFillColor(TEXT)

    for item in items:
        lines = wrap_text(item, "Helvetica", font_size, text_width)
        if not lines:
            continue
        c.drawString(x, y, "-")
        c.drawString(x + bullet_indent, y, lines[0])
        y -= leading
        for line in lines[1:]:
            c.drawString(x + bullet_indent, y, line)
            y -= leading
        y -= 2

    return y


def draw_numbered(
    c: canvas.Canvas,
    x: float,
    y: float,
    w: float,
    items: list[str],
    font_size: float = 9.0,
    leading: float = 11.0,
) -> float:
    text_width = w - 18
    c.setFont("Helvetica", font_size)
    c.setFillColor(TEXT)

    for idx, item in enumerate(items, start=1):
        label = f"{idx}."
        lines = wrap_text(item, "Helvetica", font_size, text_width)
        c.drawString(x, y, label)
        c.drawString(x + 16, y, lines[0])
        y -= leading
        for line in lines[1:]:
            c.drawString(x + 16, y, line)
            y -= leading
        y -= 2

    return y


def draw_code_flow(c: canvas.Canvas, x: float, y_top: float, w: float, h: float, text: str) -> float:
    c.setFillColor(colors.HexColor("#F1F5F9"))
    c.setStrokeColor(colors.HexColor("#D6DFEB"))
    c.roundRect(x, y_top - h, w, h, 10, stroke=1, fill=1)

    c.setFillColor(TEXT)
    c.setFont("Courier", 8.2)
    y = y_top - 16
    for line in wrap_text(text, "Courier", 8.2, w - 18):
        c.drawString(x + 10, y, line)
        y -= 10
    return y_top - h - 8


def main() -> None:
    output_path = Path("output/pdf/xocompass_app_summary_one_page.pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle("XoCompass App Summary")
    c.setAuthor("Codex")
    c.setSubject("One-page repo summary")

    c.setFillColor(BG)
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)

    y = PAGE_HEIGHT - MARGIN

    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(MARGIN, y, "XoCompass App Summary")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, y - 16, "One-page snapshot based on repo evidence only")
    c.drawRightString(PAGE_WIDTH - MARGIN, y - 16, "Backend-focused repo")
    y -= 34

    full_w = PAGE_WIDTH - (2 * MARGIN)
    left_w = 318
    right_w = full_w - left_w - GAP

    top_h = 92
    body_y = y

    body_cursor = draw_card(c, MARGIN, body_y, left_w, top_h, "What It Is")
    what_it_is = (
        "XoCompass is a context-aware tourism demand forecasting system for an MSME "
        "travel agency thesis project. In this repo, it appears as a FastAPI backend "
        "that stores weekly booking data, trains interpretable SARIMAX models with "
        "holiday and typhoon signals, and serves forecast and diagnostic endpoints."
    )
    draw_paragraph(c, MARGIN + 16, body_cursor, left_w - 32, what_it_is)

    body_cursor = draw_card(c, MARGIN + left_w + GAP, body_y, right_w, top_h, "Who It's For")
    who = (
        "Primary user: travel agency decision-makers who need explainable forecasts "
        "for planning, plus thesis evaluators or analysts who need transparent model diagnostics."
    )
    draw_paragraph(c, MARGIN + left_w + GAP + 16, body_cursor, right_w - 32, who)

    y = body_y - top_h - GAP

    features_h = 170
    feature_y = draw_card(c, MARGIN, y, full_w, features_h, "What It Does")
    feature_left = [
        "Lists trained SARIMAX models and registry metadata.",
        "Serves dashboard KPIs such as total records, data quality, revenue proxy, growth rate, expected bookings, and peak travel period.",
        "Exposes advanced diagnostics: RMSE, MAE, WMAPE, ADF, Ljung-Box, Jarque-Bera, residuals, ACF, and PACF.",
    ]
    feature_right = [
        "Returns historical weekly booking data with holiday and weather indicators.",
        "Uploads CSV booking data, deduplicates weeks, aggregates to weekly buckets, and enriches records with Philippine holiday features.",
        "Provides cached forecast graph data with 95% bounds, rule-based strategic actions, and a retraining trigger for the 6-step pipeline.",
    ]
    draw_bullets(c, MARGIN + 16, feature_y, (full_w - 48) / 2, feature_left, font_size=9.0)
    draw_bullets(
        c,
        MARGIN + 24 + (full_w - 48) / 2,
        feature_y,
        (full_w - 48) / 2,
        feature_right,
        font_size=9.0,
    )

    y -= features_h + GAP

    architecture_h = 198
    arch_y = draw_card(c, MARGIN, y, full_w, architecture_h, "How It Works")
    flow_text = (
        "CSV upload -> services/ingestion_service.py -> weekly buckets + PHHolidayEngine "
        "-> training_data_log -> /api/retrain -> services/pipeline/orchestrator.py "
        "-> steps 1-6 -> statsmodels SARIMAX + metrics -> data/models/*.joblib + "
        "sarimax_models / model_diagnostics / forecast_cache / forecast_snapshots "
        "-> FastAPI endpoints -> external UI (frontend implementation: Not found in repo)"
    )
    next_y = draw_code_flow(c, MARGIN + 16, arch_y + 4, full_w - 32, 56, flow_text)
    architecture_items = [
        "API layer: api/main.py exposes health, model registry, dashboard, diagnostics, history, upload, forecast graph, strategic actions, and retraining endpoints.",
        "Persistence: SQLAlchemy models in domain/models.py back five main tables; the database comes from DATABASE_URL or defaults to local SQLite xocompass.db.",
        "Training pipeline: services/pipeline/orchestrator.py exports DB data to CSV, runs ingestion, correlation, stationarity, decomposition, training, and evaluation, then writes all outputs atomically.",
        "Repo gap: a concrete frontend codebase and production deployment guide are Not found in repo.",
    ]
    draw_bullets(c, MARGIN + 16, next_y, full_w - 32, architecture_items, font_size=8.8, leading=10.8)

    y -= architecture_h + GAP

    run_h = 126
    run_y = draw_card(c, MARGIN, y, full_w, run_h, "How To Run")
    run_items = [
        "Install dependencies: python3 -m pip install -r requirements.txt",
        "Optional env setup: define DATABASE_URL and CORS_ALLOWED_ORIGINS; if omitted, the repo falls back to local SQLite xocompass.db",
        "Start the API: uvicorn api.main:app --reload",
        "Open http://127.0.0.1:8000/docs ; for cloud Postgres table creation use python init_db.py ; fresh SQLite bootstrap docs are Not found in repo",
    ]
    draw_numbered(c, MARGIN + 16, run_y, full_w - 32, run_items, font_size=8.9, leading=10.8)

    footer_y = 26
    c.setStrokeColor(BORDER)
    c.line(MARGIN, footer_y + 18, PAGE_WIDTH - MARGIN, footer_y + 18)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.3)
    c.drawString(
        MARGIN,
        footer_y + 6,
        "Evidence files: CLAUDE.md, api/main.py, api/schemas.py, services/ingestion_service.py, services/pipeline/orchestrator.py, domain/models.py, repository/model_repository.py, requirements.txt",
    )

    c.showPage()
    c.save()
    print(output_path.resolve())


if __name__ == "__main__":
    main()
