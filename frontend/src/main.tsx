import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

// The boundary inside App only wraps the 3D viewer, so anything the rest of the
// tree throws used to unmount the page and leave a white screen with the reason
// in the console. A response with an unexpected shape is the realistic way to
// get there: a packaged desktop shell can bootstrap a backend built from a
// different commit.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary
      fallback={
        <div className="app-shell">
          <div className="empty-state">
            The interface stopped on an error it could not render around. Reload the page. If
            it happens again, the browser console names the component that threw, and a
            backend built from a different commit than this UI is the usual cause.
          </div>
        </div>
      }
    >
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
