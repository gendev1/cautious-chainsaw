import { z } from 'zod';

export const createFirmSchema = z.object({
  name: z.string().min(1, 'Firm name is required').max(255),
  slug: z
    .string()
    .min(2, 'Slug must be at least 2 characters')
    .max(63)
    .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, 'Slug must be lowercase alphanumeric with hyphens'),
  branding: z
    .object({
      primaryColor: z.string().optional(),
      logoUrl: z.string().url().optional(),
      faviconUrl: z.string().url().optional(),
    })
    .optional()
    .default({}),
});

export const updateFirmSchema = z.object({
  name: z.string().min(1).max(255).optional(),
  branding: z
    .object({
      primaryColor: z.string().optional(),
      logoUrl: z.string().url().optional(),
      faviconUrl: z.string().url().optional(),
    })
    .optional(),
});

export const firmResponseSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  slug: z.string(),
  status: z.enum(['provisioning', 'active', 'suspended', 'deactivated']),
  branding: z.record(z.unknown()),
  createdAt: z.string(),
  updatedAt: z.string(),
});

export type CreateFirmInput = z.infer<typeof createFirmSchema>;
export type UpdateFirmInput = z.infer<typeof updateFirmSchema>;
export type FirmResponse = z.infer<typeof firmResponseSchema>;
