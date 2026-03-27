import { Hono } from 'hono';
import { requirePermission } from '../../http/middleware/permission.js';
import { paginated, success } from '../../shared/response.js';
import { ValidationError } from '../../shared/errors.js';
import type { AppEnv } from '../../shared/hono-env.js';
import type { ActorContext } from '../../shared/types.js';
import { createHouseholdSchema, listHouseholdsQuerySchema, updateHouseholdSchema } from './schemas.js';
import * as service from './service.js';

export const householdRoutes = new Hono<AppEnv>();

function toFieldErrors(issues: { path: (string | number)[]; message: string }[]): Record<string, string> {
  const details: Record<string, string> = {};
  for (const issue of issues) {
    details[issue.path.join('.')] = issue.message;
  }
  return details;
}

householdRoutes.post('/', requirePermission('client.write'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = createHouseholdSchema.safeParse(body);
  if (!parsed.success) {
    throw new ValidationError('Invalid household data', toFieldErrors(parsed.error.issues));
  }

  const household = await service.createHousehold(actor, parsed.data);
  return success(c, household, 201);
});

householdRoutes.get('/', requirePermission('client.read'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const parsed = listHouseholdsQuerySchema.safeParse({
    limit: c.req.query('limit'),
    offset: c.req.query('offset'),
    status: c.req.query('status'),
    primaryAdvisorId: c.req.query('primary_advisor_id'),
  });
  if (!parsed.success) {
    throw new ValidationError('Invalid household query', toFieldErrors(parsed.error.issues));
  }

  const { households, pagination } = await service.listHouseholds(actor.tenantId, parsed.data);
  return paginated(c, households, pagination);
});

householdRoutes.get('/:id', requirePermission('client.read'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const household = await service.getHousehold(actor.tenantId, c.req.param('id'));
  return success(c, household);
});

householdRoutes.patch('/:id', requirePermission('client.write'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = updateHouseholdSchema.safeParse(body);
  if (!parsed.success) {
    throw new ValidationError('Invalid household update', toFieldErrors(parsed.error.issues));
  }

  const household = await service.updateHousehold(actor, c.req.param('id'), parsed.data);
  return success(c, household);
});

householdRoutes.post('/:id/deactivate', requirePermission('client.write'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const household = await service.deactivateHousehold(actor, c.req.param('id'));
  return success(c, household);
});

householdRoutes.post('/:id/reactivate', requirePermission('client.write'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const household = await service.reactivateHousehold(actor, c.req.param('id'));
  return success(c, household);
});

householdRoutes.post('/:id/close', requirePermission('client.write'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const household = await service.closeHousehold(actor, c.req.param('id'));
  return success(c, household);
});
