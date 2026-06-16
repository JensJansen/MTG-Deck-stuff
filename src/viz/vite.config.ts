import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // public/ is served at root in dev; JSON data files live in public/data/
});
