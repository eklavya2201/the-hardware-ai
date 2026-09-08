import { useEffect, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useAuth } from "@/contexts/AuthContext";
import BlueprintBackground from "@/components/marketing/BlueprintBackground";

// Light-industrial theme is hardcoded locally so the core app theme is untouched.
// LINE/RUST are used in inline `style` props; the rest live as Tailwind arbitrary values.
const LINE = "#E5E4E0";
const RUST = "#B5532A";

// Tracks which in-page section is in view so the nav can highlight it.
function useActiveSection() {
  const [active, setActive] = useState<string | null>(null);
  useEffect(() => {
    const els = ["showcase", "workflow"]
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (els.length === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) setActive(e.target.id);
        });
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, []);
  return active;
}

export function LandingView() {
  // Smooth in-page scrolling for the nav anchors, scoped to this page.
  useEffect(() => {
    const html = document.documentElement;
    const prev = html.style.scrollBehavior;
    html.style.scrollBehavior = "smooth";
    return () => {
      html.style.scrollBehavior = prev;
    };
  }, []);

  return (
    <div className="relative min-h-screen bg-[#f9f9f8] font-sans text-[#16150F] antialiased">
      {/* faint full-bleed blueprint behind the whole page (fixed = parallax) */}
      <BlueprintBackground variant="ambient" fixed className="z-0" />
      <div className="relative z-10">
        <TopNav />
        <Hero />
        <StatStrip />
        <Features />
        <TrustSection />
        <FieldReports />
        <HowItWorks />
        <ClosingCta />
        <Footer />
      </div>
    </div>
  );
}

function TopNav() {
  const { user } = useAuth();
  const active = useActiveSection();
  return (
    <header
      className="sticky top-0 z-50 border-b bg-[#f9f9f8]/80 backdrop-blur-md"
      style={{ borderColor: LINE }}
    >
      <nav className="mx-auto flex h-16 max-w-[1280px] items-center justify-between px-6">
        <div className="flex items-center gap-8">
          <Link to="/" className="flex items-center gap-2 text-[20px] font-bold tracking-tight">
            <img src={`${import.meta.env.BASE_URL}synqit-logo.png`} alt="" className="h-6 w-6 object-contain" />
            Synqit
          </Link>
          <div className="hidden items-center gap-6 md:flex">
            <NavLink href="#showcase" active={active === "showcase"}>
              Product
            </NavLink>
            <NavLink href="#workflow" active={active === "workflow"}>
              How it works
            </NavLink>
            <Link
              to="/docs"
              className="font-mono text-[12px] uppercase tracking-wide text-[#6B6760] transition-colors hover:text-[#B5532A]"
            >
              Docs
            </Link>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {user ? (
            <Link
              to="/app"
              className="rounded-[3px] bg-[#111110] px-5 py-2.5 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-95"
            >
              Open app
            </Link>
          ) : (
            <>
              <Link
                to="/signin"
                className="px-3 py-2 font-mono text-[12px] uppercase tracking-wide text-[#6B6760] transition-colors hover:text-[#B5532A]"
              >
                Sign in
              </Link>
              <Link
                to="/signup"
                className="rounded-[3px] bg-[#111110] px-5 py-2.5 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-95"
              >
                Start building
              </Link>
            </>
          )}
        </div>
      </nav>
    </header>
  );
}

function NavLink({
  href,
  active,
  children,
}: {
  href: string;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className={`font-mono text-[12px] uppercase tracking-wide transition-colors hover:text-[#B5532A] ${
        active ? "border-b-2 border-[#B5532A] pb-0.5 text-[#16150F]" : "text-[#6B6760]"
      }`}
    >
      {children}
    </a>
  );
}

function Hero() {
  const { user } = useAuth();
  return (
    <section className="relative overflow-hidden px-6 pb-24 pt-20 md:pt-28">
      <BlueprintBackground />
      <div className="relative mx-auto max-w-[920px] text-center">
        <span
          className="inline-block rounded-[3px] border bg-white px-3 py-1 font-mono text-[11px] uppercase tracking-[0.08em] text-[#B5532A]"
          style={{ borderColor: LINE }}
        >
          Version 2.4 · Now in beta
        </span>
        <h1 className="mx-auto mt-7 max-w-[860px] text-[52px] font-bold leading-[1.0] tracking-[-0.035em] md:text-[82px]">
          The <span className="text-[#B5532A]">compile button</span> for hardware.
        </h1>
        <p className="mx-auto mt-6 max-w-[620px] text-[17px] leading-relaxed text-[#6B6760]">
          Describe a circuit in plain English and Synqit drafts it from real parts — then 78
          engineering checks review every net, pin, and power rail, the way a compiler checks
          code. You approve every change.
        </p>
        <PromptBox />
        <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to={user ? "/app" : "/signup"}
            className="w-full rounded-[3px] bg-[#111110] px-8 py-4 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-[0.98] sm:w-auto"
          >
            {user ? "Open the app" : "Start building"}
          </Link>
          <a
            href="#showcase"
            className="w-full rounded-[3px] border bg-white px-8 py-4 font-mono text-[12px] uppercase tracking-wide text-[#16150F] transition-colors hover:bg-[#f3f4f3] sm:w-auto"
            style={{ borderColor: LINE }}
          >
            See how it works
          </a>
        </div>
      </div>

      {/* product screenshot in a plain framed window */}
      <div className="relative mx-auto mt-16 max-w-[1040px]">
        <BrowserFrame
          src={`${import.meta.env.BASE_URL}showcase/review.png`}
          alt="Synqit design review of an ESP32 sensor board — components used, pin mapping with a strapping-pin warning, and verifier findings"
          tab="synqit.app/editor/esp32-sensor-node · design review"
        />
      </div>
    </section>
  );
}

/** A few briefs that actually build, so the box is never a blank stare. */
const EXAMPLES = [
  "A low-power ESP32-S3 sensor board: BME280 over I2C, an MCP73831 LiPo charger, and a 3.3V LDO. Add decoupling caps.",
  "An RP2040 board with a microSD slot, a 6-axis IMU and a JST battery input.",
  "An ATtiny85 driving four WS2812 LEDs from a coin cell, with a user button.",
];

/**
 * Start a board from the landing page.
 *
 * The whole promise of the page is "describe a circuit", so the page lets you
 * do exactly that instead of asking you to sign up first and find the box
 * later. The brief travels to the studio as ?prompt=, which runs it when you
 * are signed in and drops it into the composer when you are not.
 */
function PromptBox() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  const start = () => {
    const brief = value.trim();
    if (!brief) return;
    void navigate({ to: "/app", search: { prompt: brief } });
  };

  return (
    <div className="mx-auto mt-9 max-w-[720px]">
      <div
        className="flex items-end gap-2 rounded-[5px] border bg-white p-2 shadow-[0_2px_24px_rgba(0,0,0,0.06)] focus-within:border-[#B5532A]"
        style={{ borderColor: LINE }}
      >
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter is a newline, the way every composer works.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              start();
            }
          }}
          rows={2}
          placeholder="Describe the circuit you want to build..."
          className="min-h-[52px] flex-1 resize-none bg-transparent px-3 py-2 text-[15px] leading-relaxed text-[#16150F] outline-none placeholder:text-[#9C988E]"
        />
        <button
          type="button"
          onClick={start}
          disabled={!value.trim()}
          className="rounded-[3px] bg-[#111110] px-5 py-3 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-95 disabled:opacity-35"
        >
          Build it
        </button>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-[#9C988E]">Try</span>
        {EXAMPLES.map((example, i) => (
          <button
            key={example}
            type="button"
            onClick={() => setValue(example)}
            className="max-w-[240px] truncate rounded-[3px] border bg-white px-3 py-1.5 text-left text-[12px] text-[#6B6760] transition-colors hover:border-[#B5532A] hover:text-[#16150F]"
            style={{ borderColor: LINE }}
            title={example}
          >
            {["ESP32 sensor node", "RP2040 datalogger", "Coin-cell LED driver"][i]}
          </button>
        ))}
      </div>
    </div>
  );
}

function BrowserFrame({ src, alt, tab }: { src: string; alt: string; tab: string }) {
  return (
    <div
      className="overflow-hidden rounded-[5px] border bg-white p-1.5 shadow-[0_2px_24px_rgba(0,0,0,0.06)]"
      style={{ borderColor: LINE }}
    >
      <div
        className="flex items-center gap-2 rounded-t-[3px] border-b px-3 py-2"
        style={{ borderColor: LINE }}
      >
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: LINE }} />
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: LINE }} />
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: LINE }} />
        <span
          className="ml-3 flex-1 truncate rounded-[3px] bg-[#f3f4f3] px-3 py-1 font-mono text-[11px] text-[#9C988E]"
        >
          {tab}
        </span>
      </div>
      <img src={src} alt={alt} className="block w-full rounded-b-[3px]" loading="lazy" />
    </div>
  );
}

const STATS = [
  { eyebrow: "Verification engine", value: "95", label: "deterministic design checks" },
  { eyebrow: "Component library", value: "23941", label: "curated parts, no invented MPNs" },
  { eyebrow: "Handoff", value: "KiCad", label: "project, Gerbers, drill, pick-and-place, BOM, STEP, IDF" },
];

function StatStrip() {
  return (
    <section className="border-y px-6 py-12" style={{ borderColor: LINE }}>
      <div className="mx-auto grid max-w-[1100px] grid-cols-1 gap-10 md:grid-cols-3">
        {STATS.map((s, i) => (
          <div
            key={s.eyebrow}
            className={`flex flex-col items-center md:items-start ${
              i === 1 ? "border-y py-8 md:border-x md:border-y-0 md:px-12 md:py-0" : ""
            }`}
            style={i === 1 ? { borderColor: LINE } : undefined}
          >
            <span className="mb-2 font-mono text-[11px] uppercase tracking-[0.08em] text-[#9C988E]">
              {s.eyebrow}
            </span>
            <div className="flex items-baseline gap-2">
              <span className="text-[34px] font-bold leading-none tracking-tight">{s.value}</span>
              <span className="font-mono text-[12px] uppercase tracking-wide text-[#6B6760]">
                {s.label}
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

const FEATURES = [
  {
    eyebrow: "Design review",
    title: "AI that checks the work, not just does it",
    body: "Every change runs through deterministic engineering checks first: an ERC pin-type matrix that catches rail shorts and output contention, IPC-2221-cited trace-width and clearance rules, MCU pin-safety tables that flag strapping pins and 5V-into-3.3V mistakes, and KiCad's own industry DRC engine. Every finding names its rule and source — and the LLM design critic runs outside the agent loop, so the model that drafts your circuit never grades its own work.",
    img: "/showcase/review.png",
    alt: "Design review tab with components, pin mapping, power rails, and verifier findings",
    log: [
      "// DESIGN REVIEW",
      "40 schematic · 7 PCB · 6 mechanical checks",
      "ERC pin matrix · IPC-2221 clauses",
      "KiCad industry DRC · findings source-tagged",
    ],
  },
  {
    eyebrow: "Intelligence",
    title: "Real parts, never hallucinated",
    body: "Every component comes from a curated library with a real part number. The copilot can suggest a part, but it can never place one that does not exist. Expand the reasoning to see why each pick was made.",
    img: "/showcase/schematic.png",
    alt: "Generated schematic with real components and a live BOM",
    bullets: ["Curated MPNs only", "Reasoning shown per part"],
  },
  {
    eyebrow: "Routing",
    title: "It places and routes the board",
    body: "Force-directed placement and a built-in router lay down traces, vias, and a ground pour for an instant preview; Finalize hands the board to FreeRouting (the open-source autorouter, GPL-3.0) for the manufacturing-grade route. Drag any part to move it, press R to rotate, then re-route in a single click.",
    img: "/showcase/pcb.png",
    alt: "Routed two-layer PCB with traces and vias",
    log: ["// DRC PASS", "0 errors · 5 warnings", "13 parts placed", "2-layer · 33 traces · 24 vias"],
  },
  {
    eyebrow: "Visualization",
    title: "See it in 3D before you solder",
    body: "Real component package models drop onto the board so you can check fit and orientation. Spin it, toggle layers, and catch mechanical issues long before the fab does.",
    img: "/showcase/board3d.png",
    alt: "3D rendered PCB with real component models",
    bullets: ["Real package models", "Toggle copper, pads, vias"],
  },
];

function Features() {
  return (
    <section id="showcase" className="mx-auto max-w-[1180px] scroll-mt-24 space-y-28 px-6 py-24">
      {FEATURES.map((f, i) => (
        <div
          key={f.title}
          className={`flex flex-col items-center gap-14 md:flex-row ${
            i % 2 === 1 ? "md:flex-row-reverse" : ""
          }`}
        >
          <div className="w-full space-y-5 md:w-1/2">
            <span className="font-mono text-[12px] uppercase tracking-[0.08em] text-[#B5532A]">
              {f.eyebrow}
            </span>
            <h2 className="text-[28px] font-bold leading-tight tracking-tight">{f.title}</h2>
            <p className="text-[15px] leading-relaxed text-[#6B6760]">{f.body}</p>
            {f.bullets && (
              <ul className="space-y-2.5 pt-1">
                {f.bullets.map((b) => (
                  <li key={b} className="flex items-center gap-2.5 font-mono text-[12px] uppercase tracking-wide">
                    <span className="material-symbols-outlined text-[16px] text-[#B5532A]">
                      check_circle
                    </span>
                    {b}
                  </li>
                ))}
              </ul>
            )}
            {f.log && (
              <div className="rounded-[4px] bg-[#111110] p-4 font-mono text-[11px] leading-relaxed text-white/90">
                {f.log.map((l, j) => (
                  <div key={j} className={j === 0 ? "text-white/40" : ""}>
                    {l}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div
            className="w-full overflow-hidden rounded-[4px] border bg-white p-1 md:w-1/2"
            style={{ borderColor: LINE }}
          >
            <img src={f.img} alt={f.alt} className="block w-full rounded-[2px]" loading="lazy" />
          </div>
        </div>
      ))}
    </section>
  );
}

const GUARANTEES = [
  {
    icon: "verified_user",
    title: "No invented parts",
    body: "Every component is a real MPN from a curated library. The agent literally cannot place a part that is not in the library.",
  },
  {
    icon: "rule",
    title: "89 checks on every design",
    body: "Deterministic schematic, PCB, and enclosure rules catch floating pins, rail shorts, undersized regulators, and 5V-into-3.3V mistakes before anything reaches a fab.",
  },
  {
    icon: "balance",
    title: "The AI never grades its own work",
    body: "Rule-based checks run first and the LLM design critic runs outside the agent loop — the model that drafts your circuit is never the one that approves it.",
  },
  {
    icon: "history",
    title: "Every change is revertable",
    body: "Each turn and edit is saved as a commit, so you can review the design like code and roll back any change you do not want.",
  },
];

function TrustSection() {
  return (
    <section className="border-t bg-[#f3f4f3] px-6 py-24" style={{ borderColor: LINE }}>
      <div className="mx-auto max-w-[1180px]">
        <div className="mb-14 text-center">
          <span className="font-mono text-[12px] uppercase tracking-[0.12em] text-[#B5532A]">
            Why trust the output
          </span>
          <h2 className="mt-4 text-[30px] font-bold tracking-tight">
            Built so the design holds up to scrutiny
          </h2>
        </div>
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
          {GUARANTEES.map((g) => (
            <div
              key={g.title}
              className="flex flex-col gap-4 border bg-white p-8"
              style={{ borderColor: LINE }}
            >
              <span className="material-symbols-outlined text-[28px] text-[#B5532A]">{g.icon}</span>
              <h3 className="text-[17px] font-semibold tracking-tight">{g.title}</h3>
              <p className="text-[14px] leading-relaxed text-[#6B6760]">{g.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const REPORTS = [
  {
    quote:
      "It built the schematic with real components, a proper LDO, a reverse-polarity diode, and decoupling caps with actual specs, plus a live BOM and a DRC verifier with real error counts. Genuinely impressed for a beta.",
    role: "Beta tester · Hardware developer",
  },
  {
    quote:
      "The clarifying questions before generating anything felt really thoughtful. That is the part that convinced me it was not just a generic AI wrapper.",
    role: "Beta tester · Embedded systems",
  },
];

function FieldReports() {
  return (
    <section className="mx-auto max-w-[1180px] px-6 py-24">
      <h2 className="mb-12 font-mono text-[12px] uppercase tracking-[0.12em] text-[#9C988E]">
        Field reports
      </h2>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {REPORTS.map((r) => (
          <figure
            key={r.role}
            className="flex flex-col justify-between border border-l-[3px] bg-white p-8"
            style={{ borderColor: LINE, borderLeftColor: RUST }}
          >
            <blockquote className="mb-8 text-[16px] leading-relaxed text-[#16150F]">
              {r.quote}
            </blockquote>
            <figcaption className="font-mono text-[11px] uppercase tracking-wide text-[#B5532A]">
              {r.role}
            </figcaption>
          </figure>
        ))}
      </div>
      <p className="mt-6 font-mono text-[11px] uppercase tracking-wide text-[#9C988E]">
        Quotes from real beta testers, shown anonymously.
      </p>
    </section>
  );
}

const STEPS = [
  { n: "01", label: "Describe", body: "Tell Synqit what you are building, in plain English." },
  {
    n: "02",
    label: "Review",
    body: "Every change is drafted from real parts, then run through 78 engineering checks. Findings name their rule; you accept or reject each diff.",
  },
  {
    n: "03",
    label: "Ship",
    body: "Export a full KiCad project (schematic, board, netlist), RS-274X Gerbers with Excellon drill and a pick-and-place file, the BOM, plus STEP and IDF for mechanical CAD — a clean handoff to the tools you already trust.",
  },
];

function HowItWorks() {
  return (
    <section id="workflow" className="scroll-mt-16 bg-[#111110] px-6 py-20 text-white">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-12 md:flex-row md:items-start md:justify-between">
        {STEPS.map((s) => (
          <div key={s.n} className="flex-1">
            <div className="flex items-center gap-4">
              <span className="font-mono text-[22px] font-bold text-[#B5532A]">{s.n}</span>
              <span className="font-mono text-[12px] uppercase tracking-[0.1em]">{s.label}</span>
            </div>
            <p className="mt-3 max-w-[280px] text-[13px] leading-relaxed text-white/55">{s.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function ClosingCta() {
  const { user } = useAuth();
  return (
    <section className="relative overflow-hidden px-6 py-28 text-center">
      <BlueprintBackground className="opacity-60" />
      <div className="relative mx-auto max-w-[720px]">
        <h2 className="text-[38px] font-bold tracking-tight md:text-[46px]">
          Build something this weekend.
        </h2>
        <p className="mx-auto mt-5 max-w-[460px] text-[16px] text-[#6B6760]">
          Start your first project free. No credit card, free during beta.
        </p>
        <Link
          to={user ? "/app" : "/signup"}
          className="mt-9 inline-block rounded-[3px] bg-[#111110] px-10 py-4 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-[0.98]"
        >
          {user ? "Open the app" : "Start building"}
        </Link>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t bg-[#f9f9f8]" style={{ borderColor: LINE }}>
      <div className="mx-auto flex max-w-[1280px] flex-col justify-between gap-8 px-6 py-12 md:flex-row md:items-start">
        <div>
          <span className="text-[18px] font-bold tracking-tight">Synqit</span>
          <p className="mt-2 max-w-[280px] font-mono text-[11px] leading-relaxed text-[#9C988E]">
            AI design review for hardware — drafted from real parts, checked like code,
            handed off to KiCad.
          </p>
        </div>
        <div className="flex flex-wrap gap-12">
          <FooterCol
            heading="Product"
            links={[
              { label: "Product tour", href: "#showcase" },
              { label: "How it works", href: "#workflow" },
              { label: "Docs", to: "/docs" },
            ]}
          />
          <FooterCol
            heading="Company"
            links={[
              { label: "Privacy", to: "/privacy-policy" },
              { label: "Terms", to: "/terms-of-service" },
            ]}
          />
        </div>
      </div>
    </footer>
  );
}

type FLink = { label: string; to: string } | { label: string; href: string };

function FooterCol({ heading, links }: { heading: string; links: FLink[] }) {
  return (
    <div className="flex flex-col gap-3">
      <span className="font-mono text-[11px] font-semibold uppercase tracking-wide">{heading}</span>
      {links.map((l) =>
        "to" in l ? (
          <Link
            key={l.label}
            to={l.to}
            className="font-mono text-[12px] text-[#6B6760] transition-colors hover:text-[#B5532A]"
          >
            {l.label}
          </Link>
        ) : (
          <a
            key={l.label}
            href={l.href}
            className="font-mono text-[12px] text-[#6B6760] transition-colors hover:text-[#B5532A]"
          >
            {l.label}
          </a>
        ),
      )}
    </div>
  );
}
