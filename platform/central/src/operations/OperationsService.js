import { config } from '../config/index.js';
import { OPERATIONS_CAPABILITIES, OPERATIONS_CONTRACT_VERSION } from './OperationsContract.js';
import { OperationsGateway } from './OperationsGateway.js';
import { OperationsIdempotencyStore } from './OperationsIdempotencyStore.js';
import { LegacyOperationsAdapter, MEDIA_COMMANDS, SCREEN_COMMANDS } from './LegacyOperationsAdapter.js';

export class OperationsService {
  constructor(options = {}) {
    this.settings = options.settings || config.OPERATIONS_GATEWAY;
    this.adapter = options.adapter || new LegacyOperationsAdapter(options.dependencies);
    this.idempotencyStore = options.idempotencyStore || new OperationsIdempotencyStore({
      filePath: this.settings.idempotencyFile,
      maxEntries: 1000,
    });
    this.gateway = options.gateway || new OperationsGateway({
      allowedCapabilities: this.settings.allowedCapabilities,
      idempotencyStore: this.idempotencyStore,
    });
    for (const capability of OPERATIONS_CAPABILITIES) {
      this.gateway.register(capability, this.adapter.handler(capability));
    }
  }

  describe() {
    return {
      ok: true,
      version: OPERATIONS_CONTRACT_VERSION,
      enabled: this.settings.enabled === true,
      ready: this.settings.enabled === true && Boolean(this.settings.token),
      capabilities: OPERATIONS_CAPABILITIES.map(name => ({
        name,
        enabled: this.settings.allowedCapabilities.includes(name),
      })),
      commandWhitelist: {
        screen: SCREEN_COMMANDS,
        media: MEDIA_COMMANDS,
      },
      allowedCallers: [...this.settings.allowedCallers],
      endpoint: '/api/operations/v1/execute',
    };
  }

  async execute(input, callerId) {
    return this.gateway.execute({
      ...input,
      actor: { id: callerId, type: 'rag-system' },
    }, {
      defaults: {
        actorId: callerId,
        actorType: 'rag-system',
        timeoutMs: this.settings.timeoutMs,
      },
    });
  }
}

export const operationsService = new OperationsService();
export default operationsService;
