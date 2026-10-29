/**
 * Shared shell for the operator dashboard sections.
 *
 * Every section on this screen can be `ok`, `empty` or `unavailable`, and all
 * three have to render differently:
 *
 * - `empty` is a real answer from the database. A quiet morning is not an
 *   outage, and printing it in an error style would train operators to ignore
 *   the panel that later tells them something is wrong.
 * - `unavailable` means a sub-query failed. It is styled as a fault and says
 *   what could not be read, so one broken dependency degrades one panel rather
 *   than the whole operations screen.
 *
 * Centralising this means no section can quietly forget its empty state and
 * render as a blank box, which reads as "nothing wrong".
 */

import { Panel, Spinner } from '../ui'

export function SectionShell({ section, title, subtitle, actions, loading, children }) {
  const state = section?.state

  return (
    <Panel title={title} subtitle={subtitle} actions={actions}>
      {loading ? (
        <Spinner label={`Loading ${title.toLowerCase()}…`} />
      ) : state === 'unavailable' ? (
        <p className="section__fault" role="alert">
          {section?.reason ?? 'This section could not be read.'}
        </p>
      ) : state === 'ok' ? (
        children
      ) : (
        // Anything else — `empty`, a missing section, or a state this component
        // has not been told about. Falling through to `children` here would
        // render a blank box, and blank reads as "nothing wrong" rather than
        // "nothing known". An unrecognised state is treated as empty and says
        // so, which is the safe direction to be wrong in.
        <p className="section__empty">
          {section?.reason ??
            (state ? `Unrecognised section state: ${state}.` : 'Nothing to show.')}
        </p>
      )}
    </Panel>
  )
}

/** A count badge for a section header, so the numbers are scannable. */
export function SectionCount({ count, label }) {
  if (count == null) return null
  return (
    <span className="section__count">
      {count} {label}
    </span>
  )
}

export default SectionShell
