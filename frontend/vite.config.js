import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: { emptyOutDir: false },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5050',
        changeOrigin: true,
      },
      '/images': {
        target: 'http://127.0.0.1:5050',
        changeOrigin: true,
      }
    }
  }
})
