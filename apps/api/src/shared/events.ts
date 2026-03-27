import { Kafka, type Producer } from 'kafkajs';
import { getConfig } from '../config.js';

interface DomainEvent<TPayload extends Record<string, unknown>> {
  type: string;
  tenantId: string;
  resourceId: string;
  payload: TPayload;
  occurredAt: string;
}

let producerPromise: Promise<Producer> | null = null;

async function getProducer(): Promise<Producer> {
  if (producerPromise) {
    return producerPromise;
  }

  const config = getConfig();
  const kafka = new Kafka({
    brokers: config.KAFKA_BROKERS.split(',').map((broker) => broker.trim()).filter(Boolean),
    clientId: 'wealth-advisor-api',
  });

  producerPromise = (async () => {
    const producer = kafka.producer();
    await producer.connect();
    return producer;
  })();

  return producerPromise;
}

export async function publishDomainEvent<TPayload extends Record<string, unknown>>(
  event: DomainEvent<TPayload>,
): Promise<void> {
  if (process.env.NODE_ENV === 'test') {
    return;
  }

  try {
    const producer = await getProducer();
    await producer.send({
      topic: event.type,
      messages: [
        {
          key: `${event.tenantId}:${event.resourceId}`,
          value: JSON.stringify(event),
        },
      ],
    });
  } catch (error) {
    console.error('Failed to publish domain event', event.type, error);
  }
}
