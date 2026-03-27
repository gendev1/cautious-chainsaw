import { getDb } from '../../db/client.js';
import type { ServiceAccountRow } from './types.js';

export async function create(input: {
  name: string;
  firmId: string;
  keyHash: string;
  permissions: string[];
}): Promise<ServiceAccountRow> {
  const sql = getDb();
  const [row] = await sql<ServiceAccountRow[]>`
    INSERT INTO service_accounts (name, firm_id, key_hash, permissions)
    VALUES (
      ${input.name},
      ${input.firmId},
      ${input.keyHash},
      ${JSON.stringify(input.permissions)}::jsonb
    )
    RETURNING *
  `;
  return row;
}

export async function findById(id: string, firmId: string): Promise<ServiceAccountRow | undefined> {
  const sql = getDb();
  const [row] = await sql<ServiceAccountRow[]>`
    SELECT * FROM service_accounts
    WHERE id = ${id} AND firm_id = ${firmId}
  `;
  return row;
}

export async function findByKeyHash(keyHash: string): Promise<ServiceAccountRow | undefined> {
  const sql = getDb();
  const [row] = await sql<ServiceAccountRow[]>`
    SELECT * FROM service_accounts
    WHERE status = 'active'
      AND (
        key_hash = ${keyHash}
        OR (
          previous_key_hash = ${keyHash}
          AND key_grace_expires_at > now()
        )
      )
  `;
  return row;
}

export async function list(firmId: string): Promise<ServiceAccountRow[]> {
  const sql = getDb();
  return sql<ServiceAccountRow[]>`
    SELECT * FROM service_accounts
    WHERE firm_id = ${firmId}
    ORDER BY created_at DESC
  `;
}

export async function rotateKey(
  id: string,
  firmId: string,
  newKeyHash: string,
  graceExpiresAt: Date,
): Promise<ServiceAccountRow> {
  const sql = getDb();
  const [row] = await sql<ServiceAccountRow[]>`
    UPDATE service_accounts SET
      previous_key_hash = key_hash,
      key_hash = ${newKeyHash},
      key_grace_expires_at = ${graceExpiresAt.toISOString()},
      rotated_at = now()
    WHERE id = ${id} AND firm_id = ${firmId}
    RETURNING *
  `;
  return row;
}

export async function revoke(id: string, firmId: string): Promise<ServiceAccountRow> {
  const sql = getDb();
  const [row] = await sql<ServiceAccountRow[]>`
    UPDATE service_accounts SET
      status = 'revoked',
      previous_key_hash = NULL,
      key_grace_expires_at = NULL
    WHERE id = ${id} AND firm_id = ${firmId}
    RETURNING *
  `;
  return row;
}
