import { startTransition, useDeferredValue, useEffect, useState } from 'react'
import { artifactUrl, buildDesign, compilePlan, planDesign, reviseDesign } from './api'
import { initializeDesktop, getDesktopStatus, setApiBaseUrl } from './desktop'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ModelViewer } from './components/ModelViewer'
import type {
  BuildResult,
  CompileResult,
  DesignBrief,
  DesktopStatus,
  SemanticBuildPlan,
} from './types'

const initialBrief: DesignBrief = {
  prompt: 'Design a mug with an 86 mm diameter, 96 mm height, 4 mm walls, and a sturdy handle.',
  units: 'mm',
  target_dims: { diameter: 86, height: 96 },
  required_features: ['handle', 'hollow body'],
  style_notes: ['sturdy', 'revision-friendly'],
  tolerances: null,
}

function parseList(value: string) {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean)
}

function App() {
  const [brief, setBrief] = useState<DesignBrief>(initialBrief)
  const [designId, setDesignId] = useState<string | null>(null)
  const [plan, setPlan] = useState<SemanticBuildPlan | null>(null)
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null)
  const [buildResult, setBuildResult] = useState<BuildResult | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [plannerMeta, setPlannerMeta] = useState<{ path: string; model: string; risk: number } | null>(null)
  const [revisionText, setRevisionText] = useState('Make the handle thickness 10 mm.')
  const [codeDraft, setCodeDraft] = useState('')
  const [status, setStatus] = useState<'idle' | 'planning' | 'building' | 'revising'>('idle')
  const [desktopStatus, setDesktopShellStatus] = useState<DesktopStatus | null>(null)
  const [desktopBusy, setDesktopBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const deferredCode = useDeferredValue(codeDraft)
  const previewUrl =
    designId && buildResult?.artifacts.glb_path && buildResult.status !== 'failed'
      ? artifactUrl(designId, 'glb')
      : null
  const workspaceReady = desktopStatus?.bootstrapState === 'Ready'
  const actionDisabled = status !== 'idle' || !workspaceReady

  useEffect(() => {
    let active = true

    async function syncDesktopStatus() {
      const nextStatus = await getDesktopStatus()
      if (!active) {
        return
      }
      setApiBaseUrl(nextStatus.backendUrl)
      setDesktopShellStatus(nextStatus)
    }

    void syncDesktopStatus()
    const timer = window.setInterval(() => {
      void syncDesktopStatus()
    }, 1500)

    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  async function handleDesktopRetry() {
    setDesktopBusy(true)
    setError(null)
    try {
      const nextStatus = await initializeDesktop()
      startTransition(() => {
        setApiBaseUrl(nextStatus.backendUrl)
        setDesktopShellStatus(nextStatus)
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Desktop bootstrap failed.')
    } finally {
      setDesktopBusy(false)
    }
  }

  async function handlePlan() {
    setStatus('planning')
    setError(null)
    try {
      const planned = await planDesign(brief)
      const compiled = await compilePlan(planned.plan)
      startTransition(() => {
        setDesignId(planned.design_id)
        setPlan(planned.plan)
        setCompileResult(compiled)
        setBuildResult(null)
        setWarnings(planned.warnings)
        setPlannerMeta({
          path: planned.planner_path,
          model: planned.model_call.model,
          risk: planned.planning_risk_score,
        })
        setCodeDraft(compiled.source)
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Planning failed.')
    } finally {
      setStatus('idle')
    }
  }

  async function handleBuild() {
    setStatus('building')
    setError(null)
    try {
      const built = await buildDesign(brief)
      startTransition(() => {
        setDesignId(built.design_id)
        setPlan(built.plan)
        setCompileResult(built.compile)
        setBuildResult(built.build)
        setWarnings(built.warnings)
        setPlannerMeta({
          path: built.model_call.path,
          model: built.model_call.model,
          risk: built.build.metrics.planning_risk_score,
        })
        setCodeDraft(built.compile.source)
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Build failed.')
    } finally {
      setStatus('idle')
    }
  }

  async function handleRevise() {
    if (!designId) {
      setError('Build or plan a design before revising it.')
      return
    }
    setStatus('revising')
    setError(null)
    try {
      const revised = await reviseDesign(designId, revisionText)
      startTransition(() => {
        setPlan(revised.plan)
        setWarnings(revised.warnings)
        if (revised.compile) {
          setCompileResult(revised.compile)
          setCodeDraft(revised.compile.source)
        }
        if (revised.build) {
          setBuildResult(revised.build)
        }
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Revision failed.')
    } finally {
      setStatus('idle')
    }
  }

  return (
    <div className="app-shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">Local AI CAD planner</p>
          <h1>Ask for an object and get a build recipe with planes, locations, and sizes.</h1>
          <p className="subhead">
            The planner now prefers a local Ollama model so the first thing you see is a
            reproducible CAD plan, not a vague paragraph. For supported shapes the deterministic
            compiler can still turn that plan into CadQuery.
          </p>
        </div>
        <div className="status-card">
          <span className={`status-pill status-${status}`}>{status}</span>
          <p>{plannerMeta ? `${plannerMeta.model} via ${plannerMeta.path} path` : 'Planner not invoked yet'}</p>
          <p>{plannerMeta ? `Planning risk ${plannerMeta.risk.toFixed(2)}` : 'Waiting for the first design run.'}</p>
        </div>
      </header>

      <section className="desktop-banner">
        <div className="desktop-banner-copy">
          <p className="eyebrow">Desktop Runtime</p>
          <h2>
            {desktopStatus?.bootstrapState ?? 'Installing'}
            {' '}
            {desktopStatus?.devMode ? 'developer shell' : 'packaged shell'}
          </h2>
          <p>{desktopStatus?.statusMessage ?? 'Checking desktop shell status...'}</p>
          <div className="desktop-meta">
            <span className="chip">Runtime {desktopStatus?.runtimeVersion ?? 'unknown'}</span>
            <span className="chip">Backend {desktopStatus?.backendUrl || 'not assigned'}</span>
            <span className={`chip ${desktopStatus?.backendHealth.healthy ? 'chip-success' : 'chip-warning'}`}>
              {desktopStatus?.backendHealth.detail ?? 'Waiting for backend health...'}
            </span>
          </div>
        </div>
        <div className="desktop-banner-actions">
          <div className="notes compact-note">
            <h3>Shell signals</h3>
            <ul>
              <li>Logs: {desktopStatus?.logsPath || 'not available yet'}</li>
              <li>
                Mode:
                {' '}
                {desktopStatus?.isDesktopShell ? 'Tauri desktop shell' : 'browser fallback'}
              </li>
              {desktopStatus?.lastError ? <li>Error: {desktopStatus.lastError}</li> : null}
            </ul>
          </div>
          {desktopStatus?.isDesktopShell ? (
            <button
              className="secondary"
              onClick={handleDesktopRetry}
              disabled={desktopBusy || desktopStatus.bootstrapState === 'Installing'}
            >
              {desktopBusy ? 'Starting shell...' : desktopStatus.bootstrapState === 'Ready' ? 'Recheck shell' : 'Retry setup'}
            </button>
          ) : null}
        </div>
      </section>

      <main className="workspace-grid">
        <section className="panel panel-brief">
          <div className="panel-header">
            <h2>Brief + Revisions</h2>
            <p>Describe any object naturally. The local planner should break it into ordered CAD steps with workplanes, placement, and sizing details.</p>
          </div>
          <label className="field">
            <span>Prompt</span>
            <textarea
              value={brief.prompt}
              onChange={(event) => setBrief({ ...brief, prompt: event.target.value })}
              rows={7}
            />
          </label>
          <div className="inline-grid">
            <label className="field">
              <span>Units</span>
              <select
                value={brief.units}
                onChange={(event) => setBrief({ ...brief, units: event.target.value as DesignBrief['units'] })}
              >
                <option value="mm">mm</option>
                <option value="cm">cm</option>
                <option value="in">in</option>
              </select>
            </label>
            {(['diameter', 'width', 'depth', 'height'] as const).map((key) => (
              <label className="field" key={key}>
                <span>{key}</span>
                <input
                  type="number"
                  value={brief.target_dims[key] ?? ''}
                  onChange={(event) =>
                    setBrief({
                      ...brief,
                      target_dims: {
                        ...brief.target_dims,
                        [key]: event.target.value === '' ? null : Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
            ))}
          </div>
          <label className="field">
            <span>Required features</span>
            <input
              value={brief.required_features.join(', ')}
              onChange={(event) => setBrief({ ...brief, required_features: parseList(event.target.value) })}
            />
          </label>
          <label className="field">
            <span>Style notes</span>
            <input
              value={brief.style_notes.join(', ')}
              onChange={(event) => setBrief({ ...brief, style_notes: parseList(event.target.value) })}
            />
          </label>
          <div className="action-row">
            <button className="secondary" onClick={handlePlan} disabled={actionDisabled}>
              Plan
            </button>
            <button className="primary" onClick={handleBuild} disabled={actionDisabled}>
              Build
            </button>
          </div>
          <label className="field">
            <span>Revision instruction</span>
            <textarea
              value={revisionText}
              onChange={(event) => setRevisionText(event.target.value)}
              rows={3}
            />
          </label>
          <button className="ghost" onClick={handleRevise} disabled={actionDisabled || !designId}>
            Apply revision
          </button>
          {!workspaceReady ? (
            <div className="banner info-banner">
              Desktop bootstrap must reach <strong>Ready</strong> before plan/build actions are enabled.
            </div>
          ) : null}
          {error ? <div className="banner error-banner">{error}</div> : null}
          {warnings.length > 0 ? (
            <div className="notes">
              <h3>Runtime notes</h3>
              <ul>
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Build Plan</h2>
            <p>Each step should be human-checkable in CAD before we trust the automated build path.</p>
          </div>
          {plan ? (
            <div className="plan-content">
              <p className="lead">{plan.summary}</p>
              <div className="plan-meta">
                {plan.assumptions.map((assumption) => (
                  <span className="chip" key={assumption}>
                    {assumption}
                  </span>
                ))}
              </div>
              <ol className="step-list">
                {plan.steps.map((step) => (
                  <li key={step.id} className="step-card">
                    <div className="step-head">
                      <strong>{step.intent}</strong>
                      <code>{step.primitive_or_macro}</code>
                    </div>
                    {step.workplane ? <p><strong>Workplane:</strong> {step.workplane}</p> : null}
                    <p>{step.postcondition}</p>
                    {step.location_notes.length > 0 ? (
                      <div className="notes compact-note">
                        <h3>Location</h3>
                        <ul>
                          {step.location_notes.map((note) => (
                            <li key={`${step.id}-location-${note}`}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {step.size_notes.length > 0 ? (
                      <div className="notes compact-note">
                        <h3>Sizes</h3>
                        <ul>
                          {step.size_notes.map((note) => (
                            <li key={`${step.id}-size-${note}`}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {step.sketch_constraints.length > 0 ? (
                      <div className="notes compact-note">
                        <h3>Sketch constraints</h3>
                        <ul>
                          {step.sketch_constraints.map((note) => (
                            <li key={`${step.id}-constraint-${note}`}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {step.manual_instructions.length > 0 ? (
                      <div className="notes compact-note">
                        <h3>Manual recipe</h3>
                        <ul>
                          {step.manual_instructions.map((note) => (
                            <li key={`${step.id}-manual-${note}`}>{note}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    <div className="param-list">
                      {Object.entries(step.parameters).map(([key, value]) => (
                        <span className="param-chip" key={`${step.id}-${key}`}>
                          {key}: {String(value)}
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ) : (
            <div className="empty-state">Plan a design to see the semantic steps appear here.</div>
          )}
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>Generated Code</h2>
            <p>{deferredCode ? `${deferredCode.split('\n').length} lines of CadQuery source.` : 'No compile output yet.'}</p>
          </div>
          <textarea
            className="code-editor"
            value={codeDraft}
            onChange={(event) => setCodeDraft(event.target.value)}
            spellCheck={false}
          />
          {compileResult ? (
            <div className="code-metadata">
              <div>
                <h3>Editable regions</h3>
                <ul>
                  {compileResult.editable_regions.map((region) => (
                    <li key={region.step_id}>
                      {region.label} ({region.start_line}-{region.end_line})
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3>Compiler checks</h3>
                <ul>
                  {compileResult.diagnostics.map((diagnostic, index) => (
                    <li key={`${diagnostic.code}-${index}`}>{diagnostic.level}: {diagnostic.message}</li>
                  ))}
                  {compileResult.whitelist_findings.map((finding, index) => (
                    <li key={`${finding.message}-${index}`}>{finding.severity}: {finding.message}</li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
        </section>

        <section className="panel panel-preview">
          <div className="panel-header">
            <h2>Preview + Telemetry</h2>
            <p>The desktop shell still serves GLB and STEP from the local backend process.</p>
          </div>
          <div className="viewer-shell">
            {previewUrl ? (
              <ErrorBoundary fallback={<div className="empty-state">Preview failed to load.</div>}>
                <ModelViewer url={previewUrl} />
              </ErrorBoundary>
            ) : (
              <div className="empty-state">
                {buildResult?.failure
                  ? `${buildResult.failure.message} ${buildResult.failure.next_action}`
                  : workspaceReady
                    ? 'Build the design to render the GLB preview.'
                    : 'Wait for the desktop shell to finish runtime setup.'}
              </div>
            )}
          </div>
          {buildResult ? (
            <div className="telemetry-grid">
              <div className="metric">
                <span>Attempts</span>
                <strong>{buildResult.attempts_used}</strong>
              </div>
              <div className="metric">
                <span>Cache hits</span>
                <strong>{buildResult.cache_hits}</strong>
              </div>
              <div className="metric">
                <span>Volume</span>
                <strong>{buildResult.metrics.volume?.toFixed(1) ?? 'n/a'}</strong>
              </div>
              <div className="metric">
                <span>Latency</span>
                <strong>{buildResult.metrics.attempt_latency_ms ?? 0} ms</strong>
              </div>
            </div>
          ) : null}
          {buildResult?.validation ? (
            <div className="notes">
              <h3>Validation</h3>
              <ul>
                {Object.entries(buildResult.validation.checks).map(([key, value]) => (
                  <li key={key}>
                    {key}: {String(value)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      </main>
    </div>
  )
}

export default App
