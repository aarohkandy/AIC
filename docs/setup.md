# Setup

Getting the environment right is the only hard part of running this project, and
it is hard for one reason: CadQuery. Everything else is an `npm install` and a
`uvicorn` command, and those live in the [README's Quick Start](../README.md)
rather than being repeated here.

## Supported Environment

Geometry execution is supported on `Python 3.11` installed via
`Miniforge/conda`, with `mamba` doing the solve.

Hard warnings:

- Do not use `pip install cadquery` for this project.
- Python `3.12` + `pip` is a known pain point because of native dependency
  breakage around the CadQuery stack.
- Python `3.13+` is not supported by CadQuery's `pip` package.
- Treat any environment other than `3.11 + conda/mamba` as unsupported for the
  executor.

Only the executor needs CadQuery. The planner, the compiler, the API and the
revision engine are plain Python, so a non-conda 3.11 will run everything up to
the point where a solid would be built, and then fail with
`cadquery_unavailable` and a next action pointing at `environment.yml`. If planning is all you want, the
README describes a `.venv-test/` escape hatch that `./aic` and the Tauri dev
shell both pick up.

## Install Miniforge

1. Install Miniforge from the official installer.
2. Open a new shell and verify `conda --version`.
3. Create the environment:

```bash
mamba env create -f environment.yml
conda activate ai-cad
```

Then follow the README for starting the backend, the frontend, or `./aic`.

## Configuration

Every backend setting has a default and none of them have to be set. When you do
want to change one, copy the annotated template and edit it:

```bash
cp .env.example backend/.env
```

The backend reads `.env` from the directory the process is started in, which is
why that copy lands in `backend/` for the usual `cd backend && uvicorn ...`.
`./aic` does not change directory, so it reads `.env` from wherever you invoked
it; if you drive the planner from the repo root and want it configured, put a
copy there too. The defaults themselves are in
[backend/app/core/settings.py](../backend/app/core/settings.py).

## Local AI Planner

The planner asks a local Ollama model first for every prompt, including simple
shapes like mugs, because the product goal is to let the model think through the
build steps instead of jumping straight to canned geometry.

Recommended local setup:

```bash
ollama list
ollama serve
```

Default model:

- `llama3.1:8b`

Default timeout:

- `180` seconds

You can override the model, the endpoint or the timeout with:

- `AI_CAD_OLLAMA_MODEL`
- `AI_CAD_OLLAMA_BASE_URL`
- `AI_CAD_OLLAMA_TIMEOUT_SECONDS`

To skip Ollama entirely and go straight to the deterministic rule-based planner,
set:

- `AI_CAD_PREFER_LOCAL_MODEL_PLANNER=false`

That is the switch the model gateway actually reads before it decides whether to
try the local model, and the tests that drive the API set it for the same reason
you might: it keeps the run off the network and makes the answer repeatable. You
do not need it to survive Ollama being down, though. The gateway falls back on
its own and says so in the response warnings. Turning it off is for when you
want the deterministic answer on purpose.

`GET /health` reports whether the configured model is installed, so
`ollama list` disagreeing with the config shows up there rather than as a
mystery fallback.

## Trusted Local Mode

The default executor mode is `local`. That mode is for private prototyping only
and is not a sandbox boundary: the compiled source runs in a subprocess of the
same interpreter, and the AST whitelist in the compiler is a lint check, not a
jail. Hosted model calls stay disabled unless the backend is configured for a
healthy containerized Linux executor, which is a separate deployment this repo
does not ship.
