import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/developer/',
  plugins: [vue()],
  server: {
    port: 5180,
    host: '0.0.0.0',
  },
})
