import { getDb } from '../../db/client.js';
import type { SessionRow } from './types.js';

export async function listActiveByUser(userId: string, firmId: string): Promise<SessionRow[]> {
  const sql = getDb();
  return sql<SessionRow[]>`
    SELECT * FROM sessions
    WHERE user_id = ${userId}
      AND firm_id = ${firmId}
      AND revoked_at IS NULL
    ORDER BY last_active_at DESC
  `;
}

export async function findById(id: string): Promise<SessionRow | undefined> {
  const sql = getDb();
  const [row] = await sql<SessionRow[]>`
    SELECT * FROM sessions WHERE id = ${id}
  `;
  return row;
}

export async function revokeSession(id: string): Promise<SessionRow | undefined> {
  const sql = getDb();
  const [row] = await sql<SessionRow[]>`
    UPDATE sessions
    SET revoked_at = now()
    WHERE id = ${id} AND revoked_at IS NULL
    RETURNING *
  `;
  return row;
}

export async function revokeAllForUser(
  userId: string,
  firmId: string,
  exceptSessionId?: string,
): Promise<SessionRow[]> {
  const sql = getDb();
  if (exceptSessionId) {
    return sql<SessionRow[]>`
      UPDATE sessions
      SET revoked_at = now()
      WHERE user_id = ${userId}
        AND firm_id = ${firmId}
        AND revoked_at IS NULL
        AND id != ${exceptSessionId}
      RETURNING *
    `;
  }
  return sql<SessionRow[]>`
    UPDATE sessions
    SET revoked_at = now()
    WHERE user_id = ${userId}
      AND firm_id = ${firmId}
      AND revoked_at IS NULL
    RETURNING *
  `;
}

export async function countActiveByUser(userId: string, firmId: string): Promise<number> {
  const sql = getDb();
  const [row] = await sql<{ count: string }[]>`
    SELECT count(*)::text AS count FROM sessions
    WHERE user_id = ${userId}
      AND firm_id = ${firmId}
      AND revoked_at IS NULL
  `;
  return parseInt(row.count, 10);
}

export async function revokeRefreshTokensForSession(sessionId: string): Promise<void> {
  const sql = getDb();
  await sql`
    UPDATE refresh_tokens
    SET revoked_at = now()
    WHERE session_id = ${sessionId}
      AND revoked_at IS NULL
  `;
}

export async function findOldestActiveSessions(
  userId: string,
  firmId: string,
  limit: number,
): Promise<SessionRow[]> {
  const sql = getDb();
  return sql<SessionRow[]>`
    SELECT * FROM sessions
    WHERE user_id = ${userId}
      AND firm_id = ${firmId}
      AND revoked_at IS NULL
    ORDER BY last_active_at ASC
    LIMIT ${limit}
  `;
}
