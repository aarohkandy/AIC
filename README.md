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

Fastest terminal test, once the environment from
[environment.yml](environment.yml) is created and activated:

```bash
./aic "a teapot which can hold 1 gallon"
```

That runs the planner directly in the terminal with no web app startup. Run
outside that environment it prints the bootstrap commands and exits 1 rather
than a traceback.

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

By default the backend runs in trusted local mode and uses a local Ollama
planner for every prompt, including simple shapes like mugs. The default model
is `llama3.1:8b`, and the planner timeout is intentionally generous so the
model has time to think through the steps.

If Ollama is unavailable or the planner call fails, the backend falls back to
the deterministic local planner. Hosted models remain blocked unless a healthy
containerized executor is configured.

To use the local AI planner, make sure Ollama is running locally and the model
exists:

```bash
ollama list
ollama serve
```

## What the Macro Library Covers

Geometry comes from a fixed macro library, so the compiler can only *build* the
shapes it has recipes for. Five families are modelled end to end:

- mug (`mug`, `cup`): outer body, shell, blocky handle
- L bracket (`bracket`): L profile plus two mounting holes
- project box (`box`, `enclosure`): shelled enclosure plus four standoffs
- phone stand (`stand`): base slab, tilted backrest, front lip
- bottle cap (`bottle`, `cap`): hollow cap body plus perimeter grip cutouts

Prompts are scanned for diameter, height, width, depth and wall thickness. A
recipe uses the ones it has parameters for, and the plan's assumptions say what
became of the rest: which dimensions this recipe has no parameter for, which
ones fell back to a category default, and any value that had to be clamped to
keep the solid buildable. Ask for a 20 mm box with 15 mm walls and you get
8 mm walls and a line saying so, because 15 mm walls cut a negative cavity.

Every plan is in millimetres. A prompt or form figure given in cm or inches is
converted on the way in and the assumptions name the original unit. A zero or
negative dimension cannot be extruded, so it falls back to the category default
and the plan says which one it used.

Anything outside those five families is not recognized. The plan summary and
the assumptions both say so, and the planner still hands back the closest
recipe as a stand-in, so the workplanes, locations, sizes and sketch
constraints remain a usable manual CAD recipe. It will not quietly call a
bottle cap a teapot.

When the local AI planner writes a step no macro fits, it marks it
`manual_feature`. Those compile to a pass-through so the rest of the plan still
builds, and the compile diagnostics name the step and point at its manual
instructions.

## Desktop App Base

The repo now includes a Tauri desktop shell for a Windows-first packaged app.

Developer loop:

```bash
cd frontend
npm install
npm run tauri:dev
```

Packaged Windows notes live in [docs/windows-desktop.md](docs/windows-desktop.md).

## License

MIT. See [LICENSE](LICENSE).
