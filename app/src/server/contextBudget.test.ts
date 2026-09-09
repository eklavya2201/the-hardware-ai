import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { ModelMessage } from 'ai';
import {
  compactModelMessages,
  estimateTokens,
  inputTokenBudget,
  isContextOverflowError,
  liftToolResultImages,
  stringifiesToolResultContent,
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
                // Distinct per revision, so a test can tell which survived.
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

/** A session where every revision follows its own user instruction. */
function manyTurns(): ModelMessage[] {
  const messages: ModelMessage[] = [];
  for (let turn = 0; turn < 8; turn += 1) {
    messages.push({
      role: 'user',
      content:
        turn === 0
          ? 'Design a bracket for a NEMA 17 motor.'
          : `Make revision ${turn}.`,
    });
    messages.push(...buildTurn(turn + 1, 400));
  }
  return messages;
}

function partsOfType(messages: ModelMessage[], type: string) {
  return messages.flatMap((message) =>
    Array.isArray(message.content)
      ? (message.content as Array<Record<string, unknown>>).filter(
          (part) => part.type === type,
        )
      : [],
  );
}

describe('inputTokenBudget', () => {
  it('leaves room for the output pool and the tool schemas', () => {
    // The Azure window in the original report was 272k.
    assert.ok(inputTokenBudget('azure', 64_000) < 272_000 - 64_000);
    assert.ok(
      inputTokenBudget('anthropic', 64_000) < inputTokenBudget('azure', 64_000),
    );
    assert.ok(inputTokenBudget('google', 64_000) > 500_000);
  });
});

describe('compactModelMessages', () => {
  it('leaves a conversation that fits completely untouched', () => {
    const messages = longSession(2);
    const result = compactModelMessages(messages, 1_000_000);
    assert.deepEqual(result.applied, []);
    // Identity, not just equality: the prompt cache needs a stable prefix.
    assert.equal(result.messages, messages);
  });

  it('brings an over-budget session back under the limit', () => {
    const messages = longSession(20);
    const budget = 40_000;
    assert.ok(estimateTokens(messages) > budget);

    const result = compactModelMessages(messages, budget);
    assert.ok(result.after <= budget);
    assert.ok(result.after < result.before);
    assert.ok(result.applied.length > 0);
  });

  it('drops old renders before it touches anything else', () => {
    const result = compactModelMessages(longSession(20), 120_000);

    // Renders are the whole bill here: no script touched, no turn dropped.
    assert.equal(result.applied.length, 1);
    assert.match(result.applied[0], /^inspection-sheets:/);
    assert.ok(result.after <= 120_000);

    // And oldest first -- the newest render still describes the model that
    // exists now.
    const serialized = JSON.stringify(result.messages);
    assert.ok(!serialized.includes('sheet1-'));
    assert.ok(serialized.includes('sheet20-'));
  });

  it('keeps the measured facts when it drops a render', () => {
    const serialized = JSON.stringify(
      compactModelMessages(longSession(20), 40_000).messages,
    );
    assert.ok(serialized.includes('Model facts: bbox 40.0 x 20.0 x 1.0 mm'));
    assert.ok(!serialized.includes('sheet1-'));
    assert.ok(!serialized.includes('sheet10-'));
  });

  it('keeps the newest OpenSCAD script complete', () => {
    const result = compactModelMessages(longSession(20), 20_000);
    const calls = partsOfType(result.messages, 'tool-call');
    const last = calls[calls.length - 1] as unknown as {
      input: { code: string; version: string };
    };
    assert.equal(last.input.version, 'v20');
    assert.ok(last.input.code.includes('cube([20,'));
    assert.ok(!last.input.code.includes('omitted'));
  });

  it('never orphans a tool result from its call', () => {
    // A budget small enough that stage 4 has to drop whole turns.
    const result = compactModelMessages(manyTurns(), 8_000);

    const called = partsOfType(result.messages, 'tool-call')
      .map((p) => String(p.toolCallId))
      .sort();
    const returned = partsOfType(result.messages, 'tool-result')
      .map((p) => String(p.toolCallId))
      .sort();
    assert.deepEqual(called, returned);
  });

  it('keeps the original brief when it drops early turns', () => {
    const messages = manyTurns();
    const result = compactModelMessages(messages, 8_000);
    assert.ok(result.applied.some((a) => a.startsWith('turn-groups:')));
    assert.deepEqual(result.messages[0], messages[0]);
    assert.ok(JSON.stringify(result.messages[1]).includes('omitted'));
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
    const serialized = JSON.stringify(
      compactModelMessages(messages, 2_500).messages,
    );
    assert.ok(serialized.includes('sketch.png'));
    assert.ok(serialized.includes('omitted'));
    assert.ok(serialized.includes('CCCC'));
    assert.ok(!serialized.includes('BBBB'));
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
      assert.equal(isContextOverflowError(new Error(message)), true, message);
    }
  });

  it('does not swallow unrelated failures', () => {
    assert.equal(
      isContextOverflowError(new Error('rate limit exceeded')),
      false,
    );
    assert.equal(isContextOverflowError(new Error('401 Unauthorized')), false);
    assert.equal(isContextOverflowError(null), false);
  });
});

describe('stringifiesToolResultContent', () => {
  it('names only the provider that has no image block in a tool message', () => {
    // @ai-sdk/openai: `contentValue = JSON.stringify(output.value)`.
    assert.equal(stringifiesToolResultContent('azure'), true);
    // These three map image-data to a real image block; verified in their
    // dist sources.
    assert.equal(stringifiesToolResultContent('anthropic'), false);
    assert.equal(stringifiesToolResultContent('google'), false);
    assert.equal(stringifiesToolResultContent('openrouter'), false);
  });
});

describe('liftToolResultImages', () => {
  it('does nothing to a branch with no tool-result images', () => {
    const messages: ModelMessage[] = [
      { role: 'user', content: 'hello' },
    ];
    const result = liftToolResultImages(messages);
    assert.equal(result.lifted, 0);
    assert.equal(result.messages, messages);
  });

  it('moves the newest renders into user messages the model can see', () => {
    const result = liftToolResultImages(longSession(4));

    // Every tool result is now plain text, so nothing gets JSON-stringified
    // into the prompt as base64.
    for (const message of result.messages) {
      if (message.role !== 'tool') continue;
      for (const part of partsOfType([message], 'tool-result')) {
        assert.equal((part.output as { type: string }).type, 'text');
      }
    }

    // The newest two sheets survive as user-message images.
    const carried = result.messages.filter(
      (m) =>
        m.role === 'user' &&
        Array.isArray(m.content) &&
        m.content.some((p) => p.type === 'file'),
    );
    assert.equal(carried.length, 2);
    const serialized = JSON.stringify(carried);
    assert.ok(serialized.includes('sheet3-'));
    assert.ok(serialized.includes('sheet4-'));
    assert.ok(!serialized.includes('sheet1-'));
  });

  it('keeps the measured facts beside every result it strips', () => {
    const serialized = JSON.stringify(liftToolResultImages(longSession(4)).messages);
    for (const revision of [1, 2, 3, 4]) {
      assert.ok(
        serialized.includes(`Model facts: bbox 40.0 x 20.0 x ${revision}.0 mm`),
      );
    }
  });

  it('collapses what the stringifying provider would have charged', () => {
    // Measured the way @ai-sdk/openai does it: a `content` tool output is
    // JSON.stringify'd, so every base64 character lands in the prompt as
    // text. This is the arithmetic behind the 272k failure.
    const charsInToolMessages = (messages: ModelMessage[]) =>
      messages
        .filter((m) => m.role === 'tool')
        .reduce((total, m) => total + JSON.stringify(m.content).length, 0);

    const messages = longSession(4);
    const before = charsInToolMessages(messages);
    const after = charsInToolMessages(liftToolResultImages(messages).messages);

    assert.ok(before > 1_600_000, `${before} characters of base64 as text`);
    assert.ok(after < 2_000, `${after} should be facts and a pointer only`);
  });

  it('leaves an image directly after the result it belongs to', () => {
    const result = liftToolResultImages(longSession(2));
    const index = result.messages.findIndex(
      (m) =>
        m.role === 'user' &&
        Array.isArray(m.content) &&
        m.content.some((p) => p.type === 'file'),
    );
    assert.ok(index > 0);
    assert.equal(result.messages[index - 1].role, 'tool');
  });
});
