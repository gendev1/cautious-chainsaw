import type { Context } from 'hono';

interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
    details?: Record<string, string>;
  };
  pagination?: {
    nextCursor?: string;
    hasMore: boolean;
    totalCount?: number;
  };
  requestId: string;
}

export function success<T>(c: Context, data: T, status: 200 | 201 | 202 = 200) {
  const requestId = c.get('requestId') ?? 'unknown';
  return c.json<ApiResponse<T>>({ success: true, data, requestId }, status);
}

export function paginated<T>(
  c: Context,
  data: T[],
  pagination: { nextCursor?: string; hasMore: boolean; totalCount?: number },
) {
  const requestId = c.get('requestId') ?? 'unknown';
  return c.json<ApiResponse<T[]>>({ success: true, data, pagination, requestId }, 200);
}

export function error(
  c: Context,
  code: string,
  message: string,
  status: number = 500,
  details?: Record<string, string>,
) {
  const requestId = c.get('requestId') ?? 'unknown';
  return c.json<ApiResponse<never>>(
    { success: false, error: { code, message, details }, requestId },
    status as any,
  );
}
