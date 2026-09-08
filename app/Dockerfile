# The studio: TanStack Start (SSR + server routes) on Node.
#
# Server routes are not optional here -- /api/synqit/* proxies the engine,
# /api/parametric-chat runs the agent -- so this is a Node server, not a static
# bundle on S3.
#
# Built in two stages because node_modules is ~700 MB and the build output is
# ~40 MB; the runtime image carries only what it needs to serve.
FROM node:22.14.0-slim AS build
WORKDIR /app

# Dependencies first, so a source-only change reuses this layer.
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

COPY . .

# VITE_* values are baked into the client bundle at build time, so they are
# build args rather than runtime environment. Server-only secrets (the Azure
# key, the Supabase service role) are NOT here -- they stay runtime env.
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_ANON_KEY
ARG VITE_SYNQIT_APP_URL=https://synqit.in
ARG VITE_AZURE_DEPLOYMENTS=gpt-5.1,gpt-5-pro
ARG VITE_DEFAULT_MODEL=azure/gpt-5.1
ARG VITE_GOOGLE_CLIENT_ID
RUN npm run build

FROM node:22.14.0-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
# The box has 2 GB and runs the engine too; keep the heap honest.
ENV NODE_OPTIONS=--max-old-space-size=384
COPY --from=build /app/.output ./.output
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
