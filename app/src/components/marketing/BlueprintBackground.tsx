/**
 * Animated hardware "blueprint" backdrop for the marketing pages.
 *
 * Hand-built inline SVG PCB line-art that feels alive: flowing trace dashes,
 * travelling signal pulses with a soft glow trail (reads like current), a
 * sweeping inspection beam, and breathing vias, over a drifting dot-grid.
 * Purely decorative (pointer-events-none). Two variants:
 *   - "hero"    rich + focal, with a readability fade.
 *   - "ambient" faint + slow, meant to sit full-bleed behind a whole page.
 * Colors are hardcoded (light-industrial theme) so the core-app theme is safe.
 */
const INK = "#16150F";
const RUST = "#B5532A";

const SIGNALS: { d: string; dur: number; flow: "bp-flow" | "bp-flow-2" }[] = [
  { d: "M834 286 H1010 V268 H1040", dur: 3.6, flow: "bp-flow" },
  { d: "M650 286 H540 V330 H430", dur: 4.8, flow: "bp-flow-2" },
  { d: "M760 400 V470 H600 V520", dur: 4.0, flow: "bp-flow" },
  { d: "M820 320 H980 V430 H1180", dur: 5.4, flow: "bp-flow-2" },
  { d: "M300 360 H200 V160 H520", dur: 6.0, flow: "bp-flow" },
  { d: "M724 230 V120 H1120 V250", dur: 5.6, flow: "bp-flow-2" },
  { d: "M668 400 V560 H1240", dur: 6.8, flow: "bp-flow" },
  { d: "M120 470 H360 V374", dur: 4.4, flow: "bp-flow-2" },
  { d: "M834 360 H1064 V470 H1320", dur: 5.0, flow: "bp-flow" },
  { d: "M650 320 H470 V196 H270", dur: 6.4, flow: "bp-flow-2" },
];

const BUS = [250, 262, 274, 286, 298];

const VIAS: [number, number][] = [
  [430, 320], [1040, 268], [760, 470], [600, 520], [834, 340],
  [980, 430], [1180, 430], [520, 160], [200, 360], [1120, 250],
  [668, 560], [360, 374], [1240, 560], [1064, 470], [270, 196],
];

export default function BlueprintBackground({
  variant = "hero",
  fixed = false,
  className = "",
}: {
  variant?: "hero" | "ambient";
  fixed?: boolean;
  className?: string;
}) {
  const ambient = variant === "ambient";
  const scale = ambient ? 1.5 : 0.85; // ambient slower, hero faster
  const wrapperOpacity = ambient ? 0.42 : 1;
  const glow = ambient ? 0.22 : 0.42;

  return (
    <div
      aria-hidden
      className={`${fixed ? "fixed" : "absolute"} inset-0 overflow-hidden pointer-events-none ${className}`}
      style={{ opacity: wrapperOpacity }}
    >
      <style>{`
        @keyframes bp-dash { to { stroke-dashoffset: -300; } }
        @keyframes bp-dash-slow { to { stroke-dashoffset: -160; } }
        @keyframes bp-pulse { 0%,100% { opacity:.12; r:2.3px; } 50% { opacity:.85; r:3.6px; } }
        @keyframes bp-drift { from { transform: translateX(0); } to { transform: translateX(-26px); } }
        @keyframes bp-scan { 0% { transform: translateX(-220px); } 100% { transform: translateX(1500px); } }
        .bp-flow   { stroke-dasharray: 6 9;  animation: bp-dash 4s linear infinite; }
        .bp-flow-2 { stroke-dasharray: 4 11; animation: bp-dash 6.5s linear infinite; }
        .bp-bus    { stroke-dasharray: 8 8;  animation: bp-dash-slow 3s linear infinite; }
        .bp-via    { animation: bp-pulse 3s ease-in-out infinite; }
        .bp-grid   { animation: bp-drift 6s linear infinite alternate; }
        .bp-scan   { animation: bp-scan 7s linear infinite; }
        @media (prefers-reduced-motion: reduce) {
          .bp-flow,.bp-flow-2,.bp-bus,.bp-via,.bp-grid,.bp-scan { animation: none; }
        }
      `}</style>

      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 1440 640"
        fill="none"
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <filter id="bp-glow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="3.5" />
          </filter>
          <linearGradient id="bp-beam" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={RUST} stopOpacity="0" />
            <stop offset="50%" stopColor={RUST} stopOpacity="0.30" />
            <stop offset="100%" stopColor={RUST} stopOpacity="0" />
          </linearGradient>
          <pattern id="bp-dots" width="26" height="26" patternUnits="userSpaceOnUse">
            <circle cx="1" cy="1" r="1" fill={INK} opacity="0.06" />
          </pattern>
        </defs>

        <rect className="bp-grid" x="-40" width="1520" height="640" fill="url(#bp-dots)" />

        {/* sweeping inspection beam (hero only) */}
        {!ambient && <rect className="bp-scan" x="-180" width="170" height="640" fill="url(#bp-beam)" />}

        {/* ---- faint ink line-art ---- */}
        <g stroke={INK} strokeWidth="1" opacity="0.2">
          <rect x="120" y="70" width="1200" height="500" rx="10" />
          <rect x="140" y="90" width="1160" height="460" rx="6" strokeDasharray="2 6" opacity="0.6" />

          {/* central MCU (U1) */}
          <rect x="650" y="230" width="170" height="170" rx="4" />
          {Array.from({ length: 9 }).map((_, i) => (
            <line key={`t${i}`} x1={668 + i * 18} y1="230" x2={668 + i * 18} y2="216" />
          ))}
          {Array.from({ length: 9 }).map((_, i) => (
            <line key={`b${i}`} x1={668 + i * 18} y1="400" x2={668 + i * 18} y2="414" />
          ))}
          {Array.from({ length: 8 }).map((_, i) => (
            <line key={`l${i}`} x1="650" y1={250 + i * 18} x2="636" y2={250 + i * 18} />
          ))}
          {Array.from({ length: 8 }).map((_, i) => (
            <line key={`r${i}`} x1="820" y1={250 + i * 18} x2="834" y2={250 + i * 18} />
          ))}

          {/* peripherals */}
          <rect x="300" y="300" width="96" height="74" rx="3" />
          <rect x="1040" y="250" width="130" height="96" rx="6" />
          <rect x="1052" y="262" width="106" height="72" rx="20" opacity="0.5" />
          <rect x="740" y="450" width="120" height="78" rx="3" />
          <rect x="980" y="430" width="84" height="60" rx="3" />
          <rect x="200" y="150" width="70" height="46" rx="3" />
          <rect x="1120" y="500" width="120" height="22" rx="3" />
          {Array.from({ length: 8 }).map((_, i) => (
            <circle key={`hp${i}`} cx={1130 + i * 14} cy="511" r="2.5" />
          ))}
          {[
            [500, 180], [560, 180], [600, 180], [940, 360], [940, 388],
            [430, 470], [470, 470], [880, 250], [880, 290],
          ].map(([x, y], i) => (
            <rect key={`p${i}`} x={x} y={y} width="30" height="14" rx="2" />
          ))}
        </g>

        {/* static routed traces (ink) */}
        <g stroke={INK} strokeWidth="1.25" opacity="0.22" fill="none">
          <path d="M430 320 H560 V300 H650" />
          <path d="M834 268 H980 V300 H1040" />
          <path d="M780 400 V450" />
          <path d="M700 400 V430 H600 V470" />
          <path d="M834 340 H900 V520 H300 V374" />
          <path d="M650 250 H470 V196" />
          <path d="M820 360 H1064 V460" />
        </g>

        {/* data bus (parallel flowing dashes) */}
        <g stroke={RUST} strokeWidth="1.2" fill="none" opacity="0.5">
          {BUS.map((y, i) => (
            <path key={i} className="bp-bus" d={`M834 ${y} H1180`} style={{ animationDelay: `${i * 0.12}s` }} />
          ))}
        </g>

        {/* animated signal traces (rust) */}
        <g stroke={RUST} strokeWidth="1.6" fill="none" opacity="0.85">
          {SIGNALS.map((s, i) => (
            <path key={i} className={s.flow} d={s.d} />
          ))}
        </g>

        {/* travelling pulses: soft glow trail + sharp core, reads like current */}
        {SIGNALS.map((s, i) => (
          <g key={i}>
            <circle r="6" fill={RUST} opacity={glow} filter="url(#bp-glow)">
              <animateMotion dur={`${(s.dur * scale).toFixed(2)}s`} repeatCount="indefinite" path={s.d} />
            </circle>
            <circle r="2.8" fill={RUST}>
              <animateMotion dur={`${(s.dur * scale).toFixed(2)}s`} repeatCount="indefinite" path={s.d} />
            </circle>
          </g>
        ))}

        {/* breathing vias */}
        <g fill={RUST}>
          {VIAS.map(([x, y], i) => (
            <circle key={i} className="bp-via" cx={x} cy={y} r="3" style={{ animationDelay: `${(i % 6) * 0.5}s` }} />
          ))}
        </g>

        {/* dimension + annotation callouts */}
        <g stroke={INK} strokeWidth="0.75" opacity="0.3">
          <path d="M650 230 V150 M820 230 V150" />
          <path d="M650 160 H820" />
        </g>
        <g fill={INK} opacity="0.4" fontFamily="'IBM Plex Mono', monospace" fontSize="11">
          <text x="724" y="146">17.0 mm</text>
          <text x="838" y="252">U1 · ESP32</text>
          <text x="300" y="294">U2 · LDO</text>
          <text x="1040" y="244">J1 · USB-C</text>
          <text x="740" y="444">U3 · BME280</text>
          <text x="980" y="424">U4 · MCP73831</text>
          <text x="1120" y="494">J2 · HEADER</text>
        </g>
        <g fill={RUST} opacity="0.55" fontFamily="'IBM Plex Mono', monospace" fontSize="10">
          <text x="1046" y="284">3V3</text>
          <text x="452" y="316">GPIO18</text>
          <text x="770" y="430">I2C_SDA</text>
          <text x="1120" y="320">VBUS</text>
          <text x="206" y="146">XTAL</text>
        </g>
      </svg>

      {/* readability fade — hero only */}
      {!ambient && (
        <div
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(120% 95% at 50% 36%, rgba(249,249,248,0.82) 0%, rgba(249,249,248,0.45) 48%, rgba(249,249,248,0.12) 100%)",
          }}
        />
      )}
    </div>
  );
}
