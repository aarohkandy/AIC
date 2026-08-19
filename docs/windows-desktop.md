# Windows Desktop Workflow

The packaged path described below is designed but unverified: there is no Rust
toolchain in this working copy, so `frontend/src-tauri/` has not been built here,
no installer has been produced, and the runtime archive has not been packed. Read
this as the intended design and the steps to try first, not as a workflow that
has been walked end to end.

## Product Shape

AI CAD now has a Windows-first desktop shell built with Tauri. The packaged app
keeps the current React frontend and FastAPI backend, but the shell owns:

- runtime bootstrap
- backend startup
- local health checks
- logs
- local artifact and cache paths

## Development Loop

You should not need to uninstall and reinstall the app for normal development.

From [frontend/package.json](../frontend/package.json):

```bash
cd frontend
npm install
npm run tauri:dev
```

`tauri dev` rebuilds the Rust shell when needed and keeps using the Vite dev
server, so normal UI edits should hot reload.

## Packaged Windows Flow

1. Build the Windows runtime archive with
   [packaging/scripts/build_windows_runtime.ps1](../packaging/scripts/build_windows_runtime.ps1)
2. Confirm the archive exists at
   `packaging/runtime/windows/python-cadquery-runtime-win64.zip`
3. Build the installer:

```bash
cd frontend
npm run tauri:build
```

4. Install `v1`
5. Build `v1.0.1`
6. Install the newer build over the older one

That last sequence is the upgrade case the shell is written for, and it is the
one worth exercising first. What it is meant to do, none of which has been
observed:

- app data persists across the upgrade
- the runtime is reused rather than re-unpacked when `runtimeVersion` is
  unchanged
- no uninstall/reinstall loop is required

If any of those turns out to be false once someone builds it, the shell is
wrong, not this list.

