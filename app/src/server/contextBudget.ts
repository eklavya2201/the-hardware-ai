import type { ModelMessage } from 'ai';

/**
 * Keep a CAD conversation inside the model's input window.
 *
 * Nothing used to bound it. Every request rebuilt the whole branch from the
 * messages table and re-sent it, and a parametric turn may take up to 60 steps
 * on top of that, so three things piled up without limit:
 *
 *  - every `build_parametric_model` call carries the COMPLETE OpenSCAD script,
 *    a few thousand tokens each, and a long session holds dozens of revisions;
 *  - every one of those calls comes back with a 1536x1536 nine-view inspection
 *    sheet, re-downloaded and re-inlined as base64 on every request by
 *    `toModelOutput`;
 *  - user-attached reference images are re-inlined the same way.
 *
 * The result was the report that started this: "Input tokens exceed the
 * configured limit of 272000 tokens. Your messages resulted in 332726 tokens."
 * The turn did not degrade — it failed outright, and kept failing, because the
 * next attempt sent the same oversized branch again. A conversation could
 * become permanently unusable.
 *
 * What follows is a ladder, applied only when the estimate is over budget, in
 * the order that costs the model the least understanding:
 *
 *  1. stale inspection sheets. Only the newest render describes the model that
 *     exists now; the numbers measured from every earlier one (bbox, bodies,
 *     the gate's findings) stay in the text beside them. Pictures go, queries
 *     stay -- which is the same trade the board agent already makes.
 *  2. stale OpenSCAD source. Each build emits the entire script, so the newest
 *     one is the whole truth and the ones before it are superseded drafts.
 *  3. older reference images, oldest first.
 *  4. whole early turns, dropped as complete user->assistant->tool groups so a
 *     tool call is never separated from its result (providers reject that).
 *
 * The first user message is never dropped: it is the brief the design is
 * measured against.
 */

/** Rough token count for text. Four characters per token is close enough. */
const CHARS_PER_TOKEN = 4;

/**
 * Flat cost for one image. The inspection sheet is always 1536x1536, which
 * bills around 1.6k tokens on OpenAI and ~3k on Anthropic; the exact number
 * does not matter because images are the first thing the ladder drops.
 */
const IMAGE_TOKENS = 2000;

/** Newest N inspection sheets kept when trimming. */
const KEEP_INSPECTION_IMAGES = 2;

/** Newest N complete OpenSCAD scripts kept when trimming. */
const KEEP_SCAD_SOURCES = 1;

/** Newest N user reference images kept when trimming. */
const KEEP_USER_IMAGES = 1;

/** Turn groups at the end of the branch that stage 4 will never drop. */
const KEEP_RECENT_GROUPS = 3;

const BUILD_TOOL = 'build_parametric_model';

/**
 * How many input tokens this provider will accept, less the output pool and a
 * margin for the system prompt and tool schemas (~29k on the parametric lane).
 *
 * Deliberately pessimistic. Overshooting the budget costs a failed turn;
 * undershooting costs a little re-reading.
 */
export function inputTokenBudget(
  provider: string,
  maxOutputTokens: number,
): number {
  // Context windows, in tokens, for the families this app can select.
  const window =
    provider === 'google'
      ? 1_000_000
      : provider === 'anthropic'
        ? 200_000
        : 272_000; // Azure OpenAI gpt-5.x, the limit in the original report.
  const margin = 40_000; // system prompt, tool schemas, and slack.
  return Math.max(50_000, window - maxOutputTokens - margin);
}

function textTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

/** Estimated input tokens for a set of model messages. */
export function estimateTokens(messages: ModelMessage[]): number {
  let total = 0;
  for (const message of messages) {
    total += 4; // per-message envelope
    const content = message.content as unknown;
    if (typeof content === 'string') {
      total += textTokens(content);
      continue;
    }
    if (!Array.isArray(content)) continue;
    for (const part of content as Array<Record<string, unknown>>) {
      total += partTokens(part);
    }
  }
  return total;
}

function partTokens(part: Record<string, unknown>): number {
  switch (part.type) {
    case 'text':
    case 'reasoning':
      return textTokens(String(part.text ?? ''));
    case 'image':
    case 'file':
      return IMAGE_TOKENS;
    case 'tool-call':
      return textTokens(JSON.stringify(part.input ?? {})) + 8;
    case 'tool-result':
      return toolResultTokens(part.output as ToolOutput | undefined);
    default:
      return textTokens(JSON.stringify(part));
  }
}

type ContentValuePart =
  | { type: 'text'; text: string }
  | { type: 'media' | 'file-data'; data: string; mediaType: string }
  | { type: string; [key: string]: unknown };

type ToolOutput =
  | { type: 'text' | 'error-text'; value: string }
  | { type: 'json' | 'error-json'; value: unknown }
  | { type: 'content'; value: ContentValuePart[] }
  | { type: string; [key: string]: unknown };

function toolResultTokens(output: ToolOutput | undefined): number {
  if (!output) return 0;
  if (output.type === 'content' && Array.isArray(output.value)) {
    let total = 0;
    for (const item of output.value as ContentValuePart[]) {
      total +=
        item.type === 'text'
          ? textTokens(String((item as { text?: string }).text ?? ''))
          : IMAGE_TOKENS;
    }
    return total;
  }
  const value = (output as { value?: unknown }).value;
  return textTokens(
    typeof value === 'string' ? value : JSON.stringify(value ?? {}),
  );
}

function isImagePart(part: ContentValuePart): boolean {
  return part.type !== 'text';
}

/** Index every part in the branch that the ladder is allowed to shrink. */
type Site = { message: number; part: number };

function findSites(messages: ModelMessage[]) {
  const inspectionImages: Site[] = [];
  const scadSources: Site[] = [];
  const userImages: Site[] = [];

  messages.forEach((message, m) => {
    const content = message.content as unknown;
    if (!Array.isArray(content)) return;
    (content as Array<Record<string, unknown>>).forEach((part, p) => {
      if (
        message.role === 'assistant' &&
        part.type === 'tool-call' &&
        part.toolName === BUILD_TOOL
      ) {
        const input = part.input as { code?: unknown } | undefined;
        if (typeof input?.code === 'string' && input.code.length > 0) {
          scadSources.push({ message: m, part: p });
        }
        return;
      }
      if (
        message.role === 'tool' &&
        part.type === 'tool-result' &&
        part.toolName === BUILD_TOOL
      ) {
        const output = part.output as ToolOutput | undefined;
        if (
          output?.type === 'content' &&
          Array.isArray(output.value) &&
          (output.value as ContentValuePart[]).some(isImagePart)
        ) {
          inspectionImages.push({ message: m, part: p });
        }
        return;
      }
      if (
        message.role === 'user' &&
        (part.type === 'image' || part.type === 'file')
      ) {
        userImages.push({ message: m, part: p });
      }
    });
  });

  return { inspectionImages, scadSources, userImages };
}

/** Clone only what a stage rewrites; everything else keeps its identity. */
function editPart(
  messages: ModelMessage[],
  site: Site,
  rewrite: (part: Record<string, unknown>) => Record<string, unknown> | null,
): ModelMessage[] {
  const message = messages[site.message];
  const content = message.content as unknown as Array<Record<string, unknown>>;
  const replacement = rewrite(content[site.part]);
  const nextContent =
    replacement === null
      ? content.filter((_, index) => index !== site.part)
      : content.map((part, index) => (index === site.part ? replacement : part));
  const next = messages.slice();
  next[site.message] = {
    ...message,
    content: nextContent,
  } as ModelMessage;
  return next;
}

/** Stage 1: drop the render, keep every number that was measured from it. */
function dropInspectionImage(messages: ModelMessage[], site: Site) {
  return editPart(messages, site, (part) => {
    const output = part.output as { type: string; value: ContentValuePart[] };
    const text = (output.value as ContentValuePart[])
      .filter((item) => !isImagePart(item))
      .map((item) => String((item as { text?: string }).text ?? ''))
      .join('\n');
    return {
      ...part,
      output: {
        type: 'text',
        value:
          `${text}\n[The inspection sheet for this earlier revision has been ` +
          `omitted to stay inside the context window. The measured facts above ` +
          `still describe it; the newest render is further down.]`.trim(),
      },
    };
  });
}

/** Stage 2: a superseded script is a draft; its shape is in the facts. */
function elideScadSource(messages: ModelMessage[], site: Site) {
  return editPart(messages, site, (part) => {
    const input = part.input as { code: string; [key: string]: unknown };
    const lines = input.code.split('\n').length;
    return {
      ...part,
      input: {
        ...input,
        code:
          `// [${lines} lines of OpenSCAD omitted: this revision was ` +
          `superseded. The newest build_parametric_model call below carries ` +
          `the complete current script.]`,
      },
    };
  });
}

/** Stage 3: an older reference image, named so the model knows it existed. */
function dropUserImage(messages: ModelMessage[], site: Site) {
  return editPart(messages, site, (part) => {
    const filename = typeof part.filename === 'string' ? part.filename : null;
    return {
      type: 'text',
      text: `[Earlier attached image${filename ? ` "${filename}"` : ''} omitted to stay inside the context window.]`,
    };
  });
}

/**
 * Split the branch into turn groups. A group starts at a user message and
 * absorbs the assistant and tool messages that answer it, so dropping a group
 * can never orphan a tool call from its result.
 */
function groupTurns(messages: ModelMessage[]): number[][] {
  const groups: number[][] = [];
  messages.forEach((message, index) => {
    if (message.role === 'user' || groups.length === 0) groups.push([index]);
    else groups[groups.length - 1].push(index);
  });
  return groups;
}

export type CompactionResult = {
  messages: ModelMessage[];
  /** Estimated tokens before and after, for the log line. */
  before: number;
  after: number;
  /** What the ladder actually did, in order. Empty when nothing was needed. */
  applied: string[];
};

export function compactModelMessages(
  messages: ModelMessage[],
  budget: number,
): CompactionResult {
  const before = estimateTokens(messages);
  if (before <= budget) {
    return { messages, before, after: before, applied: [] };
  }

  let working = messages;
  const applied: string[] = [];
  const under = () => estimateTokens(working) <= budget;

  // Stage 1 -- stale inspection sheets, oldest first.
  {
    const { inspectionImages } = findSites(working);
    const stale = inspectionImages.slice(
      0,
      Math.max(0, inspectionImages.length - KEEP_INSPECTION_IMAGES),
    );
    let dropped = 0;
    for (const site of stale) {
      working = dropInspectionImage(working, site);
      dropped += 1;
      if (under()) break;
    }
    if (dropped) applied.push(`inspection-sheets:${dropped}`);
  }

  // Stage 2 -- superseded OpenSCAD scripts, oldest first.
  if (!under()) {
    const { scadSources } = findSites(working);
    const stale = scadSources.slice(
      0,
      Math.max(0, scadSources.length - KEEP_SCAD_SOURCES),
    );
    let elided = 0;
    for (const site of stale) {
      working = elideScadSource(working, site);
      elided += 1;
      if (under()) break;
    }
    if (elided) applied.push(`scad-sources:${elided}`);
  }

  // Stage 3 -- older reference images, oldest first.
  if (!under()) {
    const { userImages } = findSites(working);
    const stale = userImages.slice(
      0,
      Math.max(0, userImages.length - KEEP_USER_IMAGES),
    );
    let dropped = 0;
    for (const site of stale) {
      working = dropUserImage(working, site);
      dropped += 1;
      if (under()) break;
    }
    if (dropped) applied.push(`user-images:${dropped}`);
  }

  // Stage 4 -- whole early turns. Last resort: this is the only stage that
  // loses something the model cannot reconstruct from what remains.
  if (!under()) {
    const groups = groupTurns(working);
    let dropped = 0;
    // Group 0 holds the original brief and stays. Never touch the tail.
    while (groups.length - dropped > KEEP_RECENT_GROUPS + 1 && !under()) {
      dropped += 1;
      const keep = new Set<number>();
      groups.forEach((group, index) => {
        if (index === 0 || index > dropped) for (const i of group) keep.add(i);
      });
      const kept = working.filter((_, index) => keep.has(index));
      const note: ModelMessage = {
        role: 'user',
        content: `[${dropped} earlier exchange${dropped === 1 ? '' : 's'} in this conversation ${dropped === 1 ? 'was' : 'were'} omitted to stay inside the context window. If you need a detail from them, ask rather than assume.]`,
      };
      working = [kept[0], note, ...kept.slice(1)];
    }
    if (dropped) applied.push(`turn-groups:${dropped}`);
  }

  return { messages: working, before, after: estimateTokens(working), applied };
}

/**
 * True when a provider error is the input window being exceeded.
 *
 * Every provider phrases it differently and none of them set a distinct code,
 * so this matches on the wording. Used to replace an opaque provider string
 * with something a user can act on.
 */
export function isContextOverflowError(error: unknown): boolean {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : ((error as { message?: unknown } | null)?.message ?? '');
  const text = String(message).toLowerCase();
  return (
    text.includes('exceed the configured limit') ||
    text.includes('context length') ||
    text.includes('context_length_exceeded') ||
    text.includes('maximum context') ||
    text.includes('prompt is too long') ||
    text.includes('too many tokens') ||
    (text.includes('input') &&
      text.includes('token') &&
      text.includes('exceed'))
  );
}

export const CONTEXT_OVERFLOW_MESSAGE =
  'This conversation has grown past what the model can read in one go. ' +
  'Start a new conversation to carry on — the board and the model you have ' +
  'built are saved and will still be there.';
