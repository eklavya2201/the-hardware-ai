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

/** Characters per token in a long unbroken string: base64, a hash, a blob. */
const DENSE_CHARS_PER_TOKEN = 2;

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
  systemPromptChars = 0,
): number {
  // Context windows, in tokens, for the families this app can select.
  const window =
    provider === 'google'
      ? 1_000_000
      : provider === 'anthropic'
        ? 200_000
        : 272_000; // Azure OpenAI gpt-5.x, the limit in the original report.
  // The system prompt is measured rather than guessed at -- it is the largest
  // thing outside the messages, and it changes whenever the prompt is edited.
  const system = Math.ceil(systemPromptChars / CHARS_PER_TOKEN);
  const toolSchemas = 8_000; // zod schemas, not serializable to a real count
  return Math.max(
    50_000,
    window - maxOutputTokens - system - toolSchemas - 4_000,
  );
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

/**
 * The size of a request, measured two ways.
 *
 * This module used to add up a guess per part type -- so much for a text
 * part, a flat 2,000 for anything that looked like an image. That is the
 * shape of estimator that fails silently: it can only see the costs it was
 * told to look for, and it charged 8,000 tokens for four renders the
 * provider charged about 150,000 for. The conversation sailed under budget
 * and the request was refused twice.
 *
 * So measurement is inverted here. Serialize the message and count every
 * character, which cannot miss a field nobody anticipated, then subtract
 * only what we can PROVE the provider sends as a compact image block. The
 * default is safe and the discount is explicit.
 *
 * `chars` also gives a real guarantee, not an estimate. A BPE token always
 * consumes at least one character, so a payload of N characters can never
 * be more than N tokens. Keep `chars + imageTokens` under the window and
 * the request cannot overflow, whatever the tokenizer does.
 */
export type Size = {
  /** Serialized characters, excluding base64 that travels as an image. */
  chars: number;
  /** Images sent as a compact block, and what they cost. */
  images: number;
  imageTokens: number;
  /** Realistic estimate: what we expect the provider to bill. */
  tokens: number;
  /** Upper bound the provider cannot exceed. */
  ceiling: number;
};

/**
 * Whether a tool result's images reach the provider as image blocks.
 *
 * On the OpenAI chat API they do not -- the output is JSON-stringified, so
 * the base64 is text and must be counted as text. `liftToolResultImages`
 * removes that case before we ever measure, but the flag keeps the two
 * facts from drifting apart.
 */
export type MeasureOptions = { toolResultImagesAreBlocks?: boolean };

export function measure(
  messages: ModelMessage[],
  options: MeasureOptions = {},
): Size {
  const toolImagesAreBlocks = options.toolResultImagesAreBlocks ?? true;
  let chars = 0;
  let images = 0;

  for (const message of messages) {
    chars += JSON.stringify(message)?.length ?? 0;

    const content = message.content as unknown;
    if (!Array.isArray(content)) continue;
    for (const part of content as Array<Record<string, unknown>>) {
      // A user-message image is always a real block on every provider.
      if (part.type === 'image' || part.type === 'file') {
        images += 1;
        chars -= blobLength(part.data ?? part.image);
        continue;
      }
      if (part.type !== 'tool-result' || !toolImagesAreBlocks) continue;
      const output = part.output as ToolOutput | undefined;
      if (output?.type !== 'content' || !Array.isArray(output.value)) continue;
      for (const item of output.value as ContentValuePart[]) {
        if (!isImagePart(item)) continue;
        images += 1;
        chars -= blobLength((item as { data?: unknown }).data);
      }
    }
  }

  chars = Math.max(0, chars);
  const dense = Math.min(chars, denseChars(messages, toolImagesAreBlocks));
  const imageTokens = images * IMAGE_TOKENS;
  return {
    chars,
    images,
    imageTokens,
    tokens:
      Math.ceil((chars - dense) / CHARS_PER_TOKEN) +
      Math.ceil(dense / DENSE_CHARS_PER_TOKEN) +
      imageTokens,
    ceiling: chars + imageTokens,
  };
}

/**
 * Characters in strings a tokenizer cannot break into words.
 *
 * Prose runs about four characters to a token. Base64, a hash, a data URL --
 * anything long with no whitespace -- runs closer to two, because there are
 * no word boundaries to merge on. That difference is not academic: the
 * incident's 454,485 characters of base64 come to 113k tokens at four and
 * 227k at two, and the provider billed 298,293 for that request plus the
 * rebuild it was in the middle of. Counting them as prose is how a payload
 * over the window looked comfortably under it.
 */
function denseChars(
  messages: ModelMessage[],
  toolImagesAreBlocks: boolean,
): number {
  let total = 0;
  const walk = (node: unknown, key: string | number | null) => {
    if (typeof node === 'string') {
      // Skip the blobs already discounted as image blocks.
      if (toolImagesAreBlocks && key === 'data') return;
      if (node.length > 512 && !/\s/.test(node.slice(0, 512))) {
        total += node.length;
      }
      return;
    }
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, i));
      return;
    }
    if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) walk(v, k);
    }
  };
  for (const message of messages) walk(message, null);
  return total;
}

/** Serialized length of a base64 payload, whatever shape it arrived in. */
function blobLength(data: unknown): number {
  return typeof data === 'string' ? data.length : 0;
}

/**
 * Realistic token estimate. Kept as its own name because that is what the
 * ladder reports; `measure` is the thing to reach for when the guarantee
 * matters.
 */
export function estimateTokens(
  messages: ModelMessage[],
  options: MeasureOptions = {},
): number {
  return measure(messages, options).tokens;
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
  const size = measure(messages);
  const before = size.tokens;
  // Two conditions, and both have to hold. `tokens` is what we expect to be
  // billed. `ceiling` is what the provider cannot exceed whatever its
  // tokenizer does, because a token never consumes less than a character.
  // The second is the one that makes this a guarantee: an estimate can be
  // wrong, and twice now it was.
  const fits = (s: Size) => s.tokens <= budget && s.ceiling <= budget;
  if (fits(size)) {
    return { messages, before, after: before, applied: [] };
  }

  let working = messages;
  const applied: string[] = [];
  const under = () => fits(measure(working));

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

  // Stage 5 -- the gate. Everything above is judgement about what matters
  // least; this is arithmetic. If the payload still cannot be proved to fit,
  // strip every remaining picture and then shorten the longest text there is,
  // repeatedly, until it does. Losing detail is a bad outcome; a refused
  // request the user cannot recover from is a worse one.
  if (!under()) {
    const forced = forceUnder(working, budget);
    if (forced.changed) {
      working = forced.messages;
      applied.push('forced');
    }
  }

  return { messages: working, before, after: measure(working).tokens, applied };
}

/**
 * Shrink until the proof holds. Structure is preserved -- no message is
 * removed, so no tool call loses its result -- and only the longest strings
 * are cut, which is where any real excess has to be.
 */
function forceUnder(
  messages: ModelMessage[],
  budget: number,
): { messages: ModelMessage[]; changed: boolean } {
  let working = messages.map((message) =>
    Array.isArray(message.content)
      ? ({
          ...message,
          content: (message.content as Array<Record<string, unknown>>).map(
            (part) =>
              part.type === 'image' || part.type === 'file'
                ? {
                    type: 'text',
                    text: '[image omitted to stay inside the context window]',
                  }
                : part,
          ),
        } as ModelMessage)
      : message,
  );
  let changed = JSON.stringify(working) !== JSON.stringify(messages);

  // Halve the longest string in the payload, over and over. Terminates: each
  // pass strictly reduces the total, and the loop stops when nothing is left
  // long enough to be worth cutting.
  for (let pass = 0; pass < 200; pass += 1) {
    const size = measure(working);
    if (size.tokens <= budget && size.ceiling <= budget) break;
    const site = longestString(working);
    if (!site || site.length < 200) break;
    working = replaceString(working, site.path, (value) =>
      `${value.slice(0, Math.floor(value.length / 2))}
[...truncated to stay inside the context window]`,
    );
    changed = true;
  }

  return { messages: working, changed };
}

type StringSite = { path: (string | number)[]; length: number };

function longestString(messages: ModelMessage[]): StringSite | null {
  let best: StringSite | null = null;
  const walk = (node: unknown, path: (string | number)[]) => {
    if (typeof node === 'string') {
      if (!best || node.length > best.length) best = { path, length: node.length };
      return;
    }
    if (Array.isArray(node)) {
      node.forEach((item, i) => walk(item, [...path, i]));
      return;
    }
    if (node && typeof node === 'object') {
      for (const [key, value] of Object.entries(node)) {
        walk(value, [...path, key]);
      }
    }
  };
  messages.forEach((message, i) => walk(message, [i]));
  return best;
}

function replaceString(
  messages: ModelMessage[],
  path: (string | number)[],
  rewrite: (value: string) => string,
): ModelMessage[] {
  const clone = structuredClone(messages) as unknown as Record<string, unknown>[];
  let node: Record<string, unknown> | unknown[] = clone;
  for (const key of path.slice(0, -1)) {
    node = (node as Record<string, unknown>)[key as string] as Record<
      string,
      unknown
    >;
  }
  const last = path[path.length - 1] as string;
  const current = (node as Record<string, unknown>)[last];
  if (typeof current === 'string') {
    (node as Record<string, unknown>)[last] = rewrite(current);
  }
  return clone as unknown as ModelMessage[];
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

/**
 * Providers that turn a `content` tool result into a JSON string.
 *
 * The OpenAI chat-completions API has no image block in a tool message, so
 * @ai-sdk/openai does `contentValue = JSON.stringify(output.value)` and the
 * whole base64 render travels as raw TEXT. Two things follow, and both were
 * live on the default model:
 *
 *  - the model never saw the render. It was told to "inspect the multi-view
 *    render in this tool result" and handed a hundred thousand characters of
 *    base64. The multi-view inspection loop -- the CAD side's only visual
 *    self-correction -- has been blind on this provider from the start, which
 *    is why revisions of a simple shape wander instead of converging.
 *  - one 89 KB sheet costs about 30k tokens instead of about 1.5k. Four of
 *    them in a branch is 113k-151k tokens of gibberish, which is what pushed
 *    a four-message conversation past a 272k window.
 *
 * Anthropic, Google and OpenRouter all map `image-data` to a real image
 * block, so they keep the native path.
 */
export function stringifiesToolResultContent(provider: string): boolean {
  return provider === 'azure';
}

/** Newest inspection sheets that keep their image when lifted. */
const LIFTED_SHEETS = 2;

/**
 * Move images out of tool results and into user messages that follow them.
 *
 * A user message is the one place every provider accepts an image, so the
 * render arrives as a picture the model can actually look at. Older sheets
 * lose the image entirely -- the newest one describes the model that exists
 * now, and the numbers measured from each of the others are in the text that
 * stays behind.
 */
export function liftToolResultImages(messages: ModelMessage[]): {
  messages: ModelMessage[];
  lifted: number;
} {
  // Which tool results carry an image, so "the newest two" can be decided
  // before any of them is rewritten.
  const withImages: number[] = [];
  messages.forEach((message, index) => {
    if (message.role !== 'tool' || !Array.isArray(message.content)) return;
    const parts = message.content as Array<Record<string, unknown>>;
    if (
      parts.some((part) => {
        const output = part.output as ToolOutput | undefined;
        return (
          output?.type === 'content' &&
          Array.isArray(output.value) &&
          (output.value as ContentValuePart[]).some(isImagePart)
        );
      })
    ) {
      withImages.push(index);
    }
  });
  if (withImages.length === 0) return { messages, lifted: 0 };

  const keep = new Set(withImages.slice(-LIFTED_SHEETS));
  const out: ModelMessage[] = [];
  let lifted = 0;

  messages.forEach((message, index) => {
    if (!withImages.includes(index)) {
      out.push(message);
      return;
    }

    const images: ContentValuePart[] = [];
    const parts = (message.content as Array<Record<string, unknown>>).map(
      (part) => {
        const output = part.output as ToolOutput | undefined;
        if (
          output?.type !== 'content' ||
          !Array.isArray(output.value) ||
          !(output.value as ContentValuePart[]).some(isImagePart)
        ) {
          return part;
        }
        const value = output.value as ContentValuePart[];
        const text = value
          .filter((item) => !isImagePart(item))
          .map((item) => String((item as { text?: string }).text ?? ''))
          .join('\n');
        const note = keep.has(index)
          ? '\n[The multi-view inspection sheet follows as an image in the next message.]'
          : '\n[The inspection sheet for this earlier revision was omitted; the ' +
            'measured facts above still describe it, and the newest render is ' +
            'further down.]';
        if (keep.has(index)) images.push(...value.filter(isImagePart));
        return { ...part, output: { type: 'text', value: text + note } };
      },
    );

    out.push({ ...message, content: parts } as ModelMessage);
    lifted += 1;

    if (images.length) {
      out.push({
        role: 'user',
        content: [
          {
            type: 'text',
            text: 'Multi-view inspection sheet for the model you just built. Check it against the request from every visible angle.',
          },
          ...images.map((image) => ({
            type: 'file' as const,
            mediaType: String((image as { mediaType?: string }).mediaType ?? 'image/png'),
            data: String((image as { data?: string }).data ?? ''),
          })),
        ],
      } as unknown as ModelMessage);
    }
  });

  return { messages: out, lifted };
}
