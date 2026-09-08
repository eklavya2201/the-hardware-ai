import { useState, type ReactNode } from 'react';
import { CircuitBoard } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useSynqitProject } from '@/lib/synqitProject';
import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';
import { BomView } from './BomView';
import { BuildGuideView } from './BuildGuideView';

/**
 * The studio's tool panel: Parameters plus the Synqit views for the linked
 * project. Everything is drawn natively in the studio theme — no framed
 * Synqit pages. Schematic and PCB live in the centre viewport instead, behind
 * the floating 3D / PCB / Schematic switcher.
 */
type TabId = 'parameters' | 'bom' | 'guide';

const TABS: { id: TabId; label: string }[] = [
  { id: 'parameters', label: 'Parameters' },
  { id: 'bom', label: 'BOM' },
  { id: 'guide', label: 'Build guide' },
];

export function SynqitPanel({ parameters }: { parameters: ReactNode }) {
  const [tab, setTab] = useState<TabId>('parameters');
  const project = useSynqitProject();

  return (
    <div className="flex h-full flex-col">
      {/* pl-12 clears the app's fixed sidebar toggle, which sits ~16px into
          whichever panel is leftmost — this one, since the swap. */}
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-sq-neutral-800 pl-12 pr-2 pt-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              'whitespace-nowrap rounded-t-md px-3 py-1.5 text-xs transition-colors',
              tab === t.id ? 'bg-sq-bg-dark text-sq-text-primary' : 'text-sq-text-secondary hover:text-sq-text-primary',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="relative min-h-0 flex-1">
        {tab === 'parameters' ? (
          parameters
        ) : !project?.id ? (
          <NoProject />
        ) : tab === 'bom' ? (
          <BomView projectId={project.id} projectName={project.name} />
        ) : (
          <BuildGuideView projectId={project.id} projectName={project.name} />
        )}
      </div>
    </div>
  );
}

function NoProject() {
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
