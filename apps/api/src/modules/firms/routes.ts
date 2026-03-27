import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { createFirmSchema, updateFirmSchema } from './schemas.js';
import * as service from './service.js';
import { success, error } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import { requirePermission } from '../../http/middleware/permission.js';
import type { ActorContext, TenantContext } from '../../shared/types.js';

export const firmRoutes = new Hono<AppEnv>();

// POST / — create a new firm
firmRoutes.post('/', requirePermission('firm.update'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = createFirmSchema.safeParse(body);
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid firm data', details);
  }

  const firm = await service.createFirm(parsed.data, actor);
  return success(c, firm, 201);
});

// GET /current — get the current tenant's firm
firmRoutes.get('/current', requirePermission('firm.read'), async (c) => {
  const tenant = c.get('tenant') as TenantContext;
  const firm = await service.getCurrentFirm(tenant.tenantId);
  return success(c, firm);
});

// PATCH /current — update the current tenant's firm
firmRoutes.patch('/current', requirePermission('firm.update'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const tenant = c.get('tenant') as TenantContext;
  const body = await c.req.json();
  const parsed = updateFirmSchema.safeParse(body);
  if (!parsed.success) {
    const details: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid firm update data', details);
  }

  const firm = await service.updateFirm(tenant.tenantId, parsed.data, actor);
  return success(c, firm);
});

// POST /current/suspend — suspend the current tenant's firm
firmRoutes.post('/current/suspend', requirePermission('firm.update'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const tenant = c.get('tenant') as TenantContext;
  const firm = await service.suspendFirm(tenant.tenantId, actor);
  return success(c, firm);
});

// POST /current/activate — activate the current tenant's firm
firmRoutes.post('/current/activate', requirePermission('firm.update'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const tenant = c.get('tenant') as TenantContext;
  const firm = await service.activateFirm(tenant.tenantId, actor);
  return success(c, firm);
});
