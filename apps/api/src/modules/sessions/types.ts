export interface SessionRow {
  id: string;
  user_id: string;
  firm_id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: Date;
  last_active_at: Date;
  revoked_at: Date | null;
}

export interface SessionDto {
  id: string;
  userId: string;
  firmId: string;
  ipAddress: string | null;
  userAgent: string | null;
  createdAt: string;
  lastActiveAt: string;
  current: boolean;
}

export function toSessionDto(row: SessionRow, currentSessionId: string): SessionDto {
  return {
    id: row.id,
    userId: row.user_id,
    firmId: row.firm_id,
    ipAddress: row.ip_address,
    userAgent: row.user_agent,
    createdAt: row.created_at.toISOString(),
    lastActiveAt: row.last_active_at.toISOString(),
    current: row.id === currentSessionId,
  };
}
