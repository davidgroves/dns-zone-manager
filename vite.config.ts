import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig(({ command }) => {
  const isServe = command === 'serve';
  
  return {
    root: 'frontend',
    
    build: isServe ? {} : {
      // SPA build for nginx/Docker deployment
      outDir: resolve(__dirname, 'dist'),
      emptyOutDir: true,
    },
    
    resolve: {
      alias: {
        '@': resolve(__dirname, 'frontend')
      }
    },
    
    // Development server configuration
    server: {
      port: 5173,
      host: true,  // Bind to 0.0.0.0 for container/devcontainer access
      // Proxy API requests to local FastAPI server
      proxy: {
        '/ui/config': 'http://localhost:8000',
        '/health': 'http://localhost:8000',
        '/metrics': 'http://localhost:8000',
        '/v1': 'http://localhost:8000',
      }
    },
  };
});
