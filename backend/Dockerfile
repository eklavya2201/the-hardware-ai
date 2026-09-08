# Synqit backend — FastAPI + ngspice on Linux.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ngspice from apt — replaces the Windows binary in backend/ngspice-46_64/.
# kicad-cli + pcbnew (KiCad's python module) back the KiCad bridge
# (app/services/kicad_bridge.py); apt builds pcbnew against the *system*
# python3 (/usr/bin/python3), which the bridge discovers by probing.
# build-essential covers any wheels that need to compile (rare with these pins).
# kicad-footprints (the standard .pretty libs) is only a Recommends of kicad, so
# under --no-install-recommends it must be listed explicitly — without it
# build_pcb.py silently skips every standard-lib footprint and exports an empty
# board (FreeRouting then routes nothing and Finalize 503s).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ngspice \
      kicad \
      kicad-footprints \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# FreeRouting (the routing offload — app/services/routing_freerouting.py). It's an
# external GPL autorouter run as a subprocess (like ngspice/kicad-cli): the
# on-demand "Finalize" step routes a board completely + DRC-clean. Use **v2.1.0**
# — it is headless-capable (the service passes -Djava.awt.headless=true, so NO GUI
# window) AND Java-21-compatible. Bookworm ships Java 17, so pull 21 from
# backports. The jar download is best-effort: if it fails, FREEROUTING_JAR points
# at a missing file and the app degrades gracefully to the in-house A* router.
ARG FREEROUTING_VERSION=2.1.0
RUN echo 'deb http://deb.debian.org/debian bookworm-backports main' \
      > /etc/apt/sources.list.d/backports.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends -t bookworm-backports openjdk-21-jre-headless \
 && apt-get install -y --no-install-recommends curl \
 && mkdir -p /opt/freerouting \
 && ( curl -fsSL "https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar" \
        -o /opt/freerouting/freerouting.jar \
      || echo "WARNING: FreeRouting download failed — in-house router will be used." ) \
 && apt-get purge -y curl && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
ENV FREEROUTING_JAR=/opt/freerouting/freerouting.jar

# CAD kernel for the mechanical lane (app/services/mechanical_bridge.py):
# CadQuery pulls the OCP (OpenCASCADE) wheel. Without it `GET /export/step`
# answers 503 and /api/health reports mechanical_mode: absent — which is what
# prod did until the Prompt-to-Product launch (2026-09). The OCP wheel needs a
# few X/GL runtime libs even headless. Kept out of requirements.txt so a
# KiCad-less dev box can still `pip install -r requirements.txt` in seconds.
ARG CADQUERY_VERSION=2.8.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglu1-mesa libxrender1 libxext6 libxi6 libsm6 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install "cadquery==${CADQUERY_VERSION}" \
 && python -c "import cadquery as cq; cq.exporters.export(cq.Workplane('XY').box(1,1,1), '/tmp/_k.step'); print('CAD kernel ok')" \
 && rm -f /tmp/_k.step

# FreeCAD (editable .FCStd of the product — app/services/freecad_bridge.py) and
# Blender (headless Cycles product render — app/services/blender_bridge.py).
# Both are separate GPL processes driven by files, like KiCad/FreeRouting/
# ngspice (see THIRD_PARTY_LICENSES.md). Measured on this base: FreeCAD 1.0
# ~0.7 GB, Blender 4.3 ~0.95 GB; a 640x400 Cycles CPU frame renders in ~1 s.
RUN apt-get update && apt-get install -y --no-install-recommends \
      freecad \
      blender \
 && rm -rf /var/lib/apt/lists/*
ENV FREECAD_CMD=/usr/bin/freecadcmd \
    BLENDER_BIN=/usr/bin/blender

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Render injects $PORT; default to 10000 for local `docker run`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
