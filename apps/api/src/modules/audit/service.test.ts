import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { AuditEventRow, AuditQueryFilters, PaginatedAuditResult } from './types.js';
import { ValidationError } from '../../shared/errors.js';
import { uuid } from '../../test/helpers.js';

// ---- Mocks ----

vi.mock('./repository.js', () => ({
  query: vi.fn(),
}));

// Import after mocks are declared
import * as repo from './repository.js';
import { queryAuditEvents } from './service.js';

// ---- Helpers ----

const NOW = new Date('2026-03-15T12:00:00.000Z');

function makeEventRow(overrides: Partial<AuditEventRow> = {}): AuditEventRow {
  return {
    id: uuid(),
    firm_id: 'tenant-001',
    actor_id: 'user-admin-001',
    actor_type: 'user',
    action: 'client.create',
    resource_type: 'client',
    resource_id: 'client-001',
    metadata: {},
    ip_address: '127.0.0.1',
    user_agent: 'vitest',
    correlation_id: null,
    created_at: NOW,
    ...overrides,
  };
}

function baseFilters(overrides: Partial<AuditQueryFilters> = {}): AuditQueryFilters {
  return {
    firmId: 'tenant-001',
    limit: 50,
    ...overrides,
  };
}

// ---- Tests ----

describe('queryAuditEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('passes valid filters to repository and returns DTOs', async () => {
    const row = makeEventRow();
    const repoResult: PaginatedAuditResult = {
      events: [row],
      nextCursor: 'cursor-abc',
      hasMore: true,
    };
    vi.mocked(repo.query).mockResolvedValue(repoResult);

    const filters = baseFilters({
      from: '2026-01-01',
      to: '2026-03-01',
      action: 'client.create',
    });

    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledOnce();
    expect(repo.query).toHaveBeenCalledWith(filters);

    expect(result.hasMore).toBe(true);
    expect(result.nextCursor).toBe('cursor-abc');
    expect(result.events).toHaveLength(1);

    const dto = result.events[0];
    expect(dto.id).toBe(row.id);
    expect(dto.firmId).toBe(row.firm_id);
    expect(dto.actorId).toBe(row.actor_id);
    expect(dto.actorType).toBe(row.actor_type);
    expect(dto.action).toBe(row.action);
    expect(dto.resourceType).toBe(row.resource_type);
    expect(dto.resourceId).toBe(row.resource_id);
    expect(dto.ipAddress).toBe(row.ip_address);
    expect(dto.userAgent).toBe(row.user_agent);
    expect(dto.correlationId).toBe(row.correlation_id);
    expect(dto.createdAt).toBe(NOW.toISOString());
  });

  it('rejects date range exceeding 90 days with ValidationError', async () => {
    const filters = baseFilters({
      from: '2025-01-01',
      to: '2025-07-01', // ~181 days
    });

    await expect(queryAuditEvents(filters)).rejects.toThrow(ValidationError);
    await expect(queryAuditEvents(filters)).rejects.toThrow(
      /Date range must not exceed 90 days/,
    );

    expect(repo.query).not.toHaveBeenCalled();
  });

  it('allows exactly 90 days', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters({
      from: '2026-01-01',
      to: '2026-04-01', // 90 days
    });

    await expect(queryAuditEvents(filters)).resolves.toBeDefined();
    expect(repo.query).toHaveBeenCalledOnce();
  });

  it('works with only actor_id filter', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters({ actorId: 'user-advisor-001' });
    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledWith(filters);
    expect(result.events).toEqual([]);
    expect(result.hasMore).toBe(false);
  });

  it('works with only action filter', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters({ action: 'account.open' });
    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledWith(filters);
    expect(result.events).toEqual([]);
  });

  it('works with only from date (no to) — skips date range check', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters({ from: '2025-01-01' });
    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledOnce();
    expect(result.events).toEqual([]);
  });

  it('works with only to date (no from) — skips date range check', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters({ to: '2026-03-01' });
    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledOnce();
    expect(result.events).toEqual([]);
  });

  it('works with no optional filters at all', async () => {
    vi.mocked(repo.query).mockResolvedValue({
      events: [],
      nextCursor: undefined,
      hasMore: false,
    });

    const filters = baseFilters();
    const result = await queryAuditEvents(filters);

    expect(repo.query).toHaveBeenCalledWith(filters);
    expect(result.events).toEqual([]);
    expect(result.hasMore).toBe(false);
    expect(result.nextCursor).toBeUndefined();
  });

  it('maps multiple rows to DTOs', async () => {
    const rows = [
      makeEventRow({ action: 'client.create' }),
      makeEventRow({ action: 'account.open', actor_type: 'service' }),
    ];
    vi.mocked(repo.query).mockResolvedValue({
      events: rows,
      nextCursor: undefined,
      hasMore: false,
    });

    const result = await queryAuditEvents(baseFilters());

    expect(result.events).toHaveLength(2);
    expect(result.events[0].action).toBe('client.create');
    expect(result.events[1].action).toBe('account.open');
    expect(result.events[1].actorType).toBe('service');
  });
});
