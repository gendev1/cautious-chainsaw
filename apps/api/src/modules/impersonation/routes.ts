import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { startImpersonationSchema, listImpersonationSessionsSchema } from './schemas.js';
import * as service from './service.js';
import { success, paginated } from '../../shared/response.js';
import { ForbiddenError, ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext } from '../../shared/types.js';

export const impersonationRoutes = new Hono<AppEnv>();

// POST / -- start impersonation (requires support.impersonate)
impersonationRoutes.post('/', requirePermission('support.impersonate'), async (c) => {
  const actor = c.get('actor') as ActorContext;

  // Impersonation tokens cannot start another impersonation
  if (actor.actorType === 'impersonator') {
    throw new ForbiddenError('Cannot start impersonation while already impersonating');
  }

  const body = await c.req.json();
  const parsed = startImpersonationSchema.safeParse(body);
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid impersonation request', details);
  }

  const result = await service.startImpersonation(parsed.data, actor, {
    correlationId: c.get('correlationId'),
    ipAddress: c.req.header('x-forwarded-for') ?? c.req.header('x-real-ip'),
    userAgent: c.req.header('user-agent'),
  });

  return success(c, result, 201);
});

// POST /:id/end -- end impersonation
impersonationRoutes.post('/:id/end', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const sessionId = c.req.param('id');

  const session = await service.endImpersonation(sessionId, actor, {
    correlationId: c.get('correlationId'),
    ipAddress: c.req.header('x-forwarded-for') ?? c.req.header('x-real-ip'),
    userAgent: c.req.header('user-agent'),
  });

  return success(c, session);
});

// GET / -- list impersonation sessions
impersonationRoutes.get('/', requirePermission('support.impersonate'), async (c) => {
  const actor = c.get('actor') as ActorContext;

  // Impersonation tokens cannot list sessions (guards against privilege escalation)
  if (actor.actorType === 'impersonator') {
    throw new ForbiddenError('Impersonation tokens cannot manage impersonation sessions');
  }

  const query = c.req.query();
  const parsed = listImpersonationSessionsSchema.safeParse({
    impersonator_user_id: query.impersonator_user_id,
    target_user_id: query.target_user_id,
    started_after: query.started_after,
    started_before: query.started_before,
    cursor: query.cursor,
    limit: query.limit,
  });
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid query parameters', details);
  }

  const { sessions, hasMore, nextCursor, total } =
    await service.listImpersonationSessions(parsed.data, actor);

  return paginated(c, sessions, { hasMore, nextCursor, totalCount: total });
});
