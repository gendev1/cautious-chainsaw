import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { createServiceAccountSchema } from './schemas.js';
import * as service from './service.js';
import { success } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext } from '../../shared/types.js';

export const serviceAccountRoutes = new Hono<AppEnv>();

// POST / — create a new service account
serviceAccountRoutes.post('/', requirePermission('service_account:create'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = createServiceAccountSchema.safeParse(body);
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid service account data', details);
  }

  const result = await service.createServiceAccount(parsed.data, actor);
  return success(
    c,
    {
      ...result.account,
      apiKey: result.apiKey,
    },
    201,
  );
});

// GET / — list service accounts for the current firm
serviceAccountRoutes.get('/', requirePermission('service_account:read'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const accounts = await service.listServiceAccounts(actor);
  return success(c, accounts);
});

// POST /:id/rotate-key — rotate the API key
serviceAccountRoutes.post('/:id/rotate-key', requirePermission('service_account:rotate_key'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const id = c.req.param('id');

  const result = await service.rotateKey(id, actor);
  return success(c, {
    ...result.account,
    apiKey: result.apiKey,
    graceExpiresAt: result.graceExpiresAt,
  });
});

// DELETE /:id — revoke a service account
serviceAccountRoutes.delete('/:id', requirePermission('service_account:revoke'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const id = c.req.param('id');

  const account = await service.revokeServiceAccount(id, actor);
  return success(c, account);
});
