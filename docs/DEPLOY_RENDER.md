# Deploying Synqit CAD to Render (for Lucky and other testers)

Three pieces: the app on Render, a hosted Supabase project for its database,
auth and file storage, and Synqit production as the source of boards. About
30 minutes the first time. Everything below that touches an account is a
human step; the repo already contains the blueprint and the scripts.

## Live service (created 2026-09-08)

| | |
|---|---|
| URL | https://synqit-cad.onrender.com/cad |
| service id | `srv-dafvb8fqj5pc738ggrf0` |
| workspace | "My Workspace" (pandeyeklavya220@gmail.com), `tea-d9j1crjeo5us73amk2u0` |
| plan / region | free / singapore (sleeps after 15 min idle; first load ~1 min) |
| repo | `eklavya2201/the-hardware-ai`, branch `main`, root `app/`, auto-deploy on push |

It was created through the Render API, not the dashboard blueprint, so
`render.yaml` is now documentation rather than the source of truth. Env vars
are set on the service; change them in the dashboard or via
`PUT /v1/services/{id}/env-vars`. `VITE_*` values are baked in at build time,
so changing one needs a redeploy.

**Status: fully working (2026-09-08).** Supabase project `lkicwbmvigrhbprasote`
("synqit", ap-northeast-2) is wired: seven tables, three storage buckets, the
confirmed guest account, site URL and redirect list set. Verified on the live
URL: guest auto sign-in, a phone-holder prompt built in 43 s with 13 sliders
and an STL button, no console errors. An UptimeRobot ping keeps the free
instance awake.

The whole Supabase setup was done through the management API, not the
dashboard — migrations via `POST /v1/projects/{ref}/database/query` (send a
normal `User-Agent`, Cloudflare blocks `Python-urllib` with error 1010),
buckets and the guest via `scripts/seed-remote.mjs`, auth config via
`PATCH /v1/projects/{ref}/config/auth`.

## The electronics engine on Render (2026-09-08) — deployed, needs more RAM

| | |
|---|---|
| URL | https://synqit-api.onrender.com/api/health |
| service id | `srv-dag151n40ujc73dc9s8g` |
| image | `backend/Dockerfile.render` — lean (no KiCad, FreeRouting or CAD kernel); ~400 MB, builds in about 80 s |
| database | the Supabase Postgres we already run, under its own role and schema `synqit_api` (Render's one free Postgres slot is taken by another project) |
| plan | **free (512 MB) — not enough** |

**What works on free:** health, signup, login, creating and listing projects,
persistence across restarts (Postgres, not a disk).

**What does not:** `GET /library/index` and any schematic-agent run kill the
instance. The part library alone is ~400 MB resident once 23,941 parts are
parsed, leaving ~100 MB for everything else, so the first real workload OOMs
and Render restarts the container.

**Two ways forward.**

1. Add a card to the Render workspace and move the service to `standard`
   (2 GB, $25/month). One call, no redeploy:

   ```bash
   curl -X PATCH https://api.render.com/v1/services/srv-dag151n40ujc73dc9s8g      -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json"      -d '{"serviceDetails":{"plan":"standard"}}'
   ```

2. Make the library cheap to hold: keep the parts as plain dicts keyed by MPN
   and build `LibraryPart` objects only when a part is actually used, and
   precompute `/library/index` into a static gzipped file at image build time.
   That is real work in `app/services/library.py`, but it would fit in 512 MB
   and speed up cold starts everywhere, including production.

Until one of those lands, the studio keeps pointing at production
`api.synqit.in`, which is where the testers' projects already live.

**Still missing for a full cut-over:** the studio has no way to create a Synqit
*account*. Sign-in works when the user already has one; a new tester on a fresh
backend cannot register from the studio UI. Either add a sign-up path or have
the studio provision a backend account for its signed-in user.

## 1. Supabase (hosted, free tier is enough)

1. https://supabase.com → New project. Region: Mumbai (ap-south-1) if offered.
   Keep the database password.
2. Project Settings → API: copy **Project URL**, **anon key**, **service_role key**.
3. Apply the schema. From `app/` on your laptop:

   ```bash
   npx supabase login                       # opens the browser once
   npx supabase link --project-ref <ref>    # ref = the part before .supabase.co
   npx supabase db push                     # applies supabase/migrations/*.sql
   ```

   If `link` asks for the database password, use the one from step 1.
4. Seed buckets and the guest account (pick a strong guest password):

   ```bash
   node scripts/seed-remote.mjs --url https://<ref>.supabase.co \
     --service-key <SERVICE_ROLE_KEY> --guest-password <PASSWORD>
   ```

5. Authentication → URL configuration: **Site URL** = your Render URL
   (e.g. `https://synqit-cad.onrender.com/cad`), and add it to Redirect URLs.
   Authentication → Providers → Email: leave "Confirm email" on; the guest is
   pre-confirmed, and real sign-ups will get Supabase's confirmation mail.

## 2. Render

1. https://dashboard.render.com → New → **Blueprint** → connect
   `eklavya2201/the-hardware-ai` → it reads `render.yaml` (service
   `synqit-cad`, root `app/`, Node 22, build `npm ci && npm run build`, start
   `node .output/server/index.mjs`).
2. Fill the secrets it asks for:

   | key | value |
   |---|---|
   | `VITE_SUPABASE_URL` | Project URL from step 1.2 |
   | `VITE_SUPABASE_ANON_KEY` | anon key |
   | `SUPABASE_SERVICE_ROLE_KEY` | service_role key |
   | `AZURE_OPENAI_API_KEY` | the Azure key (same as Synqit prod, or Key 2 of the aatreus resource) |
   | `VITE_GUEST_PASSWORD` | the password you gave `seed-remote.mjs` |
   | `WEBHOOK_BASE_URL` | the service URL once you know it, e.g. `https://synqit-cad.onrender.com` |
   | `VITE_GOOGLE_CLIENT_ID`, `NEXAR_*` | optional, can stay empty |

   `VITE_*` variables are baked in at build time, so any change to them needs
   a redeploy ("Manual deploy → Clear build cache & deploy" is the safe one).
3. Deploy. First build is 4–6 minutes. Health check is `/cad/`; the root `/`
   redirects there.

## 3. What testers get

- Open `https://<service>.onrender.com/cad`. No login: guest mode signs them
  in as `guest@synqit.in`. Everyone shares that account's creations.
- **Board** button lists the tester's Synqit projects **if they are signed in
  to synqit.in in the same browser** (the studio calls
  `api.synqit.in` with their Synqit cookie; production CORS already allows
  `*.onrender.com` with credentials). Tell Lucky: log in at synqit.in first,
  then open the studio.
- PCB and Schematic views in the viewport read the same project data.
- The board brief is built in the browser on production (the server-side
  `cad-brief` endpoint lives on the Synqit `cad-sso` branch, not yet
  deployed), so part heights are estimated from footprints. When `cad-sso`
  ships to production the studio picks up the curated heights automatically.
- "Continue with Synqit" needs the `cad-sso` backend too; until then it
  returns 404. Guest mode makes it unnecessary for evaluation.

## 4. Turning guest mode off later

Remove `VITE_GUEST_EMAIL` and `VITE_GUEST_PASSWORD` on Render and redeploy.
Then testers sign up with email (Supabase sends the confirmation) or Google
(after the Render origin is added to the Google client and
`VITE_GOOGLE_CLIENT_ID` is set).

## 5. Costs and limits

- Render starter: $7/month. Free tier also works but sleeps after 15 minutes
  and the OpenSCAD worker download makes cold starts feel slow.
- Supabase free: 500 MB database, 1 GB storage; previews are small PNGs.
- Azure: gpt-5.1 usage counts against the same quota as Synqit production.
  Every prompt is 6–10 model calls; budget accordingly or create Key 2 to
  track it separately.
