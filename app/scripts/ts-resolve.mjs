// Vite resolves extensionless relative imports; node does not. Add ".ts" when
// the bare specifier does not exist, so app modules can be run under
// --experimental-strip-types for a headless check.
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
export async function resolve(specifier, context, next) {
  if (specifier.startsWith('.') && !/\.[mc]?[jt]sx?$/.test(specifier)) {
    try {
      const url = new URL(specifier + '.ts', context.parentURL);
      if (existsSync(fileURLToPath(url))) return next(specifier + '.ts', context);
    } catch { /* fall through */ }
  }
  return next(specifier, context);
}
