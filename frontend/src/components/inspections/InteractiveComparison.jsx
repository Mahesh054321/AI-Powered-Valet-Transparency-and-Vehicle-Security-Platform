import React, { useState, useRef, useCallback } from 'react'
import { angleLabel } from '../../utils/constants'
import { Badge } from '../ui'

export function InteractiveComparison({
  comparison,
  allComparisons = [],
  onSelectAngle,
  findings = [],
  onFindingClick,
}) {
  const [viewMode, setViewMode] = useState('slider') // 'slider' | 'side-by-side' | 'overlay' | 'composite'
  const [sliderPos, setSliderPos] = useState(50)
  const [isDragging, setIsDragging] = useState(false)
  const [showHeatmap, setShowHeatmap] = useState(true)
  const [heatmapOpacity, setHeatmapOpacity] = useState(65)
  const [showBoxes, setShowBoxes] = useState(true)
  const [selectedBoxIndex, setSelectedBoxIndex] = useState(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const containerRef = useRef(null)

  const handlePointerDown = useCallback(() => {
    setIsDragging(true)
  }, [])

  const handlePointerUp = useCallback(() => {
    setIsDragging(false)
  }, [])

  const handlePointerMove = useCallback(
    (e) => {
      if (!isDragging || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const clientX = e.touches ? e.touches[0].clientX : e.clientX
      const offsetX = clientX - rect.left
      const percent = Math.min(100, Math.max(0, (offsetX / rect.width) * 100))
      setSliderPos(Math.round(percent))
    },
    [isDragging]
  )

  if (!comparison) {
    return (
      <div className="panel muted" style={{ textAlign: 'center', padding: '2rem' }}>
        No comparison data available for this view.
      </div>
    )
  }

  const { angle, drop_off_url, pickup_url, comparison_url, overlay_url, alignment, damage_detected } = comparison
  const angleFindings = findings.filter(
    (f) => (f.angle || f.algorithm_params?.angle) === angle || findings.length <= 3
  )

  return (
    <div className={`comparison-viewer ${isFullscreen ? 'comparison-viewer--fullscreen' : ''}`}>
      {/* Angle Selector Tabs */}
      {allComparisons.length > 1 && (
        <div className="comparison-viewer__tabs" role="tablist" aria-label="Camera viewpoints">
          {allComparisons.map((c) => {
            const isSelected = c.angle === angle
            return (
              <button
                key={c.angle}
                type="button"
                role="tab"
                aria-selected={isSelected}
                className={`btn btn--tab ${isSelected ? 'btn--tab-active' : ''}`}
                onClick={() => onSelectAngle && onSelectAngle(c.angle)}
              >
                <span>{angleLabel(c.angle)}</span>
                {c.damage_detected ? (
                  <span className="badge badge--danger badge--dot" title="Potential damage detected" />
                ) : (
                  <span className="badge badge--success badge--dot" title="Clean inspection" />
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* Control Bar */}
      <div className="comparison-viewer__controls">
        <div className="btn-group" role="group" aria-label="View Mode">
          <button
            type="button"
            className={`btn btn--sm ${viewMode === 'slider' ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => setViewMode('slider')}
          >
            ↔ Before/After Slider
          </button>
          <button
            type="button"
            className={`btn btn--sm ${viewMode === 'side-by-side' ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => setViewMode('side-by-side')}
          >
            ⧉ Side-by-Side
          </button>
          {overlay_url && (
            <button
              type="button"
              className={`btn btn--sm ${viewMode === 'overlay' ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => setViewMode('overlay')}
            >
              ◎ Difference Heatmap
            </button>
          )}
          {comparison_url && (
            <button
              type="button"
              className={`btn btn--sm ${viewMode === 'composite' ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => setViewMode('composite')}
            >
              ▦ Full 3-Panel
            </button>
          )}
        </div>

        <div className="comparison-viewer__actions">
          {viewMode === 'overlay' && (
            <div className="comparison-viewer__slider-control">
              <label htmlFor="heatmap-opacity" className="label--inline">
                Heatmap: {heatmapOpacity}%
              </label>
              <input
                id="heatmap-opacity"
                type="range"
                min="0"
                max="100"
                value={heatmapOpacity}
                onChange={(e) => setHeatmapOpacity(Number(e.target.value))}
                className="range-input"
              />
            </div>
          )}

          <label className="checkbox-toggle">
            <input
              type="checkbox"
              checked={showBoxes}
              onChange={(e) => setShowBoxes(e.target.checked)}
            />
            <span>Show Findings ({angleFindings.length})</span>
          </label>

          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => setIsFullscreen(!isFullscreen)}
            title={isFullscreen ? 'Exit Fullscreen' : 'View Fullscreen'}
          >
            {isFullscreen ? '✕ Exit' : '⛶ Fullscreen'}
          </button>
        </div>
      </div>

      {/* Viewport Renderers */}
      <div className="comparison-viewer__stage">
        {/* MODE 1: Interactive Draggable Wipe Slider */}
        {viewMode === 'slider' && (
          <div
            ref={containerRef}
            className="slider-stage"
            onPointerDown={handlePointerDown}
            onPointerUp={handlePointerUp}
            onPointerMove={handlePointerMove}
            onPointerLeave={handlePointerUp}
            onTouchMove={handlePointerMove}
            onTouchEnd={handlePointerUp}
          >
            {/* Background: Drop-off Baseline */}
            <div className="slider-stage__layer slider-stage__before">
              <img
                src={drop_off_url || comparison_url}
                alt={`Drop-off baseline for ${angleLabel(angle)}`}
                className="slider-stage__img"
                draggable={false}
              />
              <span className="slider-stage__badge slider-stage__badge--before">
                Drop-off (Baseline)
              </span>
            </div>

            {/* Foreground: Pickup Return clipped to sliderPos */}
            <div
              className="slider-stage__layer slider-stage__after"
              style={{ clipPath: `inset(0 0 0 ${sliderPos}%)` }}
            >
              <img
                src={pickup_url || comparison_url}
                alt={`Pickup return for ${angleLabel(angle)}`}
                className="slider-stage__img"
                draggable={false}
              />
              <span className="slider-stage__badge slider-stage__badge--after">
                Pickup (Return)
              </span>

              {/* Bounding box annotations on pickup */}
              {showBoxes && (
                <svg className="slider-stage__annotations" viewBox="0 0 100 100" preserveAspectRatio="none">
                  {angleFindings.map((finding, idx) => {
                    const box = finding.bounding_box || {}
                    const x = (box.x || 0) * 100
                    const y = (box.y || 0) * 100
                    const w = (box.width || 0) * 100
                    const h = (box.height || 0) * 100
                    const isSelected = selectedBoxIndex === idx

                    return (
                      <g
                        key={finding.id || idx}
                        className={`annotation-box ${isSelected ? 'annotation-box--active' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedBoxIndex(idx)
                          if (onFindingClick) onFindingClick(finding)
                        }}
                      >
                        <rect
                          x={x}
                          y={y}
                          width={w}
                          height={h}
                          rx="1"
                          className="annotation-box__rect"
                        />
                        <text x={x} y={Math.max(3, y - 2)} className="annotation-box__label">
                          #{idx + 1} {finding.damage_type || 'CHANGE'}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              )}
            </div>

            {/* Draggable Divider Handle */}
            <div
              className="slider-stage__handle"
              style={{ left: `${sliderPos}%` }}
              role="slider"
              aria-label="Comparison slider position"
              aria-valuenow={sliderPos}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="slider-stage__line" />
              <div className="slider-stage__knob">
                <span>◀ ▶</span>
              </div>
            </div>
          </div>
        )}

        {/* MODE 2: Side-by-Side View */}
        {viewMode === 'side-by-side' && (
          <div className="side-by-side-stage">
            <div className="side-by-side-panel">
              <div className="side-by-side-header">
                <span className="label">Drop-off Baseline</span>
                <span className="muted">{angleLabel(angle)}</span>
              </div>
              <div className="side-by-side-frame">
                <img
                  src={drop_off_url || comparison_url}
                  alt={`Drop-off baseline for ${angleLabel(angle)}`}
                  className="responsive-img"
                />
              </div>
            </div>

            <div className="side-by-side-panel">
              <div className="side-by-side-header">
                <span className="label">Pickup Return</span>
                {damage_detected ? (
                  <Badge tone="badge--danger">Potential Change</Badge>
                ) : (
                  <Badge tone="badge--success">No Change</Badge>
                )}
              </div>
              <div className="side-by-side-frame" style={{ position: 'relative' }}>
                <img
                  src={pickup_url || comparison_url}
                  alt={`Pickup return for ${angleLabel(angle)}`}
                  className="responsive-img"
                />
                {showBoxes && (
                  <svg className="slider-stage__annotations" viewBox="0 0 100 100" preserveAspectRatio="none">
                    {angleFindings.map((finding, idx) => {
                      const box = finding.bounding_box || {}
                      return (
                        <g
                          key={finding.id || idx}
                          className="annotation-box"
                          onClick={() => onFindingClick && onFindingClick(finding)}
                        >
                          <rect
                            x={(box.x || 0) * 100}
                            y={(box.y || 0) * 100}
                            width={(box.width || 0) * 100}
                            height={(box.height || 0) * 100}
                            className="annotation-box__rect"
                          />
                        </g>
                      )
                    })}
                  </svg>
                )}
              </div>
            </div>
          </div>
        )}

        {/* MODE 3: Difference Heatmap Overlay */}
        {viewMode === 'overlay' && (
          <div className="overlay-stage">
            <div className="overlay-stage__frame">
              <img
                src={pickup_url || drop_off_url}
                alt={`Base aligned pickup frame for ${angleLabel(angle)}`}
                className="overlay-stage__base"
              />
              {overlay_url && (
                <img
                  src={overlay_url}
                  alt="Difference Heatmap Overlay"
                  className="overlay-stage__heat"
                  style={{ opacity: heatmapOpacity / 100 }}
                />
              )}
              {showBoxes && (
                <svg className="slider-stage__annotations" viewBox="0 0 100 100" preserveAspectRatio="none">
                  {angleFindings.map((finding, idx) => {
                    const box = finding.bounding_box || {}
                    return (
                      <rect
                        key={finding.id || idx}
                        x={(box.x || 0) * 100}
                        y={(box.y || 0) * 100}
                        width={(box.width || 0) * 100}
                        height={(box.height || 0) * 100}
                        className="annotation-box__rect"
                      />
                    )
                  })}
                </svg>
              )}
            </div>
            <div className="overlay-stage__legend">
              <span className="legend-chip legend-chip--heat">Color Spectrum = OpenCV Differencing Heatmap</span>
              <span className="legend-chip legend-chip--box">Yellow Boxes = Candidate Damage Regions</span>
            </div>
          </div>
        )}

        {/* MODE 4: Full 3-Panel Composite */}
        {viewMode === 'composite' && (
          <div className="composite-stage">
            <img
              src={comparison_url}
              alt={`Full three-panel comparison for ${angleLabel(angle)}`}
              className="composite-img"
            />
          </div>
        )}
      </div>

      {/* Alignment Quality & Diagnostics Bar */}
      <div className="comparison-viewer__footer">
        <div className="alignment-stat">
          <span className="muted">Registration Quality:</span>{' '}
          <strong>
            {alignment
              ? `${(alignment.quality * 100).toFixed(0)}% (${alignment.matches || 0} features matched)`
              : 'Feature-based affine alignment'}
          </strong>
        </div>
        <div className="viewpoint-stat">
          <span className="muted">Camera Viewpoint:</span> <strong>{angleLabel(angle)}</strong>
        </div>
        <div className="status-stat">
          <span className="muted">Detection Status:</span>{' '}
          {damage_detected ? (
            <Badge tone="badge--warning">POTENTIAL DAMAGE DETECTED</Badge>
          ) : (
            <Badge tone="badge--success">NO SIGNIFICANT CHANGE DETECTED</Badge>
          )}
        </div>
      </div>
    </div>
  )
}

export default InteractiveComparison
