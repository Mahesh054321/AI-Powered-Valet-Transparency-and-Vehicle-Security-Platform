/**
 * React Query provider.
 *
 * Defaults are tuned for this project: short stale time so dashboards refresh
 * on navigation, one retry, and errors surfaced to the caller instead of being
 * thrown globally - every page renders its own error notice.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

export function QueryProvider({ children }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
          mutations: { retry: 0 },
        },
      }),
  )

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

export default QueryProvider
