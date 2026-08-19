import type { BuildResponse, CompileResult, DesignBrief, PlanResponse, ReviseResponse, SemanticBuildPlan } from './types'
import { getApiBaseUrl } from './desktop'

// Deadlines mirror the backend's own budgets in backend/app/core/settings.py. Planning
// waits on the local model for up to ollama_timeout_seconds (180 s). A build plans and
// then runs up to three executor attempts at default_executor_timeout_seconds (30 s)
// each, so around 270 s is legitimate and a revision that rebuilds costs the same. The
// margin on top covers process startup; past it the backend is not coming back, and the
// UI should say so instead of sitting on a "building" pill forever.
const COMPILE_TIMEOUT_MS = 20_000
const PLAN_TIMEOUT_MS = 210_000
const BUILD_TIMEOUT_MS = 300_000
const REVISE_TIMEOUT_MS = 300_000

/**
 * The longest each user-facing action can run, for the UI to quote. Planning is
 * two requests with a deadline each, so 230 is a ceiling and not one timer: a
 * backend that hangs on /designs/plan gives up at 210. The card says "by".
 */
export const actionDeadlineSeconds = {
  planning: (PLAN_TIMEOUT_MS + COMPILE_TIMEOUT_MS) / 1000,
  building: BUILD_TIMEOUT_MS / 1000,
  revising: REVISE_TIMEOUT_MS / 1000,
}

/** A request that outlived its deadline. */
export class RequestTimeoutError extends Error {
  constructor(path: string, timeoutMs: number) {
    super(
      `${path} did not answer within ${Math.round(timeoutMs / 1000)} s. ` +
        'The backend may still be working on it, so check its log before retrying.',
    )
    this.name = 'RequestTimeoutError'
  }
}

/** A request the app abandoned itself: the user cancelled, or the view went away. */
export class RequestCancelledError extends Error {
  constructor() {
    super('Request cancelled.')
    this.name = 'RequestCancelledError'
  }
}

/** FastAPI reports every failure as {"detail": ...}; anything else reaches the user raw. */
async function readErrorMessage(response: Response): Promise<string> {
  const body = (await response.text()).trim()
  if (!body) {
    return `Request failed with ${response.status}`
  }

  let detail: unknown
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail
  } catch {
    // Not JSON: a proxy error page, or the dev server answering for a dead backend.
    return body
  }

  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    // A 422 body is pydantic's list of {loc, msg, type} entries.
    return detail
      .map((entry) => {
        const { loc, msg } = entry as { loc?: unknown[]; msg?: string }
        return loc?.length ? `${loc.join('.')}: ${msg ?? 'invalid'}` : (msg ?? 'invalid')
      })
      .join('; ')
  }
  return body
}

async function postJson<T>(
  path: string,
  payload: object,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted) {
    throw new RequestCancelledError()
  }

  const controller = new AbortController()
  const cancel = () => controller.abort(new RequestCancelledError())
  signal?.addEventListener('abort', cancel)
  const deadline = window.setTimeout(
    () => controller.abort(new RequestTimeoutError(path, timeoutMs)),
    timeoutMs,
  )

  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    })
    if (!response.ok) {
      throw new Error(await readErrorMessage(response))
    }
    return (await response.json()) as T
  } catch (caught) {
    // An aborted fetch rejects with whatever we handed abort(), but a body read that
    // loses its stream mid-flight does not, so read the reason off the signal.
    throw controller.signal.aborted ? controller.signal.reason : caught
  } finally {
    window.clearTimeout(deadline)
    signal?.removeEventListener('abort', cancel)
  }
}

export function buildDesign(brief: DesignBrief, signal?: AbortSignal): Promise<BuildResponse> {
  return postJson<BuildResponse>('/designs/build', { brief }, BUILD_TIMEOUT_MS, signal)
}

export function planDesign(brief: DesignBrief, signal?: AbortSignal): Promise<PlanResponse> {
  return postJson<PlanResponse>('/designs/plan', brief, PLAN_TIMEOUT_MS, signal)
}

export function compilePlan(plan: SemanticBuildPlan, signal?: AbortSignal) {
  return postJson<CompileResult>('/designs/compile', { plan }, COMPILE_TIMEOUT_MS, signal)
}

export function reviseDesign(
  designId: string,
  instruction: string,
  signal?: AbortSignal,
): Promise<ReviseResponse> {
  return postJson<ReviseResponse>(
    '/designs/revise',
    { design_id: designId, instruction },
    REVISE_TIMEOUT_MS,
    signal,
  )
}

export function artifactUrl(designId: string, kind: 'glb' | 'step_export' | 'stl') {
  return `${getApiBaseUrl()}/designs/${designId}/artifacts/${kind}`
}
