/**
 * Status and provenance of the prototype security classifier.
 *
 * The tracking dashboard shows what the rules measured. This panel shows what
 * the classifier is - and, just as importantly, what it is not. It exists so
 * that nobody reading the dashboard has to take a badge at face value: the
 * version, the training metrics, the confusion matrix and the synthetic-data
 * disclaimer are all on screen together.
 *
 * An untrained deployment is rendered as an ordinary state with the command that
 * would fix it, not as an error. Rules-only is a supported way to run this
 * platform, and a red banner would wrongly suggest the security module is
 * broken when it is merely untrained.
 */

import { Badge, Panel } from '../ui'
import { ML_PROTOTYPE_NOTICE } from '../../utils/constants'

function formatPercent(value) {
  if (value == null) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

/**
 * @param {object} props
 * @param {object|null} props.info    The `/v1/ml/model` response payload.
 * @param {boolean} [props.loading]
 * @param {object} [props.error]      Normalised error from the query.
 */
export function SecurityModelPanel({ info = null, loading = false, error = null }) {
  if (loading) {
    return (
      <Panel title="Security classifier" subtitle="Loading model status…">
        <p className="muted">Reading the trained artifact…</p>
      </Panel>
    )
  }

  if (error) {
    return (
      <Panel title="Security classifier" subtitle="Status unavailable">
        <p className="muted">
          The model status could not be loaded. Rule-based geofence checks are
          unaffected and continue to run on every fix.
        </p>
      </Panel>
    )
  }

  if (!info) {
    return (
      <Panel title="Security classifier" subtitle="Not loaded">
        <p className="muted">
          No classifier is configured. Every fix is classified by the rule
          engine alone, which is the behaviour this platform shipped and is
          fully supported.
        </p>
      </Panel>
    )
  }

  const metrics = info.metrics ?? null
  const matrix = metrics?.confusion_matrix ?? null

  return (
    <Panel
      title="Security classifier"
      subtitle={info.model_version ? `Prototype · ${info.model_version}` : 'Prototype'}
      actions={
        <Badge tone={info.available ? 'badge--success' : 'badge--muted'}>
          {info.available ? 'Loaded' : 'Not trained'}
        </Badge>
      }
    >
      {!info.available ? (
        <>
          <p className="muted">{info.reason ?? 'No model artifact is present.'}</p>
          <p className="muted">
            Run <code className="mono">python utils/scripts/train_security_model.py</code> from
            the <code className="mono">backend</code> directory to train one. Until then every
            verdict comes from the rule engine.
          </p>
        </>
      ) : (
        <>
          <dl className="ml-panel__meta">
            <div>
              <dt>Algorithm</dt>
              <dd className="mono">{info.algorithm}</dd>
            </div>
            <div>
              <dt>Escalates only above</dt>
              <dd>{formatPercent(info.escalation_threshold)}</dd>
            </div>
            <div>
              <dt>Features</dt>
              <dd>{info.feature_columns?.length ?? 0}</dd>
            </div>
          </dl>

          {metrics && (
            <dl className="ml-panel__metrics">
              <div>
                <dt>Accuracy</dt>
                <dd>{formatPercent(metrics.accuracy)}</dd>
              </div>
              <div>
                <dt>Precision</dt>
                <dd>{formatPercent(metrics.precision)}</dd>
              </div>
              <div>
                <dt>Recall</dt>
                <dd>{formatPercent(metrics.recall)}</dd>
              </div>
              <div>
                <dt>F1 score</dt>
                <dd>{formatPercent(metrics.f1)}</dd>
              </div>
              <div>
                <dt>Baseline</dt>
                <dd>
                  {formatPercent(metrics.baseline_accuracy)}
                  <span className="muted"> always answering “normal”</span>
                </dd>
              </div>
            </dl>
          )}

          {matrix?.matrix && (
            <div className="ml-panel__matrix">
              <p className="muted">{matrix.reading ?? 'Rows: true label. Columns: predicted.'}</p>
              <table className="ml-panel__matrix-table">
                <thead>
                  <tr>
                    <th scope="col">true ↓ / predicted →</th>
                    {(matrix.labels ?? []).map((label) => (
                      <th key={label} scope="col">
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matrix.matrix.map((row, rowIndex) => (
                    <tr key={matrix.labels?.[rowIndex] ?? rowIndex}>
                      <th scope="row">{matrix.labels?.[rowIndex] ?? rowIndex}</th>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="mono">
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* The numbers above describe how well the model recovers a
              hand-written distinction in synthetic data. Without this line
              attached to them, they read as evidence about real vehicles. */}
          <p className="ml-panel__notice">
            {info.disclaimer ?? ML_PROTOTYPE_NOTICE}
          </p>
          <p className="muted">
            It may only raise a fix&apos;s verdict, never lower it — a geofence
            breach always raises its alert regardless of what this model
            concludes.
          </p>
        </>
      )}
    </Panel>
  )
}

export default SecurityModelPanel
