import { useEffect, useMemo, useState } from 'react';
import { Loader2, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SYNQIT_API_BASE } from '@/lib/synqitApi';
import { cn } from '@/lib/utils';

/**
 * Synqit's curated part library, browsed natively in the studio. The index
 * (`GET /library/index`, card-sized rows, gzipped) is fetched once per session
 * and filtered in the browser — the same payload Synqit's own library page
 * uses. Picking a part appends its facts to the composer through the
 * `synqit-insert-prompt` event, so the model designs around a real package
 * instead of a guessed one.
 */
export const INSERT_EVENT = 'synqit-insert-prompt';

/** Same channel, but "do it now" rather than "put it in the box". */
export const SEND_EVENT = 'synqit-send-prompt';

type Row = {
  mpn: string;
  description?: string;
  category?: string;
  package?: string;
  footprint?: string;
  pinCount?: number;
  unitCost?: number;
  imageUrl?: string | null;
  jlcStock?: number | null;
};

let cache: Promise<Row[]> | null = null;
function loadIndex(): Promise<Row[]> {
  cache ??= fetch(`${SYNQIT_API_BASE}/library/index`)
    .then(async (r) => {
      if (!r.ok) throw new Error(`library ${r.status}`);
      const data = (await r.json()) as unknown;
      const rows = Array.isArray(data)
        ? data
        : ((data as { parts?: Row[] })?.parts ?? (Object.values(data as object).find(Array.isArray) as Row[]) ?? []);
      return rows as Row[];
    })
    .catch((e) => {
      cache = null;
      throw e;
    });
  return cache;
}

export function LibraryDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [cat, setCat] = useState<string | null>(null);

  useEffect(() => {
    if (!open || rows) return;
    setError(null);
    loadIndex().then(setRows, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, [open, rows]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const categories = useMemo(() => {
    const c = new Map<string, number>();
    for (const r of rows ?? []) c.set(r.category || 'other', (c.get(r.category || 'other') ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12);
  }, [rows]);

  const hits = useMemo(() => {
    if (!rows) return [];
    const needle = q.trim().toLowerCase();
    const out: Row[] = [];
    for (const r of rows) {
      if (cat && (r.category || 'other') !== cat) continue;
      if (needle) {
        const hay = `${r.mpn} ${r.description ?? ''} ${r.package ?? ''} ${r.footprint ?? ''}`.toLowerCase();
        if (!hay.includes(needle)) continue;
      }
      out.push(r);
      if (out.length >= 300) break;
    }
    return out;
  }, [rows, q, cat]);

  if (!open) return null;

  const insert = (r: Row) => {
    const lines = [
      `Synqit library part: ${r.mpn}${r.description ? ` — ${r.description}` : ''}`,
      r.category ? `  category: ${r.category}` : '',
      r.package ? `  package: ${r.package}` : '',
      r.footprint ? `  footprint: ${r.footprint}` : '',
      r.pinCount ? `  pins: ${r.pinCount}` : '',
    ].filter(Boolean);
    window.dispatchEvent(new CustomEvent(INSERT_EVENT, { detail: lines.join('\n') }));
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-6" onClick={onClose}>
      <div
        className="flex h-[80vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-sq-neutral-800 bg-sq-bg-secondary-dark shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-sq-neutral-800 px-4 py-3">
          <div className="flex-1">
            <div className="text-sm font-medium text-sq-text-primary">Synqit part library</div>
            <div className="text-[11px] text-sq-text-secondary">
              {rows ? `${rows.length.toLocaleString()} curated parts · showing ${hits.length}${hits.length === 300 ? '+' : ''}` : 'loading the index…'}
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1.5 text-sq-text-secondary hover:text-sq-text-primary">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex items-center gap-2 border-b border-sq-neutral-800 px-4 py-2">
          <Search className="h-4 w-4 shrink-0 text-sq-text-tertiary" />
          <Input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="MPN, description, package — e.g. ESP32, 0603, USB-C"
            className="h-8 border-sq-neutral-700 bg-sq-bg-dark text-sm"
          />
        </div>

        {categories.length > 0 && (
          <div className="flex flex-wrap gap-1 border-b border-sq-neutral-800 px-4 py-2">
            {categories.map(([c, n]) => (
              <button
                key={c}
                type="button"
                onClick={() => setCat(cat === c ? null : c)}
                className={cn(
                  'rounded-full px-2 py-0.5 text-[11px] transition-colors',
                  cat === c ? 'bg-sq-blue/20 text-sq-blue' : 'bg-sq-neutral-800 text-sq-text-secondary hover:text-sq-text-primary',
                )}
              >
                {c} {n}
              </button>
            ))}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {error && <p className="p-4 text-sm text-sq-text-secondary">Could not load the library ({error}).</p>}
          {!rows && !error && (
            <div className="flex h-full items-center justify-center text-sm text-sq-text-secondary">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> fetching the part index…
            </div>
          )}
          {rows && hits.length === 0 && <p className="p-4 text-sm text-sq-text-secondary">No parts match.</p>}
          <ul className="space-y-1">
            {hits.map((r) => (
              <li key={r.mpn}>
                <div className="flex items-center gap-3 rounded-md border border-sq-neutral-800 px-3 py-2 hover:border-sq-neutral-700">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-sq-text-primary">{r.mpn}</div>
                    <div className="truncate text-xs text-sq-text-secondary">{r.description}</div>
                    <div className="mt-0.5 flex flex-wrap gap-x-3 text-[10px] text-sq-text-tertiary">
                      {r.category && <span>{r.category}</span>}
                      {r.package && <span>{r.package}</span>}
                      {r.pinCount ? <span>{r.pinCount} pins</span> : null}
                      {r.jlcStock != null && <span>JLC {r.jlcStock.toLocaleString()}</span>}
                    </div>
                  </div>
                  <Button size="sm" className="h-7 shrink-0 px-2 text-xs" onClick={() => insert(r)}>
                    Insert
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
