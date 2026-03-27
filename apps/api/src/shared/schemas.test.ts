import { describe, it, expect } from 'vitest';
import { loginSchema, mfaChallengeSchema } from '../modules/auth/schemas.js';
import { registerSchema, inviteUserSchema } from '../modules/users/schemas.js';
import { createFirmSchema } from '../modules/firms/schemas.js';
import { assignRolesSchema } from '../modules/roles/schemas.js';
import { auditQuerySchema } from '../modules/audit/schemas.js';

// ---------------------------------------------------------------------------
// loginSchema
// ---------------------------------------------------------------------------

describe('loginSchema', () => {
  it('accepts valid input', () => {
    const result = loginSchema.safeParse({
      email: 'user@example.com',
      password: 'secret123',
    });
    expect(result.success).toBe(true);
  });

  it('lowercases the email', () => {
    const result = loginSchema.safeParse({
      email: 'User@Example.COM',
      password: 'secret123',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.email).toBe('user@example.com');
    }
  });

  it('fails when email is missing', () => {
    const result = loginSchema.safeParse({ password: 'secret123' });
    expect(result.success).toBe(false);
  });

  it('fails when email is invalid', () => {
    const result = loginSchema.safeParse({
      email: 'not-an-email',
      password: 'secret123',
    });
    expect(result.success).toBe(false);
  });

  it('fails when password is missing', () => {
    const result = loginSchema.safeParse({ email: 'user@example.com' });
    expect(result.success).toBe(false);
  });

  it('fails when password is empty string', () => {
    const result = loginSchema.safeParse({
      email: 'user@example.com',
      password: '',
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// registerSchema
// ---------------------------------------------------------------------------

describe('registerSchema', () => {
  it('accepts valid input', () => {
    const result = registerSchema.safeParse({
      token: 'invite-token-abc',
      password: 'strongPassword1!',
    });
    expect(result.success).toBe(true);
  });

  it('accepts valid input with optional displayName', () => {
    const result = registerSchema.safeParse({
      token: 'invite-token-abc',
      password: 'strongPassword1!',
      displayName: 'Jane Doe',
    });
    expect(result.success).toBe(true);
  });

  it('fails when password is under 12 characters', () => {
    const result = registerSchema.safeParse({
      token: 'invite-token-abc',
      password: 'short',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const pwdIssue = result.error.issues.find((i) => i.path.includes('password'));
      expect(pwdIssue).toBeDefined();
      expect(pwdIssue!.message).toMatch(/at least 12 characters/);
    }
  });

  it('fails when token is missing', () => {
    const result = registerSchema.safeParse({
      password: 'strongPassword1!',
    });
    expect(result.success).toBe(false);
  });

  it('fails when token is empty', () => {
    const result = registerSchema.safeParse({
      token: '',
      password: 'strongPassword1!',
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// inviteUserSchema
// ---------------------------------------------------------------------------

describe('inviteUserSchema', () => {
  it('accepts valid input', () => {
    const result = inviteUserSchema.safeParse({
      email: 'advisor@firm.com',
      role: 'advisor',
      displayName: 'John Smith',
    });
    expect(result.success).toBe(true);
  });

  it('fails when email is invalid', () => {
    const result = inviteUserSchema.safeParse({
      email: 'bad-email',
      role: 'advisor',
      displayName: 'John Smith',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const emailIssue = result.error.issues.find((i) => i.path.includes('email'));
      expect(emailIssue).toBeDefined();
    }
  });

  it('fails when email is missing', () => {
    const result = inviteUserSchema.safeParse({
      role: 'advisor',
      displayName: 'John Smith',
    });
    expect(result.success).toBe(false);
  });

  it('fails when role is missing', () => {
    const result = inviteUserSchema.safeParse({
      email: 'advisor@firm.com',
      displayName: 'John Smith',
    });
    expect(result.success).toBe(false);
  });

  it('fails when displayName is missing', () => {
    const result = inviteUserSchema.safeParse({
      email: 'advisor@firm.com',
      role: 'advisor',
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// createFirmSchema
// ---------------------------------------------------------------------------

describe('createFirmSchema', () => {
  it('accepts valid slug', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme Financial',
      slug: 'acme-financial',
    });
    expect(result.success).toBe(true);
  });

  it('accepts slug with only lowercase letters', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: 'acme',
    });
    expect(result.success).toBe(true);
  });

  it('rejects uppercase slug', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: 'Acme',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const slugIssue = result.error.issues.find((i) => i.path.includes('slug'));
      expect(slugIssue).toBeDefined();
    }
  });

  it('rejects slug with spaces', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: 'acme financial',
    });
    expect(result.success).toBe(false);
  });

  it('rejects slug under 2 characters', () => {
    const result = createFirmSchema.safeParse({
      name: 'A',
      slug: 'a',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const slugIssue = result.error.issues.find((i) => i.path.includes('slug'));
      expect(slugIssue).toBeDefined();
      expect(slugIssue!.message).toMatch(/at least 2/);
    }
  });

  it('rejects slug starting with a hyphen', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: '-acme',
    });
    expect(result.success).toBe(false);
  });

  it('rejects slug ending with a hyphen', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: 'acme-',
    });
    expect(result.success).toBe(false);
  });

  it('defaults branding to empty object when omitted', () => {
    const result = createFirmSchema.safeParse({
      name: 'Acme',
      slug: 'acme',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.branding).toEqual({});
    }
  });
});

// ---------------------------------------------------------------------------
// assignRolesSchema
// ---------------------------------------------------------------------------

describe('assignRolesSchema', () => {
  it('accepts valid roles array', () => {
    const result = assignRolesSchema.safeParse({
      roles: ['firm_admin', 'advisor'],
    });
    expect(result.success).toBe(true);
  });

  it('accepts a single role', () => {
    const result = assignRolesSchema.safeParse({
      roles: ['viewer'],
    });
    expect(result.success).toBe(true);
  });

  it('rejects empty roles array', () => {
    const result = assignRolesSchema.safeParse({
      roles: [],
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const issue = result.error.issues[0];
      expect(issue.message).toMatch(/At least one role/);
    }
  });

  it('rejects missing roles field', () => {
    const result = assignRolesSchema.safeParse({});
    expect(result.success).toBe(false);
  });

  it('rejects roles containing empty strings', () => {
    const result = assignRolesSchema.safeParse({
      roles: [''],
    });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// auditQuerySchema
// ---------------------------------------------------------------------------

describe('auditQuerySchema', () => {
  it('accepts valid date range', () => {
    const result = auditQuerySchema.safeParse({
      from: '2026-01-01',
      to: '2026-03-01',
    });
    expect(result.success).toBe(true);
  });

  it('accepts full ISO 8601 datetime strings', () => {
    const result = auditQuerySchema.safeParse({
      from: '2026-01-01T00:00:00Z',
      to: '2026-03-01T23:59:59Z',
    });
    expect(result.success).toBe(true);
  });

  it('accepts query with no filters (all optional)', () => {
    const result = auditQuerySchema.safeParse({});
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.limit).toBe(50); // default
    }
  });

  it('rejects when "from" is after "to"', () => {
    const result = auditQuerySchema.safeParse({
      from: '2026-06-01',
      to: '2026-01-01',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const issue = result.error.issues.find((i) => i.path.includes('to'));
      expect(issue).toBeDefined();
      expect(issue!.message).toMatch(/"to" must be on or after "from"/);
    }
  });

  it('accepts when from equals to', () => {
    const result = auditQuerySchema.safeParse({
      from: '2026-03-15',
      to: '2026-03-15',
    });
    expect(result.success).toBe(true);
  });

  it('rejects invalid date format', () => {
    const result = auditQuerySchema.safeParse({
      from: '01/01/2026',
    });
    expect(result.success).toBe(false);
  });

  it('accepts valid actor_id as UUID', () => {
    const result = auditQuerySchema.safeParse({
      actor_id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    });
    expect(result.success).toBe(true);
  });

  it('rejects non-UUID actor_id', () => {
    const result = auditQuerySchema.safeParse({
      actor_id: 'not-a-uuid',
    });
    expect(result.success).toBe(false);
  });

  it('coerces limit from string to number', () => {
    const result = auditQuerySchema.safeParse({ limit: '25' });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.limit).toBe(25);
    }
  });

  it('rejects limit above 100', () => {
    const result = auditQuerySchema.safeParse({ limit: 200 });
    expect(result.success).toBe(false);
  });

  it('rejects limit below 1', () => {
    const result = auditQuerySchema.safeParse({ limit: 0 });
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// mfaChallengeSchema
// ---------------------------------------------------------------------------

describe('mfaChallengeSchema', () => {
  it('accepts valid 6-digit code with UUID sessionId', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      code: '123456',
    });
    expect(result.success).toBe(true);
  });

  it('rejects 5-digit code', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      code: '12345',
    });
    expect(result.success).toBe(false);
  });

  it('rejects 7-digit code', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      code: '1234567',
    });
    expect(result.success).toBe(false);
  });

  it('rejects non-numeric code', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      code: 'abcdef',
    });
    expect(result.success).toBe(false);
  });

  it('rejects code with letters mixed in', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
      code: '12ab56',
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing sessionId', () => {
    const result = mfaChallengeSchema.safeParse({
      code: '123456',
    });
    expect(result.success).toBe(false);
  });

  it('rejects non-UUID sessionId', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'not-a-uuid',
      code: '123456',
    });
    expect(result.success).toBe(false);
  });

  it('rejects missing code', () => {
    const result = mfaChallengeSchema.safeParse({
      sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    });
    expect(result.success).toBe(false);
  });
});
