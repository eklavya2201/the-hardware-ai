#!/usr/bin/env node
/**
 * One-time seed for a HOSTED Supabase project (Render deploy):
 *   node scripts/seed-remote.mjs --url https://xxxx.supabase.co --service-key <SERVICE_ROLE_KEY> \
 *        --guest-password <password> [--guest-email guest@synqit.in]
 *
 * 1. Creates the storage buckets the app expects (images, meshes, previews), private.
 * 2. Creates the confirmed guest account for no-auth evaluation mode.
 * Run `npx supabase db push` (or paste supabase/migrations/*.sql into the SQL editor)
 * BEFORE this, so the profiles trigger exists. Idempotent: safe to re-run.
 */
const args = process.argv.slice(2);
const opt = (n, d) => {
  const i = args.indexOf(n);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};
const URL_ = (opt('--url', process.env.VITE_SUPABASE_URL) || '').replace(/\/$/, '');
const KEY = opt('--service-key', process.env.SUPABASE_SERVICE_ROLE_KEY);
const EMAIL = opt('--guest-email', 'guest@synqit.in');
const PASSWORD = opt('--guest-password', process.env.VITE_GUEST_PASSWORD);
if (!URL_ || !KEY || !PASSWORD) {
  console.error('need --url, --service-key and --guest-password');
  process.exit(1);
}
const H = { apikey: KEY, Authorization: `Bearer ${KEY}`, 'Content-Type': 'application/json' };

for (const id of ['images', 'meshes', 'previews']) {
  const r = await fetch(`${URL_}/storage/v1/bucket`, { method: 'POST', headers: H, body: JSON.stringify({ id, name: id, public: false }) });
  const t = await r.text();
  console.log(r.ok ? `✔ bucket ${id}` : /already|exists|Duplicate/i.test(t) ? `✔ bucket ${id} (exists)` : `✖ bucket ${id}: ${t.slice(0, 120)}`);
}

let r = await fetch(`${URL_}/auth/v1/admin/users`, {
  method: 'POST',
  headers: H,
  body: JSON.stringify({ email: EMAIL, password: PASSWORD, email_confirm: true, user_metadata: { full_name: 'Synqit Guest' } }),
});
let body = await r.json().catch(() => ({}));
if (r.ok) console.log(`✔ guest ${EMAIL} created`);
else if (/already|exists|registered/i.test(JSON.stringify(body))) {
  const list = await fetch(`${URL_}/auth/v1/admin/users?per_page=1000`, { headers: H }).then((x) => x.json());
  const u = (list.users || []).find((x) => x.email === EMAIL);
  if (u) {
    await fetch(`${URL_}/auth/v1/admin/users/${u.id}`, { method: 'PUT', headers: H, body: JSON.stringify({ password: PASSWORD, email_confirm: true }) });
    console.log(`✔ guest ${EMAIL} existed; password synced`);
  }
} else {
  console.error('✖ guest creation failed:', JSON.stringify(body).slice(0, 200));
  process.exit(1);
}
console.log('\nNow set VITE_GUEST_EMAIL / VITE_GUEST_PASSWORD on Render to the same values and redeploy.');
