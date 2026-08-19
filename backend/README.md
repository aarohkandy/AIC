# Backend

This service exposes the semantic planning, deterministic compilation,
incremental build orchestration, and revision APIs for AI CAD.

Geometry execution expects the supported conda environment described in the root
project docs. Without CadQuery installed, the API still supports planning and
compilation, and build responses fail gracefully with actionable setup errors.

[docs/architecture.md](../docs/architecture.md) walks the pipeline module by
module: the gateway's planner ladder, the compiler and its whitelist, the
executor subprocess, and what each stage leaves under `runtime/`.
