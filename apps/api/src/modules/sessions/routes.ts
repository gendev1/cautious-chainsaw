import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import * as service from './service.js';
import { revokeSessionParamsSchema, adminRevokeAllSchema } from './schemas.js';
import { success } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext } from '../../shared/types.js';

export const sessionRoutes = new Hono<AppEnv>();

// GET / — list current user's active sessions
sessionRoutes.get('/', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const sessions = await service.listSessions(actor);
  return success(c, sessions);
});

// DELETE /:id — revoke a specific session
sessionRoutes.delete('/:id', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const parsed = revokeSessionParamsSchema.safeParse({ id: c.req.param('id') });
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid session ID', details);
  }

  await service.revokeSession(parsed.data.id, actor);
  return success(c, { message: 'Session revoked' });
});

// POST /revoke-all — revoke all sessions except the current one
sessionRoutes.post('/revoke-all', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const revokedCount = await service.revokeAllOtherSessions(actor);
  return success(c, { revokedCount });
});

// POST /admin/revoke-all — admin force-revoke all sessions for a target user
sessionRoutes.post(
  '/admin/revoke-all',
  requirePermission('user.manage_roles'),
  async (c) => {
    const actor = c.get('actor') as ActorContext;
    const body = await c.req.json();
    const parsed = adminRevokeAllSchema.safeParse(body);
    if (!parsed.success) {
      const details: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        details[issue.path.join('.')] = issue.message;
      }
      throw new ValidationError('Invalid request body', details);
    }

    const revokedCount = await service.adminForceRevokeAllSessions(parsed.data.userId, actor);
    return success(c, { revokedCount });
  },
);
