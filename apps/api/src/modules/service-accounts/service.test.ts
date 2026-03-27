import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ServiceAccountRow } from './types.js';
import { mockFirmAdmin } from '../../test/helpers.js';

// ---- Mocks ----

vi.mock('./repository.js', () => ({
  create: vi.fn(),
  findById: vi.fn(),
  list: vi.fn(),
  rotateKey: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock('../../shared/crypto.js', () => ({
  generateToken: vi.fn(() => 'mock-random-token-32bytes'),
  hashToken: vi.fn((raw: string) => `hashed:${raw}`),
}));

vi.mock('../../shared/audit.js', () => ({
  emitAuditEvent: vi.fn(),
}));

import * as repo from './repository.js';
import { emitAuditEvent } from '../../shared/audit.js';
import {
  createServiceAccount,
  listServiceAccounts,
  rotateKey,
  revokeServiceAccount,
} from './service.js';
import { WorkflowStateError } from '../../shared/errors.js';

// ---- Fixtures ----

const now = new Date('2026-03-26T12:00:00.000Z');

function makeRow(overrides: Partial<ServiceAccountRow> = {}): ServiceAccountRow {
  return {
    id: 'sa-001',
    name: 'CI Bot',
    firm_id: 'tenant-001',
    key_hash: 'hashed:wsa_mock-random-token-32bytes',
    previous_key_hash: null,
    key_grace_expires_at: null,
    permissions: ['account.read', 'report.read'],
    status: 'active',
    created_at: now,
    rotated_at: null,
    ...overrides,
  };
}

// ---- Tests ----

beforeEach(() => {
  vi.clearAllMocks();
  vi.setSystemTime(now);
});

describe('createServiceAccount', () => {
  const input = { name: 'CI Bot', permissions: ['account.read', 'report.read'] };
  const actor = mockFirmAdmin();

  it('returns account DTO and raw API key (shown once)', async () => {
    const row = makeRow();
    vi.mocked(repo.create).mockResolvedValue(row);

    const result = await createServiceAccount(input, actor);

    expect(result.apiKey).toBe('wsa_mock-random-token-32bytes');
    expect(result.account).toEqual({
      id: 'sa-001',
      name: 'CI Bot',
      firmId: 'tenant-001',
      permissions: ['account.read', 'report.read'],
      status: 'active',
      createdAt: now.toISOString(),
      rotatedAt: null,
    });
    expect(repo.create).toHaveBeenCalledWith({
      name: 'CI Bot',
      firmId: 'tenant-001',
      keyHash: 'hashed:wsa_mock-random-token-32bytes',
      permissions: ['account.read', 'report.read'],
    });
  });

  it('emits service_account.created audit event', async () => {
    const row = makeRow();
    vi.mocked(repo.create).mockResolvedValue(row);

    await createServiceAccount(input, actor);

    expect(emitAuditEvent).toHaveBeenCalledWith({
      firmId: 'tenant-001',
      actorId: 'user-admin-001',
      actorType: 'user',
      action: 'service_account.created',
      resourceType: 'service_account',
      resourceId: 'sa-001',
      metadata: { name: 'CI Bot', permissions: ['account.read', 'report.read'] },
    });
  });
});

describe('listServiceAccounts', () => {
  it('returns accounts for the firm', async () => {
    const rows = [makeRow(), makeRow({ id: 'sa-002', name: 'Deploy Bot' })];
    vi.mocked(repo.list).mockResolvedValue(rows);

    const actor = mockFirmAdmin();
    const result = await listServiceAccounts(actor);

    expect(repo.list).toHaveBeenCalledWith('tenant-001');
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('sa-001');
    expect(result[1].id).toBe('sa-002');
  });
});

describe('rotateKey', () => {
  const actor = mockFirmAdmin();
  const graceExpiresAt = new Date(now.getTime() + 24 * 60 * 60 * 1000);

  it('generates new key, moves current to previous with 24h grace', async () => {
    const existing = makeRow();
    const rotatedRow = makeRow({
      key_hash: 'hashed:wsa_mock-random-token-32bytes',
      previous_key_hash: 'old-hash',
      key_grace_expires_at: graceExpiresAt,
      rotated_at: now,
    });
    vi.mocked(repo.findById).mockResolvedValue(existing);
    vi.mocked(repo.rotateKey).mockResolvedValue(rotatedRow);

    const result = await rotateKey('sa-001', actor);

    expect(result.apiKey).toBe('wsa_mock-random-token-32bytes');
    expect(result.graceExpiresAt).toBe(graceExpiresAt.toISOString());
    expect(result.account.id).toBe('sa-001');
    expect(repo.rotateKey).toHaveBeenCalledWith(
      'sa-001',
      'tenant-001',
      'hashed:wsa_mock-random-token-32bytes',
      graceExpiresAt,
    );
  });

  it('rejects if account is revoked', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeRow({ status: 'revoked' }));

    await expect(rotateKey('sa-001', actor)).rejects.toThrow(WorkflowStateError);
    await expect(rotateKey('sa-001', actor)).rejects.toThrow(
      'Cannot rotate key for a revoked service account',
    );
  });

  it('emits service_account.key_rotated audit event', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeRow());
    vi.mocked(repo.rotateKey).mockResolvedValue(makeRow({ rotated_at: now }));

    await rotateKey('sa-001', actor);

    expect(emitAuditEvent).toHaveBeenCalledWith({
      firmId: 'tenant-001',
      actorId: 'user-admin-001',
      actorType: 'user',
      action: 'service_account.key_rotated',
      resourceType: 'service_account',
      resourceId: 'sa-001',
      metadata: { graceExpiresAt: graceExpiresAt.toISOString() },
    });
  });
});

describe('revokeServiceAccount', () => {
  const actor = mockFirmAdmin();

  it('sets status to revoked', async () => {
    const revokedRow = makeRow({ status: 'revoked' });
    vi.mocked(repo.findById).mockResolvedValue(makeRow());
    vi.mocked(repo.revoke).mockResolvedValue(revokedRow);

    const result = await revokeServiceAccount('sa-001', actor);

    expect(result.status).toBe('revoked');
    expect(repo.revoke).toHaveBeenCalledWith('sa-001', 'tenant-001');
  });

  it('rejects if already revoked', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeRow({ status: 'revoked' }));

    await expect(revokeServiceAccount('sa-001', actor)).rejects.toThrow(WorkflowStateError);
    await expect(revokeServiceAccount('sa-001', actor)).rejects.toThrow(
      'Service account is already revoked',
    );
  });

  it('emits service_account.revoked audit event', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeRow());
    vi.mocked(repo.revoke).mockResolvedValue(makeRow({ status: 'revoked' }));

    await revokeServiceAccount('sa-001', actor);

    expect(emitAuditEvent).toHaveBeenCalledWith({
      firmId: 'tenant-001',
      actorId: 'user-admin-001',
      actorType: 'user',
      action: 'service_account.revoked',
      resourceType: 'service_account',
      resourceId: 'sa-001',
      metadata: { name: 'CI Bot' },
    });
  });
});
