import { invoke } from '@tauri-apps/api/core'
import type { DesktopStatus } from './types'

let apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

export function isDesktopShell() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export function getApiBaseUrl() {
  return apiBaseUrl
}

export function setApiBaseUrl(nextBaseUrl: string) {
  apiBaseUrl = nextBaseUrl.replace(/\/$/, '')
}

// /health does no work, so anything slower than this is a backend that accepted
// the connection and then went quiet. The poll in App only schedules its next
// tick once this settles, so without a deadline that one socket stops the loop
// for good: no banner, no state change, buttons frozen where they were.
const HEALTH_TIMEOUT_MS = 5_000

async function browserStatus(): Promise<DesktopStatus> {
  const base = getApiBaseUrl()
  try {
    const response = await fetch(`${base}/health`, {
      signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
    })
    if (!response.ok) {
      throw new Error(`Backend health returned ${response.status}`)
    }
    await response.json()
    return {
      isDesktopShell: false,
      bootstrapState: 'Ready',
      runtimeVersion: 'browser-dev',
      backendUrl: base,
      logsPath: '',
      backendHealth: {
        healthy: true,
        detail: 'Backend reachable through browser dev workflow.',
      },
      statusMessage: 'Browser mode is active and the backend answered its health check.',
      devMode: true,
      lastError: null,
    }
  } catch (error) {
    return {
      isDesktopShell: false,
      bootstrapState: 'Broken',
      runtimeVersion: 'browser-dev',
      backendUrl: base,
      logsPath: '',
      backendHealth: {
        healthy: false,
        detail: 'Backend health check failed.',
      },
      statusMessage:
        'Desktop shell is not active and the local backend is not reachable. Start uvicorn in backend/ and the next poll picks it up.',
      devMode: true,
      lastError: error instanceof Error ? error.message : 'Unknown browser health check failure.',
    }
  }
}

export async function getDesktopStatus(): Promise<DesktopStatus> {
  if (!isDesktopShell()) {
    return browserStatus()
  }
  return invoke<DesktopStatus>('desktop_status')
}

export async function initializeDesktop(): Promise<DesktopStatus> {
  if (!isDesktopShell()) {
    return browserStatus()
  }
  return invoke<DesktopStatus>('initialize_desktop')
}

