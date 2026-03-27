import { Hono } from 'hono';
import type { AppEnv } from './shared/hono-env.js';
import { requestIdMiddleware } from './http/middleware/request-id.js';
import { tenantMiddleware } from './http/middleware/tenant.js';
import { authMiddleware } from './http/middleware/auth.js';
import { roleResolverMiddleware } from './http/middleware/role-resolver.js';
import { rateLimitMiddleware } from './http/middleware/rate-limit.js';
import { errorHandler } from './http/errors/handler.js';

// Module routes
import { firmRoutes } from './modules/firms/routes.js';
import { userRoutes } from './modules/users/routes.js';
import { authRoutes } from './modules/auth/routes.js';
import { roleRoutes } from './modules/roles/routes.js';
import { sessionRoutes } from './modules/sessions/routes.js';
import { serviceAccountRoutes } from './modules/service-accounts/routes.js';
import { impersonationRoutes } from './modules/impersonation/routes.js';
import { auditRoutes } from './modules/audit/routes.js';
import { householdRoutes } from './modules/households/routes.js';

export function createApp() {
  const app = new Hono<AppEnv>();

  // Global error handler
  app.onError(errorHandler);

  // Health check (no middleware)
  app.get('/health', (c) => c.json({ status: 'ok', timestamp: new Date().toISOString() }));

  // ---- Middleware Pipeline ----
  // Position 1: Request ID
  app.use('*', requestIdMiddleware);

  // ---- Public routes (no tenant/auth) ----
  app.route('/api/auth', authRoutes);

  // Public user registration (invitation-token-based, no auth needed but needs tenant)
  // Only the /register endpoint is public — invitations/list/update require auth
  app.post('/api/users/register', tenantMiddleware, async (c) => {
    // Delegate to users module register handler
    const { register } = await import('./modules/users/service.js');
    const { registerSchema } = await import('./modules/users/schemas.js');
    const body = await c.req.json();
    const parsed = registerSchema.parse(body);
    const user = await register(parsed);
    return c.json({ success: true, data: { user }, requestId: c.get('requestId') }, 201);
  });

  // Firm provisioning (platform bootstrap — no tenant context needed)
  app.post('/api/firms/bootstrap', requestIdMiddleware, async (c) => {
    const { createFirmSchema } = await import('./modules/firms/schemas.js');
    const { createFirm: createFirmDirect } = await import('./modules/firms/service.js');
    const body = await c.req.json();
    const parsed = createFirmSchema.parse(body);
    const bootstrapActor = {
      userId: 'system', tenantId: 'system', actorType: 'service' as const,
      sessionId: '', roles: [], permissions: [], mfa: false,
    };
    const firm = await createFirmDirect(parsed, bootstrapActor);
    return c.json({ success: true, data: firm, requestId: c.get('requestId') }, 201);
  });

  // ---- Protected routes: tenant + auth + role + rate limit ----
  app.use('/api/*', tenantMiddleware);
  app.use('/api/*', authMiddleware);
  app.use('/api/*', roleResolverMiddleware);
  app.use('/api/*', rateLimitMiddleware);

  // Domain routes (permission enforcement per-route via requirePermission())
  app.route('/api/firms', firmRoutes);
  app.route('/api/users', userRoutes);
  app.route('/api/roles', roleRoutes);
  app.route('/api/auth/sessions', sessionRoutes);
  app.route('/api/admin/service-accounts', serviceAccountRoutes);
  app.route('/api/support/impersonation', impersonationRoutes);
  app.route('/api/audit', auditRoutes);
  app.route('/api/households', householdRoutes);

  return app;
}
