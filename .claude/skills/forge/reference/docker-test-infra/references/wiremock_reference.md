# WireMock Stub Reference

WireMock is a Docker container that serves mock HTTP responses. You define "stubs" as JSON files — each maps a request pattern to a response. The container reads stubs from `/home/wiremock/mappings/` on startup.

## Quick Start

```yaml
# In docker-compose.test.yml
wiremock:
  image: wiremock/wiremock:3
  volumes:
    - ./wiremock:/home/wiremock
  ports:
    - "8080:8080"
```

Stubs go in `./wiremock/mappings/*.json`. Static response files go in `./wiremock/__files/`.

## Stub Structure

Every stub file is a JSON object with `request` (what to match) and `response` (what to return):

```json
{
  "id": "unique-stub-id",
  "priority": 1,
  "request": { ... },
  "response": { ... }
}
```

`priority`: Lower number = higher priority. When multiple stubs match, the highest-priority one wins. Default is 5.

## Request Matching

### URL Matching

```json
// Exact path
"request": { "urlPath": "/api/v1/users" }

// Path with regex
"request": { "urlPathPattern": "/api/v1/users/[a-f0-9-]+" }

// Full URL including query params (exact)
"request": { "url": "/api/v1/users?page=1&limit=10" }

// URL with query param pattern
"request": { "urlPattern": "/api/v1/users\\?.*page=\\d+" }
```

### Method

```json
"request": {
  "method": "POST",
  "urlPath": "/api/v1/orders"
}
```

Supported: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`.

### Query Parameters

```json
"request": {
  "urlPath": "/api/v1/search",
  "queryParameters": {
    "q": { "equalTo": "widget" },
    "page": { "matches": "\\d+" }
  }
}
```

### Headers

```json
"request": {
  "headers": {
    "Authorization": { "contains": "Bearer" },
    "Content-Type": { "equalTo": "application/json" }
  }
}
```

### Body Matching

```json
// Exact JSON body
"request": {
  "bodyPatterns": [
    { "equalToJson": { "amount": 5000, "currency": "usd" } }
  ]
}

// JSON path exists
"request": {
  "bodyPatterns": [
    { "matchesJsonPath": "$.amount" }
  ]
}

// JSON path with value
"request": {
  "bodyPatterns": [
    { "matchesJsonPath": { "expression": "$.status", "equalTo": "active" } }
  ]
}

// Regex on body
"request": {
  "bodyPatterns": [
    { "matches": ".*order_id.*" }
  ]
}
```

### String Matching Operators

These work in headers, query params, and body patterns:

| Operator | Example | Description |
|---|---|---|
| `equalTo` | `{ "equalTo": "exact value" }` | Exact match |
| `contains` | `{ "contains": "partial" }` | Substring match |
| `matches` | `{ "matches": "regex.*pattern" }` | Regex match |
| `doesNotMatch` | `{ "doesNotMatch": "exclude.*this" }` | Negative regex |
| `absent` | `true` | Header/param must not be present |

## Response Definition

### Static JSON Response

```json
"response": {
  "status": 200,
  "headers": {
    "Content-Type": "application/json"
  },
  "jsonBody": {
    "id": "ch_test_001",
    "status": "succeeded",
    "amount": 5000
  }
}
```

### Response from File

For large responses, put the body in `__files/`:

```json
"response": {
  "status": 200,
  "headers": { "Content-Type": "application/json" },
  "bodyFileName": "large-response.json"
}
```

The file goes at `wiremock/__files/large-response.json`.

### Response with Delay

```json
"response": {
  "status": 200,
  "jsonBody": { "id": "slow-response" },
  "fixedDelayMilliseconds": 2000
}
```

Or random delay:
```json
"response": {
  "delayDistribution": {
    "type": "lognormal",
    "median": 1000,
    "sigma": 0.25
  }
}
```

### Fault Responses

Simulate network failures:

```json
// Connection reset
"response": { "fault": "CONNECTION_RESET_BY_PEER" }

// Empty response (no body)
"response": { "fault": "EMPTY_RESPONSE" }

// Garbage data
"response": { "fault": "MALFORMED_RESPONSE_CHUNK" }
```

## Scenarios (Stateful Stubs)

Simulate sequences where the same endpoint returns different responses on successive calls:

```json
// First call: order is pending
{
  "scenarioName": "order-lifecycle",
  "requiredScenarioState": "Started",
  "newScenarioState": "charged",
  "request": { "method": "GET", "urlPath": "/api/v1/orders/001" },
  "response": {
    "jsonBody": { "id": "001", "status": "pending" }
  }
}

// Second call: order is completed (after charge)
{
  "scenarioName": "order-lifecycle",
  "requiredScenarioState": "charged",
  "request": { "method": "GET", "urlPath": "/api/v1/orders/001" },
  "response": {
    "jsonBody": { "id": "001", "status": "completed" }
  }
}
```

All scenarios start in state `"Started"`. Each stub can transition to a new state via `newScenarioState`.

## Response Templating

Dynamic responses using Handlebars:

```json
{
  "request": {
    "method": "POST",
    "urlPath": "/api/v1/users"
  },
  "response": {
    "status": 201,
    "headers": { "Content-Type": "application/json" },
    "jsonBody": {
      "id": "{{randomValue type='UUID'}}",
      "name": "{{jsonPath request.body '$.name'}}",
      "created_at": "{{now}}"
    },
    "transformers": ["response-template"]
  }
}
```

Template helpers:
- `{{randomValue type='UUID'}}` — random UUID
- `{{randomValue type='ALPHANUMERIC' length=10}}` — random string
- `{{now}}` — current timestamp
- `{{now offset='3 days'}}` — offset timestamp
- `{{jsonPath request.body '$.field'}}` — extract from request body
- `{{request.headers.Authorization}}` — echo request header

## Admin API

WireMock exposes an admin API at `/__admin/`:

```bash
# List all stubs
curl http://localhost:8080/__admin/mappings

# Add a stub at runtime
curl -X POST http://localhost:8080/__admin/mappings \
  -H "Content-Type: application/json" \
  -d '{"request":{"url":"/dynamic"},"response":{"status":200,"body":"added at runtime"}}'

# Reset all stubs to those on disk
curl -X POST http://localhost:8080/__admin/reset

# Get request log (what was called)
curl http://localhost:8080/__admin/requests

# Verify a specific endpoint was called
curl -X POST http://localhost:8080/__admin/requests/count \
  -d '{"method":"POST","url":"/api/v1/charges"}'

# Health check
curl http://localhost:8080/__admin/health
```

### Request Verification in Tests

After a flow test, verify WireMock received the expected calls:

```typescript
// Verify Stripe was called with correct amount
const count = await fetch('http://localhost:8080/__admin/requests/count', {
  method: 'POST',
  body: JSON.stringify({
    method: 'POST',
    urlPath: '/v1/charges',
    bodyPatterns: [{ matchesJsonPath: { expression: '$.amount', equalTo: '5000' } }]
  })
}).then(r => r.json());

expect(count.count).toBe(1);
```

## Common Stub Patterns

### Payment Gateway (Stripe-like)

```json
// Success
{
  "request": { "method": "POST", "urlPath": "/v1/charges" },
  "response": {
    "status": 200,
    "jsonBody": {
      "id": "ch_test_001",
      "object": "charge",
      "amount": "{{jsonPath request.body '$.amount'}}",
      "status": "succeeded"
    },
    "transformers": ["response-template"]
  },
  "priority": 5
}

// Declined card (higher priority — matches specific token)
{
  "request": {
    "method": "POST",
    "urlPath": "/v1/charges",
    "bodyPatterns": [
      { "matchesJsonPath": { "expression": "$.source", "equalTo": "tok_declined" } }
    ]
  },
  "response": {
    "status": 402,
    "jsonBody": {
      "error": { "type": "card_error", "code": "card_declined" }
    }
  },
  "priority": 1
}
```

### OAuth / Auth Provider

```json
// Token endpoint
{
  "request": { "method": "POST", "urlPath": "/oauth/token" },
  "response": {
    "jsonBody": {
      "access_token": "test-access-token-001",
      "token_type": "Bearer",
      "expires_in": 3600
    }
  }
}

// User info endpoint
{
  "request": {
    "method": "GET",
    "urlPath": "/userinfo",
    "headers": { "Authorization": { "equalTo": "Bearer test-access-token-001" } }
  },
  "response": {
    "jsonBody": {
      "sub": "user-099",
      "email": "test@example.com",
      "name": "Test User"
    }
  }
}
```

### Webhook Receiver

```json
// Your app sends webhooks — WireMock catches them for verification
{
  "request": {
    "method": "POST",
    "urlPath": "/webhook/order-completed"
  },
  "response": { "status": 200, "body": "OK" }
}
```

After the test, query `/__admin/requests` to verify the webhook was sent with the right payload.

### Rate-Limited API

```json
// First 3 calls succeed
{
  "scenarioName": "rate-limit",
  "requiredScenarioState": "Started",
  "newScenarioState": "call-2",
  "request": { "method": "GET", "urlPath": "/api/data" },
  "response": { "status": 200, "jsonBody": { "data": "ok" } }
}
// ... (call-2 → call-3 transitions)

// 4th call gets rate limited
{
  "scenarioName": "rate-limit",
  "requiredScenarioState": "call-3",
  "request": { "method": "GET", "urlPath": "/api/data" },
  "response": {
    "status": 429,
    "headers": { "Retry-After": "60" },
    "jsonBody": { "error": "rate_limited" }
  }
}
```

## Converting Spike Observations to Stubs

When the spike agent records a real API interaction:

1. Take the **request** — method, URL path, relevant body fields
2. Take the **response** — status code, headers, body
3. Generalize where appropriate:
   - Use `urlPathPattern` instead of `urlPath` if the path contains IDs
   - Use `matchesJsonPath` instead of `equalToJson` for flexible body matching
   - Replace real IDs in responses with deterministic test IDs (e.g., `ch_test_001`)
4. Create a second stub for the error case observed in spike
5. If the spike observed specific edge behaviors, create stubs for those too

**Rule of thumb:** One spike observation = one happy-path stub + one error stub minimum.
