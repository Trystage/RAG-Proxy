import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500
  },
  server: {
    port: 5173,
    proxy: {
      '/admin': { target: 'http://127.0.0.1:8100', changeOrigin: true },
      '/v1': { target: 'http://127.0.0.1:8100', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8100', changeOrigin: true }
    }
  }
})
