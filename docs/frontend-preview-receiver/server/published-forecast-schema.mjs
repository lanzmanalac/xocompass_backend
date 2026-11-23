// The publishing boundary only accepts fields from the approved aggregate views.
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const string = (value) => typeof value === "string";
const number = (value) => typeof value === "number" && Number.isFinite(value);
const integer = Number.isSafeInteger;
const nullable = (check) => (value) => value === null || check(value);
const list = (check) => (value) => Array.isArray(value) && value.every(check);
const year = (value) => string(value) && /^\d{4}$/.test(value);
const month = (value) => string(value) && /^\d{4}-(?:0[1-9]|1[0-2])$/.test(value);

function shape(required, optional = {}) {
  return (value) =>
    record(value) &&
    Object.entries(required).every(([key, check]) => Object.hasOwn(value, key) && check(value[key])) &&
    Object.entries(value).every(([key, field]) =>
      Object.hasOwn(required, key) || (Object.hasOwn(optional, key) && optional[key](field))
    );
}

function calendarDate(value) {
  if (!string(value) || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return number(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}

function aggregateDateTime(value) {
  return string(value) &&
    /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$/.test(value) &&
    calendarDate(value.slice(0, 10));
}

export function utcOrderKey(value) {
  if (!string(value)) return null;
  const match = /^(\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)$/.exec(value);
  if (!match || !calendarDate(value.slice(0, 10))) return null;
  // Preserve sub-millisecond ordering for timestamps from the office database.
  return `${match[1]}.${(match[2] ?? "").padEnd(9, "0")}`;
}

const businessAnalytics = shape({
  generated_at: aggregateDateTime,
  total_transaction_count: integer,
  total_weekly_records: integer,
  total_revenue: nullable(number),
  avg_weekly_bookings: number,
  peak_week_date: aggregateDateTime,
  peak_week_bookings: integer,
  growth_rate: number,
  date_coverage: shape({
    start_date: aggregateDateTime,
    end_date: aggregateDateTime,
    span_weeks: integer,
  }),
  bookings_by_year: list(shape({ year, bookings: integer })),
  bookings_by_month: list(shape({ month, bookings: integer })),
  holiday_breakdown: shape({
    holiday_weeks: integer,
    non_holiday_weeks: integer,
    holiday_pct: number,
  }),
}, {
  revenue_by_month: list(shape({ month, revenue: number })),
  avg_lead_time_days: nullable(number),
  lead_time_distribution: list(shape({ bucket: string, count: integer })),
  top_airlines: list(shape({ airline_code: string, count: integer, pct: number })),
  top_routes: list(shape({ route: string, count: integer, pct: number })),
  revenue_by_year: list(shape({ year, revenue: number })),
  data_quality: nullable(shape({
    total_rows: integer,
    duplicate_rows: integer,
    missing_airline: integer,
    missing_route: integer,
    missing_travel_date: integer,
    invalid_travel_date: integer,
    missing_revenue: integer,
    quality_score_pct: number,
  })),
  available_years: list(year),
});

const analyticsViews = (value) =>
  record(value) &&
  Object.hasOwn(value, "overall") &&
  Object.entries(value).every(([key, view]) => (key === "overall" || year(key)) && businessAnalytics(view));

const graphPoint = shape({ date: aggregateDateTime }, {
  actual: nullable(number),
  predicted: nullable(number),
  lower_bound: nullable(number),
  upper_bound: nullable(number),
  confidence_tier: nullable(string),
  ci_ratio: nullable(number),
  ci_gap: nullable(number),
  risk_factor: nullable(string),
});

const forecastOutlook = shape({
  forecasted_bookings_2w: integer,
  highest_forecast_week_date: aggregateDateTime,
  highest_forecast_week_value: integer,
  forecasted_bookings_10w: integer,
  critical_weeks: list(shape({
    week_start: aggregateDateTime,
    forecasted_volume: integer,
    risk_factor: string,
  }, {
    week_end: nullable(aggregateDateTime),
    confidence_tier: nullable(string),
  })),
});

const strategicActions = shape({
  actions: list(shape({ priority: string, category: string, action: string, trigger: string })),
  generated_for_period: string,
});

const utc = (value) => utcOrderKey(value) !== null;

export const isForecastSnapshot = shape({
  schema_version: (value) => value === 1,
  run_id: (value) => string(value) && /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(value),
  model_id: (value) => integer(value) && value > 0,
  run_created_at: utc,
  data_through: calendarDate,
  business_analytics: analyticsViews,
  forecast_graph: shape({ data: list(graphPoint) }),
  forecast_outlook: forecastOutlook,
  strategic_actions: strategicActions,
});

export const isPublishedForecast = shape({ snapshot: isForecastSnapshot, published_at: utc });
