import { getDb } from '../../db/client.js';
import type { HouseholdRow, HouseholdStatus, ServiceTeamMember } from './types.js';

export async function create(input: {
  tenantId: string;
  name: string;
  primaryAdvisorId: string;
  serviceTeam: ServiceTeamMember[];
  notes?: string | null;
  createdBy: string;
}): Promise<HouseholdRow> {
  const sql = getDb();
  const [row] = await sql<HouseholdRow[]>`
    INSERT INTO households (
      tenant_id,
      name,
      primary_advisor_id,
      service_team_json,
      notes,
      created_by
    )
    VALUES (
      ${input.tenantId},
      ${input.name},
      ${input.primaryAdvisorId},
      ${JSON.stringify(input.serviceTeam)},
      ${input.notes ?? null},
      ${input.createdBy}
    )
    RETURNING *
  `;
  return row;
}

export async function findById(tenantId: string, id: string): Promise<HouseholdRow | undefined> {
  const sql = getDb();
  const [row] = await sql<HouseholdRow[]>`
    SELECT * FROM households
    WHERE tenant_id = ${tenantId} AND id = ${id}
  `;
  return row;
}

export async function list(
  tenantId: string,
  opts: { limit: number; offset: number; status?: HouseholdStatus; primaryAdvisorId?: string },
): Promise<{ rows: HouseholdRow[]; total: number }> {
  const sql = getDb();
  const conditions = [sql`tenant_id = ${tenantId}`];

  if (opts.status) {
    conditions.push(sql`status = ${opts.status}`);
  }
  if (opts.primaryAdvisorId) {
    conditions.push(sql`primary_advisor_id = ${opts.primaryAdvisorId}`);
  }

  const where = conditions.reduce((left, right) => sql`${left} AND ${right}`);

  const rows = await sql<HouseholdRow[]>`
    SELECT * FROM households
    WHERE ${where}
    ORDER BY created_at DESC, id DESC
    LIMIT ${opts.limit}
    OFFSET ${opts.offset}
  `;

  const [{ count }] = await sql<{ count: number }[]>`
    SELECT COUNT(*)::int AS count
    FROM households
    WHERE ${where}
  `;

  return { rows, total: count };
}

export async function update(
  tenantId: string,
  id: string,
  input: {
    name?: string;
    primaryAdvisorId?: string;
    serviceTeam?: ServiceTeamMember[];
    notes?: string | null;
  },
): Promise<HouseholdRow> {
  const sql = getDb();
  const [row] = await sql<HouseholdRow[]>`
    UPDATE households
    SET
      name = CASE WHEN ${input.name !== undefined} THEN ${input.name ?? null} ELSE name END,
      primary_advisor_id = CASE
        WHEN ${input.primaryAdvisorId !== undefined} THEN ${input.primaryAdvisorId ?? null}
        ELSE primary_advisor_id
      END,
      service_team_json = CASE
        WHEN ${input.serviceTeam !== undefined} THEN ${JSON.stringify(input.serviceTeam ?? [])}::jsonb
        ELSE service_team_json
      END,
      notes = CASE WHEN ${input.notes !== undefined} THEN ${input.notes ?? null} ELSE notes END,
      updated_at = now()
    WHERE tenant_id = ${tenantId} AND id = ${id}
    RETURNING *
  `;
  return row;
}

export async function updateStatus(
  tenantId: string,
  id: string,
  status: HouseholdStatus,
): Promise<HouseholdRow> {
  const sql = getDb();
  const [row] = await sql<HouseholdRow[]>`
    UPDATE households
    SET status = ${status}, updated_at = now()
    WHERE tenant_id = ${tenantId} AND id = ${id}
    RETURNING *
  `;
  return row;
}

export async function countBlockingAccounts(tenantId: string, householdId: string): Promise<number> {
  const sql = getDb();

  try {
    const [{ count }] = await sql<{ count: number }[]>`
      SELECT COUNT(*)::int AS count
      FROM accounts
      WHERE tenant_id = ${tenantId}
        AND household_id = ${householdId}
        AND status IN ('active', 'restricted')
    `;
    return count;
  } catch (error) {
    const code = typeof error === 'object' && error !== null && 'code' in error ? (error as { code?: string }).code : undefined;
    if (code === '42P01') {
      return 0;
    }
    throw error;
  }
}
