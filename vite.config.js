import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: 4317,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:4318' },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/three/')) return 'universe';
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/')) return 'react';
        },
      },
    },
    chunkSizeWarningLimit: 700,
  },
});
