import { z } from 'zod';

export const createServiceAccountSchema = z.object({
  name: z
    .string()
    .min(1, 'Name is required')
    .max(255, 'Name must be at most 255 characters'),
  permissions: z
    .array(z.string().min(1))
    .min(1, 'At least one permission is required')
    .default([]),
});

export const serviceAccountResponseSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  firmId: z.string().uuid(),
  permissions: z.array(z.string()),
  status: z.enum(['active', 'revoked']),
  createdAt: z.string(),
  rotatedAt: z.string().nullable(),
});

export const serviceAccountCreatedResponseSchema = serviceAccountResponseSchema.extend({
  apiKey: z.string().describe('Raw API key. Shown only once at creation time.'),
});

export const rotateKeyResponseSchema = serviceAccountResponseSchema.extend({
  apiKey: z.string().describe('New API key. Shown only once.'),
  graceExpiresAt: z.string().describe('Previous key remains valid until this time.'),
});

export type CreateServiceAccountInput = z.infer<typeof createServiceAccountSchema>;
export type ServiceAccountResponse = z.infer<typeof serviceAccountResponseSchema>;
export type ServiceAccountCreatedResponse = z.infer<typeof serviceAccountCreatedResponseSchema>;
export type RotateKeyResponse = z.infer<typeof rotateKeyResponseSchema>;
