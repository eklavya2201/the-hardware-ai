import { useState, type ReactNode } from 'react';
import { Box, CircuitBoard, GitBranch, Loader2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSynqitProject } from '@/lib/synqitProject';
import { fetchSynqitProject, useSynqitProjectData } from '@/lib/synqitData';
import { PcbView } from './PcbView';
import { SchematicView } from './SchematicView';
import { INSERT_EVENT, SEND_EVENT } from './LibraryDialog';
import { activityLabel, useBoardActivity } from '@/lib/boardProgress';
import { Link } from '@tanstack/react-router';

/**
 * Example briefs for the empty PCB and schematic space. Clicking one drops it
 * into the composer rather than sending it, so the person can shape it first.
 */
const STARTERS = [
  'Design a board: ESP32-C3, USB-C power, a 3.3 V regulator, a status LED and a user button.',
  'Design a board: RP2040 with a microSD slot, an IMU and a JST battery input.',
  'Design a board: ATtiny85 driving four WS2812 LEDs from a coin cell.',
];

/** Put it in the composer for the person to shape. */
function insertPrompt(text: string) {
  window.dispatchEvent(new CustomEvent(INSERT_EVENT, { detail: text }));
}

/** Do it now — what a button labelled "Place and route it" should mean. */
function runPrompt(text: string) {
  window.dispatchEvent(new CustomEvent(SEND_EVENT, { detail: text }));
}

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
              <p className="mb-2 text-sq-text-primary">
                {mode === 'pcb' ? 'No board here yet' : 'No schematic here yet'}
              </p>
              <p className="mb-4">
                Describe the circuit in the chat and it is designed here — parts chosen from the library,
                schematic wired, board placed and routed, then drawn in this space.
              </p>
              <div className="flex flex-col items-stretch gap-1.5">
                {STARTERS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => insertPrompt(prompt)}
                    className="rounded-md border border-sq-neutral-600 px-3 py-1.5 text-left text-xs text-sq-text-secondary hover:border-sq-blue hover:text-sq-text-primary"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </Empty>
          ) : loading ? (
            <Empty>
              <Loader2 className="h-5 w-5 animate-spin" />
            </Empty>
          ) : error ? (
            <Empty>
              {error === 'not_signed_in' ? (
                <>
                  <p className="mb-3">Your session has expired. Sign in again to see this board.</p>
                  <Link
                    to="/signin"
                    className="rounded-md bg-sq-blue/15 px-3 py-1.5 text-xs text-sq-blue hover:bg-sq-blue/25"
                  >
                    Sign in
                  </Link>
                </>
              ) : (
                `Could not load the project (${error}).`
              )}
            </Empty>
          ) : mode === 'pcb' ? (
            data?.board && data.board.components.length ? (
              <PcbView board={data.board} mechanical={data.mechanical} components={data.ir?.components ?? []} />
            ) : (
              <Empty>
                <p className="mb-3">This board has a schematic but no placed parts yet.</p>
                <button
                  type="button"
                  onClick={() => runPrompt('Place and route this board.')}
                  className="rounded-md bg-sq-blue/15 px-3 py-1.5 text-xs text-sq-blue hover:bg-sq-blue/25"
                >
                  Place and route it
                </button>
              </Empty>
            )
          ) : data?.ir?.components?.length ? (
            <SchematicView components={data.ir.components} nets={data.ir.nets} />
          ) : (
            <Empty>
              <p className="mb-3">This project has no schematic yet.</p>
              <button
                type="button"
                onClick={() => runPrompt('Design the schematic for this board.')}
                className="rounded-md bg-sq-blue/15 px-3 py-1.5 text-xs text-sq-blue hover:bg-sq-blue/25"
              >
                Design the schematic
              </button>
            </Empty>
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
      <BoardActivityOverlay />
    </div>
  );
}

/**
 * What the board agent is doing, over whichever view is open. Without it a run
 * that takes half a minute looks like a hang: the pane keeps showing the same
 * empty state until everything lands at once.
 */
function BoardActivityOverlay() {
  const activity = useBoardActivity();
  if (!activity.active) return null;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
      <div className="flex items-center gap-2.5 rounded-full border border-sq-neutral-600 bg-sq-bg-dark/90 px-4 py-2 text-xs text-sq-text-primary shadow-lg backdrop-blur">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-sq-blue" />
        <span>{activityLabel(activity)}</span>
        {activity.detail && (
          <span className="max-w-[240px] truncate text-sq-text-secondary">· {activity.detail}</span>
        )}
      </div>
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
