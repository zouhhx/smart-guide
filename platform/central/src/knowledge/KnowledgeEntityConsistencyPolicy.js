const ENTITY_SUFFIXES = [
  '博物馆',
  '博物院',
  '纪念馆',
  '科技馆',
  '美术馆',
  '文化馆',
  '展览馆',
  '研究院',
  '研究所',
  '委员会',
  '管理局',
  '基金会',
  '有限公司',
  '股份公司',
  '公司',
  '集团',
  '大学',
  '学院',
  '医院',
  '政府',
  '银行',
  '协会',
];

const LEADING_NOISE = /^(?:(?:请问|想问|想了解|请介绍|介绍一下|介绍下|帮我查一下|帮我查|查询一下|查询|关于|有关|截至目前|截至现在|当前|现在|目前|今年|明年|去年|有哪些|哪个|哪家|什么|一家|这家|该家|当地|本地|国内|全国)(?:的)?)+/;
const GENERIC_PREFIXES = new Set([
  '一个',
  '一家',
  '这家',
  '该家',
  '哪些',
  '哪个',
  '哪家',
  '什么',
  '当地',
  '本地',
  '国内',
  '全国',
  '智慧',
  '数字',
  '智能',
]);

function normalize(value) {
  return String(value || '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】+\-_/]/g, '');
}

function splitMetadataList(value) {
  if (Array.isArray(value)) return value.flatMap(splitMetadataList);
  return String(value || '')
    .split(/[、,，|;/；\n]/)
    .map(item => item.trim())
    .filter(Boolean);
}

function suffixOf(value) {
  return ENTITY_SUFFIXES.find(suffix => value.endsWith(suffix)) || '';
}

export function extractExplicitEntityAnchors(value) {
  const text = String(value || '').normalize('NFKC');
  if (!text) return [];
  const suffixSource = ENTITY_SUFFIXES
    .sort((left, right) => right.length - left.length)
    .join('|');
  const pattern = new RegExp(`([\\p{Script=Han}A-Za-z0-9·]{2,36}?(?:${suffixSource}))`, 'gu');
  const anchors = [];
  const seen = new Set();

  for (const match of text.matchAll(pattern)) {
    let label = String(match[1] || '').replace(LEADING_NOISE, '');
    const suffix = suffixOf(label);
    if (!suffix) continue;
    const prefix = label.slice(0, -suffix.length);
    if (prefix.length < 2 || GENERIC_PREFIXES.has(prefix)) continue;
    const canonical = normalize(label);
    if (!canonical || seen.has(canonical)) continue;
    seen.add(canonical);
    anchors.push({
      label,
      canonical,
      suffix,
      confidence: 'HIGH',
    });
  }
  return anchors;
}

function extractExplicitDocumentAnchors(value) {
  const text = String(value || '').normalize('NFKC');
  if (!text) return [];
  const labels = [];
  for (const match of text.matchAll(/《([^》]{2,60})》/gu)) labels.push(match[1]);
  for (const match of text.matchAll(/([\p{Script=Han}A-Za-z0-9·+&_-]{2,60}?)(?:的)?(?:\.pptx?|PPT|演示文稿)/giu)) {
    labels.push(match[1]);
  }

  const anchors = [];
  const seen = new Set();
  for (const rawLabel of labels) {
    const label = String(rawLabel || '').replace(LEADING_NOISE, '').trim();
    const canonical = normalize(label);
    if (canonical.length < 2 || seen.has(canonical)) continue;
    seen.add(canonical);
    anchors.push({
      label,
      canonical,
      suffix: '',
      confidence: 'HIGH',
    });
  }
  return anchors;
}

function extractKnowledgeScopeAnchors(value) {
  const anchors = [...extractExplicitEntityAnchors(value), ...extractExplicitDocumentAnchors(value)];
  return anchors.filter((anchor, index) => anchors.findIndex(item => item.canonical === anchor.canonical) === index);
}

function candidateEvidenceText(item = {}) {
  const vectorMetadata = item.raw_json?.vector?.metadata || {};
  const metadata = item.metadata || {};
  const values = [
    item.question,
    item.title,
    item.answer,
    item.summary,
    item.excerpt,
    item.content,
    item.file,
    item.resourceId,
    item.resource_id,
    item.sourceFile,
    item.source_file,
    item.fileName,
    item.file_name,
    ...(item.tags || []),
    ...(item.keywords || []),
    vectorMetadata.question,
    vectorMetadata.title,
    vectorMetadata.answer,
    vectorMetadata.resource_id,
    vectorMetadata.sourceFile,
    vectorMetadata.source_file,
    vectorMetadata.fileName,
    vectorMetadata.file_name,
    metadata.resource_id,
    metadata.sourceFile,
    metadata.source_file,
    metadata.fileName,
    metadata.file_name,
    ...splitMetadataList(vectorMetadata.tags),
    ...splitMetadataList(vectorMetadata.entity_aliases),
    ...splitMetadataList(vectorMetadata.entity_names),
    ...splitMetadataList(metadata.entity_aliases),
    ...splitMetadataList(metadata.entity_names),
    ...splitMetadataList(item.entityAliases),
    ...splitMetadataList(item.entityNames),
  ];
  return normalize(values.filter(Boolean).join(' '));
}

export function assessKnowledgeEntityConsistency(question, item = {}) {
  const anchors = extractKnowledgeScopeAnchors(question);
  if (!anchors.length) {
    return {
      applicable: false,
      allowed: true,
      status: 'NO_EXPLICIT_ENTITY',
      queryEntities: [],
      missingEntities: [],
    };
  }

  const evidence = candidateEvidenceText(item);
  const missing = anchors.filter(anchor => !evidence.includes(anchor.canonical));
  return {
    applicable: true,
    allowed: missing.length === 0,
    status: missing.length ? 'ENTITY_MISSING_FROM_CANDIDATE' : 'ENTITY_MATCH',
    queryEntities: anchors.map(anchor => anchor.label),
    missingEntities: missing.map(anchor => anchor.label),
  };
}

export default {
  extractExplicitEntityAnchors,
  assessKnowledgeEntityConsistency,
};
