import { getDb } from '../../db/client.js';
import type { ImpersonationSessionRow } from './types.js';

export async function create(input: {
  impersonatorUserId: string;
  targetUserId: string;
  firmId: string;
  reason: string;
  idempotencyKey: string;
  expiresAt: Date;
}): Promise<ImpersonationSessionRow> {
  const sql = getDb();
  const [row] = await sql<ImpersonationSessionRow[]>`
    INSERT INTO impersonation_sessions (
      impersonator_user_id, target_user_id, firm_id, reason, idempotency_key, expires_at
    )
    VALUES (
      ${input.impersonatorUserId},
      ${input.targetUserId},
      ${input.firmId},
      ${input.reason},
      ${input.idempotencyKey},
      ${input.expiresAt}
    )
    RETURNING *
  `;
  return row;
}

export async function findByIdempotencyKey(
  firmId: string,
  idempotencyKey: string,
): Promise<ImpersonationSessionRow | undefined> {
  const sql = getDb();
  const [row] = await sql<ImpersonationSessionRow[]>`
    SELECT * FROM impersonation_sessions
    WHERE firm_id = ${firmId} AND idempotency_key = ${idempotencyKey}
  `;
  return row;
}

export async function findById(
  id: string,
  firmId: string,
): Promise<ImpersonationSessionRow | undefined> {
  const sql = getDb();
  const [row] = await sql<ImpersonationSessionRow[]>`
    SELECT * FROM impersonation_sessions
    WHERE id = ${id} AND firm_id = ${firmId}
  `;
  return row;
}

export async function endSession(
  id: string,
  firmId: string,
): Promise<ImpersonationSessionRow> {
  const sql = getDb();
  const [row] = await sql<ImpersonationSessionRow[]>`
    UPDATE impersonation_sessions
    SET ended_at = now()
    WHERE id = ${id} AND firm_id = ${firmId}
    RETURNING *
  `;
  return row;
}

export async function listSessions(
  firmId: string,
  opts: {
    impersonatorUserId?: string;
    targetUserId?: string;
    startedAfter?: string;
    startedBefore?: string;
    cursor?: string;
    limit: number;
  },
): Promise<{ rows: ImpersonationSessionRow[]; total: number }> {
  const sql = getDb();

  const conditions = [sql`firm_id = ${firmId}`];

  if (opts.impersonatorUserId) {
    conditions.push(sql`impersonator_user_id = ${opts.impersonatorUserId}`);
  }
  if (opts.targetUserId) {
    conditions.push(sql`target_user_id = ${opts.targetUserId}`);
  }
  if (opts.startedAfter) {
    conditions.push(sql`started_at >= ${opts.startedAfter}`);
  }
  if (opts.startedBefore) {
    conditions.push(sql`started_at <= ${opts.startedBefore}`);
  }
  if (opts.cursor) {
    conditions.push(sql`id < ${opts.cursor}`);
  }

  const where = conditions.reduce((acc, cond) => sql`${acc} AND ${cond}`);

  const [countResult] = await sql<[{ count: string }]>`
    SELECT count(*)::text AS count FROM impersonation_sessions WHERE ${where}
  `;
  const total = parseInt(countResult.count, 10);

  const rows = await sql<ImpersonationSessionRow[]>`
    SELECT * FROM impersonation_sessions
    WHERE ${where}
    ORDER BY started_at DESC, id DESC
    LIMIT ${opts.limit}
  `;

  return { rows, total };
}
