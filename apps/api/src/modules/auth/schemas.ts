import { z } from 'zod';

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

export const loginSchema = z.object({
  email: z.string().email().max(255).toLowerCase(),
  password: z.string().min(1).max(256),
});
export type LoginInput = z.infer<typeof loginSchema>;

// ---------------------------------------------------------------------------
// Refresh
// ---------------------------------------------------------------------------

export const refreshSchema = z.object({
  refreshToken: z.string().min(1),
});
export type RefreshInput = z.infer<typeof refreshSchema>;

// ---------------------------------------------------------------------------
// Logout
// ---------------------------------------------------------------------------

export const logoutSchema = z.object({
  refreshToken: z.string().min(1),
});
export type LogoutInput = z.infer<typeof logoutSchema>;

// ---------------------------------------------------------------------------
// MFA Enroll (TOTP)
// ---------------------------------------------------------------------------

export const mfaEnrollSchema = z.object({
  type: z.literal('totp').default('totp'),
});
export type MfaEnrollInput = z.infer<typeof mfaEnrollSchema>;

// ---------------------------------------------------------------------------
// MFA Verify — confirms newly-enrolled factor
// ---------------------------------------------------------------------------

export const mfaVerifySchema = z.object({
  code: z.string().length(6).regex(/^\d{6}$/),
});
export type MfaVerifyInput = z.infer<typeof mfaVerifySchema>;

// ---------------------------------------------------------------------------
// MFA Challenge — user submits TOTP during login
// ---------------------------------------------------------------------------

export const mfaChallengeSchema = z.object({
  sessionId: z.string().uuid(),
  code: z.string().length(6).regex(/^\d{6}$/),
});
export type MfaChallengeInput = z.infer<typeof mfaChallengeSchema>;

// ---------------------------------------------------------------------------
// MFA Recover — use recovery code instead of TOTP
// ---------------------------------------------------------------------------

export const mfaRecoverSchema = z.object({
  sessionId: z.string().uuid(),
  recoveryCode: z.string().min(1).max(64),
});
export type MfaRecoverInput = z.infer<typeof mfaRecoverSchema>;
