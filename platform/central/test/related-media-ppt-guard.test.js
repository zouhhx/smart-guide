import test from 'node:test';
import assert from 'node:assert/strict';
import messageRouter from '../src/router/MessageRouter.js';

function qaResult() {
  return {
    reply: 'The current slide answer.',
    actions: [{
      type: 'show_qa',
      target: 2,
      params: {
        question: 'What does this slide explain?',
        answer: 'The current slide answer.',
      },
    }],
  };
}

test('current PPT QA keeps the QA action and does not open related media', async () => {
  const router = messageRouter;
  const result = qaResult();
  let searchCount = 0;

  router._currentPptStates.clear();
  router._mediaResultSets.clear();
  router._currentPptStates.set(2, {
    fileName: 'current-slide.pptx',
    slideNumber: 1,
    updatedAt: Date.now(),
  });
  router._searchMediaWithVector = async () => {
    searchCount += 1;
    return { items: [{ id: 'unexpected-file' }] };
  };

  const actual = await router._attachRelatedMediaToAnswerResult(result, {
    question: 'What does this slide explain?',
    target: 2,
    source: 'asr-apk',
  });

  assert.strictEqual(actual, result);
  assert.equal(searchCount, 0);
  assert.equal(actual.actions[0].type, 'show_qa');
});

test('QA outside PPT mode can still include related media', async () => {
  const router = messageRouter;
  router._currentPptStates.clear();
  router._mediaResultSets.clear();
  router._searchMediaWithVector = async () => ({
    usedVector: true,
    items: [{
      id: 'related-file',
      title: 'Related material',
      name: 'related.pptx',
      type: 'ppt',
      ext: '.pptx',
      url: '/media/related.pptx',
      localPath: 'D:\\media\\related.pptx',
    }],
  });

  const actual = await router._attachRelatedMediaToAnswerResult(qaResult(), {
    question: 'What does this slide explain?',
    target: 2,
    source: 'asr-apk',
  });

  assert.equal(actual.actions[0].type, 'show_media_results');
  assert.equal(actual.context.relatedMedia, 1);
});
