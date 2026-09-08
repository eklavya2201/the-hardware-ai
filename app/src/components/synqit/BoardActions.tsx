import { useState } from 'react';
import { Factory, Loader2, ShoppingCart } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { SYNQIT_API_BASE, SYNQIT_APP_URL } from '@/lib/synqitApi';
import { useSynqitProject } from '@/lib/synqitProject';

type OrderInfo = {
  ready?: boolean;
  boards?: number;
  bom_total?: number | null;
  currency?: string | null;
  blockers?: string[];
  lines?: { mpn?: string; refdes?: string; qty?: number; unit_price?: number | null }[];
};

/**
 * What you do with a finished board: buy the parts, or get it made.
 *
 * Both answers live on the engine — `/order` prices the BOM at the quantity
 * you are building and says whether the design is actually ready, and the
 * Gerbers are what a fab wants. Neither was reachable from the studio, so a
 * board you had just designed had nowhere to go.
 */
export function BoardActions() {
  const project = useSynqitProject();
  const [order, setOrder] = useState<OrderInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!project?.id) return null;

  const loadOrder = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${SYNQIT_API_BASE}/projects/${encodeURIComponent(project.id)}/order?boards=1`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`the engine answered ${res.status}`);
      setOrder((await res.json()) as OrderInfo);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the order.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Popover onOpenChange={(open) => open && !order && void loadOrder()}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            aria-label="Order the parts"
            title="Order the parts"
            className="flex h-8 items-center gap-2 rounded-full px-2.5 text-sq-text-primary hover:bg-sq-neutral-950 hover:text-sq-neutral-10 focus-visible:ring-0"
          >
            <ShoppingCart className="h-[14px] w-[14px] min-w-[14px]" />
            <span className="hidden lg:inline">Order</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 rounded-xl bg-sq-background-1 p-3 text-sm">
          {loading ? (
            <div className="flex items-center gap-2 text-sq-text-secondary">
              <Loader2 className="h-4 w-4 animate-spin" /> Pricing the BOM…
            </div>
          ) : error ? (
            <p className="text-xs text-red-400">{error}</p>
          ) : order ? (
            <div className="space-y-2">
              <div className="flex items-baseline justify-between">
                <span className="text-sq-text-primary">Parts for one board</span>
                <span className="font-medium text-sq-text-primary">
                  {typeof order.bom_total === 'number'
                    ? `${order.currency ?? 'USD'} ${order.bom_total.toFixed(2)}`
                    : '—'}
                </span>
              </div>
              {/* An unknown price is a dash, never a zero. */}
              <p className="text-xs text-sq-text-secondary">
                {order.lines?.length ?? 0} lines · {order.ready ? 'ready to order' : 'not ready yet'}
              </p>
              {order.blockers?.length ? (
                <ul className="list-disc space-y-1 pl-4 text-xs text-amber-400">
                  {order.blockers.slice(0, 4).map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              ) : null}
              <a
                href={`${SYNQIT_API_BASE}/projects/${encodeURIComponent(project.id)}/export/bom`}
                className="block pt-1 text-xs text-sq-blue hover:underline"
              >
                Download the BOM as CSV
              </a>
            </div>
          ) : null}
        </PopoverContent>
      </Popover>

      <Popover>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            aria-label="Manufacture this board"
            title="Manufacture this board"
            className="flex h-8 items-center gap-2 rounded-full px-2.5 text-sq-text-primary hover:bg-sq-neutral-950 hover:text-sq-neutral-10 focus-visible:ring-0"
          >
            <Factory className="h-[14px] w-[14px] min-w-[14px]" />
            <span className="hidden lg:inline">Manufacture</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 rounded-xl bg-sq-background-1 p-3 text-sm">
          <p className="text-xs text-sq-text-secondary">
            Gerbers are what a board house needs. Download them, then upload the zip to your fab.
          </p>
          <div className="mt-3 space-y-2">
            <a
              href={`${SYNQIT_API_BASE}/projects/${encodeURIComponent(project.id)}/export/gerbers`}
              className="block rounded-md bg-sq-blue/15 px-3 py-2 text-center text-xs text-sq-blue hover:bg-sq-blue/25"
            >
              Download Gerbers (.zip)
            </a>
            <a
              href={`${SYNQIT_APP_URL}/projects/${encodeURIComponent(project.id)}`}
              target="_blank"
              rel="noreferrer"
              className="block text-center text-xs text-sq-text-secondary hover:text-sq-text-primary"
            >
              Open this board in Synqit
            </a>
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
