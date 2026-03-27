import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { success } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext } from '../../shared/types.js';
import { assignRolesSchema } from './schemas.js';
import * as service from './service.js';

export const roleRoutes = new Hono<AppEnv>();

// GET / — list all system roles
roleRoutes.get('/', async (c) => {
  const roles = await service.listAllRoles();
  return success(c, roles);
});

// GET /permissions — list all permissions
roleRoutes.get('/permissions', async (c) => {
  const permissions = await service.listAllPermissions();
  return success(c, permissions);
});

// GET /users/:id — get user's active role assignments
roleRoutes.get('/users/:id', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const userId = c.req.param('id');
  const assignments = await service.getUserRoles(userId, actor.tenantId);
  return success(c, assignments);
});

// PUT /users/:id — assign roles to a user (requires user.manage_roles)
roleRoutes.put('/users/:id', requirePermission('user.manage_roles'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const userId = c.req.param('id');

  const body = await c.req.json();
  const parsed = assignRolesSchema.safeParse(body);
  if (!parsed.success) {
    const fieldErrors = parsed.error.flatten().fieldErrors;
    const details: Record<string, string> = {};
    for (const [key, msgs] of Object.entries(fieldErrors)) {
      if (msgs && msgs.length > 0) {
        details[key] = msgs[0];
      }
    }
    throw new ValidationError('Invalid role assignment input', details);
  }

  const assignments = await service.assignRolesToUser(
    userId,
    parsed.data.roles,
    actor,
    {
      correlationId: c.get('correlationId'),
      ipAddress: c.req.header('x-forwarded-for') ?? c.req.header('x-real-ip'),
      userAgent: c.req.header('user-agent'),
    },
  );

  return success(c, assignments);
});
