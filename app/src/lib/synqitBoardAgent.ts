import { SYNQIT_API_BASE } from '@/components/auth/SynqitSignInButton';
import { fetchSynqitProject, type SqProject } from './synqitData';
import { setSynqitProject } from './synqitProject';

/**
 * Drive Synqit's electronics engine from the studio: create a project, run the
 * schematic agent on a brief, then place and route the board. Everything goes
 * through the user's own Synqit session (cookie, CORS with credentials), so
 * the work lands in their account exactly as if they had used Synqit directly.
 *
 * The chat endpoint streams Server-Sent Events; we read the stream for
 * progress and take the final `snapshot` event as the result, falling back to
 * a plain project fetch if the stream ends without one.
 */
export type BoardProgress = { phase: string; detail?: string };

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${SYNQIT_API_BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw new Error('not_signed_in');
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || `synqit ${res.status}`);
  }
  return (await res.json()) as T;
}

/** POST /projects — a new hardware project in the user's Synqit account. */
export async function createProject(name: string): Promise<{ id: string; name: string }> {
  return post<{ id: string; name: string }>('/projects', { name, kind: 'hardware' });
}

/**
 * POST /projects/{id}/chat — run the schematic agent and stream its events.
 * Resolves with the project snapshot the stream ends on.
 */
export async function runBoardAgent(
  projectId: string,
  brief: string,
  onProgress?: (p: BoardProgress) => void,
): Promise<SqProject | null> {
  const res = await fetch(`${SYNQIT_API_BASE}/projects/${encodeURIComponent(projectId)}/chat`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ text: brief }),
  });
  if (res.status === 401) throw new Error('not_signed_in');
  if (!res.ok || !res.body) throw new Error(`synqit chat ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let snapshot: SqProject | null = null;

  // Minimal SSE parse: blocks separated by a blank line, `event:` + `data:`.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = /^event:\s*(.+)$/m.exec(block)?.[1]?.trim();
      const data = block
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('');
      if (!event) continue;
      if (event === 'snapshot' && data) {
        try {
          snapshot = JSON.parse(data) as SqProject;
        } catch {
          /* keep whatever we have */
        }
      } else if (event === 'error') {
        throw new Error(data.slice(0, 200) || 'the board agent failed');
      } else if (event === 'tool_use') {
        let name = '';
        try {
          name = (JSON.parse(data) as { name?: string }).name ?? '';
        } catch {
          /* progress only */
        }
        onProgress?.({ phase: 'designing', detail: name });
      } else if (event === 'assistant_message') {
        onProgress?.({ phase: 'designing', detail: 'writing up the design' });
      }
    }
  }
  return snapshot;
}

/** Place and route. Both are best-effort: a board that will not route is still a board. */
export async function layoutBoard(projectId: string, onProgress?: (p: BoardProgress) => void): Promise<void> {
  onProgress?.({ phase: 'placing' });
  await post(`/projects/${encodeURIComponent(projectId)}/pcb/auto-place`);
  onProgress?.({ phase: 'routing' });
  try {
    await post(`/projects/${encodeURIComponent(projectId)}/pcb/auto-route`);
  } catch {
    /* unrouted nets show as ratlines in the PCB view */
  }
}

export type BoardResult = {
  projectId: string;
  name: string;
  components: number;
  nets: number;
  placed: number;
  traces: number;
  unrouted: number;
  errors: number;
  warnings: number;
  boardSize?: string;
};

/**
 * The whole run: create, design, lay out, link it to this studio session so
 * the PCB and Schematic views and the Board brief all point at it. Returns a
 * compact summary — numbers, not prose — for the agent to reason about.
 */
export async function designBoard(
  name: string,
  brief: string,
  onProgress?: (p: BoardProgress) => void,
): Promise<BoardResult> {
  onProgress?.({ phase: 'creating' });
  const created = await createProject(name);
  setSynqitProject({ id: created.id, name: created.name });
  window.dispatchEvent(new Event('synqit-project-change'));

  await runBoardAgent(created.id, brief, onProgress);
  await layoutBoard(created.id, onProgress);

  const project = await fetchSynqitProject(created.id, true);
  window.dispatchEvent(new Event('synqit-project-change'));

  const issues = project.issues ?? [];
  const board = project.board;
  return {
    projectId: created.id,
    name: project.name,
    components: project.ir?.components?.length ?? 0,
    nets: project.ir?.nets?.length ?? 0,
    placed: board?.components?.length ?? 0,
    traces: board?.traces?.length ?? 0,
    unrouted: board?.rats?.length ?? 0,
    errors: issues.filter((i) => i.severity === 'error').length,
    warnings: issues.filter((i) => i.severity === 'warning').length,
    boardSize: board ? `${board.width.toFixed(1)} x ${board.height.toFixed(1)} mm` : undefined,
  };
}
