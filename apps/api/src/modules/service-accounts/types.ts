export type ServiceAccountStatus = 'active' | 'revoked';

export interface ServiceAccountRow {
  id: string;
  name: string;
  firm_id: string;
  key_hash: string;
  previous_key_hash: string | null;
  key_grace_expires_at: Date | null;
  permissions: string[];
  status: ServiceAccountStatus;
  created_at: Date;
  rotated_at: Date | null;
}

export interface ServiceAccountDto {
  id: string;
  name: string;
  firmId: string;
  permissions: string[];
  status: ServiceAccountStatus;
  createdAt: string;
  rotatedAt: string | null;
}

export function toServiceAccountDto(row: ServiceAccountRow): ServiceAccountDto {
  return {
    id: row.id,
    name: row.name,
    firmId: row.firm_id,
    permissions: row.permissions,
    status: row.status,
    createdAt: row.created_at.toISOString(),
    rotatedAt: row.rotated_at?.toISOString() ?? null,
  };
}
