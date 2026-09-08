import { useState, type ReactNode } from 'react';
import { Box, CircuitBoard, GitBranch, Loader2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSynqitProject } from '@/lib/synqitProject';
import { fetchSynqitProject, useSynqitProjectData } from '@/lib/synqitData';
import { PcbView } from './PcbView';
import { SchematicView } from './SchematicView';
import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';

/**
 * The studio's centre space. A floating 3D / PCB / Schematic switcher sits
 * over it; the 3D preview stays mounted (hidden) so switching back never
 * recompiles the model. PCB and schematic are drawn natively from the linked
 * Synqit project, in the studio theme.
 */
type Mode = '3d' | 'pcb' | 'schematic';

export function StudioViewport({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>('3d');
  const project = useSynqitProject();
  const { data, error, loading } = useSynqitProjectData(mode === '3d' ? null : (project?.id ?? null));
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    if (!project?.id) return;
    setRefreshing(true);
    try {
      await fetchSynqitProject(project.id, true);
      setMode((m) => m); // data hook re-reads via the cache on next render
      window.dispatchEvent(new Event('synqit-project-change'));
    } finally {
      setRefreshing(false);
    }
  };

  const items: { id: Mode; label: string; icon: ReactNode }[] = [
    { id: '3d', label: '3D', icon: <Box className="h-3.5 w-3.5" /> },
    { id: 'pcb', label: 'PCB', icon: <CircuitBoard className="h-3.5 w-3.5" /> },
    { id: 'schematic', label: 'Schematic', icon: <GitBranch className="h-3.5 w-3.5" /> },
  ];

  return (
    <div className="relative h-full w-full">
      <div className={cn('h-full w-full', mode !== '3d' && 'invisible absolute inset-0')}>{children}</div>
      {mode !== '3d' && (
        <div className="absolute inset-0">
          {!project?.id ? (
            <Empty>
              No Synqit project linked. Use the <b>Board</b> button in the composer to pick one, or open the studio from a Synqit project.
            </Empty>
          ) : loading ? (
            <Empty>
              <Loader2 className="h-5 w-5 animate-spin" />
            </Empty>
          ) : error ? (
            <Empty>
              {error === 'not_signed_in' ? (
                <>
                  Sign in to Synqit to see this board.{' '}
                  <a href={synqitHandoffUrl('/')} className="text-sq-blue hover:underline">
                    Continue with Synqit
                  </a>
                </>
              ) : (
                `Could not load the project (${error}).`
              )}
            </Empty>
          ) : mode === 'pcb' ? (
            data?.board && data.board.components.length ? (
              <PcbView board={data.board} mechanical={data.mechanical} />
            ) : (
              <Empty>This project has no placed board yet. Place parts in Synqit first.</Empty>
            )
          ) : data?.ir?.components?.length ? (
            <SchematicView components={data.ir.components} nets={data.ir.nets} />
          ) : (
            <Empty>This project has no schematic yet.</Empty>
          )}
        </div>
      )}

      {/* floating switcher */}
      <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
        <div className="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-sq-neutral-800 bg-sq-bg-dark/85 p-0.5 shadow-lg backdrop-blur">
          {items.map((it) => (
            <button
              key={it.id}
              type="button"
              onClick={() => setMode(it.id)}
              className={cn(
                'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors',
                mode === it.id ? 'bg-sq-neutral-800 text-sq-text-primary' : 'text-sq-text-secondary hover:text-sq-text-primary',
              )}
            >
              {it.icon}
              {it.label}
            </button>
          ))}
          {mode !== '3d' && project?.id && (
            <button
              type="button"
              onClick={() => void refresh()}
              title="Reload from Synqit"
              className="ml-1 rounded-md p-1 text-sq-text-secondary hover:text-sq-text-primary"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', refreshing && 'animate-spin')} />
            </button>
          )}
        </div>
      </div>
      {mode !== '3d' && project?.name && (
        <div className="pointer-events-none absolute right-3 top-3 rounded-md bg-sq-bg-dark/80 px-2 py-1 text-[11px] text-sq-text-secondary backdrop-blur">
          Synqit · {project.name}
        </div>
      )}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center bg-sq-neutral-700 p-8 text-center text-sm text-sq-text-secondary">
      <div className="max-w-sm">{children}</div>
    </div>
  );
}
