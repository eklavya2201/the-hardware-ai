import { Link } from '@tanstack/react-router';

const LINE = '#E5E4E0';

/**
 * What the product does and what it will not claim, in the landing page's
 * voice and colours. The old app's help page lived inside a shell this app
 * does not have, so this is written fresh rather than dragged across.
 */
export function DocsView() {
  return (
    <div className="min-h-screen bg-[#f9f9f8] text-[#16150F]">
      <header className="sticky top-0 z-50 border-b bg-[#f9f9f8]/80 backdrop-blur-md" style={{ borderColor: LINE }}>
        <nav className="mx-auto flex h-16 max-w-[1280px] items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-2 text-[20px] font-bold tracking-tight">
            <img src={`${import.meta.env.BASE_URL}synqit-logo.png`} alt="" className="h-6 w-6 object-contain" />
            Synqit
          </Link>
          <Link
            to="/app"
            className="rounded-[3px] bg-[#111110] px-5 py-2.5 font-mono text-[12px] uppercase tracking-wide text-white transition-transform active:scale-95"
          >
            Open app
          </Link>
        </nav>
      </header>

      <main className="mx-auto max-w-[760px] px-6 py-16">
        <h1 className="text-[40px] font-bold leading-tight tracking-[-0.03em]">Docs</h1>
        <p className="mt-4 text-[17px] leading-relaxed text-[#6B6760]">
          Synqit drafts a board from a sentence and then checks it. The checking is the part that
          matters, so this page is mostly about what is checked and what is not.
        </p>

        <Section title="Getting started">
          <p>
            Describe the circuit on the home page or in the composer — the parts, the interfaces and
            anything that constrains the board, like a battery or a fixed size. Synqit picks real
            parts from its library, wires the schematic, places the board and routes it.
          </p>
          <p>
            Every change arrives as a diff you accept or reject. Nothing is applied behind your back.
          </p>
        </Section>

        <Section title="What gets checked">
          <p>
            After each change, 78 deterministic rules run over the whole design: power rails, pin
            types and directions, footprint sanity, board size, duplicate controllers, unconnected
            nets. They are code, not model opinion, which is why &ldquo;0 errors&rdquo; means
            something. Findings name the rule that produced them.
          </p>
          <p>
            Treat warnings as review items and errors as things to fix before you export. A board
            with unrouted nets or an oversize outline is not reported as ready.
          </p>
        </Section>

        <Section title="What you can export">
          <p>
            The netlist and BOM at any time; Gerbers, a KiCad board and a STEP model once the board
            is placed and routed. The BOM carries real order codes, so a part that nobody sells is a
            finding rather than a surprise at checkout.
          </p>
        </Section>

        <Section title="What it will not do">
          <p>
            It does not certify a design. It does not check thermals, EMC, or mechanical fit against
            an enclosure it did not generate. It will not tell you a board is manufacturable — it
            tells you which of its own rules passed. A human still signs off before anything is
            fabricated.
          </p>
        </Section>

        <Section title="Views">
          <p>
            <strong>Schematic</strong> groups parts into functional blocks the way a drawn sheet
            does. <strong>PCB</strong> shows the real outline, pads, traces and remaining ratlines.
            <strong> 3D</strong> is the enclosure, built around the board&rsquo;s measured geometry
            rather than a guess. <strong>BOM</strong> and <strong>Build guide</strong> come from the
            same design, so they cannot drift from it.
          </p>
        </Section>

        <div className="mt-14 border-t pt-8" style={{ borderColor: LINE }}>
          <Link to="/app" className="font-mono text-[12px] uppercase tracking-wide text-[#B5532A] hover:underline">
            Open the app →
          </Link>
        </div>
      </main>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-12">
      <h2 className="font-mono text-[12px] uppercase tracking-[0.08em] text-[#B5532A]">{title}</h2>
      <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-[#3D3A34]">{children}</div>
    </section>
  );
}
