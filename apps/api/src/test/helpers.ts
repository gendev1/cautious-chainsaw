import { vi } from 'vitest';
import type { ActorContext, TenantContext } from '../shared/types.js';

// ---- Mock actor contexts ----

export function mockFirmAdmin(overrides: Partial<ActorContext> = {}): ActorContext {
  return {
    userId: 'user-admin-001',
    tenantId: 'tenant-001',
    actorType: 'user',
    sessionId: 'session-001',
    roles: ['firm_admin'],
    permissions: [
      'client.read', 'client.write', 'account.open', 'account.read', 'account.write',
      'transfer.submit', 'transfer.cancel', 'order.submit', 'order.cancel',
      'billing.read', 'billing.post', 'billing.reverse', 'report.read', 'report.publish',
      'document.read', 'document.read_sensitive', 'document.write',
      'user.read', 'user.invite', 'user.manage_roles', 'firm.read', 'firm.update',
      'support.impersonate', 'mfa.manage',
    ],
    mfa: true,
    ...overrides,
  };
}

export function mockAdvisor(overrides: Partial<ActorContext> = {}): ActorContext {
  return {
    userId: 'user-advisor-001',
    tenantId: 'tenant-001',
    actorType: 'user',
    sessionId: 'session-002',
    roles: ['advisor'],
    permissions: [
      'client.read', 'client.write', 'account.open', 'account.read', 'account.write',
      'transfer.submit', 'order.submit', 'billing.read', 'report.read',
      'document.read', 'document.write', 'user.read', 'firm.read',
    ],
    mfa: false,
    ...overrides,
  };
}

export function mockViewer(overrides: Partial<ActorContext> = {}): ActorContext {
  return {
    userId: 'user-viewer-001',
    tenantId: 'tenant-001',
    actorType: 'user',
    sessionId: 'session-003',
    roles: ['viewer'],
    permissions: ['client.read', 'account.read', 'billing.read', 'report.read', 'document.read'],
    mfa: false,
    ...overrides,
  };
}

export function mockTenant(overrides: Partial<TenantContext> = {}): TenantContext {
  return {
    tenantId: 'tenant-001',
    firmSlug: 'acme',
    firmName: 'Acme Financial',
    firmStatus: 'active',
    ...overrides,
  };
}

// ---- Mock DB helper ----

export function createMockSql() {
  const sql = vi.fn().mockResolvedValue([]);
  (sql as any).begin = vi.fn().mockImplementation(async (fn: any) => fn(sql));
  return sql;
}

// ---- Mock Redis helper ----

export function createMockRedis() {
  const store = new Map<string, string>();
  return {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    set: vi.fn(async (key: string, value: string, ...args: any[]) => {
      store.set(key, value);
      return 'OK';
    }),
    del: vi.fn(async (...keys: string[]) => {
      keys.forEach((k) => store.delete(k));
      return keys.length;
    }),
    pipeline: vi.fn(() => ({
      zremrangebyscore: vi.fn().mockReturnThis(),
      zadd: vi.fn().mockReturnThis(),
      zcard: vi.fn().mockReturnThis(),
      expire: vi.fn().mockReturnThis(),
      exec: vi.fn().mockResolvedValue([[null, 0], [null, 1], [null, 1], [null, 1]]),
    })),
    _store: store,
  };
}

// ---- UUID helper ----

export function uuid(): string {
  return crypto.randomUUID();
}
