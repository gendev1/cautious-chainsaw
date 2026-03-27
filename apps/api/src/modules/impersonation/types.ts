export interface ImpersonationSessionRow {
  id: string;
  impersonator_user_id: string;
  target_user_id: string;
  firm_id: string;
  reason: string;
  idempotency_key: string;
  started_at: Date;
  expires_at: Date;
  ended_at: Date | null;
}

export interface ImpersonationSessionDto {
  id: string;
  impersonatorUserId: string;
  targetUserId: string;
  firmId: string;
  reason: string;
  idempotencyKey: string;
  startedAt: string;
  expiresAt: string;
  endedAt: string | null;
}

export function toImpersonationSessionDto(
  row: ImpersonationSessionRow,
): ImpersonationSessionDto {
  return {
    id: row.id,
    impersonatorUserId: row.impersonator_user_id,
    targetUserId: row.target_user_id,
    firmId: row.firm_id,
    reason: row.reason,
    idempotencyKey: row.idempotency_key,
    startedAt: row.started_at.toISOString(),
    expiresAt: row.expires_at.toISOString(),
    endedAt: row.ended_at?.toISOString() ?? null,
  };
}
