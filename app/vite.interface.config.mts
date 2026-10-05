import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Independent frontend development: does not load Electron or spawn Python.
// The same-origin proxy retains API/session headers without changing backend CORS.
export default defineConfig({
  plugins: [
    react(),
    {
      name: 'interface-development-api',
      transformIndexHtml() {
        return [{
          tag: 'script', injectTo: 'head-prepend',
          children: "window.bellomberg = { apiUrl: window.location.origin + '/api' };",
        }];
      },
    },
  ],
  cacheDir: 'dist/.vite-interface',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '127.0.0.1', port: 5174, strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765', changeOrigin: true,
        rewrite: path => path.replace(/^\/api(?=\/|$)/, ''),
      },
    },
  },
});
