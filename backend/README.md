# Backend

This service exposes the semantic planning, deterministic compilation,
incremental build orchestration, and revision APIs for AI CAD.

Geometry execution expects the supported conda environment described in the root
project docs. Without CadQuery installed, the API still supports planning and
compilation, and build responses fail gracefully with actionable setup errors.
