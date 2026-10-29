/**
 * Inline SVG brand mark.
 *
 * Embedded rather than loaded from a file so the UI renders identically with no
 * network access (offline demos, sandboxed previews).
 */

export function Logo({ size = 38, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="img"
      aria-label="Valet Transparency Platform"
      className={className}
    >
      <defs>
        <linearGradient id="vp-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="12" fill="url(#vp-logo)" />
      {/* Shield: custody is protected */}
      <path
        d="M24 9l11 4v9.5c0 7.2-4.6 12.6-11 14.5-6.4-1.9-11-7.3-11-14.5V13l11-4z"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      {/* Location pin: custody is observable */}
      <circle cx="24" cy="21" r="3.4" fill="#ffffff" />
      <path
        d="M24 25.5c0 0-5-4.2-5-8a5 5 0 0 1 10 0c0 3.8-5 8-5 8z"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinejoin="round"
        opacity="0.55"
      />
    </svg>
  )
}

export default Logo
