import type { UserStatus, InvitationStatus } from '../../shared/types.js';

export interface UserRow {
  id: string;
  firm_id: string;
  email: string;
  password_hash: string | null;
  display_name: string;
  status: UserStatus;
  created_at: Date;
  updated_at: Date;
}

export interface UserDto {
  id: string;
  firmId: string;
  email: string;
  displayName: string;
  status: UserStatus;
  createdAt: string;
  updatedAt: string;
}

export interface InvitationRow {
  id: string;
  firm_id: string;
  email: string;
  role: string;
  display_name: string | null;
  invited_by: string;
  token_hash: string;
  status: InvitationStatus;
  expires_at: Date;
  accepted_at: Date | null;
  created_at: Date;
}

export interface InvitationDto {
  id: string;
  firmId: string;
  email: string;
  role: string;
  displayName: string | null;
  invitedBy: string;
  status: InvitationStatus;
  expiresAt: string;
  acceptedAt: string | null;
  createdAt: string;
}

export function toUserDto(row: UserRow): UserDto {
  return {
    id: row.id,
    firmId: row.firm_id,
    email: row.email,
    displayName: row.display_name,
    status: row.status,
    createdAt: row.created_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
  };
}

export function toInvitationDto(row: InvitationRow): InvitationDto {
  return {
    id: row.id,
    firmId: row.firm_id,
    email: row.email,
    role: row.role,
    displayName: row.display_name,
    invitedBy: row.invited_by,
    status: row.status,
    expiresAt: row.expires_at.toISOString(),
    acceptedAt: row.accepted_at?.toISOString() ?? null,
    createdAt: row.created_at.toISOString(),
  };
}
