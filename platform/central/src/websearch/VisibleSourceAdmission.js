const ENTITY_SUFFIX = /([\u4e00-\u9fffA-Za-z0-9·]{2,28}?(?:博物馆|纪念馆|科技馆|美术馆|公司|集团|医院|研究院|交易所|地铁|城际|铁路))/g;
const EVENT_RELATION = /(?:上市|发布|上线|推出|开通|通车|投产|交付|获批|批准|生效|实施|落地|并购|收购|融资|发售|上映)/i;
const QUERY_PREFIX = /^(?:请问|想问|帮我查一下|查询一下|查一下|截至[^，。！？?]{0,20})/i;
const GENERIC_ENTITY_ONLY = new Set([
  '博物馆', '纪念馆', '科技馆', '美术馆', '公司', '集团',
  '医院', '研究院', '交易所', '地铁', '城际', '铁路',
]);

function normalize(value) {
  return String(value || '').normalize('NFKC').toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '');
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

export function extractExplicitWebEntity(question) {
  const text = String(question || '').replace(QUERY_PREFIX, '');
  const matches = [...text.matchAll(ENTITY_SUFFIX)].map(match => match[1]
    .replace(/^(?:现在的?|当前的?|目前的?|近期的?|最近的?|最新的?|今年的?|本年的?|20\d{2}年的?)/i, '')
    .trim());
  if (matches.length) {
    return matches.sort((left, right) => right.length - left.length)[0];
  }

  const relation = text.match(EVENT_RELATION);
  if (!relation || relation.index == null) return '';
  const subject = text.slice(0, relation.index)
    .replace(/(?:现在|当前|目前|近期|最近|最新|今年|本年|20\d{2}年?)/gi, '')
    .replace(/(?:是否|有没有|有无|何时|什么时候|哪天|具体时间|进展|状态)+$/i, '')
    .replace(/[，。！？、,.!?;；:"'“”‘’（）()[\]{}<>《》【】]/g, '')
    .trim();
  // 国际机构和会议名称经常在外文来源中只出现英文名；在没有显式别名表时，
  // 不以中文全称做硬过滤，避免把本来相关的权威页面全部隐藏。
  if (/(?:委员会|大会|峰会|会议)/.test(subject)) return '';
  return subject;
}

function entityAliases(entity) {
  const value = normalize(entity);
  if (!value) return [];
  const aliases = [value];
  const suffix = [...GENERIC_ENTITY_ONLY].find(item => value.endsWith(item));
  if (suffix) {
    const stem = value.slice(0, -suffix.length);
    if (stem.length >= 2) aliases.push(`${stem}|${suffix}`);
  } else if (value.length >= 6) {
    const shortened = value.slice(2);
    if (shortened.length >= 4 && !GENERIC_ENTITY_ONLY.has(shortened)) aliases.push(shortened);
  }
  return unique(aliases);
}

function matchesEntity(text, aliases) {
  return aliases.some(alias => {
    if (!alias.includes('|')) return text.includes(alias);
    const [stem, suffix] = alias.split('|');
    return text.includes(stem) && text.includes(suffix);
  });
}

function relationRelevant(question, sourceText) {
  const relation = String(question || '').match(EVENT_RELATION)?.[0] || '';
  if (!relation) return true;
  const lifecycleWords = {
    上市: /(?:上市|ipo|首次公开募股|交易所|辅导备案|招股书|挂牌)/i,
    发布: /(?:发布|推出|亮相|宣布|发布会)/i,
    上线: /(?:上线|推出|启用|投入使用|开放|服务|功能)/i,
    推出: /(?:推出|发布|上线|提供|服务|功能)/i,
    开通: /(?:开通|通车|运营|试运行)/i,
    通车: /(?:通车|开通|运营|试运行)/i,
    投产: /(?:投产|量产|生产线|产能)/i,
    交付: /(?:交付|交货|投入使用)/i,
    获批: /(?:获批|批准|批复|许可)/i,
    批准: /(?:获批|批准|批复|许可)/i,
    生效: /(?:生效|实施|施行)/i,
    实施: /(?:生效|实施|施行)/i,
  };
  return (lifecycleWords[relation] || new RegExp(relation, 'i')).test(sourceText);
}

export function admitVisibleSources(question, sources = []) {
  const entity = extractExplicitWebEntity(question);
  if (!entity) {
    return {
      entity: '',
      admitted: sources,
      rejected: [],
    };
  }

  const aliases = entityAliases(entity);
  const admitted = [];
  const rejected = [];
  for (const source of sources) {
    const sourceText = normalize(`${source?.title || ''} ${source?.snippet || ''} ${source?.content || ''}`);
    let reason = '';
    if (!matchesEntity(sourceText, aliases)) reason = 'ENTITY_MISMATCH';
    else if (source?.temporalStatus === 'OTHER_YEAR') reason = 'TIME_SCOPE_MISMATCH';
    else if (!relationRelevant(question, sourceText)) reason = 'RELATION_MISMATCH';

    if (reason) rejected.push({ source, reason });
    else admitted.push(source);
  }
  return { entity, admitted, rejected };
}

export default { extractExplicitWebEntity, admitVisibleSources };
