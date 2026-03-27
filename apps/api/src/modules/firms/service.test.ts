import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { FirmRow } from './types.js';
import { createFirmSchema } from './schemas.js';
import { mockFirmAdmin, createMockRedis } from '../../test/helpers.js';
import { ConflictError, NotFoundError, WorkflowStateError } from '../../shared/errors.js';

// ---- Mocks ----

const mockRedis = createMockRedis();

vi.mock('../../db/redis.js', () => ({
  getRedis: () => mockRedis,
}));

vi.mock('../../shared/audit.js', () => ({
  emitAuditEvent: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('./repository.js', () => ({
  create: vi.fn(),
  findById: vi.fn(),
  findBySlug: vi.fn(),
  update: vi.fn(),
  updateStatus: vi.fn(),
}));

// Import after mocks are declared
import * as repo from './repository.js';
import { emitAuditEvent } from '../../shared/audit.js';
import {
  createFirm,
  getCurrentFirm,
  updateFirm,
  suspendFirm,
  activateFirm,
} from './service.js';

// ---- Helpers ----

const NOW = new Date('2026-01-15T12:00:00.000Z');

function makeFirmRow(overrides: Partial<FirmRow> = {}): FirmRow {
  return {
    id: 'firm-001',
    name: 'Acme Financial',
    slug: 'acme',
    status: 'provisioning',
    branding: {},
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

const actor = mockFirmAdmin();

// ---- Setup ----

beforeEach(() => {
  vi.clearAllMocks();
  mockRedis._store.clear();
});

// ---- Tests ----

describe('createFirm', () => {
  it('returns a firm DTO with status provisioning on success', async () => {
    const row = makeFirmRow();
    vi.mocked(repo.findBySlug).mockResolvedValue(undefined);
    vi.mocked(repo.create).mockResolvedValue(row);

    const result = await createFirm({ name: 'Acme Financial', slug: 'acme', branding: {} }, actor);

    expect(result).toEqual({
      id: 'firm-001',
      name: 'Acme Financial',
      slug: 'acme',
      status: 'provisioning',
      branding: {},
      createdAt: NOW.toISOString(),
      updatedAt: NOW.toISOString(),
    });
    expect(repo.create).toHaveBeenCalledWith({
      name: 'Acme Financial',
      slug: 'acme',
      branding: {},
    });
  });

  it('throws ConflictError when slug already exists', async () => {
    vi.mocked(repo.findBySlug).mockResolvedValue(makeFirmRow());

    await expect(
      createFirm({ name: 'Another Firm', slug: 'acme', branding: {} }, actor),
    ).rejects.toThrow(ConflictError);
    expect(repo.create).not.toHaveBeenCalled();
  });
});

describe('createFirmSchema — slug validation', () => {
  it('rejects slugs with uppercase letters', () => {
    const result = createFirmSchema.safeParse({ name: 'Test', slug: 'UPPER' });
    expect(result.success).toBe(false);
  });

  it('rejects slugs with spaces', () => {
    const result = createFirmSchema.safeParse({ name: 'Test', slug: 'no spaces' });
    expect(result.success).toBe(false);
  });

  it('rejects slugs starting with a hyphen', () => {
    const result = createFirmSchema.safeParse({ name: 'Test', slug: '-leading' });
    expect(result.success).toBe(false);
  });

  it('rejects slugs that are too short', () => {
    const result = createFirmSchema.safeParse({ name: 'Test', slug: 'a' });
    expect(result.success).toBe(false);
  });

  it('accepts a valid slug', () => {
    const result = createFirmSchema.safeParse({ name: 'Test', slug: 'valid-slug-123' });
    expect(result.success).toBe(true);
  });
});

describe('getCurrentFirm', () => {
  it('returns from Redis cache when cached', async () => {
    const dto = {
      id: 'firm-001',
      name: 'Acme Financial',
      slug: 'acme',
      status: 'provisioning',
      branding: {},
      createdAt: NOW.toISOString(),
      updatedAt: NOW.toISOString(),
    };
    mockRedis._store.set('firm:firm-001', JSON.stringify(dto));

    const result = await getCurrentFirm('firm-001');

    expect(result).toEqual(dto);
    expect(repo.findById).not.toHaveBeenCalled();
  });

  it('falls through to DB on cache miss and populates cache', async () => {
    const row = makeFirmRow();
    vi.mocked(repo.findById).mockResolvedValue(row);

    const result = await getCurrentFirm('firm-001');

    expect(repo.findById).toHaveBeenCalledWith('firm-001');
    expect(result.id).toBe('firm-001');
    expect(mockRedis.set).toHaveBeenCalledWith(
      'firm:firm-001',
      JSON.stringify(result),
      'EX',
      300,
    );
  });

  it('throws NotFoundError when firm does not exist', async () => {
    vi.mocked(repo.findById).mockResolvedValue(undefined);

    await expect(getCurrentFirm('nonexistent')).rejects.toThrow(NotFoundError);
  });
});

describe('updateFirm', () => {
  it('returns updated firm and invalidates both cache keys', async () => {
    const existing = makeFirmRow({ status: 'active' });
    const updated = makeFirmRow({ name: 'Acme Updated', status: 'active' });
    vi.mocked(repo.findById).mockResolvedValue(existing);
    vi.mocked(repo.update).mockResolvedValue(updated);

    // Pre-populate cache to verify invalidation
    mockRedis._store.set('firm:firm-001', 'stale');
    mockRedis._store.set('tenant:acme', 'stale');

    const result = await updateFirm('firm-001', { name: 'Acme Updated' }, actor);

    expect(result.name).toBe('Acme Updated');
    expect(mockRedis.del).toHaveBeenCalledWith('firm:firm-001', 'tenant:acme');
    expect(mockRedis._store.has('firm:firm-001')).toBe(false);
    expect(mockRedis._store.has('tenant:acme')).toBe(false);
  });
});

describe('suspendFirm', () => {
  it('succeeds when firm is active', async () => {
    const existing = makeFirmRow({ status: 'active' });
    const suspended = makeFirmRow({ status: 'suspended' });
    vi.mocked(repo.findById).mockResolvedValue(existing);
    vi.mocked(repo.updateStatus).mockResolvedValue(suspended);

    const result = await suspendFirm('firm-001', actor);

    expect(result.status).toBe('suspended');
    expect(repo.updateStatus).toHaveBeenCalledWith('firm-001', 'suspended');
  });

  it('throws WorkflowStateError when already suspended', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeFirmRow({ status: 'suspended' }));

    await expect(suspendFirm('firm-001', actor)).rejects.toThrow(WorkflowStateError);
    expect(repo.updateStatus).not.toHaveBeenCalled();
  });
});

describe('activateFirm', () => {
  it('succeeds from provisioning status', async () => {
    const existing = makeFirmRow({ status: 'provisioning' });
    const activated = makeFirmRow({ status: 'active' });
    vi.mocked(repo.findById).mockResolvedValue(existing);
    vi.mocked(repo.updateStatus).mockResolvedValue(activated);

    const result = await activateFirm('firm-001', actor);

    expect(result.status).toBe('active');
    expect(repo.updateStatus).toHaveBeenCalledWith('firm-001', 'active');
  });

  it('succeeds from suspended status', async () => {
    const existing = makeFirmRow({ status: 'suspended' });
    const activated = makeFirmRow({ status: 'active' });
    vi.mocked(repo.findById).mockResolvedValue(existing);
    vi.mocked(repo.updateStatus).mockResolvedValue(activated);

    const result = await activateFirm('firm-001', actor);

    expect(result.status).toBe('active');
  });

  it('throws WorkflowStateError when already active', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeFirmRow({ status: 'active' }));

    await expect(activateFirm('firm-001', actor)).rejects.toThrow(WorkflowStateError);
    expect(repo.updateStatus).not.toHaveBeenCalled();
  });
});

describe('audit events', () => {
  it('emits firm.created on createFirm', async () => {
    const row = makeFirmRow();
    vi.mocked(repo.findBySlug).mockResolvedValue(undefined);
    vi.mocked(repo.create).mockResolvedValue(row);

    await createFirm({ name: 'Acme Financial', slug: 'acme', branding: {} }, actor);

    expect(emitAuditEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'firm.created',
        firmId: 'firm-001',
        actorId: actor.userId,
        resourceType: 'firm',
        resourceId: 'firm-001',
      }),
    );
  });

  it('emits firm.updated on updateFirm', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeFirmRow({ status: 'active' }));
    vi.mocked(repo.update).mockResolvedValue(makeFirmRow({ name: 'New Name', status: 'active' }));

    await updateFirm('firm-001', { name: 'New Name' }, actor);

    expect(emitAuditEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'firm.updated',
        firmId: 'firm-001',
        actorId: actor.userId,
      }),
    );
  });

  it('emits firm.suspended on suspendFirm', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeFirmRow({ status: 'active' }));
    vi.mocked(repo.updateStatus).mockResolvedValue(makeFirmRow({ status: 'suspended' }));

    await suspendFirm('firm-001', actor);

    expect(emitAuditEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'firm.suspended',
        firmId: 'firm-001',
        actorId: actor.userId,
      }),
    );
  });

  it('emits firm.activated on activateFirm', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeFirmRow({ status: 'provisioning' }));
    vi.mocked(repo.updateStatus).mockResolvedValue(makeFirmRow({ status: 'active' }));

    await activateFirm('firm-001', actor);

    expect(emitAuditEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'firm.activated',
        firmId: 'firm-001',
        actorId: actor.userId,
      }),
    );
  });
});
