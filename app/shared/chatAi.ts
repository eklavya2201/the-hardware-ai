import { tool, type InferUITools, type UIMessage } from 'ai';
import { z } from 'zod';
import type { MeshFileType, Model } from './types.ts';

export const parametricArtifactSchema = z.object({
  title: z.string().min(1),
  version: z.string().default('v1'),
  code: z.string().min(20),
});

export const parametricCompileOutputSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
  inspection: z
    .object({
      views: z.array(
        z.enum(['ISO', 'FRONT', 'BACK', 'LEFT', 'RIGHT', 'TOP', 'BOTTOM']),
      ),
      imageAttached: z.boolean(),
    })
    .optional(),
  // Numeric facts measured from the compiled mesh (queries, not pictures).
  facts: z
    .object({
      bbox_mm: z.object({ x: z.number(), y: z.number(), z: z.number() }),
      min_mm: z.object({ x: z.number(), y: z.number(), z: z.number() }),
      triangles: z.number(),
      bodies: z.number(),
    })
    .optional(),
  // The mechanical gate's ruling on those facts. `fail` means the geometry is
  // measurably wrong -- empty, zero-thickness, in pieces, or floating off the
  // ground plane -- and the model may not report the build as finished.
  verdict: z.enum(['pass', 'fail']).optional(),
  findings: z
    .array(
      z.object({
        id: z.string(),
        severity: z.enum(['error', 'warning']),
        message: z.string(),
      }),
    )
    .optional(),
});

export const answerUserSchema = z.object({
  message: z.string().min(1),
});

export const designBoardInputSchema = z.object({
  name: z.string().min(1).max(60),
  brief: z.string().min(10),
});

export const designBoardOutputSchema = z.object({
  status: z.enum(['success', 'error']),
  message: z.string(),
  projectId: z.string().optional(),
  components: z.number().optional(),
  nets: z.number().optional(),
  placed: z.number().optional(),
  traces: z.number().optional(),
  unrouted: z.number().optional(),
  errors: z.number().optional(),
  warnings: z.number().optional(),
  boardSize: z.string().optional(),
});

export const layoutBoardInputSchema = z.object({
  reason: z.string().optional(),
});

export const chatTools = {
  layout_board: tool({
    description:
      "Place and route the board already linked to this conversation: arrange the parts on the PCB and route the copper. Use this when the user asks to place, route, lay out, or finish an existing board -- NOT design_board, which starts a new one from a brief. Returns counts; the PCB view fills in automatically.",
    inputSchema: layoutBoardInputSchema,
    outputSchema: designBoardOutputSchema,
  }),
  design_board: tool({
    description:
      "Design the electronics in Synqit: create a project, run the schematic agent on the brief, then place and route the PCB. Use this whenever the user asks for a circuit, schematic, PCB, wiring, or names electronic parts (MCU, sensor, display, battery). Returns counts, not prose; the PCB and Schematic views fill in automatically.",
    inputSchema: designBoardInputSchema,
    outputSchema: designBoardOutputSchema,
  }),
  build_parametric_model: tool({
    description:
      'Create or update the complete OpenSCAD CAD artifact. After the browser compiles it, inspect the returned multi-view preview sheet and call this tool again if the model needs another revision.',
    inputSchema: parametricArtifactSchema,
    outputSchema: parametricCompileOutputSchema,
  }),
  answer_user: tool({
    description:
      'Send the final user-facing chat message. Use this for normal non-CAD replies, and after a CAD build when the multi-view preview satisfies the user request.',
    inputSchema: answerUserSchema,
    outputSchema: answerUserSchema,
  }),
};

export type AppTools = InferUITools<typeof chatTools>;

export type MeshContextData = {
  meshId: string;
  fileType: MeshFileType;
  filename?: string;
  boundingBox?: { x: number; y: number; z: number };
};

export type MeshPreferencesData = {
  topology: 'quads' | 'polys';
  polygonCount: number;
};

/**
 * Conversation-level signals the server emits as transient stream parts
 * (`writer.write({ transient: true, type: 'data-X', data })`). Transient
 * parts never land in `messages.parts` — they're side-channel updates the
 * client folds straight into the conversation query cache.
 *
 *  * `title-update`    fires once when the server generates a title for
 *    a fresh conversation; client updates `conversations.title`.
 *  * `suggestions-update` fires after each assistant turn finishes;
 *    client updates `conversations.settings.suggestions` so the pills
 *    below the input refresh in lock-step with the response.
 */
export type ConversationTitleUpdate = {
  conversationId: string;
  title: string;
};
export type ConversationSuggestionsUpdate = {
  conversationId: string;
  suggestions: string[];
};

export type AppDataTypes = {
  'mesh-context': MeshContextData;
  'mesh-preferences': MeshPreferencesData;
  'title-update': ConversationTitleUpdate;
  'suggestions-update': ConversationSuggestionsUpdate;
};

export const meshContextDataSchema = z.object({
  meshId: z.string(),
  fileType: z.enum(['glb', 'stl', 'obj', 'fbx']),
  filename: z.string().optional(),
  boundingBox: z
    .object({ x: z.number(), y: z.number(), z: z.number() })
    .optional(),
});

export const meshPreferencesDataSchema = z.object({
  topology: z.enum(['quads', 'polys']),
  polygonCount: z.number(),
});

export type AppUIMessage = UIMessage<
  {
    model?: Model;
    billingTokens?: number;
    // The model's original OpenSCAD for this message's artifact, captured
    // lazily on the FIRST parameter edit (see `persistParameterEdit`).
    // Parameter edits rewrite the live `tool-build_parametric_model` input
    // code in place, which would otherwise move the derived `defaultValue`
    // to the edited value on every reload. Stashing the original here —
    // message metadata is UI-only and NOT sent to the model by
    // `convertToModelMessages` — lets the client re-derive stable defaults
    // (Reset / slider home / auto range) with no second code copy in the
    // model's context, no migration, and no storage cost on the (common)
    // never-edited artifacts.
    originalCode?: string;
  },
  AppDataTypes,
  AppTools
>;
