import { getDb } from '../../db/client.js';
import type { FirmRow } from './types.js';
import type { FirmStatus } from '../../shared/types.js';

export async function create(input: {
  name: string;
  slug: string;
  branding: Record<string, unknown>;
}): Promise<FirmRow> {
  const sql = getDb();
  const [row] = await sql<FirmRow[]>`
    INSERT INTO firms (name, slug, branding)
    VALUES (${input.name}, ${input.slug}, ${JSON.stringify(input.branding)})
    RETURNING *
  `;
  return row;
}

export async function findById(id: string): Promise<FirmRow | undefined> {
  const sql = getDb();
  const [row] = await sql<FirmRow[]>`
    SELECT * FROM firms WHERE id = ${id}
  `;
  return row;
}

export async function findBySlug(slug: string): Promise<FirmRow | undefined> {
  const sql = getDb();
  const [row] = await sql<FirmRow[]>`
    SELECT * FROM firms WHERE slug = ${slug}
  `;
  return row;
}

export async function update(
  id: string,
  input: { name?: string; branding?: Record<string, unknown> },
): Promise<FirmRow> {
  const sql = getDb();
  const [row] = await sql<FirmRow[]>`
    UPDATE firms SET
      name = COALESCE(${input.name ?? null}, name),
      branding = COALESCE(${input.branding ? JSON.stringify(input.branding) : null}::jsonb, branding),
      updated_at = now()
    WHERE id = ${id}
    RETURNING *
  `;
  return row;
}

export async function updateStatus(
  id: string,
  status: FirmStatus,
): Promise<FirmRow> {
  const sql = getDb();
  const [row] = await sql<FirmRow[]>`
    UPDATE firms SET status = ${status}, updated_at = now()
    WHERE id = ${id}
    RETURNING *
  `;
  return row;
}
