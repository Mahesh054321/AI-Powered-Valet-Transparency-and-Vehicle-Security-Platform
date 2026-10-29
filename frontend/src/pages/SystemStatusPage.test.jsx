import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { SystemStatusPage } from './SystemStatusPage'
import { renderWithProviders } from '../test/utils.jsx'
import { apiGet } from '../services/apiClient'

/**
 * The page imports `apiGet` as a module binding, so spying on the namespace is
 * not enough - the module itself has to be mocked. Everything else in
 * `../api/client` is re-exported untouched.
 */
vi.mock('../services/apiClient', async (importActual) => {
  const actual = await importActual()
  return { ...actual, apiGet: vi.fn() }
})

const HEALTH = {
  status: 'up',
  service: 'valet-transparency-api',
  api_version: 'v1',
  environment: 'development',
  uptime_seconds: 42.5,
  server_time: new Date().toISOString(),
}

const READY = {
  status: 'ready',
  telemetry_source: 'simulated',
  checks: { database: { status: 'up', latency_ms: 12.4, server_version: '17.11' } },
}

function networkError() {
  const error = new Error('down')
  error.apiError = { code: 'NETWORK_ERROR', message: 'Cannot reach the API.' }
  return error
}

describe('SystemStatusPage', () => {
  beforeEach(() => {
    vi.mocked(apiGet).mockReset()
  })

  it('renders live API and database data from the backend', async () => {
    vi.mocked(apiGet).mockImplementation(async (path) =>
      path.endsWith('/health') ? HEALTH : READY,
    )

    renderWithProviders(<SystemStatusPage />)

    expect(await screen.findByText('valet-transparency-api')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByText('17.11')).toBeInTheDocument()
      expect(screen.getByText('12.4 ms')).toBeInTheDocument()
    })
    expect(screen.getAllByText('ready').length).toBeGreaterThan(0)
    expect(screen.getAllByText('simulated').length).toBeGreaterThan(0)
  })

  it('shows a recovery hint when the backend is unreachable', async () => {
    vi.mocked(apiGet).mockRejectedValue(networkError())

    renderWithProviders(<SystemStatusPage />)

    expect(await screen.findByText('Backend unreachable')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /retry/i }).length).toBeGreaterThan(0)
  })

  it('reports a degraded database rather than claiming everything is fine', async () => {
    vi.mocked(apiGet).mockImplementation(async (path) =>
      path.endsWith('/health')
        ? { ...HEALTH, uptime_seconds: 1 }
        : { status: 'degraded', telemetry_source: 'simulated', checks: { database: { status: 'down' } } },
    )

    renderWithProviders(<SystemStatusPage />)

    expect(await screen.findAllByText('down')).not.toHaveLength(0)
    expect(screen.getAllByText('degraded').length).toBeGreaterThan(0)
  })
})
