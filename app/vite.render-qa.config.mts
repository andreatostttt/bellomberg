// Renderer-only production build for isolated render QA (no Electron main/preload
// rebuild, no packaging). Usage: npx vite build --config vite.render-qa.config.mts --outDir dist-qa-<owner>
// The page bundle is the same as vite.config.ts; dist-electron is left untouched.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [
    {
      name: 'desktop-production-csp',
      transformIndexHtml(html) {
        const policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https:; connect-src 'self' http://127.0.0.1:*; object-src 'none'; base-uri 'none'; frame-src 'none'";
        return html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${policy}">`);
      },
    },
    react(),
  ],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  logLevel: 'warn',
  // Fonts are always separate files: the production CSP allows font-src 'self'
  // only, so a woff2 subset inlined as data: (Vite's 4 KB default) would be blocked.
  build: { assetsInlineLimit: (file: string) => (/\.woff2?$/.test(file) ? false : undefined) },
});
