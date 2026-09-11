import { qaStore } from './QaStore.js';
import { webQaCandidateStore } from './WebQaCandidateStore.js';

function normalizeExactQuestion(value) {
  return String(value || '').normalize('NFKC').toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

function isActive(item, now) {
  return item?.enabled !== false
    && (!item?.expiresAt || Date.parse(item.expiresAt) > now);
}

export function findApprovedWebQaMatch(text, options = {}) {
  const query = normalizeExactQuestion(text);
  if (!query) return null;

  const qaStoreInstance = options.qaStore || qaStore;
  const candidateStore = options.candidateStore || webQaCandidateStore;
  const now = options.now instanceof Date ? options.now.getTime() : Date.now();
  const qaItems = qaStoreInstance.list();
  const approvedQaItems = qaItems.filter(item =>
    item?.source === 'web-candidate'
    && item?.reviewStatus === 'approved'
    && isActive(item, now));

  const direct = approvedQaItems.find(item =>
    normalizeExactQuestion(item.question) === query);
  if (direct) {
    return { item: direct, score: 100, exact: true, approvedWebQa: true };
  }

  const candidate = candidateStore.list().find(item =>
    item?.status === 'PROMOTED'
    && item?.promotedQaId
    && (!item.expiresAt || Date.parse(item.expiresAt) > now)
    && [item.question, item.suggestedQuestion]
      .some(question => normalizeExactQuestion(question) === query));
  if (!candidate) return null;

  const item = approvedQaItems.find(row => row.id === candidate.promotedQaId);
  return item
    ? { item, score: 100, exact: true, approvedWebQa: true, candidateId: candidate.id }
    : null;
}

export function promoteWebQaCandidate(candidate, options = {}) {
  if (!candidate?.id) throw new Error('candidate is required');
  const question = String(options.question || candidate.suggestedQuestion || candidate.question || '').trim();
  const answer = String(options.answer || candidate.answer || '').trim();
  if (!question || !answer) throw new Error('question and answer are required');

  const qaStoreInstance = options.qaStore || qaStore;
  const candidateStore = options.candidateStore || webQaCandidateStore;
  const qaItem = qaStoreInstance.saveByQuestion({
    question,
    answer,
    keywords: [
      'web-candidate',
      String(candidate.freshnessClass || 'stable').toLowerCase(),
      String(candidate.candidateType || 'public_stable').toLowerCase(),
    ],
    source: 'web-candidate',
    sourceUrls: (candidate.sources || []).map(source => source.url).filter(Boolean),
    freshnessClass: candidate.freshnessClass,
    expiresAt: candidate.expiresAt,
    reviewStatus: 'approved',
    useCount: candidate.useCount,
    enabled: true,
  });
  const promotedCandidate = candidateStore.markPromoted(candidate.id, {
    qaId: qaItem.id,
    promotionMode: options.promotionMode || 'MANUAL',
  });
  return { qaItem, candidate: promotedCandidate };
}

export default { promoteWebQaCandidate, findApprovedWebQaMatch };
