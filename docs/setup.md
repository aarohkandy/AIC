# Setup

## Supported Environment

This project supports geometry execution only on `Python 3.11` installed via
`Miniforge/conda` with `mamba`.

Hard warnings:

- Do not use `pip install cadquery` for this project.
- Python `3.12` + `pip` is a known pain point because of native dependency
  breakage around the CadQuery stack.
- Python `3.13+` is not supported by CadQuery's `pip` package.
- Treat any environment other than `3.11 + conda/mamba` as unsupported for the
  executor.

## Install Miniforge

1. Install Miniforge from the official installer.
2. Open a new shell and verify `conda --version`.
3. Create the environment:

```bash
mamba env create -f environment.yml
conda activate ai-cad
```

## Backend

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

## One-Command Terminal Test

From the repo root:

```bash
./aic "a teapot which can hold 1 gallon"
```

If you run `./aic` with no prompt, it opens an interactive terminal loop.

## Local AI Planner

The planner now uses a local Ollama model first for every prompt. This includes
simple shapes like mugs, because the product goal is to let the AI think
through the build steps instead of jumping straight to canned geometry.

Recommended local setup:

```bash
ollama list
ollama serve
```

Default model:

- `llama3.1:8b`

Default timeout:

- `180` seconds

You can override the model or base URL with:

- `AI_CAD_OLLAMA_MODEL`
- `AI_CAD_OLLAMA_BASE_URL`
- `AI_CAD_OLLAMA_TIMEOUT_SECONDS`
- `AI_CAD_FORCE_LOCAL_MODEL_PLANNER=false` if you want to temporarily restore
  the faster deterministic planner for known canned shapes

## Frontend

```bash
cd frontend
npm install
npm run dev
```

## Trusted Local Mode

The default executor mode is `local`. That mode is for private prototyping only
and is not a sandbox boundary. Hosted model calls remain disabled unless the
backend is configured for a healthy containerized Linux executor.
