export class ReviewOperationRegistry {
  constructor() {
    this.operations = new Map();
  }

  run(key, operationFactory) {
    const operationKey = String(key || '');
    const runningOperation = this.operations.get(operationKey);
    if (runningOperation) return runningOperation;

    const operation = Promise.resolve().then(operationFactory);
    this.operations.set(operationKey, operation);
    const cleanup = () => {
      if (this.operations.get(operationKey) === operation) {
        this.operations.delete(operationKey);
      }
    };
    operation.then(cleanup, cleanup);
    return operation;
  }
}

export default ReviewOperationRegistry;
