import { env } from './env';

/**
 * Octopart parts data through the Nexar API (Octopart's official API since
 * 2021). OAuth2 client-credentials, then GraphQL. Needs NEXAR_CLIENT_ID and
 * NEXAR_CLIENT_SECRET (free tier: https://nexar.com, "Supply" scope).
 *
 * What we return per part is what a CAD prompt needs: MPN, manufacturer,
 * one-line description, package / dimension specs, datasheet, stock and price.
 */
const TOKEN_URL = 'https://identity.nexar.com/connect/token';
const GRAPHQL_URL = 'https://api.nexar.com/graphql';

export type PartHit = {
  mpn: string;
  manufacturer: string;
  description: string;
  datasheetUrl: string | null;
  specs: { name: string; value: string }[];
  totalAvail: number | null;
  medianPriceUsd: number | null;
  octopartUrl: string | null;
};

let cachedToken: { value: string; expiresAt: number } | null = null;

export function nexarConfigured(): boolean {
  return Boolean(env('NEXAR_CLIENT_ID') && env('NEXAR_CLIENT_SECRET'));
}

async function token(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now() + 30_000) return cachedToken.value;
  const body = new URLSearchParams({
    grant_type: 'client_credentials',
    client_id: env('NEXAR_CLIENT_ID'),
    client_secret: env('NEXAR_CLIENT_SECRET'),
    scope: 'supply.domain',
  });
  const res = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok) throw new Error(`nexar token ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const data = (await res.json()) as { access_token: string; expires_in: number };
  cachedToken = { value: data.access_token, expiresAt: Date.now() + data.expires_in * 1000 };
  return cachedToken.value;
}

const QUERY = `
query PartSearch($q: String!, $limit: Int!) {
  supSearchMpn(q: $q, limit: $limit) {
    results {
      part {
        mpn
        octopartUrl
        manufacturer { name }
        shortDescription
        bestDatasheet { url }
        totalAvail
        medianPrice1000 { price currency }
        specs { attribute { name shortname } displayValue }
      }
    }
  }
}`;

// Specs a CAD model actually cares about, in display order.
const DIMENSION_SPECS = [
  'case_package', 'package', 'mounting_type', 'length', 'width', 'height',
  'diameter', 'thickness', 'pitch', 'numberofpins', 'number_of_pins',
  'leadpitch', 'lead_pitch', 'weight', 'material', 'color', 'voltage_rating_dc',
  'capacitance', 'resistance', 'inductance',
];

type RawPart = {
  mpn: string;
  octopartUrl?: string | null;
  manufacturer?: { name?: string } | null;
  shortDescription?: string | null;
  bestDatasheet?: { url?: string } | null;
  totalAvail?: number | null;
  medianPrice1000?: { price?: number; currency?: string } | null;
  specs?: { attribute?: { name?: string; shortname?: string }; displayValue?: string }[] | null;
};

export async function searchParts(q: string, limit = 5): Promise<PartHit[]> {
  const res = await fetch(GRAPHQL_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${await token()}` },
    body: JSON.stringify({ query: QUERY, variables: { q, limit } }),
  });
  if (!res.ok) throw new Error(`nexar graphql ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const payload = (await res.json()) as {
    data?: { supSearchMpn?: { results?: { part: RawPart }[] } };
    errors?: { message: string }[];
  };
  if (payload.errors?.length) throw new Error(`nexar: ${payload.errors[0].message}`);
  const results = payload.data?.supSearchMpn?.results ?? [];
  return results.map(({ part }) => {
    const specs = (part.specs ?? [])
      .map((s) => ({
        key: (s.attribute?.shortname || '').toLowerCase(),
        name: s.attribute?.name || s.attribute?.shortname || '',
        value: s.displayValue || '',
      }))
      .filter((s) => s.name && s.value);
    const ranked = [
      ...DIMENSION_SPECS.flatMap((k) => specs.filter((s) => s.key === k)),
      ...specs.filter((s) => !DIMENSION_SPECS.includes(s.key)),
    ].slice(0, 12);
    const price = part.medianPrice1000?.price;
    return {
      mpn: part.mpn,
      manufacturer: part.manufacturer?.name ?? '',
      description: part.shortDescription ?? '',
      datasheetUrl: part.bestDatasheet?.url ?? null,
      specs: ranked.map(({ name, value }) => ({ name, value })),
      totalAvail: part.totalAvail ?? null,
      medianPriceUsd: typeof price === 'number' && (part.medianPrice1000?.currency ?? 'USD') === 'USD' ? price : null,
      octopartUrl: part.octopartUrl ?? null,
    };
  });
}

/** The block the composer inserts into the prompt for a chosen part. */
export function partPromptBlock(p: PartHit): string {
  const lines = [
    `Part: ${p.mpn}${p.manufacturer ? ` (${p.manufacturer})` : ''}${p.description ? ` — ${p.description}` : ''}`,
    ...p.specs.map((s) => `  ${s.name}: ${s.value}`),
  ];
  if (p.datasheetUrl) lines.push(`  Datasheet: ${p.datasheetUrl}`);
  return lines.join('\n');
}
