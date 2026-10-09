import { defineConfig } from 'astro/config';

export default defineConfig({
  // Canonical links have no trailing slash. Nginx still serves both forms.
  trailingSlash: 'never',
  build: {
    format: 'directory',
  },
  server: {
    host: '127.0.0.1',
    port: 18473,
  },
  vite: {
    server: {
      strictPort: true,
    },
  },
});
