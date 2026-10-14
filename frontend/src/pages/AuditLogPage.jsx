/**
 * Audit log viewer for managers and administrators.
 *
 * **The screen states that it cannot edit anything.** The backend registers no
 * write route for the trail and the `audit_logs` table rejects UPDATE and
 * DELETE at the database level. Rather than simply omitting edit controls —
 * which leaves a reader wondering whether they are hidden or absent — the page
 * says so, using the `mutable: false` flag the API returns. A log viewer that
 * is quietly read-only is less trustworthy than one that declares it.
 *
 * **Server-side everything.** Filters, paging and the manager/admin gate all
 * happen in the query. There is no client-side role check and no client-side
 * filtering: hiding rows that were already sent is cosmetic, and a viewer that
 * filtered in the browser would still have transferred other people's email and
 * IP addresses to a machine that should never have had them.
 *
 * **Filters come from the data.** The dropdowns are populated from
 * `/audit/filters`, which reads the distinct values actually present. A filter
 * offering an action that has never occurred is noise; one missing an action
 * that has is a bug.
 *
 * **The timeline is a separate mode.** The log answers "what happened recently,
 * across everything"; the timeline answers "what happened to this vehicle".
 * They are the same table read in opposite orders — newest-first for a
 * worklist, oldest-first for a narrative — so they are separate views rather
 * than one list with a sort toggle.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertBanner, Badge, ErrorNotice, Field, Panel, Spinner } from '../components/ui'
import CustodyTimeline from '../components/audit/CustodyTimeline'
import {
  actorLabel,
  fetchAuditFilters,
  fetchAuditLog,
  fetchCustodyTimeline,
  formatEntryTime,
  summariseEntry,
} from '../services/auditService'

const PAGE_SIZE = 50

const OUTCOME_TONE = {
  SUCCESS: 'badge--success',
  FAILURE: 'badge--danger',
  DENIED: 'badge--warning',
}

/** The empty filter set, as one object so "clear" is a single setState. */
const EMPTY_FILTERS = {
  action: '',
  entity_type: '',
  actor_id: '',
  custody_session_id: '',
  outcome: '',
  date_from: '',
  date_to: '',
  lifecycle_only: false,
}

function LogTable({ items }) {
  if (!items?.length) {
    return <p className="audit__empty">No entries match these filters.</p>
  }
  return (
    <div className="audit__table-wrap">
      <table className="audit__table">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Action</th>
            <th scope="col">Actor</th>
            <th scope="col">Entity</th>
            <th scope="col">Outcome</th>
            <th scope="col">IP</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {items.map((entry) => (
            <tr key={entry.id} className={`audit__row audit__row--${entry.outcome?.toLowerCase()}`}>
              <td className="audit__cell-time">
                <time dateTime={entry.occurred_at || undefined}>
                  {formatEntryTime(entry.occurred_at)}
                </time>
              </td>
              <td>
                <span className="audit__action">{entry.label || entry.action}</span>
                {entry.is_lifecycle_event && (
                  <span className="audit__lifecycle-flag" title="Part of the custody lifecycle">
                    ●
                  </span>
                )}
              </td>
              <td>{actorLabel(entry)}</td>
              <td className="mono">{entry.entity_type}</td>
              <td>
                <Badge tone={OUTCOME_TONE[entry.outcome] || 'badge--neutral'}>{entry.outcome}</Badge>
              </td>
              <td className="mono">{entry.ip_address || '—'}</td>
              <td className="audit__detail">{summariseEntry(entry)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function AuditLogPage() {
  const [mode, setMode] = useState('log')
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useState(1)
  const [custodyId, setCustodyId] = useState('')
  const [timelineId, setTimelineId] = useState('')

  const logQuery = useQuery({
    queryKey: ['audit', 'log', filters, page],
    queryFn: () =>
      fetchAuditLog({
        page,
        pageSize: PAGE_SIZE,
        action: filters.action || undefined,
        entityType: filters.entity_type || undefined,
        actorId: filters.actor_id || undefined,
        custodySessionId: filters.custody_session_id || undefined,
        outcome: filters.outcome || undefined,
        dateFrom: filters.date_from || undefined,
        dateTo: filters.date_to || undefined,
        lifecycleOnly: filters.lifecycle_only,
      }),
    enabled: mode === 'log',
  })

  const filtersQuery = useQuery({
    queryKey: ['audit', 'filters'],
    queryFn: fetchAuditFilters,
    enabled: mode === 'log',
  })

  const timelineQuery = useQuery({
    queryKey: ['audit', 'timeline', timelineId],
    queryFn: () => fetchCustodyTimeline(timelineId),
    enabled: mode === 'timeline' && Boolean(timelineId),
  })

  const data = logQuery.data
  const pagination = data?.pagination

  const setFilter = (key) => (event) => {
    const target = event.target
    const value = target.type === 'checkbox' ? target.checked : target.value
    setFilters((current) => ({ ...current, [key]: value }))
    // Any filter change invalidates the current page position.
    setPage(1)
  }

  const available = filtersQuery.data

  return (
    <div className="audit">
      <header className="audit__header">
        <div>
          <h1>Audit trail</h1>
          <p className="audit__lede">
            Every significant event in the platform, recorded append-only.
          </p>
        </div>
        <div className="audit__mode-switch" role="group" aria-label="View">
          <button
            type="button"
            className={mode === 'log' ? 'is-active' : ''}
            onClick={() => setMode('log')}
          >
            Log
          </button>
          <button
            type="button"
            className={mode === 'timeline' ? 'is-active' : ''}
            onClick={() => setMode('timeline')}
          >
            Custody timeline
          </button>
        </div>
      </header>

      <AlertBanner tone="info" title="This log cannot be edited">
        {data?.mutability_note ||
          'The audit trail is append-only. There is no update or delete endpoint, and the ' +
            'database rejects both at the trigger level.'}
      </AlertBanner>

      {mode === 'log' && (
        <>
          <Panel title="Filters" subtitle="Narrow the log. Every filter is applied server-side.">
            <div className="audit__filters">
              <Field label="Action">
                <select value={filters.action} onChange={setFilter('action')}>
                  <option value="">Any action</option>
                  {(available?.actions || []).map((action) => (
                    <option key={action} value={action}>
                      {action}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Entity type">
                <select value={filters.entity_type} onChange={setFilter('entity_type')}>
                  <option value="">Any entity</option>
                  {(available?.entity_types || []).map((entityType) => (
                    <option key={entityType} value={entityType}>
                      {entityType}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Outcome">
                <select value={filters.outcome} onChange={setFilter('outcome')}>
                  <option value="">Any outcome</option>
                  {(available?.outcomes || []).map((outcome) => (
                    <option key={outcome} value={outcome}>
                      {outcome}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Actor id" hint="Everything one user did.">
                <input
                  type="text"
                  value={filters.actor_id}
                  onChange={setFilter('actor_id')}
                  placeholder="uuid"
                />
              </Field>

              <Field label="Custody session id">
                <input
                  type="text"
                  value={filters.custody_session_id}
                  onChange={setFilter('custody_session_id')}
                  placeholder="uuid"
                />
              </Field>

              <Field label="From">
                <input type="date" value={filters.date_from} onChange={setFilter('date_from')} />
              </Field>

              <Field label="To">
                <input type="date" value={filters.date_to} onChange={setFilter('date_to')} />
              </Field>

              <Field label="Lifecycle events only" hint="The fourteen custody events.">
                <input
                  type="checkbox"
                  checked={filters.lifecycle_only}
                  onChange={setFilter('lifecycle_only')}
                />
              </Field>

              <div className="audit__filter-actions">
                <button
                  type="button"
                  onClick={() => {
                    setFilters(EMPTY_FILTERS)
                    setPage(1)
                  }}
                >
                  Clear filters
                </button>
              </div>
            </div>
          </Panel>

          <Panel
            title="Entries"
            subtitle={
              pagination
                ? `Newest first · page ${pagination.page} of ${pagination.pages || 1} · ${pagination.total} matching`
                : 'Newest first'
            }
          >
            {logQuery.isLoading && <Spinner label="Loading audit entries…" />}
            {logQuery.error && <ErrorNotice error={logQuery.error} onRetry={logQuery.refetch} />}
            {data && <LogTable items={data.items} />}

            {pagination && pagination.pages > 1 && (
              <nav className="audit__pager" aria-label="Pagination">
                <button
                  type="button"
                  disabled={pagination.page <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  Previous
                </button>
                <span>
                  {pagination.page} / {pagination.pages}
                </span>
                <button
                  type="button"
                  disabled={pagination.page >= pagination.pages}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </button>
              </nav>
            )}
          </Panel>
        </>
      )}

      {mode === 'timeline' && (
        <Panel
          title="Custody timeline"
          subtitle="One custody, oldest first, with the lifecycle gaps marked."
        >
          <div className="audit__timeline-picker">
            <Field label="Custody session id" hint="Found on the ticket or the operator screen.">
              <input
                type="text"
                value={custodyId}
                onChange={(event) => setCustodyId(event.target.value)}
                placeholder="uuid"
              />
            </Field>
            <button
              type="button"
              disabled={!custodyId.trim()}
              onClick={() => setTimelineId(custodyId.trim())}
            >
              Load timeline
            </button>
          </div>

          {!timelineId && (
            <p className="audit__empty">Enter a custody session id to load its timeline.</p>
          )}

          {timelineId && (
            <CustodyTimeline
              timeline={timelineQuery.data}
              isLoading={timelineQuery.isLoading}
              error={timelineQuery.error}
              onRetry={timelineQuery.refetch}
            />
          )}
        </Panel>
      )}
    </div>
  )
}

export default AuditLogPage
