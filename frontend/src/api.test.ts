import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  actionDeadlineSeconds,
  buildDesign,
  compilePlan,
  planDesign,
  RequestCancelledError,
  RequestTimeoutError,
  reviseDesign,
} from './api'
import type { DesignBrief, SemanticBuildPlan } from './types'

// desktop.ts imports @tauri-apps/api, which wants a Tauri host to talk to. The
// base URL is the only thing api.ts takes from it.
vi.mock('./desktop', () => ({ getApiBaseUrl: () => 'http://backend.test' }))

const brief: DesignBrief = {
  prompt: 'a mug 86 mm across and 96 mm tall',
  units: 'mm',
  target_dims: { diameter: 86, height: 96 },
  required_features: ['handle'],
  style_notes: [],
  tolerances: null,
}

const plan: SemanticBuildPlan = {
  summary: 'A mug.',
  assumptions: [],
  parameters: {},
  steps: [],
}

/** A backend that answers immediately with this status and body. */
function answerWith(status: number, body: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body, { status })),
  )
}

/** A backend that accepts the request and then goes quiet. */
function neverAnswer() {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          // A real fetch rejects an aborted request with a DOMException rather
          // than with the reason it was handed, which is the case api.ts has to
          // translate back before the banner sees it.
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('The user aborted a request.', 'AbortError')),
          )
        }),
    ),
  )
}

/** The error a request rejected with. App renders its message in the banner. */
async function rejection(request: Promise<unknown>): Promise<Error> {
  try {
    await request
  } catch (error) {
    return error as Error
  }
  expect.fail('the request resolved, so there was no error to report')
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('error bodies', () => {
  it('reads a FastAPI detail string straight out', async () => {
    answerWith(404, JSON.stringify({ detail: 'Design not found' }))

    const error = await rejection(reviseDesign('gone', 'make the walls 5 mm'))

    expect(error.message).toBe('Design not found')
  })

  it('names the field a 422 is complaining about', async () => {
    answerWith(
      422,
      JSON.stringify({
        detail: [{ type: 'missing', loc: ['body', 'instruction'], msg: 'Field required' }],
      }),
    )

    const error = await rejection(planDesign(brief))

    expect(error.message).toBe('body.instruction: Field required')
  })

  it('passes a proxy error page through, since it is not JSON at all', async () => {
    const page = '<html><head><title>502 Bad Gateway</title></head><body>502</body></html>'
    answerWith(502, page)

    const error = await rejection(buildDesign(brief))

    expect(error.message).toBe(page)
  })

  it('falls back to the status when there is no body', async () => {
    answerWith(500, '')

    const error = await rejection(compilePlan(plan))

    expect(error.message).toBe('Request failed with 500')
  })
})

describe('cancelling', () => {
  it('sends nothing when the signal is already aborted', async () => {
    const fetchStub = vi.fn()
    vi.stubGlobal('fetch', fetchStub)
    const controller = new AbortController()
    controller.abort()

    const error = await rejection(planDesign(brief, controller.signal))

    expect(error).toBeInstanceOf(RequestCancelledError)
    expect(fetchStub).not.toHaveBeenCalled()
  })

  it('reports a cancel mid-flight as a cancel, not as the fetch AbortError', async () => {
    neverAnswer()
    const controller = new AbortController()
    const failure = rejection(buildDesign(brief, controller.signal))

    controller.abort()

    expect(await failure).toBeInstanceOf(RequestCancelledError)
  })
})

describe('deadlines', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  /** Run a request against a silent backend until its deadline expires. */
  async function expireDeadline(start: () => Promise<unknown>) {
    neverAnswer()
    const startedAt = Date.now()
    const failure = rejection(start())
    await vi.advanceTimersToNextTimerAsync()
    return { error: await failure, elapsedMs: Date.now() - startedAt }
  }

  it('gives up on a silent backend and names the path and the deadline', async () => {
    const { error, elapsedMs } = await expireDeadline(() => buildDesign(brief))

    expect(error).toBeInstanceOf(RequestTimeoutError)
    expect(error.message).toBe(
      '/designs/build did not answer within 300 s. The backend may still be working on it, ' +
        'so check its log before retrying.',
    )
    expect(elapsedMs).toBe(actionDeadlineSeconds.building * 1000)
  })

  it('gives a revision the same budget the status card quotes for it', async () => {
    const { elapsedMs } = await expireDeadline(() => reviseDesign('d1', 'make the walls 5 mm'))

    expect(elapsedMs).toBe(actionDeadlineSeconds.revising * 1000)
  })

  it('quotes planning as the plan and compile deadlines added up', async () => {
    // The card says "giving up by 230s" for a plan because planning is two
    // requests with a deadline each, not one 230 s timer. If that stops being
    // true the card starts quoting a number nothing will honour.
    const planning = await expireDeadline(() => planDesign(brief))
    const compiling = await expireDeadline(() => compilePlan(plan))

    expect(planning.elapsedMs + compiling.elapsedMs).toBe(actionDeadlineSeconds.planning * 1000)
  })
})
