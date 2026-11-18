import { loadConfig } from './configSchema.js';

// Validated once at start-up. Vite replaces import.meta.env at build time.
export const config = loadConfig(import.meta.env);
