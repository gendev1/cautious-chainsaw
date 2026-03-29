# Docker Service Configuration Reference

Detailed configuration for each service commonly used in test infrastructure. All configurations are optimized for test environments — fast startup, minimal resource usage, deterministic behavior.

## PostgreSQL

### Compose Configuration

```yaml
postgres:
  image: postgres:16
  environment:
    POSTGRES_DB: test
    POSTGRES_USER: postgres
    POSTGRES_PASSWORD: test
    # Performance tuning for tests (not for production)
    POSTGRES_INITDB_ARGS: "--data-checksums"
  command: >
    postgres
    -c shared_buffers=128MB
    -c max_connections=200
    -c fsync=off
    -c synchronous_commit=off
    -c full_page_writes=off
  volumes:
    - ./fixtures/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    - ./fixtures/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
  ports:
    - "5432:5432"
  healthcheck:
    test: pg_isready -U postgres -d test
    interval: 2s
    timeout: 5s
    retries: 5
```

**Notes:**
- `fsync=off` and `synchronous_commit=off` disable durability guarantees — tests don't need them, and it makes writes ~10x faster
- Files in `/docker-entrypoint-initdb.d/` run in alphabetical order on first startup only. Prefix with numbers for ordering.
- Supported file types: `.sql`, `.sql.gz`, `.sh`
- `POSTGRES_DB` creates the database automatically

### Connection String

```
DATABASE_URL=postgresql://postgres:test@localhost:5432/test
```

### Fixture Patterns

**Schema from project migrations:**
```bash
# Extract DDL from Prisma
npx prisma migrate diff --from-empty --to-schema-datamodel prisma/schema.prisma --script > fixtures/init.sql

# Extract DDL from Knex/Sequelize migrations
# Run migrations against a temp DB and dump schema
pg_dump --schema-only -h localhost -U postgres test > fixtures/init.sql

# From Alembic
alembic upgrade head  # run against temp DB
pg_dump --schema-only ... > fixtures/init.sql
```

**Seed data best practices:**
- Use deterministic UUIDs: `'00000000-0000-0000-0000-000000000001'`
- Use `INSERT ... ON CONFLICT DO NOTHING` for idempotency
- Include data for all test scenarios: happy path, edge cases, error states
- Keep seed files small (<100 rows per table) — tests should create scenario-specific data programmatically

### Resetting Between Tests

```typescript
// Truncate all tables between tests (faster than recreating)
async function resetDatabase(db: Pool) {
  const tables = await db.query(`
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
    AND tablename != '_prisma_migrations'
  `);
  for (const { tablename } of tables.rows) {
    await db.query(`TRUNCATE TABLE "${tablename}" CASCADE`);
  }
  // Re-seed
  const seed = fs.readFileSync('fixtures/seed.sql', 'utf8');
  await db.query(seed);
}
```

---

## Redis

### Compose Configuration

```yaml
redis:
  image: redis:7
  command: redis-server --save "" --appendonly no --maxmemory 64mb --maxmemory-policy allkeys-lru
  ports:
    - "6379:6379"
  healthcheck:
    test: redis-cli ping
    interval: 2s
    timeout: 5s
    retries: 5
```

**Notes:**
- `--save ""` disables RDB snapshots (no persistence needed in tests)
- `--appendonly no` disables AOF (same reason)
- `--maxmemory 64mb` caps memory usage on the test machine

### Connection String

```
REDIS_URL=redis://localhost:6379
```

### Fixture Patterns

**Seed via script:**
```bash
#!/bin/bash
# fixtures/redis-seed.sh — run after container starts
redis-cli -h localhost SET "user:session:099" '{"userId":"user-099","role":"admin","expiresAt":9999999999}'
redis-cli -h localhost HSET "product:001" name "Widget" price "2500" stock "100"
redis-cli -h localhost LPUSH "queue:notifications" '{"type":"welcome","userId":"user-099"}'
```

**Seed programmatically in test setup:**
```typescript
beforeEach(async () => {
  await redis.flushall(); // Clean slate
  await redis.set('session:user-099', JSON.stringify({ userId: 'user-099', role: 'admin' }));
});
```

### Resetting Between Tests

```typescript
await redis.flushall(); // Wipe everything — fast and complete
```

---

## Kafka

### Compose Configuration

```yaml
kafka:
  image: apache/kafka:3.7.0
  environment:
    KAFKA_NODE_ID: 1
    KAFKA_PROCESS_ROLES: broker,controller
    KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
    KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
    KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
    KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
    KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
    KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
    KAFKA_LOG_RETENTION_MS: 60000
    CLUSTER_ID: "test-kafka-cluster-001"
  ports:
    - "9092:9092"
  healthcheck:
    test: /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
    interval: 5s
    timeout: 10s
    retries: 10
```

**Notes:**
- Uses KRaft mode (no Zookeeper) — single container
- `KAFKA_LOG_RETENTION_MS: 60000` — messages expire after 60 seconds (test cleanup)
- Replication factor = 1 (single broker, test only)
- Startup takes 10-15 seconds — healthcheck retries account for this

### Connection

```
KAFKA_BROKER=localhost:9092
```

### Topic Creation

**Via init script (run after container is healthy):**
```bash
#!/bin/bash
# fixtures/kafka-init.sh
KAFKA_BIN=/opt/kafka/bin

$KAFKA_BIN/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists \
  --topic orders --partitions 1 --replication-factor 1

$KAFKA_BIN/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists \
  --topic notifications --partitions 1 --replication-factor 1

$KAFKA_BIN/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists \
  --topic dead-letter --partitions 1 --replication-factor 1
```

**Programmatically:**
```typescript
import { Kafka } from 'kafkajs';

const kafka = new Kafka({ brokers: [process.env.KAFKA_BROKER || 'localhost:9092'] });
const admin = kafka.admin();
await admin.connect();
await admin.createTopics({
  topics: [
    { topic: 'orders', numPartitions: 1, replicationFactor: 1 },
    { topic: 'notifications', numPartitions: 1, replicationFactor: 1 },
  ],
});
await admin.disconnect();
```

### Testing Patterns

```typescript
// Produce a message and verify consumer processes it
const producer = kafka.producer();
await producer.connect();
await producer.send({
  topic: 'orders',
  messages: [{ key: 'order-001', value: JSON.stringify({ id: 'order-001', action: 'created' }) }],
});

// Wait for consumer to process (with timeout)
const result = await waitFor(() => db.query("SELECT * FROM orders WHERE id = 'order-001'"), {
  timeout: 5000,
  interval: 100,
});
expect(result.rows[0].status).toBe('processed');
```

### Resetting Between Tests

```typescript
const admin = kafka.admin();
await admin.connect();
const topics = await admin.listTopics();
await admin.deleteTopics({ topics });
// Recreate with clean state
await admin.createTopics({ topics: [{ topic: 'orders', ... }] });
```

---

## LocalStack (AWS Services)

### Compose Configuration

```yaml
localstack:
  image: localstack/localstack
  environment:
    SERVICES: s3,sqs,sns,dynamodb
    DEFAULT_REGION: us-east-1
    EAGER_SERVICE_LOADING: 1
  ports:
    - "4566:4566"
  healthcheck:
    test: >
      curl -f http://localhost:4566/_localstack/health | grep -q '"s3": "running"'
    interval: 5s
    timeout: 10s
    retries: 5
```

**Notes:**
- `SERVICES` — only list what you need. Each service adds startup time.
- `EAGER_SERVICE_LOADING: 1` — start all services immediately instead of on first request
- Single port (4566) serves all AWS APIs — the service is determined by the request path/headers
- Uses the `awslocal` CLI wrapper (installed in the container) for setup commands

### Connection

```env
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
```

The AWS SDK (v3) uses `AWS_ENDPOINT_URL` automatically when set. No code changes needed.

### S3 Setup

```bash
#!/bin/bash
# fixtures/localstack-init.sh

# Create buckets
awslocal s3 mb s3://uploads
awslocal s3 mb s3://processed

# Upload test fixtures
awslocal s3 cp fixtures/test-document.pdf s3://uploads/documents/test.pdf
```

### SQS Setup

```bash
# Create queues
awslocal sqs create-queue --queue-name order-events
awslocal sqs create-queue --queue-name order-events-dlq

# Configure dead letter queue
awslocal sqs set-queue-attributes \
  --queue-url http://localhost:4566/000000000000/order-events \
  --attributes '{
    "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-east-1:000000000000:order-events-dlq\",\"maxReceiveCount\":\"3\"}"
  }'
```

### DynamoDB Setup

```bash
awslocal dynamodb create-table \
  --table-name Sessions \
  --attribute-definitions AttributeName=sessionId,AttributeType=S \
  --key-schema AttributeName=sessionId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

### Testing Patterns

```typescript
import { S3Client, PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';

const s3 = new S3Client({
  endpoint: process.env.AWS_ENDPOINT_URL, // points to LocalStack
  region: 'us-east-1',
  credentials: { accessKeyId: 'test', secretAccessKey: 'test' },
  forcePathStyle: true, // required for LocalStack
});

it('uploads and retrieves a file', async () => {
  await s3.send(new PutObjectCommand({
    Bucket: 'uploads',
    Key: 'test.txt',
    Body: 'hello world',
  }));

  const response = await s3.send(new GetObjectCommand({
    Bucket: 'uploads',
    Key: 'test.txt',
  }));
  const body = await response.Body.transformToString();
  expect(body).toBe('hello world');
});
```

---

## Mailpit (Email Testing)

### Compose Configuration

```yaml
mailpit:
  image: axllent/mailpit
  ports:
    - "1025:1025"   # SMTP server
    - "8025:8025"   # Web UI + API
  healthcheck:
    test: wget -q --spider http://localhost:8025/api/v1/info || exit 1
    interval: 2s
    timeout: 5s
    retries: 5
```

### Connection

```env
SMTP_HOST=localhost
SMTP_PORT=1025
```

Configure your app's email transport to use these values. No authentication needed.

### Testing Patterns

```typescript
// Send email through your app (which connects to Mailpit's SMTP)
await sendWelcomeEmail({ to: 'user@example.com', name: 'Test User' });

// Wait a moment for delivery
await new Promise(resolve => setTimeout(resolve, 500));

// Query Mailpit API to verify the email was sent
const response = await fetch('http://localhost:8025/api/v1/messages');
const messages = await response.json();

expect(messages.messages).toHaveLength(1);
expect(messages.messages[0].To[0].Address).toBe('user@example.com');
expect(messages.messages[0].Subject).toContain('Welcome');

// Get full message content
const msgId = messages.messages[0].ID;
const full = await fetch(`http://localhost:8025/api/v1/message/${msgId}`).then(r => r.json());
expect(full.HTML).toContain('Test User');
```

### Resetting Between Tests

```typescript
// Delete all messages
await fetch('http://localhost:8025/api/v1/messages', { method: 'DELETE' });
```

### Mailpit API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/messages` | GET | List all messages |
| `/api/v1/messages` | DELETE | Delete all messages |
| `/api/v1/message/{id}` | GET | Get full message |
| `/api/v1/message/{id}/part/{partId}` | GET | Get attachment |
| `/api/v1/search?query={q}` | GET | Search messages |
| `/api/v1/info` | GET | Server info (healthcheck) |

Search supports: `to:user@example.com`, `from:noreply@app.com`, `subject:Welcome`, and freetext.

---

## Startup Time Reference

| Service | Image | Typical startup | With healthcheck wait |
|---|---|---|---|
| PostgreSQL | postgres:16 | 1-2s | 3-5s |
| Redis | redis:7 | <1s | 2-3s |
| Kafka (KRaft) | apache/kafka:3.7.0 | 8-12s | 15-20s |
| LocalStack (s3,sqs) | localstack/localstack | 5-10s | 10-15s |
| WireMock | wiremock/wiremock:3 | 1-2s | 3-5s |
| Mailpit | axllent/mailpit | <1s | 2-3s |

**Full stack (all services):** 15-25 seconds with `docker compose up --wait`.

**Optimization tips:**
- Pre-pull images: `docker compose pull` in CI setup step (cached across runs)
- Only include services the feature actually needs
- Use `EAGER_SERVICE_LOADING` for LocalStack to avoid lazy-start delays during tests
