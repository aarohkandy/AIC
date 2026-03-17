import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/designs': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
