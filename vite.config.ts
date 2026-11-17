import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import { bundleInventory } from './vite-inventory.mjs'

export default defineConfig({plugins: [tailwindcss(), bundleInventory]})
