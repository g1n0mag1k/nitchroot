import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const apiTarget = process.env.VITE_DEV_API_PROXY ?? 'http://localhost:8000'
const previewApiTarget = process.env.VITE_PREVIEW_API_PROXY ?? 'http://api:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/health': { target: apiTarget, changeOrigin: true },
      '/api': { target: apiTarget, changeOrigin: true },
      '/webhooks': { target: apiTarget, changeOrigin: true },
    },
  },
  preview: {
    port: 3000,
    host: true,
    proxy: {
      '/health': { target: previewApiTarget, changeOrigin: true },
      '/api': { target: previewApiTarget, changeOrigin: true },
      '/webhooks': { target: previewApiTarget, changeOrigin: true },
    },
  },
})
