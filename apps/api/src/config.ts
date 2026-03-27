import { z } from 'zod';

const envSchema = z.object({
  PORT: z.coerce.number().default(8080),
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  BASE_DOMAIN: z.string().default('wealthadvisor.com'),

  // Postgres
  DATABASE_URL: z.string().default('postgres://wealth:wealth_dev@localhost:5432/wealth_advisor'),

  // Redis
  REDIS_URL: z.string().default('redis://localhost:6379'),

  // Kafka
  KAFKA_BROKERS: z.string().default('localhost:9092'),

  // JWT
  JWT_PRIVATE_KEY: z.string().optional(),
  JWT_PUBLIC_KEY: z.string().optional(),
  JWT_ACCESS_TOKEN_TTL: z.coerce.number().default(900), // 15 min
  JWT_REFRESH_TOKEN_TTL: z.coerce.number().default(604800), // 7 days

  // Rate limiting
  RATE_LIMIT_TENANT_RPM: z.coerce.number().default(1000),
  RATE_LIMIT_USER_RPM: z.coerce.number().default(100),
  RATE_LIMIT_LOGIN_RPM: z.coerce.number().default(10),

  // Tenant cache
  TENANT_CACHE_TTL: z.coerce.number().default(60),

  // Sidecar
  SIDECAR_URL: z.string().default('http://localhost:8081'),

  // LLM (passed through to sidecar)
  LLM_BASE_URL: z.string().optional(),
  LLM_MODEL: z.string().optional(),
  LLM_API_KEY: z.string().optional(),
});

export type Config = z.infer<typeof envSchema>;

let _config: Config | null = null;

export function loadConfig(): Config {
  if (_config) return _config;
  _config = envSchema.parse(process.env);
  return _config;
}

export function getConfig(): Config {
  if (!_config) throw new Error('Config not loaded. Call loadConfig() first.');
  return _config;
}
