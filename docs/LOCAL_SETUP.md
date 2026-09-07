# Running Synqit CAD locally — verified 2026-09-08, Windows 11

The app in `app/` is the upstream open-source project (see `app/NOTICE.md`) with three changes: an **Azure OpenAI
provider** (upstream only speaks Anthropic, Google and OpenRouter), a
**Synqit rebrand**, and **Synqit sign-in** (details in the sections at the
end).

## Rebrand summary (2026-09-08)

- Base path `/cad`; package name `synqit-cad`; page title "Synqit CAD".
- Every user-visible upstream product string → "Synqit" / "Synqit CAD" (agent persona included).
- Tailwind tokens → `sq-*`, button variants → `sq_dark*`, localStorage keys → `synqit-*`.
- Upstream logos, Lottie animation, product banner, community links and logo particle cloud removed; `synqit-logo.png` and a generated `synqit-wordmark.svg` in their place. Sidebar links go to synqit.in and hello@synqit.in.
- Terms and Privacy pages point at synqit.in/terms and synqit.in/privacy.
- Env: `SYNQIT_APP_URL`, `SYNQIT_BILLING_MULTIPLIER` (upstream names dropped).
- The upstream name remains only in `app/NOTICE.md` and `app/LICENSE`, which the GPL requires.

## Sign-in shared with Synqit (2026-09-08)

Two paths, both verified locally:

1. **Google, the Synqit way.** `GoogleSignInButton` loads Google Identity
   Services with the SAME `VITE_GOOGLE_CLIENT_ID` the Synqit frontend uses and
   exchanges the ID token with `supabase.auth.signInWithIdToken`. No client
   secret. Requirements: the Google client's "Authorized JavaScript origins"
   must include the CAD app origin, and the Supabase Google provider must be
   enabled with that client id — locally `supabase/config.toml` has
   `skip_nonce_check = true` and the CLI needs `GOOGLE_CLIENT_ID` /
   `GOOGLE_SECRET` (any value) in its environment at `supabase start`.
2. **"Continue with Synqit" handoff.** Button → Synqit backend
   `GET /api/auth/handoff?return_to=<cad>/sso` → (login first if needed) →
   `/sso?token=…` → CAD server `POST /api/synqit-sso` verifies the token with
   Synqit (`/auth/handoff/verify`), creates/finds the Supabase user by email,
   returns a magic-link token hash → client `verifyOtp` → signed in. Synqit-side
   code is on the `cad-sso` branch of Cursor-for-hardware (not pushed):
   `backend/app/api/auth.py`, `backend/app/services/auth.py`,
   `frontend/src/routes/Login.tsx` (`?next=` support). Env on the Synqit side:
   `CAD_APP_ORIGINS` (allow-list, default `http://localhost:3000`) and
   `SYNQIT_APP_URL` (login page, default `http://localhost:5173`). Env on the CAD
   side: `SYNQIT_API_BASE` and `VITE_SYNQIT_API_BASE` (default
   `http://localhost:8000/api`).

## What was changed (all in `app/`)

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
cd app
npm ci --ignore-scripts
npx supabase start                    # first run pulls ~13 images, 2-3 min
npx supabase status -o env            # copy ANON_KEY and SERVICE_ROLE_KEY
```

Create `app/.env.local` (git-ignored):

```
VITE_SUPABASE_URL='http://127.0.0.1:54321'
VITE_SUPABASE_ANON_KEY="<ANON_KEY>"
SUPABASE_SERVICE_ROLE_KEY="<SERVICE_ROLE_KEY>"
AZURE_RESOURCE_NAME="aatreus"
AZURE_OPENAI_API_KEY="<the OPENAI_API_KEY from Cursor-for-hardware/backend/.env>"
ENVIRONMENT="local"                   # bypasses the billing service: every user gets 3M tokens
SYNQIT_APP_URL="http://localhost:3000"
WEBHOOK_BASE_URL="http://localhost:3000"
```

```bash
npm run dev                           # http://localhost:3000/cad
```

## Accounts

Local Supabase requires email confirmation and there is no mail server wired
up. After signing up with email, confirm the row directly:

```bash
docker exec supabase_db_synqit psql -U postgres -d postgres -c \
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

`scripts/e2e/cad_e2e.py` (sign-up → prompt → model) and
`scripts/e2e/cad_iter.py` (sign-in → follow-up edit → STL download) drive
the app with Playwright on the installed Chrome (`channel="chrome"`, so no
browser download). They are how the screenshots were produced.

## No-auth mode (2026-09-08, on by default locally)

The founder asked to skip sign-in while evaluating. With `VITE_GUEST_EMAIL` and
`VITE_GUEST_PASSWORD` set in `app/.env.local`, `AuthProvider` signs any
visitor without a session in as that account automatically, so the app opens
straight into the workspace and `/signin` redirects home. The local account is
`guest@synqit.in` (created confirmed through the Supabase admin API). Every
visitor shares that account's creations. To restore the real sign-in flow
(Google, "Continue with Synqit", email), remove both variables and restart
`npm run dev`. Never set them on a public deployment.
