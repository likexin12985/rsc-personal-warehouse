import type { Plugin } from 'vite';

export const SOURCE_URL: string;
export function validatePublicCatalog(
  catalog: unknown,
  options?: { requireReady?: boolean; now?: number },
): { status: 'pending' | 'ready'; records: number };
export function publicCatalogPlugin(enabled: boolean): Plugin;
