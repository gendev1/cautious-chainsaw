import type { FirmStatus } from '../../shared/types.js';

export interface FirmRow {
  id: string;
  name: string;
  slug: string;
  status: FirmStatus;
  branding: Record<string, unknown>;
  created_at: Date;
  updated_at: Date;
}

export interface FirmDto {
  id: string;
  name: string;
  slug: string;
  status: FirmStatus;
  branding: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export function toFirmDto(row: FirmRow): FirmDto {
  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    status: row.status,
    branding: row.branding,
    createdAt: row.created_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
  };
}
