import { useState } from 'react';
import { Cpu, Loader2, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { apiUrl } from '@/services/api';
import { cn } from '@/lib/utils';

type PartHit = {
  mpn: string;
  manufacturer: string;
  description: string;
  datasheetUrl: string | null;
  specs: { name: string; value: string }[];
  totalAvail: number | null;
  medianPriceUsd: number | null;
  octopartUrl: string | null;
  promptBlock: string;
};

/**
 * "Parts" — Octopart lookup (through the Nexar API) from the composer. Pick a
 * part and its MPN, package and dimension specs are inserted into the prompt,
 * so the model builds around real component sizes instead of guessing.
 */
export function PartsButton({
  onInsert,
  disabled,
  showFullLabels = true,
}: {
  onInsert: (block: string) => void;
  disabled?: boolean;
  showFullLabels?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<PartHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    const query = q.trim();
    if (query.length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${apiUrl('parts')}?q=${encodeURIComponent(query)}&limit=6`);
      const data = (await res.json()) as { parts?: PartHit[]; error?: string; message?: string };
      if (!res.ok) {
        setHits([]);
        setError(data.message || (data.error === 'parts_not_configured'
          ? 'Parts lookup is not configured (Nexar credentials).'
          : `Lookup failed (${res.status}).`));
        return;
      }
      setHits(data.parts ?? []);
      if (!data.parts?.length) setError('No parts found.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Lookup failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              disabled={disabled}
              className={cn(
                'flex h-8 items-center gap-1.5 rounded-lg border border-[#2a2a2a] bg-sq-background-2 px-2 text-sm transition-colors',
                open
                  ? 'border-sq-blue/50 bg-sq-blue/10 text-sq-blue'
                  : 'text-sq-text-secondary hover:bg-sq-bg-secondary-dark',
              )}
              onClick={(e) => e.stopPropagation()}
            >
              <Cpu className="h-4 w-4" />
              {showFullLabels && <span className="hidden text-xs lg:inline">Parts</span>}
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Look up a component on Octopart and drop its dimensions into the prompt</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-[26rem] border-sq-neutral-800 bg-sq-bg-secondary-dark p-3 text-sq-text-primary">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <Input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="MPN or description, e.g. ESP32-WROOM-32 or 18650 holder"
            className="h-9 border-sq-neutral-700 bg-sq-bg-dark text-sm"
          />
          <Button type="submit" size="sm" disabled={busy || q.trim().length < 2} className="h-9">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </Button>
        </form>
        {error && <p className="mt-2 text-xs text-sq-text-secondary">{error}</p>}
        <ul className="mt-2 max-h-72 space-y-1 overflow-y-auto">
          {hits.map((p) => (
            <li key={`${p.manufacturer}-${p.mpn}`} className="rounded-md border border-sq-neutral-800 p-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {p.mpn} <span className="font-normal text-sq-text-secondary">{p.manufacturer}</span>
                  </div>
                  <div className="truncate text-xs text-sq-text-secondary">{p.description}</div>
                  <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-sq-text-tertiary">
                    {p.specs.slice(0, 4).map((s) => (
                      <span key={s.name}>{s.name}: {s.value}</span>
                    ))}
                    {p.totalAvail != null && <span>stock {p.totalAvail.toLocaleString()}</span>}
                    {p.medianPriceUsd != null && <span>~${p.medianPriceUsd.toFixed(2)} @1k</span>}
                  </div>
                </div>
                <Button
                  size="sm"
                  className="h-7 shrink-0 px-2 text-xs"
                  onClick={() => {
                    onInsert(p.promptBlock);
                    setOpen(false);
                  }}
                >
                  Insert
                </Button>
              </div>
              {(p.datasheetUrl || p.octopartUrl) && (
                <div className="mt-1 flex gap-3 text-[11px]">
                  {p.datasheetUrl && (
                    <a href={p.datasheetUrl} target="_blank" rel="noreferrer" className="text-sq-blue hover:underline">
                      datasheet
                    </a>
                  )}
                  {p.octopartUrl && (
                    <a href={p.octopartUrl} target="_blank" rel="noreferrer" className="text-sq-blue hover:underline">
                      octopart
                    </a>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
