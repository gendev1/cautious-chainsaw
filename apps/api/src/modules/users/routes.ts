import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { requirePermission } from '../../http/middleware/permission.js';
import { success, paginated, error } from '../../shared/response.js';
import { AppError, ValidationError } from '../../shared/errors.js';
import type { ActorContext } from '../../shared/types.js';
import {
  inviteUserSchema,
  registerSchema,
  updateUserSchema,
  listUsersQuerySchema,
} from './schemas.js';
import * as service from './service.js';

const userRoutes = new Hono<AppEnv>();

// ---------------------------------------------------------------------------
// POST /invitations — invite a new user (requires user.invite permission)
// ---------------------------------------------------------------------------
userRoutes.post('/invitations', requirePermission('user.invite'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = inviteUserSchema.safeParse(body);
  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      fieldErrors[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid invitation input', fieldErrors);
  }

  const { invitation, token } = await service.inviteUser(actor, parsed.data);
  return success(c, { invitation, token: token || undefined }, 201);
});

// ---------------------------------------------------------------------------
// POST /invitations/:id/resend — resend a pending invitation
// ---------------------------------------------------------------------------
userRoutes.post('/invitations/:id/resend', requirePermission('user.invite'), async (c) => {
  const actor = c.get('actor') as ActorContext;
  const invitationId = c.req.param('id');

  const { invitation, token } = await service.resendInvitation(actor, invitationId);
  return success(c, { invitation, token });
});

// ---------------------------------------------------------------------------
// POST /register — public: register via invitation token
// ---------------------------------------------------------------------------
userRoutes.post('/register', async (c) => {
  const body = await c.req.json();
  const parsed = registerSchema.safeParse(body);
  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      fieldErrors[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid registration input', fieldErrors);
  }

  const user = await service.register(parsed.data);
  return success(c, { user }, 201);
});

// ---------------------------------------------------------------------------
// GET / — list users in the tenant (paginated)
// ---------------------------------------------------------------------------
userRoutes.get('/', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const rawQuery = {
    cursor: c.req.query('cursor'),
    limit: c.req.query('limit'),
    status: c.req.query('status'),
    search: c.req.query('search'),
  };

  const parsed = listUsersQuerySchema.safeParse(rawQuery);
  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      fieldErrors[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid query parameters', fieldErrors);
  }

  const { users, pagination } = await service.listUsers(actor.tenantId, parsed.data);
  return paginated(c, users, pagination);
});

// ---------------------------------------------------------------------------
// GET /:id — get a single user
// ---------------------------------------------------------------------------
userRoutes.get('/:id', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const userId = c.req.param('id');
  const user = await service.getUser(actor.tenantId, userId);
  return success(c, { user });
});

// ---------------------------------------------------------------------------
// PATCH /:id — update display name / status
// ---------------------------------------------------------------------------
userRoutes.patch('/:id', async (c) => {
  const actor = c.get('actor') as ActorContext;
  const userId = c.req.param('id');
  const body = await c.req.json();

  const parsed = updateUserSchema.safeParse(body);
  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      fieldErrors[issue.path.join('.')] = issue.message;
    }
    throw new ValidationError('Invalid update input', fieldErrors);
  }

  const user = await service.updateUser(actor, userId, parsed.data);
  return success(c, { user });
});

export { userRoutes };
