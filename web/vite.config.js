import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { attach } from './server/api.mjs';

const PORT = Number(process.env.PORT || 3000);

// The API (run control, SSE, LLM metering proxy, simulated endpoint) is mounted straight into
// the dev server, so the whole demo is one process on one port.
function clawboardApi() {
  const mount = (server) => attach(server.middlewares, { origin: `http://127.0.0.1:${PORT}` });
  return { name: 'clawboard-api', configureServer: mount, configurePreviewServer: mount };
}

export default defineConfig({
  plugins: [react(), clawboardApi()],
  server: {
    port: PORT,
    strictPort: true,
    host: '127.0.0.1',
    // the PCB tools rewrite these on every board change; they must not trigger a page reload
    watch: { ignored: ['**/public/board_preview*.svg', '**/public/schematic_preview*.svg', '**/runs/**'] },
  },
  preview: { port: PORT, strictPort: true, host: '127.0.0.1' },
});
