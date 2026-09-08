import { useMemo } from 'react';
import { Download, ExternalLink, Loader2 } from 'lucide-react';
import { SYNQIT_API_BASE, SYNQIT_APP_URL } from '@/lib/synqitApi';
import { useSynqitProjectData } from '@/lib/synqitData';

/**
 * Bill of materials for the linked Synqit project, in the studio theme.
 * Reads the `bom` array already on the project (no extra endpoint), shows
 * quantity, unit cost and the price source, and links out to Synqit for the
 * live-price refresh and the CSV exports the fabs want.
 */
type BomLine = {
  refdes: string;
  description?: string | null;
  qty: number;
  unitCost?: number | null;
  mpn?: string | null;
  priceSource?: string | null;
  priceVendor?: string | null;
  stock?: number | null;
  lifecycle?: string | null;
  priceStale?: boolean;
};

export function BomView({ projectId, projectName }: { projectId: string; projectName?: string }) {
  const { data, error, loading } = useSynqitProjectData(projectId);
  const bom = (data as unknown as { bom?: BomLine[] } | null)?.bom ?? [];

  const totals = useMemo(() => {
    const lines = bom.length;
    const parts = bom.reduce((n, b) => n + (b.qty || 0), 0);
    const cost = bom.reduce((n, b) => n + (b.unitCost ?? 0) * (b.qty || 0), 0);
    const unpriced = bom.filter((b) => b.unitCost == null).length;
    return { lines, parts, cost, unpriced };
  }, [bom]);

  if (loading) return <Center><Loader2 className="h-5 w-5 animate-spin" /></Center>;
  if (error) return <Center>{error === 'not_signed_in' ? 'Sign in to Synqit to see the BOM.' : `Could not load the BOM (${error}).`}</Center>;
  if (!bom.length) return <Center>No BOM yet. Add parts to the design in Synqit first.</Center>;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-sq-neutral-800 px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-sq-text-primary">BOM{projectName ? ` · ${projectName}` : ''}</div>
          <div className="text-[11px] text-sq-text-secondary">
            {totals.lines} lines · {totals.parts} parts · ~${totals.cost.toFixed(2)} per board
            {totals.unpriced ? ` · ${totals.unpriced} unpriced` : ''}
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          <a
            href={`${SYNQIT_API_BASE}/projects/${encodeURIComponent(projectId)}/export/bom`}
            target="_blank"
            rel="noreferrer"
            title="Download BOM CSV"
            className="rounded p-1.5 text-sq-text-secondary hover:text-sq-text-primary"
          >
            <Download className="h-4 w-4" />
          </a>
          <a
            href={`${SYNQIT_APP_URL}/projects/${encodeURIComponent(projectId)}`}
            target="_blank"
            rel="noreferrer"
            title="Open in Synqit for live prices and ordering"
            className="rounded p-1.5 text-sq-text-secondary hover:text-sq-text-primary"
          >
            <ExternalLink className="h-4 w-4" />
          </a>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-sq-bg-secondary-dark text-[10px] uppercase tracking-wide text-sq-text-tertiary">
            <tr>
              <th className="px-3 py-1.5 font-normal">Ref</th>
              <th className="px-2 py-1.5 font-normal">Part</th>
              <th className="px-2 py-1.5 text-right font-normal">Qty</th>
              <th className="px-3 py-1.5 text-right font-normal">Cost</th>
            </tr>
          </thead>
          <tbody>
            {bom.map((b, i) => (
              <tr key={`${b.refdes}-${i}`} className="border-t border-sq-neutral-800 align-top">
                <td className="px-3 py-1.5 font-mono text-sq-text-primary">{b.refdes}</td>
                <td className="px-2 py-1.5">
                  <div className="text-sq-text-primary">{b.mpn || '—'}</div>
                  {b.description && <div className="text-[11px] text-sq-text-secondary">{b.description}</div>}
                  <div className="mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-sq-text-tertiary">
                    {b.priceSource && <span>{b.priceSource}{b.priceVendor ? ` · ${b.priceVendor}` : ''}</span>}
                    {b.stock != null && <span>stock {b.stock.toLocaleString()}</span>}
                    {b.lifecycle && <span>{b.lifecycle}</span>}
                    {b.priceStale && <span className="text-amber-400">stale price</span>}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right text-sq-text-primary">{b.qty}</td>
                <td className="px-3 py-1.5 text-right text-sq-text-primary">
                  {b.unitCost == null ? <span className="text-sq-text-tertiary">—</span> : `$${(b.unitCost * (b.qty || 1)).toFixed(3)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center text-sm text-sq-text-secondary">
      <div className="max-w-xs">{children}</div>
    </div>
  );
}
