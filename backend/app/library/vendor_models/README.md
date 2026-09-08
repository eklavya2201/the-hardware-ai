# Per-MPN vendor 3D models (drop-in)

Drop a vendor-accurate 3D model here named exactly after the library MPN —
`<MPN>.step`, `<MPN>.stp`, or `<MPN>.glb` (e.g. `BME280.step`) — then run:

```bash
cd backend
python -m scripts.fetch_3d_models --mpn     # needs cascadio + trimesh (tooling-only)
```

Each file is converted to `frontend/public/models/auto/mpn_<MPN>.glb` and
written to `frontend/src/lib/models3d.manifest.json` under the key
`MPN:<mpn>`. The frontend resolver (`frontend/src/lib/models3d.ts`) prefers
that entry over every package stand-in — only the backend-stamped URL and the
hand-curated hero overrides rank higher. Verify with:

```bash
cd frontend
npm run audit:models3d
```

**Licensing:** many manufacturer STEP files are not redistributable. Only
commit models we are allowed to ship (KiCad-library and self-authored models
are fine; check vendor terms for the rest — see
`docs/REAL_3D_MODELS_PLAN.md` §4.5).
