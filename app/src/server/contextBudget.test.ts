import { describe, expect, it } from 'vitest';
import type { ModelMessage } from 'ai';
import {
  compactModelMessages,
  estimateTokens,
  inputTokenBudget,
  isContextOverflowError,
} from './contextBudget';

/** A build turn: the assistant's complete script, then its inspection sheet. */
function buildTurn(revision: number, lines: number): ModelMessage[] {
  const code = Array.from(
    { length: lines },
    (_, i) => `cube([${revision}, ${i}, 3]); // revision ${revision} line ${i}`,
  ).join('\n');
  return [
    {
      role: 'assistant',
      content: [
        {
          type: 'tool-call',
          toolCallId: `call-${revision}`,
          toolName: 'build_parametric_model',
          input: { title: 'Bracket', version: `v${revision}`, code },
        },
      ],
    } as unknown as ModelMessage,
    {
      role: 'tool',
      content: [
        {
          type: 'tool-result',
          toolCallId: `call-${revision}`,
          toolName: 'build_parametric_model',
          output: {
            type: 'content',
            value: [
              {
                type: 'text',
                text: `Model facts: bbox 40.0 x 20.0 x ${revision}.0 mm, bodies 1.`,
              },
              {
                type: 'media',
                // Distinct per revision, so a test can tell which sheets survived.
                data: `sheet${revision}-${'A'.repeat(400_000)}`,
                mediaType: 'image/png',
              },
            ],
          },
        },
      ],
    } as unknown as ModelMessage,
  ];
}

/** The shape that produced "your messages resulted in 332726 tokens". */
function longSession(revisions: number): ModelMessage[] {
  const messages: ModelMessage[] = [
    { role: 'user', content: 'Design a bracket for a NEMA 17 motor.' },
  ];
  for (let r = 1; r <= revisions; r += 1) messages.push(...buildTurn(r, 400));
  return messages;
}

describe('inputTokenBudget', () => {
  it('leaves room for the output pool and the tool schemas', () => {
    // The Azure window in the original report was 272k.
    expect(inputTokenBudget('azure', 64_000)).toBeLessThan(272_000 - 64_000);
    expect(inputTokenBudget('anthropic', 64_000)).toBeLessThan(
      inputTokenBudget('azure', 64_000),
    );
    expect(inputTokenBudget('google', 64_000)).toBeGreaterThan(500_000);
  });
});

describe('compactModelMessages', () => {
  it('leaves a conversation that fits completely untouched', () => {
    const messages = longSession(2);
    const result = compactModelMessages(messages, 1_000_000);
    expect(result.applied).toEqual([]);
    // Identity, not just equality: the prompt cache depends on a stable prefix.
    expect(result.messages).toBe(messages);
  });

  it('brings an over-budget session back under the limit', () => {
    const messages = longSession(20);
    const budget = 40_000;
    expect(estimateTokens(messages)).toBeGreaterThan(budget);

    const result = compactModelMessages(messages, budget);
    expect(result.after).toBeLessThanOrEqual(budget);
    expect(result.after).toBeLessThan(result.before);
    expect(result.applied.length).toBeGreaterThan(0);
  });

  it('drops old renders before it touches anything else', () => {
    const messages = longSession(20);
    const result = compactModelMessages(messages, 120_000);

    // Renders are the whole bill here: no script was touched, no turn dropped.
    expect(result.applied).toEqual([expect.stringMatching(/^inspection-sheets:/)]);
    expect(result.after).toBeLessThanOrEqual(120_000);

    // And they went oldest first -- the newest render still describes the
    // model that exists now.
    const serialized = JSON.stringify(result.messages);
    expect(serialized).not.toContain('sheet1-');
    expect(serialized).toContain('sheet20-');
  });

  it('keeps the measured facts when it drops a render', () => {
    const result = compactModelMessages(longSession(20), 40_000);
    const texts = JSON.stringify(result.messages);
    // The numbers measured from revision 1 survive; its picture does not.
    expect(texts).toContain('Model facts: bbox 40.0 x 20.0 x 1.0 mm');
    expect(texts).not.toContain('sheet1-');
    expect(texts).not.toContain('sheet10-');
  });

  it('keeps the newest OpenSCAD script complete', () => {
    const result = compactModelMessages(longSession(20), 20_000);
    const calls = result.messages.flatMap((message) =>
      Array.isArray(message.content)
        ? (message.content as Array<Record<string, unknown>>).filter(
            (part) => part.type === 'tool-call',
          )
        : [],
    );
    const last = calls[calls.length - 1] as {
      input: { code: string; version: string };
    };
    expect(last.input.version).toBe('v20');
    expect(last.input.code).toContain('cube([20,');
    expect(last.input.code).not.toContain('omitted');
  });

  it('never orphans a tool result from its call', () => {
    // Small budget, so stage 4 has to drop whole turns.
    const messages: ModelMessage[] = [];
    for (let turn = 0; turn < 8; turn += 1) {
      messages.push({ role: 'user', content: `Make revision ${turn}.` });
      messages.push(...buildTurn(turn + 1, 400));
    }
    const result = compactModelMessages(messages, 8_000);

    const called = new Set<string>();
    const returned = new Set<string>();
    for (const message of result.messages) {
      if (!Array.isArray(message.content)) continue;
      for (const part of message.content as Array<Record<string, unknown>>) {
        if (part.type === 'tool-call') called.add(String(part.toolCallId));
        if (part.type === 'tool-result') returned.add(String(part.toolCallId));
      }
    }
    expect([...returned].every((id) => called.has(id))).toBe(true);
    expect([...called].every((id) => returned.has(id))).toBe(true);
  });

  it('keeps the original brief when it drops early turns', () => {
    const messages: ModelMessage[] = [];
    for (let turn = 0; turn < 8; turn += 1) {
      messages.push({ role: 'user', content: `Make revision ${turn}.` });
      messages.push(...buildTurn(turn + 1, 400));
    }
    messages[0] = {
      role: 'user',
      content: 'Design a bracket for a NEMA 17 motor.',
    };
    const result = compactModelMessages(messages, 8_000);
    expect(result.applied.some((a) => a.startsWith('turn-groups:'))).toBe(true);
    expect(result.messages[0]).toEqual(messages[0]);
    expect(JSON.stringify(result.messages[1])).toContain('omitted');
  });

  it('drops user reference images oldest first, naming what went', () => {
    const messages: ModelMessage[] = [
      {
        role: 'user',
        content: [
          { type: 'text', text: 'Match this shape.' },
          {
            type: 'file',
            filename: 'sketch.png',
            mediaType: 'image/png',
            data: 'B'.repeat(400_000),
          },
          {
            type: 'file',
            filename: 'photo.png',
            mediaType: 'image/png',
            data: 'C'.repeat(400_000),
          },
        ],
      } as unknown as ModelMessage,
    ];
    const result = compactModelMessages(messages, 2_500);
    const serialized = JSON.stringify(result.messages);
    expect(serialized).toContain('sketch.png');
    expect(serialized).toContain('omitted');
    expect(serialized).toContain('CCCC');
    expect(serialized).not.toContain('BBBB');
  });
});

describe('isContextOverflowError', () => {
  it('recognises how each provider words it', () => {
    for (const message of [
      'Input tokens exceed the configured limit of 272000 tokens. Your messages resulted in 332726 tokens.',
      "This model's maximum context length is 200000 tokens",
      'context_length_exceeded',
      'prompt is too long: 210000 tokens > 200000 maximum',
    ]) {
      expect(isContextOverflowError(new Error(message))).toBe(true);
    }
  });

  it('does not swallow unrelated failures', () => {
    expect(isContextOverflowError(new Error('rate limit exceeded'))).toBe(false);
    expect(isContextOverflowError(new Error('401 Unauthorized'))).toBe(false);
    expect(isContextOverflowError(null)).toBe(false);
  });
});
