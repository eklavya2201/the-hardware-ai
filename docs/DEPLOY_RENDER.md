# Deploying Synqit CAD to Render (for Lucky and other testers)

Three pieces: the app on Render, a hosted Supabase project for its database,
auth and file storage, and Synqit production as the source of boards. About
30 minutes the first time. Everything below that touches an account is a
human step; the repo already contains the blueprint and the scripts.

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
