/**
 * Single place where browser environment variables are read.
 *
 * Every value here comes from `import.meta.env`, which Vite populates from
 * frontend/.env. Only VITE_* variables exist at runtime, and all of them are
 * public by design - see frontend/.env.example.
 */

const env = import.meta.env

export const APP_CONFIG = {
  appName: env.VITE_APP_NAME || 'Valet Transparency Platform',
  apiBaseUrl: env.VITE_API_BASE_URL || '/api',
  mapTileUrl: env.VITE_MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  mapAttribution: env.VITE_MAP_ATTRIBUTION || '&copy; OpenStreetMap contributors',
  mapDefaultZoom: Number(env.VITE_MAP_DEFAULT_ZOOM || 17),
  telemetryLabel: env.VITE_TELEMETRY_LABEL || 'Simulated telemetry (demo)',
  mode: env.MODE,
  isProduction: env.PROD,
}

export default APP_CONFIG
