import test from 'node:test';
import assert from 'node:assert/strict';
import { webSearchService } from '../src/websearch/WebSearchService.js';

function claim(evidenceId = 'E1') {
  return [{
    claim_id: 'C1',
    claim: '已有资料支持核心结论。',
    evidence_ids: [evidenceId],
  }];
}

test('does not append a museum-specific warning already expressed in the answer', () => {
  const reply = webSearchService._renderSimpleAnswer(
    '根据近期公开信息',
    '六合博物馆上线了三类智慧观展服务。资料中提及服务在试运行期间免费体验，但未明确说明2026年后续是否持续提供或更新了其他服务。',
    claim(),
    ['资料未明确说明2026年后续是否持续提供或更新了其他AI智慧观展服务。'],
  );
  assert.doesNotMatch(reply, /提示：/);
  assert.equal((reply.match(/未明确说明/g) || []).length, 1);
});

test('removes equivalent uncertainty paraphrases across unrelated domains', () => {
  const cases = [{
    answer: '该产品已经正式发布，但尚未公布具体售价。',
    uncertainty: '目前没有明确公布该产品的具体售价。',
  }, {
    answer: '政策自7月1日起施行，不过配套细则仍有待确认。',
    uncertainty: '该政策的配套实施细则仍待确认。',
  }, {
    answer: '球队已经晋级决赛，但决赛对手尚未确定。',
    uncertainty: '目前尚未明确决赛对手。',
  }];

  for (const item of cases) {
    const reply = webSearchService._renderSimpleAnswer('根据公开信息', item.answer, claim(), [item.uncertainty]);
    assert.doesNotMatch(reply, /提示：/, `${item.answer} / ${item.uncertainty}`);
  }
});

test('keeps a genuinely new caveat that is not present in the answer', () => {
  const cases = [{
    answer: '大会举办日期为2026年8月10日。',
    uncertainty: '举办地点尚未得到官方确认。',
  }, {
    answer: '该产品已经正式发布。',
    uncertainty: '该产品的具体售价尚未公布。',
  }, {
    answer: '球队已经晋级决赛。',
    uncertainty: '决赛开球时间仍有待确认。',
  }];

  for (const item of cases) {
    const reply = webSearchService._renderSimpleAnswer('根据公开信息', item.answer, claim(), [item.uncertainty]);
    assert.match(reply, new RegExp(`提示：${item.uncertainty.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
  }
});

test('legacy warning renderer applies the same generic deduplication', () => {
  const reply = webSearchService._renderAnswered(
    '',
    '项目已获批，但开工日期尚未公布。',
    claim(),
    { decision: 'PASS_WITH_WARNING' },
    ['目前没有公布项目的开工日期。'],
  );
  assert.doesNotMatch(reply, /提示：/);
});
