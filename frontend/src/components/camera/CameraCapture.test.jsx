import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CameraCapture } from './CameraCapture'

const oldMediaDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices')

beforeEach(() => {
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined })
})

afterEach(() => {
  if (oldMediaDevices) Object.defineProperty(navigator, 'mediaDevices', oldMediaDevices)
  else delete navigator.mediaDevices
  vi.restoreAllMocks()
})

describe('CameraCapture', () => {
  it('keeps phone-camera fallback available when live camera is unsupported', async () => {
    const user = userEvent.setup()
    const onCapture = vi.fn()
    render(<CameraCapture onCapture={onCapture} />)

    await user.click(screen.getByRole('button', { name: 'Capture with Camera' }))
    expect(await screen.findByRole('dialog', { name: /take a vehicle photo/i })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/not supported/i)

    const file = new File(['JPEG'], 'phone.jpg', { type: 'image/jpeg' })
    await user.upload(screen.getByLabelText('Take photo with phone camera'), file)

    expect(onCapture).toHaveBeenCalledWith(file)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('captures a JPEG and stops webcam tracks after photo is taken', async () => {
    const user = userEvent.setup()
    const stop = vi.fn()
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true, value: { getUserMedia },
    })
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() })
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((cb) => cb(new Blob(['jpeg'], { type: 'image/jpeg' })))
    const onCapture = vi.fn()
    render(<CameraCapture onCapture={onCapture} filenamePrefix="test-front" />)

    await user.click(screen.getByRole('button', { name: 'Capture with Camera' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({
      audio: false, video: { facingMode: { ideal: 'environment' } },
    }))

    const video = screen.getByLabelText('Live camera preview')
    Object.defineProperty(video, 'videoWidth', { configurable: true, value: 1280 })
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 720 })
    fireEvent.loadedMetadata(video)
    await user.click(screen.getByRole('button', { name: 'Take Photo' }))

    expect(onCapture).toHaveBeenCalledTimes(1)
    const file = onCapture.mock.calls[0][0]
    expect(file).toBeInstanceOf(File)
    expect(file.type).toBe('image/jpeg')
    expect(file.name).toMatch(/^test-front-.*\.jpg$/)
    expect(stop).toHaveBeenCalled()
  })

  it('stops the camera when the operator closes the preview', async () => {
    const user = userEvent.setup()
    const stop = vi.fn()
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }) },
    })
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    render(<CameraCapture onCapture={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Capture with Camera' }))
    await waitFor(() => expect(screen.getByLabelText('Live camera preview').srcObject).toBeTruthy())
    await user.click(screen.getByRole('button', { name: 'Close camera' }))

    expect(stop).toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
