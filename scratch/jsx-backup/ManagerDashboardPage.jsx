/**
 * Valet Manager Dashboard.
 *
 * Read-only analytics over the whole operation: what came in, how long we held
 * it, how long retrievals took, what the security and damage picture looks like,
 * and the underlying records behind each figure.
 *
 * Three decisions shape this file:
 *
 * **One composed read, filtered server-side.** Eight metrics, five charts and
 * four tables come from a single call. Firing them separately would mean the
 * metric cards and the charts could be computed from different moments, and a
 * manager reconciling the two would find numbers that do not add up.
 *
 * **Charts refuse to draw when the data cannot support them.** The backend
 * sends `n` and `min_points` for every series and `ChartFrame` honours them. A
 * two-point trend is a coincidence with axes, and this screen would rather say
 * "not enough data" than imply a finding.
 *
 * **The report is the same endpoint.** Downloading CSV or JSON hits the same
 * query path the screen uses, so a filed report cannot contradict the dashboard
 * it came from. Exports are audited on the backend.
 *
 * There are no mutating actions here on purpose. Changing a custody state
 * belongs on the operator dashboard, behind its confirmation dialog and the
 * state machine that owns the transition.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertBanner, Badge, ErrorNotice, Panel, Spinner } from '../components/ui'
import {
  AlertsChart,
  CustodyDurationChart,
  DamageByRegion,
  DamageChart,
  RetrievalTimeChart,
  VehiclesPerDayChart,
} from '../components/manager/Charts'
import {
  ActiveVehiclesTable,
  AlertsTable,
  DamageTable,
  FilterBar,
  RecentTicketsTable,
} from '../components/manager/Tables'
import {
  defaultFilters,
  downloadManagerReport,
  fetchManagerDashboard,
  formatMetric,
} from '../services/managerService'

/** The metric cards, in the order a manager reads them: volume first, then
 *  what is outstanding, then how long things took. */
const METRIC_ORDER = [
  'vehicles_today',
  'active_sessions',
  'completed_sessions',
  'pending_pickups',
  'open_alerts',
  'potential_damage',
  'avg_retrieval_minutes',
  'avg_custody_hours',
]

export function ManagerDashboardPage() {
  const [filters, setFilters] = useState(defaultFilters)
  const [notice, setNotice] = useState(null)
  const [exporting, setExporting] = useState(null)

  const query = useQuery({
    queryKey: ['manager-dashboard', filters],
    queryFn: () => fetchManagerDashboard(filters),
    // Refetching on window focus would re-run eight aggregates for no reason;
    // this screen is read deliberately, not watched.
    refetchOnWindowFocus: false,
    // Keep the last good screen while new filters load, so the charts do not
    // blank and re-draw on every dropdown change.
    placeholderData: (previous) => previous,
    staleTime: 30_000,
  })

  const data = query.data
  const metrics = data?.metrics ?? {}
  const charts = data?.charts ?? {}
  const tables = data?.tables ?? {}

  const handleExport = async (format) => {
    setExporting(format)
    setNotice(null)
    try {
      const result = await downloadManagerReport(filters, format)
      setNotice({
        tone: 'info',
        title: `${format.toUpperCase()} report downloaded`,
        body: result.containsGeneratedHistory
          ? `${result.filename} — note that this dataset includes synthetic operational history, so the figures describe generated demo records rather than real valet operations.`
          : `${result.filename} — every figure comes from records created through the application.`,
      })
    } catch (error) {
      setNotice({
        tone: 'warning',
        title: 'Export failed',
        body: error?.message ?? 'The report could not be generated.',
      })
    } finally {
      setExporting(null)
    }
  }

  if (query.isPending) {
    return (
      <div className="page">
        <Spinner label="Loading manager analytics…" />
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="page">
        <ErrorNotice error={query.error} onRetry={() => query.refetch()} />
      </div>
    )
  }

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Manager Dashboard</h1>
          <p className="muted">
            Operations across {data.filters.date_from} to {data.filters.date_to}.
          </p>
        </div>
        <div className="page__actions">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => handleExport('csv')}
            disabled={Boolean(exporting)}
          >
            {exporting === 'csv' ? 'Preparing…' : 'Download CSV'}
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => handleExport('json')}
            disabled={Boolean(exporting)}
          >
            {exporting === 'json' ? 'Preparing…' : 'Download JSON'}
          </button>
        </div>
      </header>

      {/* Stated on the screen and repeated in the export. If the history was
          generated by a script, a reader is entitled to know before they treat
          ninety days of figures as an operating record. */}
      {data.provenance?.contains_generated_history && (
        <AlertBanner tone="warning" title="This dataset includes synthetic history">
          <p>{data.provenance.note}</p>
        </AlertBanner>
      )}

      {notice && (
        <AlertBanner tone={notice.tone} title={notice.title} onDismiss={() => setNotice(null)}>
          <p>{notice.body}</p>
        </AlertBanner>
      )}

      <FilterBar
        filters={filters}
        options={data.filter_options}
        onChange={setFilters}
        onReset={() => setFilters(defaultFilters())}
        busy={query.isFetching}
      />

      {/* ------------------------------ METRICS ---------------------------- */}
      <div className="metric-grid">
        {METRIC_ORDER.map((key) => {
          const metric = metrics[key]
          if (!metric) return null
          return (
            <div className="metric-card" key={key}>
              <p className="metric-card__label">{metric.label}</p>
              <p className="metric-card__value">{formatMetric(metric)}</p>
              <p className="metric-card__scope">
                {/* Which period this figure covers. Without it, a point-in-time
                    count and a monthly total sit side by side looking identical. */}
                {metric.windowed ? 'selected range' : 'right now'}
                {metric.n != null && ` · n=${metric.n}`}
              </p>
              {metric.note && (
                <p className="metric-card__note" title={metric.note}>
                  {metric.note}
                </p>
              )}
            </div>
          )
        })}
      </div>

      {/* ------------------------------ ALERTS ------------------------------ */}
      {/* Alerts sit directly under the KPIs: a manager who came here because
          something is wrong should not have to scroll past five charts to find
          which vehicle it was. */}
      <Panel
        title="Security alerts"
        subtitle="Most severe first, within the selected range"
        actions={<Badge tone="badge--muted">{tables.security_alerts?.length ?? 0} rows</Badge>}
      >
        <AlertsTable rows={tables.security_alerts} />
      </Panel>

      {/* ------------------------------ CHARTS ----------------------------- */}
      <div className="chart-grid">
        <VehiclesPerDayChart chart={charts.vehicles_per_day} />
        <RetrievalTimeChart chart={charts.retrieval_time} />
        <AlertsChart chart={charts.security_alerts} />
        <DamageChart chart={charts.damage_detections} />
        <CustodyDurationChart chart={charts.custody_duration} />
        <DamageByRegion chart={charts.damage_detections} />
      </div>

      {/* ------------------------------ REPORTS -----------------------------
          The remaining tables are the detail behind the export. Grouped last
          because they are a lookup, not a headline: a manager reads the KPIs and
          the alerts, and drills in here only when a figure needs explaining. */}
      <Panel
        title="Recent tickets"
        subtitle="Issued within the selected range, newest first"
        actions={<Badge tone="badge--muted">{tables.recent_tickets?.length ?? 0} rows</Badge>}
      >
        <RecentTicketsTable rows={tables.recent_tickets} />
      </Panel>

      <Panel
        title="Active vehicles"
        subtitle="In custody at this moment, not scoped to the date range"
        actions={<Badge tone="badge--muted">{tables.active_vehicles?.length ?? 0} rows</Badge>}
      >
        <ActiveVehiclesTable rows={tables.active_vehicles} />
      </Panel>

      <Panel
        title="Damage cases"
        subtitle="Unverified findings first — these still need a human decision"
        actions={<Badge tone="badge--muted">{tables.damage_cases?.length ?? 0} rows</Badge>}
      >
        <DamageTable rows={tables.damage_cases} />
      </Panel>

      <p className="dash-footnote">
        Retrieval and custody figures are measured from recorded timestamps, not
        predicted. Damage figures count a finding as damage only once a manager
        has confirmed it; unverified findings are labelled potential. Report
        downloads are recorded in the audit log.
      </p>
    </div>
  )
}

export default ManagerDashboardPage
