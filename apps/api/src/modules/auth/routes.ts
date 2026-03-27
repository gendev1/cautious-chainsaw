import { Hono } from 'hono';
import type { AppEnv } from '../../shared/hono-env.js';
import { loginRateLimitMiddleware } from '../../http/middleware/rate-limit.js';
import { authMiddleware } from '../../http/middleware/auth.js';
import { tenantMiddleware } from '../../http/middleware/tenant.js';
import { success } from '../../shared/response.js';
import { emitAuditEvent } from '../../shared/audit.js';
import { UnauthorizedError, ValidationError } from '../../shared/errors.js';
import {
  loginSchema,
  refreshSchema,
  logoutSchema,
  mfaVerifySchema,
  mfaChallengeSchema,
  mfaRecoverSchema,
} from './schemas.js';
import * as authService from './service.js';
import { findUserById } from './repository.js';
import type { ActorContext } from '../../shared/types.js';

export const authRoutes = new Hono<AppEnv>();

// POST /login — requires tenant context (subdomain) but NOT auth
authRoutes.post('/login', tenantMiddleware, loginRateLimitMiddleware, async (c) => {
  const body = await c.req.json();
  const parsed = loginSchema.safeParse(body);
  if (!parsed.success) {
    throw new ValidationError('Invalid login input');
  }

  const tenantId = c.get('tenantId');
  const ip = c.req.header('x-forwarded-for')?.split(',')[0]?.trim() ?? null;
  const ua = c.req.header('user-agent') ?? null;

  const result = await authService.login(parsed.data.email, parsed.data.password, tenantId, ip, ua);

  const firmId = 'firmId' in result ? result.firmId : tenantId;
  const actorId = 'userId' in result ? result.userId : parsed.data.email;
  emitAuditEvent({
    firmId,
    actorId,
    actorType: 'user',
    action: 'mfaRequired' in result ? 'auth.login_mfa_required' : 'auth.login_success',
    ipAddress: ip ?? undefined,
    userAgent: ua ?? undefined,
  }).catch(() => {});

  return success(c, result);
});

// POST /refresh
authRoutes.post('/refresh', async (c) => {
  const body = await c.req.json();
  const parsed = refreshSchema.safeParse(body);
  if (!parsed.success) throw new ValidationError('Invalid refresh input');

  const result = await authService.refresh(parsed.data.refreshToken);

  // Audit uses the firmId from the refreshed token's session
  emitAuditEvent({
    firmId: result.firmId ?? 'unknown',
    actorId: result.userId ?? 'unknown',
    actorType: 'user',
    action: 'auth.token_refresh',
    ipAddress: c.req.header('x-forwarded-for')?.split(',')[0]?.trim() ?? undefined,
    userAgent: c.req.header('user-agent') ?? undefined,
  }).catch(() => {});

  return success(c, result);
});

// POST /logout
authRoutes.post('/logout', async (c) => {
  const body = await c.req.json();
  const parsed = logoutSchema.safeParse(body);
  if (!parsed.success) throw new ValidationError('Invalid logout input');

  const result = await authService.logout(parsed.data.refreshToken);

  if (result) {
    emitAuditEvent({
      firmId: result.firmId,
      actorId: result.userId,
      actorType: 'user',
      action: 'auth.logout',
      ipAddress: c.req.header('x-forwarded-for')?.split(',')[0]?.trim() ?? undefined,
      userAgent: c.req.header('user-agent') ?? undefined,
    }).catch(() => {});
  }

  return success(c, { message: 'Logged out' });
});

// POST /mfa/enroll (requires auth)
authRoutes.post('/mfa/enroll', tenantMiddleware, authMiddleware, async (c) => {
  const actor = c.get('actor') as ActorContext;
  const user = await findUserById(actor.userId);
  if (!user) throw new UnauthorizedError('User not found');

  const result = await authService.mfaEnroll(actor.userId, user.email);

  emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'auth.mfa_enrolled',
    resourceType: 'mfa_factor',
    resourceId: result.factorId,
  }).catch(() => {});

  return success(c, result, 201);
});

// POST /mfa/verify (requires auth)
authRoutes.post('/mfa/verify', tenantMiddleware, authMiddleware, async (c) => {
  const actor = c.get('actor') as ActorContext;
  const body = await c.req.json();
  const parsed = mfaVerifySchema.safeParse(body);
  if (!parsed.success) throw new ValidationError('Invalid MFA verify input');

  await authService.mfaVerify(actor.userId, parsed.data.code);

  emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'auth.mfa_challenge_success',
  }).catch(() => {});

  return success(c, { message: 'MFA factor verified' });
});

// POST /mfa/challenge (public — partial session, needs tenant for context)
authRoutes.post('/mfa/challenge', tenantMiddleware, async (c) => {
  const body = await c.req.json();
  const parsed = mfaChallengeSchema.safeParse(body);
  if (!parsed.success) throw new ValidationError('Invalid MFA challenge input');

  const result = await authService.mfaChallenge(parsed.data.sessionId, parsed.data.code);

  emitAuditEvent({
    firmId: result.firmId ?? c.get('tenantId'),
    actorId: result.userId ?? 'unknown',
    actorType: 'user',
    action: 'auth.mfa_challenge_success',
    metadata: { sessionId: parsed.data.sessionId },
  }).catch(() => {});

  return success(c, result);
});

// POST /mfa/recover (public — partial session, needs tenant)
authRoutes.post('/mfa/recover', tenantMiddleware, async (c) => {
  const body = await c.req.json();
  const parsed = mfaRecoverSchema.safeParse(body);
  if (!parsed.success) throw new ValidationError('Invalid MFA recover input');

  const result = await authService.mfaRecover(parsed.data.sessionId, parsed.data.recoveryCode);

  emitAuditEvent({
    firmId: result.firmId ?? c.get('tenantId'),
    actorId: result.userId ?? 'unknown',
    actorType: 'user',
    action: 'auth.mfa_recovery_used',
    metadata: { sessionId: parsed.data.sessionId },
  }).catch(() => {});

  return success(c, result);
});
