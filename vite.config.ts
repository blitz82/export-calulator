import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import { bundleInventory } from './vite-inventory.mjs'

export default defineConfig({define:{__APP_VERSION__:JSON.stringify('2.0.4'),__BUILD_TIME__:JSON.stringify(new Date().toISOString())},plugins: [tailwindcss(), bundleInventory]})
