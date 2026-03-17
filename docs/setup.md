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

