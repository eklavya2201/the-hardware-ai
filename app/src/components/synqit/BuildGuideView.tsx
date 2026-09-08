import { useEffect, useState } from 'react';
import { CheckCircle2, Download, Loader2, RefreshCw, Wrench } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { SYNQIT_API_BASE } from '@/lib/synqitApi';

/**
 * The assembly guide for the linked Synqit project, rendered natively in the
 * studio theme. Reads `GET /projects/{id}/build-guide`; when there is none it
 * offers `POST /projects/{id}/build-guide/generate` (deterministic skeleton,
 * the model may only reword it). Steps are checkable so a builder can work
 * down the list; ticks are per-project and live in this browser only.
 */
type Step = { n: number; title: string; body: string; refdes: string[]; check?: string | null };
type Guide = {
  summary: string;
  tools: string[];
  partsToOrder: string[];
  steps: Step[];
  powerUpChecks: string[];
  gotchas: string[];
  enclosure: string[];
  generated_at?: string;
  model_used?: string | null;
};

const doneKey = (id: string) => `synqit.guideDone.${id}`;

export function BuildGuideView({ projectId, projectName }: { projectId: string; projectName?: string }) {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Set<number>>(new Set());

  useEffect(() => {
    try {
      const raw = localStorage.getItem(doneKey(projectId));
      setDone(new Set(raw ? (JSON.parse(raw) as number[]) : []));
    } catch {
      setDone(new Set());
    }
  }, [projectId]);

  const toggle = (n: number) => {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      try {
        localStorage.setItem(doneKey(projectId), JSON.stringify([...next]));
      } catch {
        /* storage unavailable; ticks stay in memory */
      }
      return next;
    });
  };

  const load = async () => {
    setState('loading');
    setError(null);
    try {
      const res = await fetch(`${SYNQIT_API_BASE}/projects/${encodeURIComponent(projectId)}/build-guide`, { credentials: 'include' });
      if (res.status === 401) throw new Error('not_signed_in');
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as Guide | null;
      if (!data || !data.steps?.length) {
        setGuide(null);
        setState('empty');
      } else {
        setGuide(data);
        setState('ready');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setState('error');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${SYNQIT_API_BASE}/projects/${encodeURIComponent(projectId)}/build-guide/generate`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || `generate failed (${res.status})`);
      }
      const project = (await res.json()) as { buildGuide?: Guide };
      if (project.buildGuide?.steps?.length) {
        setGuide(project.buildGuide);
        setState('ready');
      } else await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (state === 'loading') return <Center><Loader2 className="h-5 w-5 animate-spin" /></Center>;
  if (state === 'error')
    return (
      <Center>
        {error === 'not_signed_in' ? 'Sign in to Synqit to see the build guide.' : `Could not load the build guide (${error}).`}
      </Center>
    );
  if (state === 'empty')
    return (
      <Center>
        <Wrench className="mx-auto mb-3 h-7 w-7 opacity-60" />
        <p className="mb-1 text-sq-text-primary">No build guide yet</p>
        <p className="mb-4 text-xs">Synqit writes the assembly order from the board: power stage first, fine-pitch parts before the rest, connectors last.</p>
        <Button onClick={() => void generate()} disabled={busy} className="bg-sq-blue/15 text-sq-blue hover:bg-sq-blue/25">
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Wrench className="mr-2 h-4 w-4" />}
          Generate build guide
        </Button>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
      </Center>
    );

  const g = guide!;
  const total = g.steps.length;
  const complete = g.steps.filter((s) => done.has(s.n)).length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-sq-neutral-800 px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-sq-text-primary">Build guide{projectName ? ` · ${projectName}` : ''}</div>
          <div className="text-[11px] text-sq-text-secondary">
            {complete}/{total} steps done{g.model_used ? ` · reworded by ${g.model_used}` : ' · deterministic'}
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          <a
            href={`${SYNQIT_API_BASE}/projects/${encodeURIComponent(projectId)}/export/build-guide`}
            target="_blank"
            rel="noreferrer"
            title="Download as Markdown"
            className="rounded p-1.5 text-sq-text-secondary hover:text-sq-text-primary"
          >
            <Download className="h-4 w-4" />
          </a>
          <button
            type="button"
            onClick={() => void generate()}
            disabled={busy}
            title="Regenerate from the current design"
            className="rounded p-1.5 text-sq-text-secondary hover:text-sq-text-primary disabled:opacity-50"
          >
            <RefreshCw className={busy ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 text-sm">
        {g.summary && <p className="mb-3 text-sq-text-secondary">{g.summary}</p>}
        {error && <p className="mb-3 text-xs text-red-400">{error}</p>}

        <Section title="Tools" items={g.tools} />
        <Section title="Parts to order" items={g.partsToOrder} />

        <ol className="my-3 space-y-2">
          {g.steps.map((s) => {
            const isDone = done.has(s.n);
            return (
              <li key={s.n}>
                <button
                  type="button"
                  onClick={() => toggle(s.n)}
                  className={`flex w-full gap-2 rounded-md border p-2 text-left transition-colors ${
                    isDone ? 'border-sq-neutral-800 bg-sq-bg-dark/60 opacity-60' : 'border-sq-neutral-800 hover:border-sq-neutral-700'
                  }`}
                >
                  <CheckCircle2 className={`mt-0.5 h-4 w-4 shrink-0 ${isDone ? 'text-sq-blue' : 'text-sq-text-tertiary'}`} />
                  <span className="min-w-0">
                    <span className={`block text-sq-text-primary ${isDone ? 'line-through' : ''}`}>
                      {s.n}. {s.title}
                    </span>
                    {s.body && <span className="mt-0.5 block text-xs text-sq-text-secondary">{s.body}</span>}
                    {s.refdes?.length > 0 && (
                      <span className="mt-1 flex flex-wrap gap-1">
                        {s.refdes.map((r) => (
                          <span key={r} className="rounded bg-sq-neutral-800 px-1 py-0.5 font-mono text-[10px] text-sq-text-secondary">
                            {r}
                          </span>
                        ))}
                      </span>
                    )}
                    {s.check && <span className="mt-1 block text-[11px] text-sq-blue">Check: {s.check}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>

        <Section title="Power-up checks" items={g.powerUpChecks} />
        <Section title="Gotchas" items={g.gotchas} />
        <Section title="Enclosure" items={g.enclosure} />
      </div>
    </div>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mb-3">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-sq-text-tertiary">{title}</div>
      <ul className="list-disc space-y-0.5 pl-4 text-xs text-sq-text-secondary">
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
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
