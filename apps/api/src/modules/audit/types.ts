export interface AuditEventRow {
  id: string;
  firm_id: string;
  actor_id: string;
  actor_type: 'user' | 'service' | 'impersonator';
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  metadata: Record<string, unknown>;
  ip_address: string | null;
  user_agent: string | null;
  correlation_id: string | null;
  created_at: Date;
}

export interface AuditEventDto {
  id: string;
  firmId: string;
  actorId: string;
  actorType: 'user' | 'service' | 'impersonator';
  action: string;
  resourceType: string | null;
  resourceId: string | null;
  metadata: Record<string, unknown>;
  ipAddress: string | null;
  userAgent: string | null;
  correlationId: string | null;
  createdAt: string;
}

export function toAuditEventDto(row: AuditEventRow): AuditEventDto {
  return {
    id: row.id,
    firmId: row.firm_id,
    actorId: row.actor_id,
    actorType: row.actor_type,
    action: row.action,
    resourceType: row.resource_type,
    resourceId: row.resource_id,
    metadata: row.metadata,
    ipAddress: row.ip_address,
    userAgent: row.user_agent,
    correlationId: row.correlation_id,
    createdAt: row.created_at.toISOString(),
  };
}

export interface AuditQueryFilters {
  firmId: string;
  actorId?: string;
  action?: string;
  resourceType?: string;
  resourceId?: string;
  from?: string;
  to?: string;
  cursor?: string;
  limit: number;
}

export interface PaginatedAuditResult {
  events: AuditEventRow[];
  nextCursor: string | undefined;
  hasMore: boolean;
}
