import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { auditQuerySchema } from './schemas.js';
import * as service from './service.js';
import { paginated } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext, TenantContext } from '../../shared/types.js';

export const auditRoutes = new Hono<AppEnv>();

// GET /events — list/filter audit events (firm_admin only)
auditRoutes.get('/events', requirePermission('audit:read'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const tenant = c.get('tenant') as TenantContext;

  const raw = {
    actor_id: c.req.query('actor_id'),
    action: c.req.query('action'),
    resource_type: c.req.query('resource_type'),
    resource_id: c.req.query('resource_id'),
    from: c.req.query('from'),
    to: c.req.query('to'),
    cursor: c.req.query('cursor'),
    limit: c.req.query('limit'),
  };

  // Strip undefined keys so zod defaults work
  const cleaned = Object.fromEntries(
    Object.entries(raw).filter(([_, v]) => v !== undefined),
  );

  const parsed = auditQuerySchema.safeParse(cleaned);
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid audit query parameters', details);
  }

  const result = await service.queryAuditEvents({
    firmId: tenant.tenantId,
    actorId: parsed.data.actor_id,
    action: parsed.data.action,
    resourceType: parsed.data.resource_type,
    resourceId: parsed.data.resource_id,
    from: parsed.data.from,
    to: parsed.data.to,
    cursor: parsed.data.cursor,
    limit: parsed.data.limit,
  });

  return paginated(c, result.events, {
    nextCursor: result.nextCursor,
    hasMore: result.hasMore,
  });
});
