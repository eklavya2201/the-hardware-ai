"""The studio designs 3D geometry. Schematics and PCBs are Synqit's job —
say so instead of implying otherwise, and give the user the way there."""
import os

ROOT = r"C:\Users\pande\AppData\Local\Temp\claude\C--Users-pande\2a81eca4-34a4-4964-b1ec-f7feae8a9f3b\scratchpad\cadam"
def rd(p): return open(os.path.join(ROOT, p), encoding="utf-8").read()
def wr(p, s): open(os.path.join(ROOT, p), "w", encoding="utf-8", newline="\n").write(s)

# 1. Agent prompt: no pretending.
p = "src/server/aiChat.ts"; s = rd(p); o = s
anchor = "Synqit board (when the message contains a \"Synqit board\" block with a"
assert anchor in s, "anchor missing"
rule = """What you are, and what you are not:
- You design 3D geometry: enclosures, brackets, holders, mechanical parts.
- You do NOT draw schematics, pick electronic components, or lay out PCBs.
  Synqit does that. If the user asks for a schematic, a wiring diagram, a
  netlist, or a PCB layout, say plainly that this studio builds the 3D model,
  that the electronics are designed in Synqit, and that they can bring the
  finished board here with the Board button in the composer. Then offer the
  mechanical part you CAN build (the enclosure, the mount, the fixture).
- Never claim to have produced a schematic or a PCB, and never describe an
  OpenSCAD model as one.

"""
s = s.replace(anchor, rule + anchor, 1)
assert s != o
wr(p, s); print("aiChat: scope rule added (%d chars)" % len(rule))

# 2. Viewport empty state: explain and point at Synqit.
p = "src/components/synqit/StudioViewport.tsx"; s = rd(p); o = s
s = s.replace(
    "import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';",
    "import { synqitHandoffUrl } from '@/components/auth/SynqitSignInButton';\nimport { SYNQIT_APP_URL } from '@/lib/synqitProject';",
)
s = s.replace(
    """            <Empty>
              No Synqit project linked. Use the <b>Board</b> button in the composer to pick one, or open the studio from a Synqit project.
            </Empty>""",
    """            <Empty>
              <p className="mb-2 text-sq-text-primary">
                {mode === 'pcb' ? 'No board here yet' : 'No schematic here yet'}
              </p>
              <p className="mb-4">
                This studio builds the 3D model. Schematics and PCBs are designed in Synqit — describe the
                circuit there, then bring the finished board back with the <b>Board</b> button in the composer.
              </p>
              <a
                href={`${SYNQIT_APP_URL}/projects`}
                target="_blank"
                rel="noreferrer"
                className="inline-block rounded-md bg-sq-blue/15 px-3 py-1.5 text-xs text-sq-blue hover:bg-sq-blue/25"
              >
                Design the board in Synqit
              </a>
            </Empty>""",
)
assert s != o
wr(p, s); print("StudioViewport: empty state explains and links out")
