import { usageStore } from './src/usage/UsageStore.js';
import { fileServer } from './src/files/FileServer.js';

usageStore.recordInteraction({
  seq: 1,
  source: 'asr-apk',
  target: 'all',
  question: '用户想了解多模态大模型',
  answer: '多模态大模型很受关注',
  actions: [{ type: 'show_qa', target: 'all' }],
});

fileServer.start();

setTimeout(async () => {
  try {
    const response = await fetch('http://127.0.0.1:3993/api/usage-stats');
    const data = await response.json();
    console.log(JSON.stringify({
      ok: data.ok,
      total: data.summary?.totalQuestions,
      top: data.summary?.topKeywords?.slice(0, 3),
    }));
    fileServer.stop();
    process.exit(0);
  } catch (err) {
    console.error(err);
    fileServer.stop();
    process.exit(1);
  }
}, 300);
