import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  assessKnowledgeEntityConsistency,
  extractExplicitEntityAnchors,
} from '../src/knowledge/KnowledgeEntityConsistencyPolicy.js';

const routerSource = fs.readFileSync(new URL('../src/router/MessageRouter.js', import.meta.url), 'utf8');

test('extracts explicit museum and organization anchors without entity-specific lists', () => {
  assert.deepEqual(
    extractExplicitEntityAnchors('黄陂博物馆的AI馆员采用了哪些地方文化IP？')
      .map(item => item.label),
    ['黄陂博物馆'],
  );
  assert.deepEqual(
    extractExplicitEntityAnchors('请问首都博物馆AI智慧导览有哪些功能？')
      .map(item => item.label),
    ['首都博物馆'],
  );
  assert.deepEqual(
    extractExplicitEntityAnchors('海岳科技有限公司提供哪些服务？')
      .map(item => item.label),
    ['海岳科技有限公司'],
  );
});

test('blocks a generic Chroma FAQ that omits the explicit query entity', () => {
  const result = assessKnowledgeEntityConsistency(
    '黄陂博物馆的AI馆员采用了哪些地方文化IP，能提供什么服务？',
    {
      question: 'AI+主要讲什么？',
      answer: 'AI+博物馆观众服务，包括智能导览和实时互动答疑。',
      tags: ['AI+', '博物馆观众服务'],
    },
  );
  assert.equal(result.applicable, true);
  assert.equal(result.allowed, false);
  assert.equal(result.status, 'ENTITY_MISSING_FROM_CANDIDATE');
  assert.deepEqual(result.missingEntities, ['黄陂博物馆']);
});

test('keeps FAQ candidates that explicitly cover the same entity', () => {
  const result = assessKnowledgeEntityConsistency(
    '首都博物馆AI智慧导览系统提供哪些主要功能？',
    {
      question: '首都博物馆的AI智慧导览有哪些功能？',
      answer: '首都博物馆提供路线规划和展品讲解。',
    },
  );
  assert.equal(result.allowed, true);
  assert.equal(result.status, 'ENTITY_MATCH');
});

test('accepts a full entity declared by candidate metadata for abbreviation content', () => {
  const result = assessKnowledgeEntityConsistency(
    '首都博物馆AI智慧导览系统提供哪些主要功能？',
    {
      question: '首博智慧导览有哪些功能？',
      answer: '首博提供路线规划和展品讲解。',
      raw_json: {
        vector: {
          metadata: {
            entity_aliases: '首都博物馆、首博',
          },
        },
      },
    },
  );
  assert.equal(result.allowed, true);
});

test('explicit PPT subject must be present in the candidate source', () => {
  const blocked = assessKnowledgeEntityConsistency(
    '请介绍《星河智慧工厂》PPT中的核心能力。',
    {
      question: '这份方案有哪些能力？',
      answer: '包括模型训练和智能问答。',
      sourceFile: '另一份人工智能方案.pptx',
    },
  );
  assert.equal(blocked.applicable, true);
  assert.equal(blocked.allowed, false);
  assert.deepEqual(blocked.missingEntities, ['星河智慧工厂']);

  const allowed = assessKnowledgeEntityConsistency(
    '请介绍星河智慧工厂PPT中的核心能力。',
    {
      question: '这份方案有哪些能力？',
      answer: '包括视觉质检和产线协同。',
      metadata: { source_file: '星河智慧工厂.pptx' },
    },
  );
  assert.equal(allowed.applicable, true);
  assert.equal(allowed.allowed, true);
});

test('does not gate generic or abbreviation-only questions without a high-confidence entity', () => {
  assert.equal(
    assessKnowledgeEntityConsistency('博物馆AI导览一般有哪些功能？', {
      question: 'AI导览有哪些功能？',
      answer: '提供路线规划。',
    }).applicable,
    false,
  );
  assert.equal(
    assessKnowledgeEntityConsistency('首博有哪些智慧导览功能？', {
      question: 'AI导览有哪些功能？',
      answer: '提供路线规划。',
    }).applicable,
    false,
  );
});

test('manual QA lookup remains outside the semantic candidate entity filter', () => {
  assert.match(
    routerSource,
    /const manualMatch = this\._isAllowedQaCandidateForQuestion\(text, rawManualMatch\)/,
  );
  assert.match(routerSource, /assessKnowledgeEntityConsistency\(question, candidate\?\.item\)/);
});

test('RAG snippets use the same entity policy and are allowed to abstain', () => {
  assert.match(routerSource, /_filterKnowledgeSnippetsForEntity\(effectiveRetrievalText, rawVectorSnippets\)/);
  assert.match(routerSource, /RAG snippet blocked by entity consistency/);
  assert.doesNotMatch(routerSource, /必须给出可展示回答，不能说不知道/);
});

test('an explicit entity with no matching local evidence records RAG miss and continues to direct LLM', () => {
  assert.match(routerSource, /handler: 'local_knowledge_entity_gate'/);
  assert.match(routerSource, /status: 'RAG_MISS_CONTINUE'/);
  assert.match(routerSource, /缺少实体证据，继续普通模型回答/);
  assert.doesNotMatch(routerSource, /localKnowledgeStatus: 'ABSTAINED'/);
});
