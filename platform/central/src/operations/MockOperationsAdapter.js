export class MockOperationsAdapter {
  constructor(options = {}) {
    this.calls = [];
    this.responses = new Map(Object.entries(options.responses || {}));
  }

  handler(capability) {
    return async request => {
      this.calls.push({ ...request, params: { ...request.params } });
      const configured = this.responses.get(capability);
      if (configured instanceof Error) throw configured;
      if (typeof configured === 'function') return configured(request);
      return configured || {
        status: 'SUCCEEDED',
        code: 'MOCK_OK',
        message: `${capability} accepted by mock adapter`,
      };
    };
  }
}

export default MockOperationsAdapter;
