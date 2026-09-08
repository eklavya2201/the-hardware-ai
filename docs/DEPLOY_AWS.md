# Running the studio on AWS

The engine already runs on AWS and the studio should sit beside it, not on a
different provider. One box serves both.

## What is on the box today

`i-02b9f9b30f8ecaec6`, a **t3.small** (2 GB) in `ap-south-1b`, Elastic IP
`3.111.115.126`, Amazon Linux 2023, everything in Docker behind Caddy.

| container | what it is | memory |
|---|---|---|
| `aws-api-1` | the engine, `api.synqit.in` | 315 MB |
| `aws-api-staging-1` | staging engine, `staging.synqit.in` | 297 MB |
| `auditor` | the MPN audit loop | 518 MB |
| `aws-caddy-1` | TLS + reverse proxy | 21 MB |

DNS (Route 53 zone `Z0013709CRIW15M9Q0ZH`):

| name | points at |
|---|---|
| `synqit.in`, `www.synqit.in` | CloudFront `E1R85FGGRKPESM` → S3 `synqit-frontend-868072837784` |
| `api.synqit.in`, `staging.synqit.in` | `3.111.115.126` |

## The one problem to solve first: memory

The box reports **76 MB available of 1913 MB**, with 1.4 GB of a 2 GB swapfile
already in use. It is thrashing before the studio is added, and the studio
needs 250-400 MB. Two ways out, and they are not equivalent:

1. **Stop the auditor.** It is in a retry loop — Mouser has been answering
   `HTTP 403` and it has recorded nothing, waking every 900 s to fail again. It
   is spending 518 MB, a quarter of the box, to accomplish zero. Stopping it
   costs nothing until the Mouser key is fixed:
   `docker stop auditor`
2. **Resize to t3.medium** (4 GB, roughly $15 → $30 a month). Needs a stop and
   start, about two minutes of `api.synqit.in` being down. The Elastic IP
   survives, so no DNS change.

Do (1) now regardless; it is free. Do (2) as well if the engine, staging and
the studio are all meant to run at once with headroom.

## Build the image somewhere else

The box has two cores and no spare memory. Building the studio there will swap
and may fail. Build it where there is room and ship the image:

```sh
# locally, from the repo root
docker build -t synqit-studio:latest \
  --build-arg VITE_SUPABASE_URL=... \
  --build-arg VITE_SUPABASE_ANON_KEY=... \
  app
docker save synqit-studio:latest | gzip | \
  ssh -i ~/.ssh/synqit-ec2.pem ec2-user@3.111.115.126 'gunzip | sudo docker load'
```

## Wire it up

1. Copy this repo (or just `deploy/aws/`) to the box, and fill in
   `deploy/aws/studio.env` from `studio.env.example`.
2. Add the `studio.synqit.in` block from `deploy/aws/Caddyfile.studio` to
   `~/synqit/deploy/aws/Caddyfile`.
3. Add a Route 53 A record for `studio.synqit.in` → `3.111.115.126`.
4. Bring it up alongside the engine:

```sh
cd ~/synqit/deploy/aws
sudo docker compose -f docker-compose.yml \
  -f ~/the-hardware-ai/deploy/aws/docker-compose.studio.yml up -d studio caddy
```

Caddy issues the certificate on first request.

## Why the studio talks to `http://api:8000/api`

The studio proxies every engine call through its own origin
(`routes/api/synqit/$.ts`). On this box that proxy hop stays inside the compose
network: no public round trip, no TLS handshake, no CORS, and no cross-site
cookie for the browser to refuse. `SYNQIT_API_BASE` is set to the container
name in `docker-compose.studio.yml` for exactly that reason.

## Moving the apex

Only after `studio.synqit.in` has been used against real projects. It replaces
the front door of the live product, and the studio does not yet cover
everything the current app does — there is no project browser and no account
management in it. When that gap is closed:

1. Uncomment the apex block in the Caddyfile.
2. Repoint the Route 53 A-alias for `synqit.in` and `www.synqit.in` from the
   CloudFront distribution to `3.111.115.126`.
3. Leave the CloudFront distribution and its S3 bucket in place for a week, so
   the old app is one DNS change away from coming back.

Note that this gives up the CDN in front of the apex. Static assets are served
by the studio's Node process instead, which is fine at this traffic but is a
real change, not a detail.
