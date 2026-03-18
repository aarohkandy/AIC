export type Units = 'mm' | 'cm' | 'in'
export type BootstrapState = 'NotInstalled' | 'Installing' | 'Ready' | 'Broken'

export type TargetDimensions = {
  width?: number | null
  depth?: number | null
  height?: number | null
  diameter?: number | null
}

export type DesignBrief = {
  prompt: string
  units: Units
  target_dims: TargetDimensions
  required_features: string[]
  style_notes: string[]
  tolerances?: Record<string, number> | null
}

export type SemanticStep = {
  id: string
  intent: string
  primitive_or_macro: string
  workplane: string
  location_notes: string[]
  size_notes: string[]
  sketch_constraints: string[]
  manual_instructions: string[]
  parameters: Record<string, string | number | boolean>
  depends_on: string[]
  postcondition: string
}

export type SemanticBuildPlan = {
  summary: string
  assumptions: string[]
  parameters: Record<string, string | number | boolean>
  steps: SemanticStep[]
}

export type EditableRegion = {
  step_id: string
  label: string
  start_line: number
  end_line: number
}

export type CompileResult = {
  language: 'cadquery_py'
  source: string
  editable_regions: EditableRegion[]
  whitelist_findings: { severity: 'info' | 'warning' | 'error'; message: string }[]
  diagnostics: { level: 'info' | 'warning' | 'error'; code: string; message: string }[]
}

export type ArtifactPaths = {
  step_path?: string | null
  glb_path?: string | null
  step_export_path?: string | null
  source_path?: string | null
  stl_path?: string | null
}

export type BuildResult = {
  status: 'succeeded' | 'failed' | 'needs_confirmation'
  artifacts: ArtifactPaths
  metrics: {
    volume?: number | null
    bounding_box: Record<string, number>
    attempt_latency_ms?: number | null
    planning_risk_score: number
    token_usage: Record<string, number>
  }
  validation: {
    status: 'passed' | 'failed' | 'skipped'
    checks: Record<string, string | number | boolean>
  }
  attempts_used: number
  cache_hits: number
  failure?: {
    failure_type: string
    failed_step_id?: string | null
    root_cause_step_id?: string | null
    message: string
    next_action: string
    attribution_basis: string
  } | null
}

export type ModelCallRecord = {
  model: string
  provider: string
  input_tokens: number
  output_tokens: number
  path: 'hosted' | 'local'
}

export type BuildResponse = {
  design_id: string
  brief: DesignBrief
  plan: SemanticBuildPlan
  compile: CompileResult
  build: BuildResult
  model_call: ModelCallRecord
  warnings: string[]
}

export type PlanResponse = {
  design_id: string
  brief: DesignBrief
  plan: SemanticBuildPlan
  planning_risk_score: number
  planner_path: 'hosted' | 'local'
  model_call: ModelCallRecord
  warnings: string[]
}

export type ReviseResponse = {
  design_id: string
  revision: {
    operation: 'update_parameter' | 'topology_change' | 'unknown'
    targets: string[]
    constraints: string[]
    confidence_score: number
    confidence_evidence: string[]
  }
  patch?: {
    reason: string
    target_step_ids: string[]
    parameter_updates: Record<string, string | number | boolean>
    topology_change: boolean
  } | null
  plan: SemanticBuildPlan
  compile?: CompileResult | null
  build?: BuildResult | null
  warnings: string[]
}

export type DesktopStatus = {
  isDesktopShell: boolean
  bootstrapState: BootstrapState
  runtimeVersion: string
  backendUrl: string
  logsPath: string
  backendHealth: {
    healthy: boolean
    detail: string
  }
  statusMessage: string
  devMode: boolean
  lastError?: string | null
}
