import test from 'node:test';
import assert from 'node:assert/strict';
import { UploadAnalysisService } from '../src/analysis/UploadAnalysisService.js';

test('FAQ generation fills a normalized 98/100 page instead of failing the PPT', async () => {
  const service = Object.create(UploadAnalysisService.prototype);
  let sequence = 0;
  service._publishProgress = () => {};
  service._generateFaqBatch = async (_fileName, _slides, options) => {
    if (sequence < 6) {
      const start = sequence++ * 15;
      return Array.from({ length: 15 }, (_, index) => ({
        question: `问题${start + index + 1}`,
        answer: `答案${start + index + 1}`,
        sourcePage: 1,
      }));
    }
    if (sequence++ === 6) {
      return [
        ...Array.from({ length: 8 }, (_, index) => ({
          question: `问题${91 + index}`,
          answer: `答案${91 + index}`,
          sourcePage: 1,
        })),
        { question: '问题1', answer: '重复答案', sourcePage: 1 },
        { question: '问题2', answer: '重复答案', sourcePage: 1 },
      ];
    }
    assert.equal(options.count, 15);
    assert.equal(options.existingQuestions.length, 98);
    return [
      { question: '问题99', answer: '答案99', sourcePage: 1 },
      { question: '问题100', answer: '答案100', sourcePage: 1 },
      ...Array.from({ length: 13 }, (_, index) => ({
        question: `候选问题${index + 1}`,
        answer: `候选答案${index + 1}`,
        sourcePage: 1,
      })),
    ];
  };

  const job = { warnings: [], generatedFaqsByPage: {}, generatedFaqs: 0 };
  const faqs = await service._generatePageFaqs('通用测试.pptx', [{ page: 1, text: '页面原文' }], job);
  assert.equal(faqs.length, 100);
  assert.equal(job.generatedFaqsByPage['1'], 100);
});

test('FAQ generation accepts a sparse page below 100 without mechanical variants', async () => {
  const service = Object.create(UploadAnalysisService.prototype);
  let sequence = 0;
  service._publishProgress = () => {};
  service._generateFaqBatch = async () => {
    if (sequence++ > 6) return [];
    const start = Math.min(sequence - 1, 6) * 15;
    const count = sequence === 7 ? 3 : 15;
    return Array.from({ length: count }, (_, index) => ({
      question: `稀疏问题${start + index + 1}`,
      answer: `稀疏答案${start + index + 1}`,
      sourcePage: 4,
    }));
  };
  const job = { warnings: [], generatedFaqsByPage: {}, generatedFaqs: 0 };
  const faqs = await service._generatePageFaqs('稀疏页面.pptx', [{ page: 4, text: '少量原文' }], job);
  assert.equal(faqs.length, 93);
  assert.match(job.warnings.join('\n'), /保留 93 条去重后的有效FAQ/);
});
