import { useState, type ReactNode } from 'react';
import { CircuitBoard } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SYNQIT_APP_URL, useSynqitProject } from '@/lib/synqitProject';
import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';

/**
 * The right-hand panel of the studio: Parameters plus the Synqit views for
 * the linked project. One tab strip, the user picks one and it opens in place.
 * Synqit views are the real Synqit editor in embed mode
 * (`/projects/:id?embed=1&tab=<view>`), so they are live and editable.
 */
type SynqitView = 'schematic' | 'pcb' | 'bom' | 'guide' | 'library';
type TabId = 'parameters' | SynqitView;

const TABS: { id: TabId; label: string }[] = [
  { id: 'parameters', label: 'Parameters' },
  { id: 'schematic', label: 'Schematic' },
  { id: 'pcb', label: 'PCB' },
  { id: 'bom', label: 'BOM' },
  { id: 'guide', label: 'Build guide' },
  { id: 'library', label: 'Library' },
];

export function synqitViewUrl(view: SynqitView, projectId: string | null): string | null {
  if (view === 'library') return `${SYNQIT_APP_URL}/library?embed=1`;
  if (!projectId) return null;
  const tab = view === 'bom' ? 'order' : view === 'guide' ? 'guide' : view;
  return `${SYNQIT_APP_URL}/projects/${encodeURIComponent(projectId)}?embed=1&tab=${tab}`;
}

export function SynqitPanel({ parameters }: { parameters: ReactNode }) {
  const [tab, setTab] = useState<TabId>('parameters');
  const project = useSynqitProject();

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-sq-neutral-800 px-2 pt-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              'whitespace-nowrap rounded-t-md px-3 py-1.5 text-xs transition-colors',
              tab === t.id
                ? 'bg-sq-bg-dark text-sq-text-primary'
                : 'text-sq-text-secondary hover:text-sq-text-primary',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="relative min-h-0 flex-1">
        {tab === 'parameters' ? (
          parameters
        ) : (
          <SynqitView view={tab} projectId={project?.id ?? null} projectName={project?.name} />
        )}
      </div>
    </div>
  );
}

function SynqitView({
  view,
  projectId,
  projectName,
}: {
  view: SynqitView;
  projectId: string | null;
  projectName?: string;
}) {
  const url = synqitViewUrl(view, projectId);
  if (!url) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-sq-text-secondary">
        <CircuitBoard className="h-8 w-8 opacity-60" />
        <p>No Synqit project linked yet.</p>
        <p className="text-xs">
          Use the <span className="text-sq-text-primary">Board</span> button in the composer to pick a project, or open the studio from a Synqit project. Signed out of Synqit?{' '}
          <a href={synqitHandoffUrl('/')} className="text-sq-blue hover:underline">
            Continue with Synqit
          </a>
          .
        </p>
      </div>
    );
  }
  return (
    <div className="absolute inset-0">
      <iframe
        key={url}
        title={`Synqit ${view}${projectName ? ` · ${projectName}` : ''}`}
        src={url}
        className="h-full w-full border-0 bg-sq-bg-dark"
        allow="clipboard-write; fullscreen"
      />
    </div>
  );
}
