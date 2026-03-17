import type { BuildResponse, CompileResult, DesignBrief, PlanResponse, ReviseResponse, SemanticBuildPlan } from './types'
import { getApiBaseUrl } from './desktop'

async function postJson<T>(path: string, payload: object): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `Request failed with ${response.status}`)
  }

  return (await response.json()) as T
}

export function buildDesign(brief: DesignBrief): Promise<BuildResponse> {
  return postJson<BuildResponse>('/designs/build', { brief })
}

export function planDesign(brief: DesignBrief): Promise<PlanResponse> {
  return postJson<PlanResponse>('/designs/plan', brief)
}

export function compilePlan(plan: SemanticBuildPlan) {
  return postJson<CompileResult>('/designs/compile', { plan })
}

export function reviseDesign(designId: string, instruction: string): Promise<ReviseResponse> {
  return postJson<ReviseResponse>('/designs/revise', { design_id: designId, instruction })
}

export function artifactUrl(designId: string, kind: 'glb' | 'step_export' | 'stl') {
  return `${getApiBaseUrl()}/designs/${designId}/artifacts/${kind}`
}
