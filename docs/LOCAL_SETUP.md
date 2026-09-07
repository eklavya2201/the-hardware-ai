# Running the CADAM clone locally (verified 2026-09-07, Windows 11)

The app in `cadam/` runs unmodified except for one addition: an **Azure OpenAI
provider** so it works with the key Synqit already has. Upstream CADAM only
speaks Anthropic, Google and OpenRouter.

## What was changed (all in `cadam/`)

| File | Change |
|---|---|
| `package.json` | add `@ai-sdk/azure@3.0.116` (the 3.x line matches the installed `@ai-sdk/provider` 3.x) |
| `src/server/aiChat.ts` | `azure/<deployment>` model ids resolve to `createAzure({ resourceName, apiKey }).chat(deployment)`; prices for `azure/gpt-5.1` and `azure/gpt-5-pro` |
| `src/lib/utils.ts` | two picker entries, "GPT-5.1 (Azure)" and "GPT-5 Pro (Azure)" |
| `src/views/PromptView.tsx`, `src/views/EditorView.tsx` | default model is `azure/gpt-5.1` instead of Gemini |

Everything else (Supabase schema, OpenSCAD-WASM worker, the seven-view
screenshot loop, parameter sliders, STL/SCAD/DXF export) is upstream code.

## Prerequisites

- Node 22 (22.14 used), npm 10
- Docker Desktop running (Supabase local stack is 12 containers)
- Azure OpenAI resource with a chat deployment (we use `aatreus` / `gpt-5.1`)

## Steps

```bash
cd cadam
npm ci --ignore-scripts
npx supabase start                    # first run pulls ~13 images, 2-3 min
npx supabase status -o env            # copy ANON_KEY and SERVICE_ROLE_KEY
```

Create `cadam/.env.local` (git-ignored):

```
VITE_SUPABASE_URL='http://127.0.0.1:54321'
VITE_SUPABASE_ANON_KEY="<ANON_KEY>"
SUPABASE_SERVICE_ROLE_KEY="<SERVICE_ROLE_KEY>"
AZURE_RESOURCE_NAME="aatreus"
AZURE_OPENAI_API_KEY="<the OPENAI_API_KEY from Cursor-for-hardware/backend/.env>"
ENVIRONMENT="local"                   # bypasses the billing service: every user gets 3M tokens
ADAM_URL="http://localhost:3000"
WEBHOOK_BASE_URL="http://localhost:3000"
```

```bash
npm run dev                           # http://localhost:3000/cadam
```

## Accounts

Local Supabase requires email confirmation and there is no mail server wired
up. After signing up with email, confirm the row directly:

```bash
docker exec supabase_db_cadam psql -U postgres -d postgres -c \
  "update auth.users set email_confirmed_at=now() where email='you@example.com'"
```

Then reload; the session created at sign-up is already valid. "Continue with
Google" needs `GOOGLE_CLIENT_ID` / `GOOGLE_SECRET` in the Supabase env.

## What was verified

- Sign-up, confirmation, editor.
- Prompt "A phone stand for a 70 mm wide phone, 15 degree tilt, with a cable
  slot at the back" → GPT-5.1 wrote OpenSCAD with 11 sliders and a colour
  parameter, the browser compiled it, the agent inspected the seven-view sheet
  and answered "Done" in 33 s. See `docs/screenshots/`.
- Follow-up edit and STL download: see the iteration screenshots.

## Not wired up (needs keys we do not have)

- **Mesh / "Creative" mode** (Hunyuan3D, Meshy, SAM-3 via fal.ai): `FAL_KEY`
  plus a public `WEBHOOK_BASE_URL` (ngrok) for the fal callback.
- **Image generation** for the prompt helper: same fal key.
- **Title and prompt suggestion helpers** call Anthropic directly
  (`src/server/anthropic.ts`); without `ANTHROPIC_API_KEY` they are skipped,
  conversations are just titled "New Conversation".
- **Billing / Stripe**: bypassed with `ENVIRONMENT=local`. A real deployment
  needs the external billing service the code expects, or a rewrite of
  `src/server/billingClient.ts`.
- **Sentry, PostHog**: optional, disabled without keys.

## Headless test drivers

`scripts/e2e/cadam_e2e.py` (sign-up → prompt → model) and
`scripts/e2e/cadam_iter.py` (sign-in → follow-up edit → STL download) drive
the app with Playwright on the installed Chrome (`channel="chrome"`, so no
browser download). They are how the screenshots were produced.
