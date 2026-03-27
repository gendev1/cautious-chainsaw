import { getDb } from '../../db/client.js';
import type { AuditEventRow, AuditQueryFilters, PaginatedAuditResult } from './types.js';

/**
 * Encode a cursor from a row's created_at + id.
 * Format: base64("<ISO timestamp>|<uuid>")
 */
function encodeCursor(row: AuditEventRow): string {
  const payload = `${row.created_at.toISOString()}|${row.id}`;
  return Buffer.from(payload).toString('base64url');
}

/**
 * Decode a cursor back to { createdAt, id }.
 */
function decodeCursor(cursor: string): { createdAt: string; id: string } {
  const payload = Buffer.from(cursor, 'base64url').toString('utf8');
  const separatorIndex = payload.lastIndexOf('|');
  if (separatorIndex === -1) {
    throw new Error('Invalid cursor format');
  }
  return {
    createdAt: payload.slice(0, separatorIndex),
    id: payload.slice(separatorIndex + 1),
  };
}

export async function query(filters: AuditQueryFilters): Promise<PaginatedAuditResult> {
  const sql = getDb();

  // We fetch limit + 1 to determine if there are more results
  const fetchLimit = filters.limit + 1;

  // Build dynamic conditions using postgres.js fragment API
  const conditions = [sql`firm_id = ${filters.firmId}`];

  if (filters.actorId) {
    conditions.push(sql`actor_id = ${filters.actorId}`);
  }

  if (filters.action) {
    // Support prefix matching: "auth.%" becomes a LIKE pattern
    if (filters.action.includes('%') || filters.action.includes('_')) {
      conditions.push(sql`action LIKE ${filters.action}`);
    } else {
      conditions.push(sql`action = ${filters.action}`);
    }
  }

  if (filters.resourceType) {
    conditions.push(sql`resource_type = ${filters.resourceType}`);
  }

  if (filters.resourceId) {
    conditions.push(sql`resource_id = ${filters.resourceId}`);
  }

  if (filters.from) {
    conditions.push(sql`created_at >= ${filters.from}`);
  }

  if (filters.to) {
    conditions.push(sql`created_at <= ${filters.to}`);
  }

  if (filters.cursor) {
    const { createdAt, id } = decodeCursor(filters.cursor);
    conditions.push(
      sql`(created_at < ${createdAt} OR (created_at = ${createdAt} AND id < ${id}))`,
    );
  }

  // Combine all conditions with AND
  const where = conditions.reduce((acc, cond) => sql`${acc} AND ${cond}`);

  const rows = await sql<AuditEventRow[]>`
    SELECT *
    FROM audit_events
    WHERE ${where}
    ORDER BY created_at DESC, id DESC
    LIMIT ${fetchLimit}
  `;

  const hasMore = rows.length > filters.limit;
  const events = hasMore ? rows.slice(0, filters.limit) : rows;
  const nextCursor =
    hasMore && events.length > 0
      ? encodeCursor(events[events.length - 1])
      : undefined;

  return { events, nextCursor, hasMore };
}
