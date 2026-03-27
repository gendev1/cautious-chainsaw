import { getDb } from '../../db/client.js';
import type { UserRow, InvitationRow } from './types.js';
import type { UserStatus, InvitationStatus } from '../../shared/types.js';

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export async function createUser(input: {
  firmId: string;
  email: string;
  displayName: string;
  passwordHash?: string;
  status?: UserStatus;
}): Promise<UserRow> {
  const sql = getDb();
  const [row] = await sql<UserRow[]>`
    INSERT INTO users (firm_id, email, display_name, password_hash, status)
    VALUES (
      ${input.firmId},
      ${input.email},
      ${input.displayName},
      ${input.passwordHash ?? null},
      ${input.status ?? 'invited'}
    )
    RETURNING *
  `;
  return row;
}

export async function findUserById(firmId: string, id: string): Promise<UserRow | undefined> {
  const sql = getDb();
  const [row] = await sql<UserRow[]>`
    SELECT * FROM users WHERE id = ${id} AND firm_id = ${firmId}
  `;
  return row;
}

export async function findUserByEmail(firmId: string, email: string): Promise<UserRow | undefined> {
  const sql = getDb();
  const [row] = await sql<UserRow[]>`
    SELECT * FROM users WHERE firm_id = ${firmId} AND email = ${email}
  `;
  return row;
}

export async function listUsers(
  firmId: string,
  opts: { cursor?: string; limit: number; status?: UserStatus; search?: string },
): Promise<{ rows: UserRow[]; total: number }> {
  const sql = getDb();

  const conditions = [sql`firm_id = ${firmId}`];

  if (opts.status) {
    conditions.push(sql`status = ${opts.status}`);
  }
  if (opts.search) {
    const pattern = `%${opts.search}%`;
    conditions.push(sql`(display_name ILIKE ${pattern} OR email ILIKE ${pattern})`);
  }
  if (opts.cursor) {
    conditions.push(sql`id > ${opts.cursor}`);
  }

  const where = conditions.reduce((a, b) => sql`${a} AND ${b}`);

  const rows = await sql<UserRow[]>`
    SELECT * FROM users
    WHERE ${where}
    ORDER BY id ASC
    LIMIT ${opts.limit + 1}
  `;

  // Total count (without cursor / limit)
  const countConditions = [sql`firm_id = ${firmId}`];
  if (opts.status) countConditions.push(sql`status = ${opts.status}`);
  if (opts.search) {
    const pattern = `%${opts.search}%`;
    countConditions.push(sql`(display_name ILIKE ${pattern} OR email ILIKE ${pattern})`);
  }
  const countWhere = countConditions.reduce((a, b) => sql`${a} AND ${b}`);

  const [{ count }] = await sql<{ count: number }[]>`
    SELECT COUNT(*)::int AS count FROM users WHERE ${countWhere}
  `;

  return { rows, total: count };
}

export async function updateUser(
  firmId: string,
  id: string,
  input: { displayName?: string; status?: UserStatus; passwordHash?: string },
): Promise<UserRow> {
  const sql = getDb();
  const [row] = await sql<UserRow[]>`
    UPDATE users SET
      display_name = COALESCE(${input.displayName ?? null}, display_name),
      status = COALESCE(${input.status ?? null}, status),
      password_hash = COALESCE(${input.passwordHash ?? null}, password_hash),
      updated_at = now()
    WHERE id = ${id} AND firm_id = ${firmId}
    RETURNING *
  `;
  return row;
}

// ---------------------------------------------------------------------------
// Invitations
// ---------------------------------------------------------------------------

export async function createInvitation(input: {
  firmId: string;
  email: string;
  role: string;
  displayName: string;
  invitedBy: string;
  tokenHash: string;
  expiresAt: Date;
}): Promise<InvitationRow> {
  const sql = getDb();
  const [row] = await sql<InvitationRow[]>`
    INSERT INTO invitations (firm_id, email, role, display_name, invited_by, token_hash, expires_at)
    VALUES (
      ${input.firmId},
      ${input.email},
      ${input.role},
      ${input.displayName},
      ${input.invitedBy},
      ${input.tokenHash},
      ${input.expiresAt}
    )
    RETURNING *
  `;
  return row;
}

export async function findInvitationById(id: string): Promise<InvitationRow | undefined> {
  const sql = getDb();
  const [row] = await sql<InvitationRow[]>`
    SELECT * FROM invitations WHERE id = ${id}
  `;
  return row;
}

export async function findPendingInvitationByEmail(
  firmId: string,
  email: string,
): Promise<InvitationRow | undefined> {
  const sql = getDb();
  const [row] = await sql<InvitationRow[]>`
    SELECT * FROM invitations
    WHERE firm_id = ${firmId} AND email = ${email} AND status = 'pending'
  `;
  return row;
}

export async function findInvitationByTokenHash(tokenHash: string): Promise<InvitationRow | undefined> {
  const sql = getDb();
  const [row] = await sql<InvitationRow[]>`
    SELECT * FROM invitations WHERE token_hash = ${tokenHash} AND status = 'pending'
  `;
  return row;
}

export async function updateInvitationStatus(
  id: string,
  status: InvitationStatus,
  opts?: { acceptedAt?: Date; tokenHash?: string; expiresAt?: Date },
): Promise<InvitationRow> {
  const sql = getDb();
  const [row] = await sql<InvitationRow[]>`
    UPDATE invitations SET
      status = ${status},
      accepted_at = COALESCE(${opts?.acceptedAt ?? null}, accepted_at),
      token_hash = COALESCE(${opts?.tokenHash ?? null}, token_hash),
      expires_at = COALESCE(${opts?.expiresAt ?? null}, expires_at)
    WHERE id = ${id}
    RETURNING *
  `;
  return row;
}
