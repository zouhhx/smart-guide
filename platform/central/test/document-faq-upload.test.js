import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import knowledgeDb from '../src/knowledge/KnowledgeDb.js';
import { MediaLibrary, mediaLibrary } from '../src/media/MediaLibrary.js';
import messageRouter from '../src/router/MessageRouter.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const analysisSource = fs.readFileSync(path.join(root, 'src', 'analysis', 'UploadAnalysisService.js'), 'utf8');
const fileServerSource = fs.readFileSync(path.join(root, 'src', 'files', 'FileServer.js'), 'utf8');
const knowledgeSource = fs.readFileSync(path.join(root, 'src', 'knowledge', 'KnowledgeDb.js'), 'utf8');
const routerSource = fs.readFileSync(path.join(root, 'src', 'router', 'MessageRouter.js'), 'utf8');
const adminBundle = fs.readFileSync(path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v14.js'), 'utf8');

test('PDF and DOCX use a FAQ-only upload and analysis path', () => {
  assert.match(fileServerSource, /\['\.pptx', '\.pdf', '\.docx'\]\.includes\(ext\)/);
  assert.match(fileServerSource, /clientDisplay: !faqOnly/);
  assert.match(fileServerSource, /req\.url !== '\/list' \|\| isClientMediaAvailable\(file\)/);
  assert.match(analysisSource, /SUPPORTED_EXTENSIONS = new Set\(\['\.pptx', '\.pdf', '\.docx'\]\)/);
  assert.match(analysisSource, /FAQ_ONLY_EXTENSIONS = new Set\(\['\.pdf', '\.docx'\]\)/);
  assert.match(analysisSource, /_generateDocumentFaqs/);
  assert.match(analysisSource, /'type: faq_bank'/);
  assert.match(analysisSource, /if \(!faqOnly\) \{\s*mediaLibrary\.updateCommentary/s);
  assert.match(knowledgeSource, /resources\.filter\(item => item\.faqOnly !== true\)/);
  assert.match(routerSource, /mediaLibrary\.getAll\(\)\.filter\(isClientMediaAvailable\)/);
  assert.match(fileServerSource, /req\.url !== '\/list' \|\| isClientMediaAvailable\(file\)/);
  assert.match(adminBundle, /\.pptx,\.pdf,\.docx/);
  assert.match(adminBundle, /PDF、Word只提取正文并生成FAQ/);
});

test('FAQ-only documents remain manageable but are excluded from media search', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'faq-document-library-'));
  const library = new MediaLibrary();
  library.libraryPath = path.join(temp, 'library.json');
  library._scanDisk = () => [];
  library._existsInMediaDir = () => true;
  try {
    const saved = library.registerUploadedFile({
      name: '制度资料.pdf',
      ext: '.pdf',
      type: 'document',
      size: 123,
      url: '/media/%E5%88%B6%E5%BA%A6%E8%B5%84%E6%96%99.pdf',
      localPath: 'C:\\media\\制度资料.pdf',
      faqOnly: true,
      clientDisplay: false,
    });
    assert.equal(saved.type, 'document');
    assert.equal(saved.faqOnly, true);
    assert.equal(saved.clientDisplay, false);
    assert.equal(library.getAll().length, 1);
    assert.deepEqual(library.search('制度资料'), []);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('PPT media stays hidden from clients until review is published', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'review-media-library-'));
  const library = new MediaLibrary();
  library.libraryPath = path.join(temp, 'library.json');
  library._scanDisk = () => [];
  library._existsInMediaDir = () => true;
  try {
    const saved = library.registerUploadedFile({
      name: '????.pptx',
      ext: '.pptx',
      type: 'ppt',
      size: 123,
      url: '/media/pending.pptx',
      localPath: 'D:\\media\\pending.pptx',
      clientDisplay: true,
    });
    assert.equal(saved.publicationStatus, 'PROCESSING');
    assert.deepEqual(library.search('????'), []);

    library.updateContentStatus(saved.name, 'PENDING_REVIEW', {
      status: 'reviewing',
      publicationStatus: 'PENDING_REVIEW',
      reviewStatus: 'PENDING',
    });
    assert.deepEqual(library.search('???????ppt'), []);
    assert.equal(library.getAll().length, 1);

    library.updateContentStatus(saved.name, 'AI_READY', {
      status: 'published',
      publicationStatus: 'PUBLISHED',
      reviewStatus: 'APPROVED',
    });
    assert.deepEqual(
      library.search('???????ppt').map(item => item.name),
      ['????.pptx'],
    );
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('FAQ-only document answers stay inactive until review is published', () => {
  const previousIndex = knowledgeDb.index;
  const previousLoaded = knowledgeDb._loaded;
  knowledgeDb.index = {
    ...knowledgeDb._emptyIndex(),
    qaItems: [
      { id: 'published', question: '智慧园区如何运营', answer: '已发布答案', status: 'published', enabled: true },
      { id: 'reviewing', question: '待审核制度是什么', answer: '待审核答案', status: 'reviewing' },
      { id: 'disabled', question: '停用制度是什么', answer: '停用答案', status: 'published', enabled: false },
    ],
  };
  knowledgeDb._loaded = true;
  try {
    assert.equal(knowledgeDb.findAnswerCandidates('智慧园区如何运营').length, 1);
    assert.deepEqual(knowledgeDb.findAnswerCandidates('待审核制度是什么'), []);
    assert.deepEqual(knowledgeDb.findAnswerCandidates('停用制度是什么'), []);
    const vectorIds = knowledgeDb.getVectorDocuments().map(item => item.metadata.item_id);
    assert.deepEqual(vectorIds, ['published']);
  } finally {
    knowledgeDb.index = previousIndex;
    knowledgeDb._loaded = previousLoaded;
  }
});

test('DOCX extractor reads text without third-party Python packages', (t) => {
  const candidates = [
    process.env.PPT_ANALYSIS_PYTHON,
    'C:\\Users\\64930\\AppData\\Local\\Programs\\Python\\Python314\\python.exe',
    'C:\\Program Files\\Python312\\python.exe',
  ].filter(Boolean);
  const python = candidates.find(candidate => fs.existsSync(candidate));
  if (!python) return t.skip('Python runtime not available');

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'faq-docx-extract-'));
  const target = path.join(temp, 'sample.docx');
  const createDocx = [
    'import sys,zipfile',
    'p=sys.argv[1]',
    'xml="""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>智慧园区支持统一运营。</w:t></w:r></w:p><w:p><w:r><w:t>平台覆盖安全、能耗和设备管理。</w:t></w:r></w:p></w:body></w:document>"""',
    'z=zipfile.ZipFile(p,"w");z.writestr("word/document.xml",xml);z.close()',
  ].join(';');
  const pythonOptions = {
    encoding: 'utf8',
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  };
  try {
    const created = spawnSync(python, ['-c', createDocx, target], pythonOptions);
    assert.equal(created.status, 0, created.stderr);
    const extracted = spawnSync(python, [path.join(root, 'scripts', 'extract-document-text.py'), target], pythonOptions);
    assert.equal(extracted.status, 0, extracted.stderr);
    const parsed = JSON.parse(extracted.stdout.replace(/^\uFEFF/, ''));
    assert.equal(parsed.units.length, 1);
    assert.match(parsed.units[0].text, /智慧园区支持统一运营/);
    assert.match(parsed.units[0].text, /安全、能耗和设备管理/);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('pending-review topics are blocked before web and model fallbacks', () => {
  const originalGetAll = mediaLibrary.getAll;
  mediaLibrary.getAll = () => [{
    name: '党政-政务智能客服.pptx',
    title: '党政-政务智能客服',
    status: 'reviewing',
    publicationStatus: 'PENDING_REVIEW',
    reviewStatus: 'PENDING',
  }];
  try {
    const matched = messageRouter._pendingReviewTopicFor('政务智能客服的主要能力是什么？');
    assert.equal(matched?.item.name, '党政-政务智能客服.pptx');
    assert.equal(messageRouter._pendingReviewTopicFor('智能客服是什么？'), null);
    assert.match(routerSource, /pendingReviewResult[\s\S]*?_tryHandleApprovedWebQaAnswer/);
  } finally {
    mediaLibrary.getAll = originalGetAll;
  }
});

test('generation prompts follow the approved commentary and FAQ contract', () => {
  assert.match(analysisSource, /(?:精炼版|简短版)\(30-60字\).*标准版\(100-180字\).*详细版\(360-600字\)/s);
  assert.match(analysisSource, /客户问题→中国移动能力→场景价值→下一步交流/);
  assert.match(analysisSource, /handoffRequired/);
  assert.match(analysisSource, /sourceId/);
  assert.match(analysisSource, /每个问题不超过24个中文字符/);
  assert.match(analysisSource, /问题必须有明确主语/);
  assert.match(analysisSource, /禁止使用“这个文件、该材料、本页、它说明了什么”/);
  assert.match(analysisSource, /答案要比问题丰富/);
  assert.match(routerSource, /return 'standard';/);
});

test('convertible FAQ metadata remains separate from the customer-facing answer', () => {
  const rows = knowledgeDb._extractConvertibleFaqs([
    '## 可转FAQ',
    '',
    '1. 问：如何确保准确性？',
    '   答：通过审核后的知识回答。',
    '   来源页：2',
    '   意图：capability',
    '   需专家承接：否',
    '   来源标识：demo.pptx#page-2',
  ].join('\n'));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].answer, '通过审核后的知识回答。');
  assert.equal(rows[0].intent, 'capability');
  assert.equal(rows[0].sourceId, 'demo.pptx#page-2');
});
