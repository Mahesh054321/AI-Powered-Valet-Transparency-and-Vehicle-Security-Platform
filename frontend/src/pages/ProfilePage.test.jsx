import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProfilePage } from './ProfilePage'

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: {
      id: 'user-123',
      email: 'customer.test@example.com',
      full_name: 'Test Customer',
      role: 'CUSTOMER',
      phone_number: '+1 555-0199',
    },
    role: 'CUSTOMER',
  }),
  useSignOut: () => vi.fn(),
}))

vi.mock('../context/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}))

describe('ProfilePage', () => {
  it('renders user details and role badges', () => {
    render(<ProfilePage />)
    expect(screen.getByText('Test Customer')).toBeInTheDocument()
    expect(screen.getByText('customer.test@example.com')).toBeInTheDocument()
    expect(screen.getAllByText('Customer').length).toBeGreaterThan(0)
  })

  it('toggles mobile GPS sharing setting', () => {
    render(<ProfilePage />)
    const toggle = screen.getByLabelText('Toggle Mobile GPS Sharing')
    expect(toggle).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(localStorage.getItem('valet_mobile_gps_sharing')).toBeDefined()
  })
})
