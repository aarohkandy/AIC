# AI CAD frontend

The React app. One page: type a brief, the backend plans it, the plan compiles
to CadQuery source, and a build produces a GLB the viewer renders. Plan, source,
telemetry and preview are all panes of `src/App.tsx`; the rest of `src/` is
small on purpose.

- `api.ts` wraps the four backend calls. Every one carries a deadline and an
  abort signal, and a FastAPI error body comes back as a sentence the banner can
  show.
- `desktop.ts` works out whether the page is running inside the Tauri shell or
  in a browser against a manually started backend, and polls `/health`.
- `previewCache.ts` frees a preview's geometry, materials and textures once it
  has been replaced. three.js disposes nothing on its own.
- `components/ModelViewer.tsx` is loaded on demand, which keeps three.js out of
  the first paint. The chunk behind it is around 900 kB, and a build that fails
  never fetches it.

## Running it

The app talks to the FastAPI backend in `backend/`, so start that first:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Then, from here:

```bash
npm install
npm run dev
```

`vite.config.ts` proxies `/designs` and `/health` to `localhost:8000`, so
browser development needs no base URL. The packaged desktop shell sets one
instead; `src/desktop.ts` is where that is picked up.

`npm run tauri:dev` runs the same app inside the Tauri shell. That needs a Rust
toolchain. The crate in `src-tauri/` has not been compiled yet, so treat that
path as unfinished.

## Tests

```bash
npm test
```

That is `vitest run` over the test files in `src/`, in jsdom. Two of them, both
driving the real module rather than a copy of its logic:

`api.test.ts` stubs `fetch` and checks what the caller gets back. The four error
bodies the backend and a proxy in front of it can produce, a cancel before and
during a request, and the per-endpoint deadlines, including that the number the
status card quotes for an action is the number that action's request actually
uses. Deadlines run on fake timers, so those cases finish in milliseconds rather
than the 830 s the four real ones add up to.

`previewCache.test.ts` builds scenes out of real three.js objects and checks
what the disposal walk reaches: a mesh, an array of materials, a texture in a
material slot, a mesh nested under a group, and a second release of a URL that
has already been freed. It also pins the limit: the walk descends into `Mesh`
and deliberately stops there.

There are no component tests. Rendering `App.tsx` needs React Three Fiber, a
WebGL context and a backend answering, and faking those three to assert on
markup buys less than it costs. Nothing tests the preview end to end either, on
this side or the backend's: producing a GLB needs the CadQuery environment from
`environment.yml`, and without it a build stops at `cadquery_unavailable`.

`npm run lint` and `npm run build` are the other two checks. CI runs all three,
on pushes to `main` and on pull requests.
