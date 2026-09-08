"""Short, human part names on the PCB silkscreen and schematic symbols."""
import os

ROOT = r"C:\Users\pande\AppData\Local\Temp\claude\C--Users-pande\2a81eca4-34a4-4964-b1ec-f7feae8a9f3b\scratchpad\cadam"
def rd(p): return open(os.path.join(ROOT, p), encoding="utf-8").read()
def wr(p, s): open(os.path.join(ROOT, p), "w", encoding="utf-8", newline="\n").write(s)

HELPER = '''
/**
 * A short, human name for a part — what an engineer says out loud, not the
 * order code. "ESP32-WROOM-32" -> "ESP32", "STM32F412RET6" -> "STM32F4",
 * a 10uF capacitor -> "10µF". Used on the PCB silkscreen and under schematic
 * symbols, where the full manufacturer part number is noise.
 */
const FAMILIES: [RegExp, string][] = [
  [/^ESP32[-_]?(C\\d)/i, 'ESP32-$1'],
  [/^ESP32[-_]?(S\\d)/i, 'ESP32-$1'],
  [/^(ESP32)/i, '$1'],
  [/^(ESP8266)/i, '$1'],
  [/^(STM32[A-Z]\\d)/i, '$1'],
  [/^(ATMEGA\\d+|ATTINY\\d+)/i, '$1'],
  [/^(RP2040|RP2350)/i, '$1'],
  [/^(NRF\\d{4})/i, '$1'],
  [/^(BNO\\d{3}|BMP\\d{3}|BME\\d{3}|MPU[-_]?\\d{4}|ICM[-_]?\\d+|LIS\\d[A-Z]*)/i, '$1'],
  [/^(SSD\\d{4}|ILI\\d{4})/i, '$1'],
  [/^(TLC\\d{3}|NE555|LM\\d{3,4}|TL\\d{3})/i, '$1'],
  [/^(AMS1117|AP\\d{4}|MCP\\d{4}|TPS\\d{4,5}|XC\\d{4}|RT\\d{4})/i, '$1'],
  [/^(CH\\d{3}[A-Z]?|CP\\d{4}|FT\\d{3}[A-Z]?)/i, '$1'],
  [/^(DRV\\d{4}|TB\\d{4}|A4988|TMC\\d{4})/i, '$1'],
  [/^(W25Q\\d+|AT24C\\d+|24LC\\d+)/i, '$1'],
  [/^(TP\\d{4}|MAX\\d{3,4}|INA\\d{3})/i, '$1'],
];

export function shortPartName(part: {
  mpn?: string | null;
  value?: string | null;
  description?: string | null;
  refdes?: string;
}): string {
  const mpn = (part.mpn || '').trim();
  const value = (part.value || '').trim();
  const prefix = (part.refdes || '').replace(/\\d+$/, '').toUpperCase();

  // Passives: the value IS the name.
  if (
    value &&
    value.length <= 12 &&
    (['R', 'C', 'L', 'Y'].includes(prefix) ||
      /^[\\d.]+\\s*(µ|u|n|p|k|M|m)?(F|Ω|ohm|H|Hz)/i.test(value))
  ) {
    return value;
  }
  for (const [re, out] of FAMILIES) {
    const m = mpn.match(re);
    if (m) return out.replace(/\\$(\\d)/g, (_, i) => m[Number(i)] ?? '');
  }
  const hay = (mpn + ' ' + (part.description ?? '')).toUpperCase();
  if (/USB[-_ ]?C|TYPE[-_ ]?C/.test(hay)) return 'USB-C';
  if (/MICRO[-_ ]?USB/.test(hay)) return 'microUSB';
  const jst = /JST[-_ ]?(PH|XH|SH|ZH)/.exec(hay);
  if (jst) return 'JST-' + jst[1];
  if (/PIN\\s?HEADER|PINHEADER|HEADER/.test(hay)) return 'header';
  if (/TACTILE|PUSH\\s?BUTTON|SW_PUSH|SWITCH/.test(hay)) return 'button';
  if (/BUZZER/.test(hay)) return 'buzzer';
  if (/CRYSTAL|OSCILLATOR/.test(hay)) return value || 'crystal';
  if (/BATTERY|18650|LIPO/.test(hay)) return 'battery';
  if (/\\bLED\\b/.test(hay)) return 'LED';
  if (/DIODE|SCHOTTKY/.test(hay)) return 'diode';
  if (/MOSFET|TRANSISTOR/.test(hay)) return 'FET';

  if (!mpn) return part.refdes || '';
  const head = mpn.split(/[-_/\\s]/)[0];
  return (head.length >= 3 ? head : mpn).slice(0, 12);
}
'''

s = rd("src/lib/synqitData.ts")
if "shortPartName" not in s:
    wr("src/lib/synqitData.ts", s + HELPER)
    print("synqitData: shortPartName added")

# ---- PcbView
p = "src/components/synqit/PcbView.tsx"; s = rd(p); o = s
s = s.replace(
    "import type { SqBoard, SqMechanical, SqPlaced } from '@/lib/synqitData';",
    "import {\n  shortPartName,\n  type SqBoard,\n  type SqComponent,\n  type SqMechanical,\n  type SqPlaced,\n} from '@/lib/synqitData';",
)
s = s.replace(
    "export function PcbView({ board, mechanical }: { board: SqBoard; mechanical: SqMechanical | null }) {",
    """export function PcbView({
  board,
  mechanical,
  components = [],
}: {
  board: SqBoard;
  mechanical: SqMechanical | null;
  /** IR components, for value and description when naming parts. */
  components?: SqComponent[];
}) {
  // Silkscreen labels: the part's short name ("ESP32", "10uF") rather than the
  // order code, with the reference designator as the alternative view.
  const [labelMode, setLabelMode] = useState<'name' | 'ref' | 'none'>('name');
  const names = useMemo(() => {
    const byRef = new Map(components.map((c) => [c.refdes, c]));
    const out = new Map<string, string>();
    for (const pc of board.components) {
      const ir = byRef.get(pc.refdes);
      out.set(
        pc.refdes,
        shortPartName({ mpn: pc.mpn, value: ir?.value, description: ir?.description, refdes: pc.refdes }),
      );
    }
    return out;
  }, [board.components, components]);""",
)
s = s.replace(
    """  const [show, setShow] = useState<{ top: boolean; bottom: boolean; rats: boolean; labels: boolean }>({
    top: true,
    bottom: true,
    rats: true,
    labels: true,
  });""",
    """  const [show, setShow] = useState<{ top: boolean; bottom: boolean; rats: boolean }>({
    top: true,
    bottom: true,
    rats: true,
  });""",
)
s = s.replace("{show.labels && (", "{labelMode !== 'none' && (")
s = s.replace(
    "            {pc.refdes}\n          </text>",
    "            {labelMode === 'name' ? names.get(pc.refdes) ?? pc.refdes : pc.refdes}\n          </text>",
)
s = s.replace(
    "fontSize={Math.min(fontSize, Math.max(0.8, Math.min(w, h) * 0.55))}",
    "fontSize={Math.min(fontSize, Math.max(0.7, Math.min(h * 0.5, (w * 1.7) / Math.max(2, (labelMode === 'name' ? names.get(pc.refdes) ?? pc.refdes : pc.refdes).length))))}",
)
s = s.replace(
    """          {(['top', 'bottom', 'rats', 'labels'] as const).map((k) => (""",
    """          {(['top', 'bottom', 'rats'] as const).map((k) => (""",
)
s = s.replace(
    "              {k === 'top' ? 'Top' : k === 'bottom' ? 'Bottom' : k === 'rats' ? 'Rats' : 'Refdes'}\n            </button>\n          ))}",
    """              {k === 'top' ? 'Top' : k === 'bottom' ? 'Bottom' : 'Rats'}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setLabelMode((m) => (m === 'name' ? 'ref' : m === 'ref' ? 'none' : 'name'))}
            title="Silkscreen labels: part name, reference designator, or none"
            className="rounded bg-sq-neutral-800 px-2 py-0.5 text-sq-text-primary"
          >
            {labelMode === 'name' ? 'Names' : labelMode === 'ref' ? 'Refs' : 'No labels'}
          </button>""",
)
assert s != o and "show.labels" not in s, "pcb patch incomplete"
wr(p, s); print("PcbView: short names + label toggle")

# ---- SchematicView
p = "src/components/synqit/SchematicView.tsx"; s = rd(p); o = s
s = s.replace(
    "import { classifyNet, hashedColor, NET_COLORS, type SqComponent, type SqNet } from '@/lib/synqitData';",
    "import {\n  classifyNet,\n  hashedColor,\n  NET_COLORS,\n  shortPartName,\n  type SqComponent,\n  type SqNet,\n} from '@/lib/synqitData';",
)
s = s.replace("{(p.c.value || p.c.mpn).slice(0, 22)}", "{shortPartName(p.c)}")
assert s != o, "schematic patch incomplete"
wr(p, s); print("SchematicView: short names")

# ---- StudioViewport passes IR components through
p = "src/components/synqit/StudioViewport.tsx"; s = rd(p); o = s
s = s.replace(
    "<PcbView board={data.board} mechanical={data.mechanical} />",
    "<PcbView board={data.board} mechanical={data.mechanical} components={data.ir?.components ?? []} />",
)
assert s != o, "viewport patch incomplete"
wr(p, s); print("StudioViewport: names wired")
