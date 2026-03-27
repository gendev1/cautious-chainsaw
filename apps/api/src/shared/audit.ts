import { getDb } from '../db/client.js';
import type { ActorContext } from './types.js';

export interface AuditEventInput {
  firmId: string;
  actorId: string;
  actorType: 'user' | 'service' | 'impersonator';
  action: string;
  resourceType?: string;
  resourceId?: string;
  metadata?: Record<string, unknown>;
  ipAddress?: string;
  userAgent?: string;
  correlationId?: string;
}

export async function emitAuditEvent(input: AuditEventInput): Promise<void> {
  const sql = getDb();
  await sql`
    INSERT INTO audit_events (firm_id, actor_id, actor_type, action, resource_type, resource_id, metadata, ip_address, user_agent, correlation_id)
    VALUES (
      ${input.firmId},
      ${input.actorId},
      ${input.actorType},
      ${input.action},
      ${input.resourceType ?? null},
      ${input.resourceId ?? null},
      ${JSON.stringify(input.metadata ?? {})},
      ${input.ipAddress ?? null},
      ${input.userAgent ?? null},
      ${input.correlationId ?? null}
    )
  `;
}

export function auditFromContext(
  actor: ActorContext,
  action: string,
  opts: {
    resourceType?: string;
    resourceId?: string;
    metadata?: Record<string, unknown>;
    ipAddress?: string;
    userAgent?: string;
    correlationId?: string;
  } = {},
): Promise<void> {
  return emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action,
    ...opts,
    metadata: {
      ...opts.metadata,
      ...(actor.impersonatorId ? { impersonatorId: actor.impersonatorId } : {}),
    },
  });
}
