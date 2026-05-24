import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // This tells Vite to strictly use one copy of React, killing the duplicate
    dedupe: ['react', 'react-dom']
  }
})