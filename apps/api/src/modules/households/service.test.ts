import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mockFirmAdmin } from '../../test/helpers.js';
import { NotFoundError, ValidationError, WorkflowStateError } from '../../shared/errors.js';
import type { HouseholdRow } from './types.js';

vi.mock('../../shared/audit.js', () => ({
  auditFromContext: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../shared/events.js', () => ({
  publishDomainEvent: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../users/repository.js', () => ({
  findUserById: vi.fn(),
}));

vi.mock('./repository.js', () => ({
  create: vi.fn(),
  findById: vi.fn(),
  list: vi.fn(),
  update: vi.fn(),
  updateStatus: vi.fn(),
  countBlockingAccounts: vi.fn(),
}));

import { auditFromContext } from '../../shared/audit.js';
import { publishDomainEvent } from '../../shared/events.js';
import * as userRepo from '../users/repository.js';
import * as repo from './repository.js';
import {
  closeHousehold,
  createHousehold,
  deactivateHousehold,
  getHousehold,
  listHouseholds,
  reactivateHousehold,
  updateHousehold,
} from './service.js';

const now = new Date('2026-03-27T12:00:00Z');

function makeHouseholdRow(overrides: Partial<HouseholdRow> = {}): HouseholdRow {
  return {
    id: 'household-001',
    tenant_id: 'tenant-001',
    name: 'Smith Household',
    status: 'active',
    primary_advisor_id: 'advisor-001',
    service_team_json: [{ userId: 'advisor-002', role: 'service_associate' }],
    notes: 'Preferred contact by phone',
    created_at: now,
    updated_at: now,
    created_by: 'user-admin-001',
    ...overrides,
  };
}

describe('households/service', () => {
  const actor = mockFirmAdmin();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('creates a household and records immutable createdBy from the actor', async () => {
    vi.mocked(userRepo.findUserById).mockResolvedValue({ id: 'advisor-001' } as any);
    vi.mocked(repo.create).mockResolvedValue(makeHouseholdRow());

    const result = await createHousehold(actor, {
      name: 'Smith Household',
      primaryAdvisorId: 'advisor-001',
      serviceTeam: [],
      notes: null,
    });

    expect(repo.create).toHaveBeenCalledWith(
      expect.objectContaining({
        tenantId: 'tenant-001',
        createdBy: 'user-admin-001',
      }),
    );
    expect(result.createdBy).toBe('user-admin-001');
    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'household.created',
      expect.objectContaining({ resourceType: 'household', resourceId: 'household-001' }),
    );
    expect(publishDomainEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'household.created', tenantId: 'tenant-001' }),
    );
  });

  it('rejects create when the primary advisor is outside the tenant', async () => {
    vi.mocked(userRepo.findUserById).mockResolvedValue(undefined);

    await expect(
      createHousehold(actor, {
        name: 'Smith Household',
        primaryAdvisorId: 'advisor-999',
        serviceTeam: [],
        notes: null,
      }),
    ).rejects.toThrow(ValidationError);
  });

  it('lists households with pagination metadata', async () => {
    vi.mocked(repo.list).mockResolvedValue({
      rows: [makeHouseholdRow(), makeHouseholdRow({ id: 'household-002' })],
      total: 3,
    });

    const result = await listHouseholds('tenant-001', { limit: 2, offset: 0 });

    expect(repo.list).toHaveBeenCalledWith('tenant-001', { limit: 2, offset: 0 });
    expect(result.households).toHaveLength(2);
    expect(result.pagination).toEqual({ hasMore: true, totalCount: 3 });
  });

  it('gets a tenant-scoped household by id', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeHouseholdRow());

    const result = await getHousehold('tenant-001', 'household-001');

    expect(repo.findById).toHaveBeenCalledWith('tenant-001', 'household-001');
    expect(result.id).toBe('household-001');
  });

  it('throws NotFoundError when a household is missing', async () => {
    vi.mocked(repo.findById).mockResolvedValue(undefined);

    await expect(getHousehold('tenant-001', 'missing')).rejects.toThrow(NotFoundError);
  });

  it('updates a household and preserves createdBy', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeHouseholdRow());
    vi.mocked(repo.update).mockResolvedValue(makeHouseholdRow({ name: 'Updated Household' }));

    const result = await updateHousehold(actor, 'household-001', { name: 'Updated Household' });

    expect(result.name).toBe('Updated Household');
    expect(result.createdBy).toBe('user-admin-001');
    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'household.updated',
      expect.objectContaining({ resourceId: 'household-001' }),
    );
  });

  it('deactivates an active household', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeHouseholdRow({ status: 'active' }));
    vi.mocked(repo.updateStatus).mockResolvedValue(makeHouseholdRow({ status: 'inactive' }));

    const result = await deactivateHousehold(actor, 'household-001');

    expect(result.status).toBe('inactive');
    expect(publishDomainEvent).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'household.status_changed' }),
    );
  });

  it('reactivates an inactive household and rejects closed households', async () => {
    vi.mocked(repo.findById).mockResolvedValueOnce(makeHouseholdRow({ status: 'inactive' }));
    vi.mocked(repo.updateStatus).mockResolvedValueOnce(makeHouseholdRow({ status: 'active' }));

    const reactivated = await reactivateHousehold(actor, 'household-001');
    expect(reactivated.status).toBe('active');

    vi.mocked(repo.findById).mockResolvedValueOnce(makeHouseholdRow({ status: 'closed' }));
    await expect(reactivateHousehold(actor, 'household-001')).rejects.toThrow(WorkflowStateError);
  });

  it('prevents closing a household with active or restricted accounts', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeHouseholdRow({ status: 'inactive' }));
    vi.mocked(repo.countBlockingAccounts).mockResolvedValue(2);

    await expect(closeHousehold(actor, 'household-001')).rejects.toThrow(WorkflowStateError);
    expect(repo.updateStatus).not.toHaveBeenCalled();
  });

  it('closes a household when there are no blocking accounts', async () => {
    vi.mocked(repo.findById).mockResolvedValue(makeHouseholdRow({ status: 'inactive' }));
    vi.mocked(repo.countBlockingAccounts).mockResolvedValue(0);
    vi.mocked(repo.updateStatus).mockResolvedValue(makeHouseholdRow({ status: 'closed' }));

    const result = await closeHousehold(actor, 'household-001');

    expect(result.status).toBe('closed');
    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'household.status_changed',
      expect.objectContaining({
        metadata: expect.objectContaining({ previousStatus: 'inactive', newStatus: 'closed' }),
      }),
    );
  });
});
