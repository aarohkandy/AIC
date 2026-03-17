# Windows Runtime Bundle

The packaged desktop app expects a bundled Windows runtime archive at:

- `packaging/runtime/windows/python-cadquery-runtime-win64.zip`

That archive should contain a relocatable Python 3.11 environment with:

- `uvicorn`
- `fastapi`
- `cadquery`
- `trimesh`
- backend dependencies from [environment.yml](/Users/a_a_k/Downloads/AI_CAD/environment.yml)

The Tauri shell unpacks the archive into:

- `%LocalAppData%/AI CAD/runtime/<runtimeVersion>/`

If the archive is missing, the packaged app will enter the `Broken` bootstrap
state and instruct the user to rebuild the runtime bundle.

