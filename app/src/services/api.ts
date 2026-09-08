import { z } from 'zod';

export function apiUrl(path: string) {
  const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');
  return `${basePath}/api/${path}`;
}

export async function apiJson(
  path: string,
  init?: RequestInit,
): Promise<unknown>;
export async function apiJson<T>(
  path: string,
  init: RequestInit,
  schema: z.ZodType<T>,
): Promise<T>;
export async function apiJson<T>(
  path: string,
  init: RequestInit = {},
  schema?: z.ZodType<T>,
): Promise<T | unknown> {
  const url = apiUrl(path);
  // No bearer token: the studio's own server reads the httpOnly engine cookie,
  // which the browser attaches because this is the same origin.
  const response = await fetch(url, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init.headers,
    },
  });
  const contentType = response.headers.get('Content-Type') ?? '';
  if (!contentType.includes('application/json')) {
    const preview = (await response.text()).slice(0, 160);
    throw new Error(`Unexpected API response from ${url}: ${preview}`);
  }
  const data: unknown = await response.json();
  if (!response.ok) {
    const errorValue =
      typeof data === 'object' && data !== null
        ? Reflect.get(data, 'error')
        : undefined;
    throw new Error(
      typeof errorValue === 'string' ? errorValue : response.statusText,
    );
  }
  return schema ? schema.parse(data) : data;
}
