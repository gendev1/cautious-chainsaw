import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mockFirmAdmin } from '../../test/helpers.js';
import type { UserRow, InvitationRow } from './types.js';
import { ConflictError, NotFoundError, ValidationError, WorkflowStateError } from '../../shared/errors.js';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('bcrypt', () => ({
  default: { hash: vi.fn().mockResolvedValue('hashed-password') },
}));

vi.mock('../../shared/crypto.js', () => ({
  generateToken: vi.fn(() => 'raw-token-abc'),
  hashToken: vi.fn((t: string) => `hashed:${t}`),
}));

vi.mock('../../shared/audit.js', () => ({
  auditFromContext: vi.fn().mockResolvedValue(undefined),
  emitAuditEvent: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('./repository.js', () => ({
  findPendingInvitationByEmail: vi.fn(),
  findUserByEmail: vi.fn(),
  createUser: vi.fn(),
  createInvitation: vi.fn(),
  findInvitationByTokenHash: vi.fn(),
  findInvitationById: vi.fn(),
  updateInvitationStatus: vi.fn(),
  findUserById: vi.fn(),
  updateUser: vi.fn(),
  listUsers: vi.fn(),
}));

// Import after mocks are declared
import * as repo from './repository.js';
import { auditFromContext, emitAuditEvent } from '../../shared/audit.js';
import { inviteUser, register, listUsers, getUser, updateUser, resendInvitation } from './service.js';

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

const now = new Date('2026-03-26T12:00:00Z');
const future = new Date('2026-03-29T12:00:00Z');
const past = new Date('2026-03-20T12:00:00Z');

function makeUserRow(overrides: Partial<UserRow> = {}): UserRow {
  return {
    id: 'user-001',
    firm_id: 'tenant-001',
    email: 'alice@acme.com',
    password_hash: null,
    display_name: 'Alice',
    status: 'active',
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function makeInvitationRow(overrides: Partial<InvitationRow> = {}): InvitationRow {
  return {
    id: 'inv-001',
    firm_id: 'tenant-001',
    email: 'bob@acme.com',
    role: 'advisor',
    display_name: 'Bob',
    invited_by: 'user-admin-001',
    token_hash: 'hashed:raw-token-abc',
    status: 'pending',
    expires_at: future,
    accepted_at: null,
    created_at: now,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe('inviteUser', () => {
  const actor = mockFirmAdmin();
  const input = { email: 'bob@acme.com', role: 'advisor', displayName: 'Bob' };

  it('creates invitation with token and returns invitation DTO', async () => {
    vi.mocked(repo.findPendingInvitationByEmail).mockResolvedValue(undefined);
    vi.mocked(repo.findUserByEmail).mockResolvedValue(undefined);
    vi.mocked(repo.createUser).mockResolvedValue(makeUserRow({ id: 'user-new', email: input.email, status: 'invited' }));
    vi.mocked(repo.createInvitation).mockResolvedValue(makeInvitationRow());

    const result = await inviteUser(actor, input);

    expect(result.token).toBe('raw-token-abc');
    expect(result.invitation).toMatchObject({
      id: 'inv-001',
      firmId: 'tenant-001',
      email: 'bob@acme.com',
      role: 'advisor',
      status: 'pending',
    });
    expect(repo.createUser).toHaveBeenCalledWith(
      expect.objectContaining({ firmId: 'tenant-001', email: input.email, status: 'invited' }),
    );
    expect(repo.createInvitation).toHaveBeenCalledWith(
      expect.objectContaining({ firmId: 'tenant-001', email: input.email, role: 'advisor' }),
    );
    expect(auditFromContext).toHaveBeenCalledWith(actor, 'user.invited', expect.anything());
  });

  it('is idempotent: re-inviting pending email returns existing invitation', async () => {
    const existing = makeInvitationRow();
    vi.mocked(repo.findPendingInvitationByEmail).mockResolvedValue(existing);

    const result = await inviteUser(actor, input);

    expect(result.invitation.id).toBe('inv-001');
    expect(result.token).toBe('');
    expect(repo.createUser).not.toHaveBeenCalled();
    expect(repo.createInvitation).not.toHaveBeenCalled();
  });

  it('rejects if active user with that email already exists (ConflictError)', async () => {
    vi.mocked(repo.findPendingInvitationByEmail).mockResolvedValue(undefined);
    vi.mocked(repo.findUserByEmail).mockResolvedValue(makeUserRow({ email: input.email }));

    await expect(inviteUser(actor, input)).rejects.toThrow(ConflictError);
  });
});

describe('register', () => {
  it('valid token creates active user with hashed password and marks invitation accepted', async () => {
    const invitation = makeInvitationRow();
    const activatedUser = makeUserRow({ id: 'user-001', email: invitation.email, status: 'active', password_hash: 'hashed-password' });

    vi.mocked(repo.findInvitationByTokenHash).mockResolvedValue(invitation);
    vi.mocked(repo.findUserByEmail).mockResolvedValue(makeUserRow({ email: invitation.email, status: 'invited' }));
    vi.mocked(repo.updateUser).mockResolvedValue(activatedUser);
    vi.mocked(repo.updateInvitationStatus).mockResolvedValue({ ...invitation, status: 'accepted' });

    const result = await register({ token: 'raw-token-abc', password: 'supersecure123' });

    expect(result.status).toBe('active');
    expect(repo.updateUser).toHaveBeenCalledWith(
      invitation.firm_id,
      expect.any(String),
      expect.objectContaining({ status: 'active', passwordHash: 'hashed-password' }),
    );
    expect(repo.updateInvitationStatus).toHaveBeenCalledWith(invitation.id, 'accepted', expect.objectContaining({ acceptedAt: expect.any(Date) }));
    expect(emitAuditEvent).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'user.registered' }),
    );
  });

  it('expired token throws ValidationError', async () => {
    const invitation = makeInvitationRow({ expires_at: past });
    vi.mocked(repo.findInvitationByTokenHash).mockResolvedValue(invitation);
    vi.mocked(repo.updateInvitationStatus).mockResolvedValue({ ...invitation, status: 'expired' });

    await expect(register({ token: 'expired-token', password: 'supersecure123' })).rejects.toThrow(ValidationError);
    expect(repo.updateInvitationStatus).toHaveBeenCalledWith(invitation.id, 'expired');
  });

  it('already-accepted token throws error (invitation not found as pending)', async () => {
    // findInvitationByTokenHash only returns pending invitations, so accepted ones return undefined
    vi.mocked(repo.findInvitationByTokenHash).mockResolvedValue(undefined);

    await expect(register({ token: 'accepted-token', password: 'supersecure123' })).rejects.toThrow(ValidationError);
  });
});

describe('listUsers', () => {
  it('returns paginated results scoped to tenant', async () => {
    const rows = [
      makeUserRow({ id: 'u1' }),
      makeUserRow({ id: 'u2' }),
      makeUserRow({ id: 'u3' }),
    ];
    vi.mocked(repo.listUsers).mockResolvedValue({ rows, total: 3 });

    const result = await listUsers('tenant-001', { limit: 25 });

    expect(repo.listUsers).toHaveBeenCalledWith('tenant-001', expect.objectContaining({ limit: 25 }));
    expect(result.users).toHaveLength(3);
    expect(result.pagination.totalCount).toBe(3);
    expect(result.pagination.hasMore).toBe(false);
  });
});

describe('getUser', () => {
  it('returns user scoped to tenant', async () => {
    vi.mocked(repo.findUserById).mockResolvedValue(makeUserRow());

    const result = await getUser('tenant-001', 'user-001');

    expect(result.id).toBe('user-001');
    expect(result.firmId).toBe('tenant-001');
    expect(repo.findUserById).toHaveBeenCalledWith('tenant-001', 'user-001');
  });

  it('throws NotFoundError if missing', async () => {
    vi.mocked(repo.findUserById).mockResolvedValue(undefined);

    await expect(getUser('tenant-001', 'no-such-user')).rejects.toThrow(NotFoundError);
  });
});

describe('updateUser', () => {
  const actor = mockFirmAdmin();

  it('status change to disabled emits user.disabled audit event', async () => {
    const existing = makeUserRow({ status: 'active' });
    const updated = makeUserRow({ status: 'disabled' });

    vi.mocked(repo.findUserById).mockResolvedValue(existing);
    vi.mocked(repo.updateUser).mockResolvedValue(updated);

    await updateUser(actor, 'user-001', { status: 'disabled' });

    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'user.disabled',
      expect.objectContaining({
        resourceType: 'user',
        resourceId: 'user-001',
        metadata: expect.objectContaining({ previousStatus: 'active', newStatus: 'disabled' }),
      }),
    );
  });

  it('status change to active emits user.enabled audit event', async () => {
    const existing = makeUserRow({ status: 'disabled' });
    const updated = makeUserRow({ status: 'active' });

    vi.mocked(repo.findUserById).mockResolvedValue(existing);
    vi.mocked(repo.updateUser).mockResolvedValue(updated);

    await updateUser(actor, 'user-001', { status: 'active' });

    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'user.enabled',
      expect.objectContaining({
        resourceType: 'user',
        resourceId: 'user-001',
        metadata: expect.objectContaining({ previousStatus: 'disabled', newStatus: 'active' }),
      }),
    );
  });
});

describe('resendInvitation', () => {
  const actor = mockFirmAdmin();

  it('generates new token, resets expiry', async () => {
    const invitation = makeInvitationRow();
    const updatedInvitation = makeInvitationRow({ token_hash: 'hashed:raw-token-abc', expires_at: future });

    vi.mocked(repo.findInvitationById).mockResolvedValue(invitation);
    vi.mocked(repo.updateInvitationStatus).mockResolvedValue(updatedInvitation);

    const result = await resendInvitation(actor, 'inv-001');

    expect(result.token).toBe('raw-token-abc');
    expect(result.invitation.id).toBe('inv-001');
    expect(repo.updateInvitationStatus).toHaveBeenCalledWith(
      'inv-001',
      'pending',
      expect.objectContaining({ tokenHash: expect.any(String), expiresAt: expect.any(Date) }),
    );
    expect(auditFromContext).toHaveBeenCalledWith(
      actor,
      'user.invited',
      expect.objectContaining({
        resourceType: 'invitation',
        resourceId: 'inv-001',
        metadata: expect.objectContaining({ resend: true }),
      }),
    );
  });
});
