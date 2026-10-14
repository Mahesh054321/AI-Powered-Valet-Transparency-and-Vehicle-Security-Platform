/**
 * Manager dashboard charts.
 *
 * **The rule these components enforce:** a chart that does not have enough data
 * says so instead of drawing. `ChartFrame` checks `sufficient` and renders an
 * explicit insufficiency notice in place of the plot, so no component can
 * quietly render a two-point line as if it were a trend.
 *
 * Every chart also prints its sample size. "Average retrieval time: 14.7 min"
 * and "…computed from 3 retrievals" are different claims, and only the second
 * one can be evaluated.
 *
 * Recharts is used for the plotting only. The decision about *whether* to plot
 * lives here, not in the library, because a library will happily draw anything
 * it is given.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const SEVERITY_COLOURS = {
  CRITICAL: '#b3261e',
  HIGH: '#d97706',
  MEDIUM: '#2563eb',
  LOW: '#64748b',
}

const OUTCOME_COLOURS = {
  CONFIRMED: '#1f8a4c',
  REJECTED: '#94a3b8',
  UNVERIFIED: '#d97706',
}

/**
 * The frame every chart shares: title, sample size, note, and the refusal.
 *
 * Centralised so the insufficiency rule cannot be forgotten by whichever chart
 * is written next.
 */
export function ChartFrame({ chart, height = 240, children }) {
  if (!chart) return null

  // The backend's `sufficient` verdict is authoritative when present, because
  // only the backend knows the sample size. Falling back to a length check here
  // would be wrong for a histogram: a custody-duration distribution over one
  // two-hour bucket can rest on hundreds of records, and refusing to draw it
  // would hide a perfectly meaningful figure. The length check stays as a
  // fallback for a payload that omits the flag.
  const drawable =
    chart.sufficient != null
      ? chart.sufficient !== false
      : Array.isArray(chart.series) && chart.series.length >= (chart.min_points ?? 1)

  return (
    <section className="chart">
      <header className="chart__head">
        <div>
          <h3>{chart.title}</h3>
          <p className="chart__stats">
            {/* Sample size is printed, not hidden behind a tooltip. A reader
                must be able to weigh the figure without hovering. */}
            {chart.n} {chart.unit}
            {chart.records != null && chart.records !== chart.n
              ? ` across ${chart.records} records`
              : ''}
            {!drawable && ` · below the ${chart.min_points}-point minimum`}
          </p>
        </div>
      </header>

      {drawable ? (
        <div className="chart__plot" style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {children}
          </ResponsiveContainer>
        </div>
      ) : (
        // Stated plainly. Drawing anyway would present a coincidence as a
        // finding, which is the specific failure this dashboard must avoid.
        <p className="chart__insufficient">
          Not enough data to chart. {chart.n} {chart.unit} in the selected range;
          at least {chart.min_points} are needed before a trend means anything.
          Widen the date range to see this.
        </p>
      )}

      {chart.note && <p className="chart__note">{chart.note}</p>}
    </section>
  )
}

/** Vehicles per day. Zero days are included by the backend, not gaps. */
export function VehiclesPerDayChart({ chart }) {
  // Guarded here rather than relying on ChartFrame: JSX children are
  // evaluated before they are passed in, so an undefined chart would
  // throw during the parent's render and blank the whole page.
  if (!chart) return null

  return (
    <ChartFrame chart={chart}>
      <BarChart data={chart.series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 10 }}
          // With 90 days of data, labelling every bar is unreadable.
          interval="preserveStartEnd"
          minTickGap={28}
          tickFormatter={(value) => value.slice(5)}
        />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip
          formatter={(value) => [value, 'vehicles']}
          labelFormatter={(label) => `Date: ${label}`}
        />
        <Bar dataKey="value" fill="#2563eb" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ChartFrame>
  )
}

/** Average measured retrieval time per day. */
export function RetrievalTimeChart({ chart }) {
  // Guarded here rather than relying on ChartFrame: JSX children are
  // evaluated before they are passed in, so an undefined chart would
  // throw during the parent's render and blank the whole page.
  if (!chart) return null

  return (
    <ChartFrame chart={chart}>
      <LineChart data={chart.series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 10 }}
          interval="preserveStartEnd"
          minTickGap={28}
          tickFormatter={(value) => value.slice(5)}
        />
        <YAxis tick={{ fontSize: 10 }} unit="m" />
        <Tooltip
          formatter={(value) => [`${value} min`, 'average']}
          labelFormatter={(label) => `Date: ${label}`}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke="#2563eb"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4 }}
        />
      </LineChart>
    </ChartFrame>
  )
}

/** Alerts per week, stacked by severity. */
export function AlertsChart({ chart }) {
  const data = (chart?.series ?? []).map((point) => ({
    week: point.week,
    ...point.by_severity,
    total: point.total,
  }))
  const present = chart?.series ?? []
  const severities = [
    ...new Set(present.flatMap((point) => Object.keys(point.by_severity ?? {}))),
  ].sort(
    (a, b) =>
      ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].indexOf(a) -
      ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].indexOf(b)
  )

  return (
    <ChartFrame chart={chart}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="week"
          tick={{ fontSize: 10 }}
          tickFormatter={(value) => `w/c ${value.slice(5)}`}
        />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip labelFormatter={(label) => `Week commencing ${label}`} />
        {severities.map((severity) => (
          <Bar
            key={severity}
            dataKey={severity}
            stackId="alerts"
            fill={SEVERITY_COLOURS[severity] ?? '#94a3b8'}
          />
        ))}
      </BarChart>
    </ChartFrame>
  )
}

/** Damage findings per week, split by verification outcome. */
export function DamageChart({ chart }) {
  // Guarded here rather than relying on ChartFrame: JSX children are
  // evaluated before they are passed in, so an undefined chart would
  // throw during the parent's render and blank the whole page.
  if (!chart) return null

  return (
    <ChartFrame chart={chart}>
      <BarChart data={chart.series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="week"
          tick={{ fontSize: 10 }}
          tickFormatter={(value) => `w/c ${value.slice(5)}`}
        />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip labelFormatter={(label) => `Week commencing ${label}`} />
        {['CONFIRMED', 'REJECTED', 'UNVERIFIED'].map((status) => (
          <Bar
            key={status}
            dataKey={status}
            stackId="damage"
            fill={OUTCOME_COLOURS[status]}
          />
        ))}
      </BarChart>
    </ChartFrame>
  )
}

/**
 * Distribution of custody durations.
 *
 * Deliberately a distribution and not a trend: the question is "how long do we
 * hold cars", and the answer a manager needs is the long tail — the cars that
 * occupy a bay for eleven hours — which an average hides completely.
 */
export function CustodyDurationChart({ chart }) {
  // Guarded here rather than relying on ChartFrame: JSX children are
  // evaluated before they are passed in, so an undefined chart would
  // throw during the parent's render and blank the whole page.
  if (!chart) return null

  return (
    <ChartFrame chart={chart}>
      <BarChart data={chart.series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip
          formatter={(value) => [value, 'sessions']}
          labelFormatter={(label) => `Held for ${label}`}
        />
        <Bar dataKey="value" radius={[2, 2, 0, 0]}>
          {(chart.series ?? []).map((point) => (
            // The tail is the point, so it is the part that is coloured.
            <Cell
              key={point.bucket}
              fill={parseInt(point.bucket, 10) >= 8 ? '#d97706' : '#2563eb'}
            />
          ))}
        </Bar>
      </BarChart>
    </ChartFrame>
  )
}

/** Where confirmed damage actually happens, which no time series can show. */
export function DamageByRegion({ chart }) {
  const rows = chart?.by_region ?? []
  if (!rows.length) return null
  const max = Math.max(...rows.map((row) => row.count))

  return (
    <section className="chart">
      <header className="chart__head">
        <div>
          <h3>Confirmed damage by area</h3>
          <p className="chart__stats">
            {rows.reduce((total, row) => total + row.count, 0)} confirmed findings
          </p>
        </div>
      </header>

      <ul className="region-bars">
        {rows.map((row) => (
          <li key={row.region}>
            <span className="region-bars__label">{row.region.replace(/_/g, ' ')}</span>
            <span className="region-bars__track">
              <span
                className="region-bars__fill"
                style={{ width: `${Math.max(4, (row.count / max) * 100)}%` }}
              />
            </span>
            <span className="region-bars__count">{row.count}</span>
          </li>
        ))}
      </ul>

      <p className="chart__note">
        Confirmed findings only. A rejected finding was a false positive and is
        deliberately not counted as damage to the vehicle.
      </p>
    </section>
  )
}
