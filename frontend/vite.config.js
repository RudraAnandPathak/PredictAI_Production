import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Configuration lives in the repository-root .env file (see .env.example).
// Only VITE_* variables reach the browser bundle.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'VITE_');
  const apiTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000';

  return {
    plugins: [react()],
    envDir: '..',
    server: {
      port: 5173,
      strictPort: true,
      // Same-origin calls to /api in development, so no CORS setup is needed locally.
      proxy: { '/api': { target: apiTarget } },
    },
    preview: { port: 4173, strictPort: true },
    build: { target: 'es2022', sourcemap: true },
  };
});
