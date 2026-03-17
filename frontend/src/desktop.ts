import { invoke } from '@tauri-apps/api/core'
import type { DesktopStatus } from './types'

let apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? ''

function isDesktopShell() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export function getApiBaseUrl() {
  return apiBaseUrl
}

export function setApiBaseUrl(nextBaseUrl: string) {
  apiBaseUrl = nextBaseUrl.replace(/\/$/, '')
}

async function browserStatus(): Promise<DesktopStatus> {
  const base = getApiBaseUrl()
  try {
    const response = await fetch(`${base}/health`)
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
      statusMessage: 'Browser mode is active. Start the FastAPI backend manually for local web testing.',
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
      statusMessage: 'Desktop shell is not active and the local backend is not reachable.',
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

