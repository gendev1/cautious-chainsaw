import { z } from 'zod';

export const inviteUserSchema = z.object({
  email: z.string().email('Valid email is required').max(255),
  role: z.string().min(1, 'Role is required').max(100),
  displayName: z.string().min(1, 'Display name is required').max(255),
});

export const registerSchema = z.object({
  token: z.string().min(1, 'Invitation token is required'),
  password: z
    .string()
    .min(12, 'Password must be at least 12 characters')
    .max(128),
  displayName: z.string().min(1).max(255).optional(),
});

export const updateUserSchema = z.object({
  displayName: z.string().min(1).max(255).optional(),
  status: z.enum(['active', 'disabled']).optional(),
});

export const listUsersQuerySchema = z.object({
  cursor: z.string().uuid().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(25),
  status: z.enum(['invited', 'active', 'disabled']).optional(),
  search: z.string().max(255).optional(),
});

export type InviteUserInput = z.infer<typeof inviteUserSchema>;
export type RegisterInput = z.infer<typeof registerSchema>;
export type UpdateUserInput = z.infer<typeof updateUserSchema>;
export type ListUsersQuery = z.infer<typeof listUsersQuerySchema>;
