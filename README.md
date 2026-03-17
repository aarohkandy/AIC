# AI CAD

AI CAD is a planner-first local prototype for single-part parametric modeling.
It turns a design brief into a visible semantic build plan, compiles that plan
into deterministic CadQuery source, and renders an interactive browser preview.

## Project Layout

- `backend/`: FastAPI API, planner/compiler/executor pipeline, runtime storage
- `frontend/`: React + Vite app with prompt, plan, code, and 3D preview panes
- `frontend/src-tauri/`: Windows-first desktop shell and backend bootstrap manager
- `docs/`: setup and architecture notes
- `packaging/`: runtime manifest and Windows packaging scripts

## Supported Runtime

The supported CAD runtime is `Python 3.11 + Miniforge/conda + mamba`.

Important:

- Do not install CadQuery with `pip` for this project.
- Python `3.13+` is unsupported by CadQuery's `pip` package.
- This repo treats anything except `Python 3.11 + conda/mamba` as unsupported
  for geometry execution, even though non-CAD backend logic may still run.

See [docs/setup.md](docs/setup.md) for the exact environment bootstrap steps.

## Quick Start

1. Create the supported CAD environment from [environment.yml](environment.yml).
2. Install frontend dependencies:

```bash
cd frontend
npm install
```

3. Start the backend for local web testing:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

4. Start the frontend:

```bash
cd frontend
npm run dev
```

By default the backend runs in trusted local mode and uses a deterministic local
planner. Hosted models remain blocked unless a healthy containerized executor is
configured.

## Desktop App Base

The repo now includes a Tauri desktop shell for a Windows-first packaged app.

Developer loop:

```bash
cd frontend
npm install
npm run tauri:dev
```

Packaged Windows notes live in [docs/windows-desktop.md](/Users/a_a_k/Downloads/AI_CAD/docs/windows-desktop.md).
