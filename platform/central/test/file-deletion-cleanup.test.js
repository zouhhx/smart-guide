import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hall-file-delete-'));
process.env.MEDIA_DIR = path.join(root, 'media');
process.env.DATA_DIR = path.join(root, 'data');
process.env.OBSIDIAN_VAULT_PATH = path.join(root, 'vault');
process.env.VECTOR_SEARCH_ENABLED = 'false';
fs.mkdirSync(process.env.MEDIA_DIR, { recursive: true });
fs.mkdirSync(process.env.DATA_DIR, { recursive: true });
fs.mkdirSync(process.env.OBSIDIAN_VAULT_PATH, { recursive: true });

const { MediaLibrary } = await import('../src/media/MediaLibrary.js');
const { UploadAnalysisService } = await import('../src/analysis/UploadAnalysisService.js');
const { SpecialConfigStore } = await import('../src/specials/SpecialConfigStore.js');

test('special scenes default to standard commentary', () => {
  const store = new SpecialConfigStore();
  assert.equal(store._normalizeCommentaryLevel(), 'standard');
});

test('re-registering the same PPT updates one library row instead of creating a suffix copy', () => {
  const fileName = '同名更新资料.pptx';
  const library = new MediaLibrary();
  library.registerUploadedFile({
    name: fileName,
    ext: '.pptx',
    type: 'ppt',
    size: 10,
    url: `http://127.0.0.1:3001/${encodeURIComponent(fileName)}`,
    localPath: path.join(process.env.MEDIA_DIR, fileName),
  });
  library.registerUploadedFile({
    name: fileName,
    originalName: fileName,
    ext: '.pptx',
    type: 'ppt',
    size: 20,
    url: `http://127.0.0.1:3001/${encodeURIComponent(fileName)}`,
    localPath: path.join(process.env.MEDIA_DIR, fileName),
  });

  const matches = library.getAll().filter(item => item.name === fileName);
  assert.equal(matches.length, 1);
  assert.equal(matches[0].size, 20);
  library.remove(fileName);
});

test('complete file deletion removes library, analysis job and generated markdown artifacts', () => {
  const fileName = '待删除资料.pptx';
  const jobId = 'delete-job-1';
  const filePath = path.join(process.env.MEDIA_DIR, fileName);
  fs.writeFileSync(filePath, 'pptx-placeholder');

  const library = new MediaLibrary();
  library.registerUploadedFile({
    name: fileName,
    ext: '.pptx',
    type: 'ppt',
    size: fs.statSync(filePath).size,
    url: `http://127.0.0.1:3001/${encodeURIComponent(fileName)}`,
    localPath: filePath,
  });
  library.updateContentStatus(fileName, 'AI_READY', { analysisJobId: jobId });

  const service = new UploadAnalysisService();
  service.jobs = [{ id: jobId, fileName, status: 'COMPLETED', phase: 'completed' }];
  const generatedDir = path.join(process.env.OBSIDIAN_VAULT_PATH, '10-units', '上传资料', `${jobId}-待删除资料`);
  fs.mkdirSync(generatedDir, { recursive: true });
  fs.writeFileSync(path.join(generatedDir, '本地AI分析.md'), `---\nresource: "${fileName}"\n---\n`, 'utf8');

  const artifacts = service.removeFileArtifacts(fileName, jobId);
  const removed = library.remove(fileName);
  fs.rmSync(filePath, { force: true });

  assert.equal(removed.name, fileName);
  assert.equal(library.getAll().some(item => item.name === fileName), false);
  assert.equal(service.get(jobId), null);
  assert.equal(fs.existsSync(generatedDir), false);
  assert.equal(fs.existsSync(filePath), false);
  assert.equal(artifacts.removedJobs, 1);
  assert.equal(artifacts.removedDirectories.length, 1);
});

test('running analysis blocks destructive cleanup', () => {
  const service = new UploadAnalysisService();
  service.jobs = [{ id: 'running-job', fileName: '处理中.pptx', status: 'RUNNING', phase: 'analyze' }];
  assert.throws(
    () => service.removeFileArtifacts('处理中.pptx', 'running-job'),
    error => error?.code === 'FILE_ANALYSIS_RUNNING',
  );
});

test('file deletion removes only matching scene references', () => {
  const store = new SpecialConfigStore();
  const special = store.save({
    id: 'delete-reference-test',
    name: '删除引用测试',
    assignments: [
      {
        screenNumber: 1,
        fileName: '待删除资料.pptx',
        fileNames: ['待删除资料.pptx', '保留资料.pptx'],
      },
      {
        screenNumber: 2,
        fileName: '待删除资料.pptx',
        fileNames: ['待删除资料.pptx'],
      },
      {
        screenNumber: 3,
        fileName: '保留资料.pptx',
        fileNames: ['保留资料.pptx'],
      },
    ],
  });

  assert.ok(special);
  assert.equal(store.removeFileReferences('待删除资料.pptx'), 2);

  const updated = store.get('delete-reference-test');
  assert.deepEqual(updated.assignments.map(item => item.screenNumber), [1, 3]);
  assert.deepEqual(updated.assignments[0].fileNames, ['保留资料.pptx']);
  assert.equal(updated.assignments[0].fileName, '保留资料.pptx');
  assert.deepEqual(updated.assignments[1].fileNames, ['保留资料.pptx']);
});

test.after(() => {
  fs.rmSync(root, { recursive: true, force: true });
});
