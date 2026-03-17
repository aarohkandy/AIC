# Windows Desktop Workflow

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

From [frontend/package.json](/Users/a_a_k/Downloads/AI_CAD/frontend/package.json):

```bash
cd frontend
npm install
npm run tauri:dev
```

`tauri dev` rebuilds the Rust shell when needed and keeps using the Vite dev
server, so normal UI edits should hot reload.

## Packaged Windows Flow

1. Build the Windows runtime archive with
   [packaging/scripts/build_windows_runtime.ps1](/Users/a_a_k/Downloads/AI_CAD/packaging/scripts/build_windows_runtime.ps1)
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

Expected result:

- app data persists
- runtime is reused if `runtimeVersion` is unchanged
- no uninstall/reinstall loop is required

