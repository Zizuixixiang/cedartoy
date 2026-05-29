import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/soup/',
  plugins: [react()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    port: 5175,
    proxy: {
      '/soup/api': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
    },
  },
})
