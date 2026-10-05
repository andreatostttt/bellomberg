import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import electron from 'vite-plugin-electron/simple';
import path from 'path';

export default defineConfig(({ command }) => ({
  plugins: [
    {
      name: 'desktop-production-csp',
      transformIndexHtml(html) {
        if (command !== 'build') return html;
        const policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; connect-src 'self' http://127.0.0.1:*; object-src 'none'; base-uri 'none'; frame-src 'none'";
        return html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${policy}">`);
      },
    },
    react(),
    electron({
      main: { entry: 'electron/main.ts' },
      preload: { input: 'electron/preload.ts' },
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: { port: 5173, strictPort: true },
  // Fonts are always separate files: the production CSP allows font-src 'self'
  // only, so a woff2 subset inlined as data: (Vite's 4 KB default) would be blocked.
  build: { assetsInlineLimit: (file: string) => (/\.woff2?$/.test(file) ? false : undefined) },
}));
