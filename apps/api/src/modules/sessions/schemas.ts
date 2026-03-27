import { z } from 'zod';

export const sessionResponseSchema = z.object({
  id: z.string().uuid(),
  userId: z.string().uuid(),
  firmId: z.string().uuid(),
  ipAddress: z.string().nullable(),
  userAgent: z.string().nullable(),
  createdAt: z.string(),
  lastActiveAt: z.string(),
  current: z.boolean(),
});

export const revokeSessionParamsSchema = z.object({
  id: z.string().uuid('Invalid session ID'),
});

export const adminRevokeAllSchema = z.object({
  userId: z.string().uuid('Invalid user ID'),
});

export type SessionResponse = z.infer<typeof sessionResponseSchema>;
export type RevokeSessionParams = z.infer<typeof revokeSessionParamsSchema>;
export type AdminRevokeAllInput = z.infer<typeof adminRevokeAllSchema>;
