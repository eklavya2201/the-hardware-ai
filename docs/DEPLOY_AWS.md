# Running the whole product on AWS

One EC2 box serves everything: the studio people use, the engine that designs
the boards, and Caddy in front. `synqit.in` is the studio. There is no Render
service and no CloudFront/S3 front end any more.

## What is on the box

`i-02b9f9b30f8ecaec6`, a **t3.small** (2 GB) in `ap-south-1b`, Elastic IP
`3.111.115.126`, Amazon Linux 2023, everything in Docker.

Route 53 zone `Z0013709CRIW15M9Q0ZH`. After the cut-over every name points at
the Elastic IP:

| name | serves |
|---|---|
| `synqit.in`, `www.synqit.in` | the studio |
| `api.synqit.in` | the engine's API |

## One account, not two

The studio owns sign-in (Supabase). The engine owns projects, the part library
and the agent, and has its own user table. Nobody should hold two accounts, so
the studio's *server* provisions the engine account:

1. A person signs in to the studio.
2. `POST /api/synqit-session` proves who they are with their studio token,
   then calls the engine's `POST /auth/service/session` with a shared secret
   and their email.
3. The engine creates the account if it does not exist (passwordless, like a
   Google account) and returns a session, which the studio keeps in an
   httpOnly `sq_engine` cookie on its own domain.
4. Every board call goes to `/api/synqit/*` on the studio's own origin and is
   forwarded server-side with that session.

So `STUDIO_SERVICE_SECRET` must be **the same value** in the engine's
`backend.env` and the studio's `studio.env`. It never reaches a browser. The
engine refuses the endpoint outright when the variable is unset, so an
unconfigured deployment is not a passwordless login for any email address.

The browser never talks to `api.synqit.in` directly. That is what removes the
cross-site cookie problem: on this box the proxy hop is `http://api:8000` over
the compose network and never leaves the machine.

## Memory

The box reported **76 MB available of 1913 MB**, with 1.4 GB of a 2 GB
swapfile in use, before the studio was added. The studio needs 250-400 MB.

Two containers on the old stack were spending that headroom:

- `auditor` — 518 MB, in a retry loop. Mouser answers `HTTP 403`, it records
  nothing, and it wakes every 900 s to fail again. It is worth nothing until
  the Mouser key is fixed.
- `aws-api-staging-1` — 297 MB, the staging engine for `staging.synqit.in`.

Stopping both frees about 815 MB, which is enough. If all of it is meant to
run at once, resize to **t3.medium** (4 GB, roughly $15 → $30 a month) — a
stop/start of about two minutes, and the Elastic IP survives so no DNS change.

## Build the image somewhere else

Two cores and no spare memory: building on the box will swap and may fail.
Build where there is room and ship the image.

```sh
# from the repo root, on a machine with Docker
docker build -t synqit-studio:latest \
  --build-arg VITE_SUPABASE_URL=... \
  --build-arg VITE_SUPABASE_ANON_KEY=... \
  app

docker save synqit-studio:latest | gzip | \
  ssh -i ~/.ssh/synqit-ec2.pem ec2-user@3.111.115.126 'gunzip | sudo docker load'
```

The engine image is built on the box, as it already is — it is unchanged from
what runs there today apart from the additions in this repo.

## Deploy

1. Copy this repo to the box at `~/the-hardware-ai`.
2. Fill in `deploy/aws/studio.env` (from `studio.env.example`) and copy the
   engine's existing `~/synqit/deploy/aws/backend.env` to
   `~/the-hardware-ai/deploy/aws/backend.env`, adding `STUDIO_SERVICE_SECRET`.
3. Stop the old stack so the ports and the volume are free:

```sh
cd ~/synqit/deploy/aws && sudo docker compose down
sudo docker stop auditor
```

4. Bring up the new one:

```sh
cd ~/the-hardware-ai/deploy/aws && sudo docker compose up -d
```

5. Move `synqit.in` and `www.synqit.in` in Route 53 from the CloudFront alias
   to an A record for `3.111.115.126`. Caddy issues the certificates on the
   first request.

The compose file declares the engine's database volume as **external**
(`aws_synqit-data`, created by the old stack) so switching stacks cannot
silently start from an empty database.

## Rolling back

The old stack is still on the box and its files are untouched:

```sh
cd ~/the-hardware-ai/deploy/aws && sudo docker compose down
cd ~/synqit/deploy/aws && sudo docker compose up -d
```

Then point `synqit.in` back at the CloudFront distribution `E1R85FGGRKPESM`.
Leave that distribution and its S3 bucket in place for a week or two — it is
the only way back to the old front end, and it costs nothing idle.

## What the old front end did that the studio does not

The current `synqit.in` app has a project browser, account settings and an
admin area. The studio has a creations list and the board views, and reaches
projects through the composer's **Board** button. Nothing is lost on the engine
side — the projects and the API are unchanged — but those screens do not exist
in the studio yet.
