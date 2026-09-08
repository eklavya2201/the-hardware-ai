import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { CircuitBoard, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { SYNQIT_API_BASE } from '@/lib/synqitApi';
import { cn } from '@/lib/utils';
import { useAuth } from '@/contexts/AuthContext';
import { setSynqitProject } from '@/lib/synqitProject';
import { fetchSynqitProject } from '@/lib/synqitData';
import { buildClientBrief } from '@/lib/synqitBrief';

type SynqitProject = { id: string; name: string; updatedAt?: string };
type Brief = { entities: unknown; openscad: string; prompt_block: string };

/**
 * "Board" — pull a Synqit project's board into the prompt as facts:
 * outline, thickness, holes, part envelopes, connector openings, clearances,
 * plus a generated `synqit_board()` OpenSCAD module. The agent designs the
 * enclosure around real geometry instead of a screenshot.
 *
 * Reads the Synqit API with the user's Synqit cookie (credentials: include),
 * so it works for anyone signed in to Synqit in this browser. Opening the app
 * with `?project=<id>` inserts that board automatically once.
 */
export function BoardButton({
  onInsert,
  disabled,
  showFullLabels = true,
}: {
  onInsert: (block: string) => void;
  disabled?: boolean;
  showFullLabels?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<SynqitProject[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autoInserted = useRef(false);

  const fetchJson = async <T,>(path: string): Promise<T> => {
    const res = await fetch(`${SYNQIT_API_BASE}${path}`, { credentials: 'include' });
    if (res.status === 401) throw new Error('not_signed_in');
    if (!res.ok) throw new Error(`${res.status}`);
    return (await res.json()) as T;
  };

  const insertProject = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      // Server brief first (curated part heights); if this Synqit backend
      // does not have /cad-brief yet (production today), build it here from
      // the project JSON with estimated heights.
      let brief: Brief;
      try {
        brief = await fetchJson<Brief>(`/projects/${encodeURIComponent(id)}/cad-brief`);
      } catch (e) {
        if (e instanceof Error && e.message === 'not_signed_in') throw e;
        const project = await fetchSynqitProject(id, true);
        const local = buildClientBrief(project);
        if (!local) throw new Error('404');
        brief = local;
      }
      const ents = brief.entities as { project?: { id?: string; name?: string } } | null;
      setSynqitProject({ id, name: ents?.project?.name });
      onInsert(`${brief.prompt_block}\n\n\`\`\`openscad\n${brief.openscad}\n\`\`\``);
      setOpen(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(
        msg === 'not_signed_in'
          ? 'Sign in to Synqit first.'
          : msg === '404'
            ? 'That project has no board yet (place parts first).'
            : `Could not load the board (${msg}).`,
      );
    } finally {
      setBusy(null);
    }
  };

  const loadProjects = async () => {
    setError(null);
    try {
      const list = await fetchJson<SynqitProject[]>('/projects');
      setProjects(list);
    } catch (e) {
      setProjects([]);
      setError(e instanceof Error && e.message === 'not_signed_in' ? 'Sign in to Synqit first.' : 'Could not reach Synqit.');
    }
  };

  // ?project=<id> on the URL: insert once the app has a signed-in user (the
  // composer remounts when auth resolves, which would wipe an earlier insert),
  // then drop the param so a reload does not insert it twice.
  const { user, isLoading } = useAuth();
  useEffect(() => {
    if (autoInserted.current || typeof window === 'undefined') return;
    if (isLoading || !user) return;
    const id = new URLSearchParams(window.location.search).get('project');
    if (!id) return;
    autoInserted.current = true;
    void insertProject(id).then(() => {
      const url = new URL(window.location.href);
      url.searchParams.delete('project');
      window.history.replaceState({}, '', url.toString());
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, isLoading]);

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (v && projects === null) void loadProjects();
      }}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              disabled={disabled}
              className={cn(
                'flex h-8 items-center gap-1.5 rounded-lg border border-[#2a2a2a] bg-sq-background-2 px-2 text-sm transition-colors',
                open ? 'border-sq-blue/50 bg-sq-blue/10 text-sq-blue' : 'text-sq-text-secondary hover:bg-sq-bg-secondary-dark',
              )}
              onClick={(e) => e.stopPropagation()}
            >
              <CircuitBoard className="h-4 w-4" />
              {showFullLabels && <span className="hidden text-xs lg:inline">Board</span>}
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>Design around a Synqit PCB: inserts its outline, holes, parts and connector openings</TooltipContent>
      </Tooltip>
      <PopoverContent align="start" className="w-[24rem] border-sq-neutral-800 bg-sq-bg-secondary-dark p-3 text-sq-text-primary">
        <div className="mb-2 text-xs text-sq-text-secondary">Your Synqit projects</div>
        {projects === null && !error && <Loader2 className="h-4 w-4 animate-spin" />}
        {error && (
          <p className="text-xs text-sq-text-secondary">
            {error}{' '}
            {error.startsWith('Sign in') && (
              <Link to="/signin" className="text-sq-blue hover:underline">
                Sign in
              </Link>
            )}
          </p>
        )}
        <ul className="max-h-72 space-y-1 overflow-y-auto">
          {(projects ?? []).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void insertProject(p.id)}
                className="flex w-full items-center justify-between rounded-md border border-sq-neutral-800 px-2 py-1.5 text-left text-sm hover:bg-sq-bg-dark disabled:opacity-60"
              >
                <span className="truncate">{p.name}</span>
                {busy === p.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <span className="text-xs text-sq-text-tertiary">insert</span>}
              </button>
            </li>
          ))}
        </ul>
        {projects?.length === 0 && !error && <p className="text-xs text-sq-text-secondary">No projects yet.</p>}
      </PopoverContent>
    </Popover>
  );
}
