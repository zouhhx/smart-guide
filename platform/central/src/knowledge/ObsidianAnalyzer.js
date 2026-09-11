import fs from 'fs';
import path from 'path';
import { config } from '../config/index.js';
import { knowledgeDb } from './KnowledgeDb.js';

function text(value) {
  return String(value ?? '').trim();
}

function cleanScalar(value) {
  return String(value ?? '').trim().replace(/^['"]|['"]$/g, '');
}

function parseFrontmatter(markdown) {
  const source = String(markdown || '').replace(/^\uFEFF/, '');
  const match = source.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)([\s\S]*)$/);
  if (!match) return { frontmatter: '', data: {}, body: source };
  const data = {};
  let currentListKey = '';
  for (const line of match[1].split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    if (currentListKey && trimmed.startsWith('- ')) {
      data[currentListKey].push(cleanScalar(trimmed.slice(2)));
      continue;
    }
    currentListKey = '';
    const index = trimmed.indexOf(':');
    if (index < 0) continue;
    const key = trimmed.slice(0, index).trim();
    const rawValue = trimmed.slice(index + 1).trim();
    if (!rawValue) {
      data[key] = [];
      currentListKey = key;
      continue;
    }
    if (rawValue.startsWith('[') && rawValue.endsWith(']')) {
      data[key] = rawValue.slice(1, -1).split(',').map(cleanScalar).filter(Boolean);
    } else {
      data[key] = cleanScalar(rawValue);
    }
  }
  return { frontmatter: match[1], data, body: match[2].trim() };
}

function listMarkdownFiles(root) {
  const result = [];
  const visit = dir => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) visit(fullPath);
      else if (entry.isFile() && /\.md$/i.test(entry.name)) result.push(fullPath);
    }
  };
  visit(root);
  return result;
}

function stripAiSection(body) {
  return String(body || '').replace(/\n*## AI分析结果[\s\S]*?(?=\n## |\s*$)/g, '').trim();
}

function extractSectionSafe(markdown, title) {
  const lines = String(markdown || '').split(/\r?\n/);
  const start = lines.findIndex(line => new RegExp(`^##+\\s*${title}\\s*$`).test(line.trim()));
  if (start < 0) return '';
  const collected = [];
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^##+\s+/.test(lines[i].trim())) break;
    collected.push(lines[i]);
  }
  return collected.join('\n').trim();
}

function extractJson(raw) {
  const trimmed = String(raw || '').trim();
  if (!trimmed) return null;
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) return trimmed;
  const codeBlock = trimmed.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
  if (codeBlock?.[1]?.trim().startsWith('{')) return codeBlock[1].trim();

  const start = trimmed.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < trimmed.length; i += 1) {
    const ch = trimmed[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (ch === '\\' && inString) {
      escape = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === '{') depth += 1;
    if (ch === '}') {
      depth -= 1;
      if (depth === 0) return trimmed.slice(start, i + 1);
    }
  }
  return null;
}

function normalizeTags(tags) {
  if (Array.isArray(tags)) return tags.map(text).filter(Boolean).slice(0, 8);
  return [];
}

function normalizeFaqs(faqs) {
  if (!Array.isArray(faqs)) return [];
  return faqs
    .map(item => ({
      question: text(item?.question),
      answer: text(item?.answer),
    }))
    .filter(item => item.question && item.answer)
    .slice(0, 3);
}

function plainExcerpt(value, maxLength) {
  return text(value)
    .replace(/^---[\s\S]*?---\s*/m, '')
    .replace(/^#+\s*/gm, '')
    .replace(/^[-*]\s*/gm, '')
    .replace(/\s+/g, ' ')
    .slice(0, maxLength);
}

function buildAiSection(result) {
  const lines = [
    '## AI分析结果',
    '',
    `分析时间：${new Date().toISOString()}`,
    `分析模型：${config.OLLAMA.model}`,
    '',
    '### 概括',
    '',
    text(result.summary || result.overview) || '待人工补充。',
    '',
    '### 解析结果（qwen-vl-plus）',
    '',
    text(result.analysis || result.parse_result) || '待人工补充。',
    '',
    '### 标签建议',
    '',
  ];
  const tags = normalizeTags(result.domain_tags || result.tags);
  lines.push(tags.length ? tags.map(tag => `- ${tag}`).join('\n') : '- 待人工补充');
  lines.push('', '### 讲解词草稿', '');
  lines.push(text(result.commentary) || '待人工补充。');
  const faqs = normalizeFaqs(result.faqs);
  if (faqs.length) {
    lines.push('', '### 可转FAQ', '');
    faqs.forEach((item, index) => {
      lines.push(`${index + 1}. 问：${item.question}`);
      lines.push(`   答：${item.answer}`);
    });
  }
  lines.push('');
  lines.push('> 以上内容为 AI 草稿，请人工确认后再改为 published。');
  return lines.join('\n');
}

class ObsidianAnalyzer {
  constructor() {
    this.baseUrl = config.OLLAMA.baseUrl;
    this.model = config.OLLAMA.model;
  }

  async analyze(options = {}) {
    const vaultPath = path.resolve(text(options.vaultPath || config.OBSIDIAN_VAULT_PATH));
    if (!fs.existsSync(vaultPath) || !fs.statSync(vaultPath).isDirectory()) {
      throw new Error(`Obsidian 目录不存在: ${vaultPath}`);
    }

    const force = Boolean(options.force);
    const missingAnalysisOnly = options.missingAnalysisOnly !== false;
    const limit = options.limit === 'all'
      ? Number.MAX_SAFE_INTEGER
      : Math.max(1, Math.min(Number(options.limit) || 5, 1000));
    const candidates = this._findCandidates(vaultPath, { force, missingAnalysisOnly }).slice(0, limit);
    const results = [];

    for (const candidate of candidates) {
      const startedAt = Date.now();
      try {
        const analysis = await this._analyzeFile(candidate);
        this._writeAnalysis(candidate, analysis);
        results.push({
          file: path.relative(vaultPath, candidate.file).replace(/\\/g, '/'),
          title: candidate.title,
          ok: true,
          durationMs: Date.now() - startedAt,
          summary: text(analysis.summary).slice(0, 80),
        });
      } catch (err) {
        results.push({
          file: path.relative(vaultPath, candidate.file).replace(/\\/g, '/'),
          title: candidate.title,
          ok: false,
          durationMs: Date.now() - startedAt,
          error: err.message,
        });
      }
    }

    const status = knowledgeDb.syncObsidian({ vaultPath });
    return {
      ...status,
      analysis: {
        vaultPath,
        model: this.model,
        limit,
        force,
        missingAnalysisOnly,
        candidateCount: candidates.length,
        analyzed: results.length,
        success: results.filter(item => item.ok).length,
        failed: results.filter(item => !item.ok).length,
        results,
      },
    };
  }

  _findCandidates(vaultPath, options = {}) {
    return listMarkdownFiles(vaultPath)
      .map(file => {
        const raw = fs.readFileSync(file, 'utf8');
        const parsed = parseFrontmatter(raw);
        const title = text(parsed.data.title || parsed.data.name || parsed.data.file_name || parsed.data['文件名称'] || path.basename(file, path.extname(file)));
        const status = text(parsed.data.status || 'draft');
        const body = parsed.body;
        return {
          file,
          raw,
          ...parsed,
          title,
          status,
          priority: this._candidatePriority({ status, body }, options),
        };
      })
      .filter(item => item.priority > 0)
      .sort((a, b) => b.priority - a.priority || a.file.localeCompare(b.file, 'zh-CN'));
  }

  _candidatePriority({ status, body }, options = {}) {
    const value = String(body || '');
    if (options.force) {
      const aiBody = value.match(/\n*##\s*AI分析结果[\s\S]*$/)?.[0] || '';
      const hasAnalysis = /###\s*解析结果（qwen-vl-plus）/.test(aiBody) && !/###\s*解析结果（qwen-vl-plus）\s*(?:\r?\n){1,2}\s*(?:###|>|$)/.test(aiBody);
      if (options.missingAnalysisOnly && /## AI分析结果/.test(value) && hasAnalysis) return 0;
      if (options.missingAnalysisOnly && !/## AI分析结果/.test(value)) return 0;
      return /## AI分析结果/.test(value) ? 4 : 2;
    }
    if (/## AI分析结果/.test(value)) return 0;
    if (/待补充讲解词|待人工补充/.test(value)) return 3;
    if (status === 'draft') return 2;
    if (value.length < 800) return 1;
    return 0;
  }

  async _analyzeFile(candidate) {
    const body = stripAiSection(candidate.body).slice(0, 2500);
    const prompt = [
      '你是展厅知识库内容整理助手。请根据下面 Markdown 为展厅中控生成草稿内容。',
      '要求：',
      '1. 只基于给定内容，不编造不存在的事实。',
      '2. 输出严格 JSON，不要 Markdown 代码块。',
      '3. summary 控制在 60 字以内。',
      '4. analysis 控制在 180 字以内，用于“解析结果（qwen-vl-plus）”草稿。',
      '5. commentary 控制在 180 字以内，适合作为展厅讲解词草稿。',
      '6. domain_tags 给 3-6 个中文领域标签。',
      '7. content_type 判断为 PPT、视频、图片或文档之一。',
      '8. case_region 能判断就给地区，不能判断留空。',
      '9. faqs 给 0-3 个问答，答案每条 50 字以内。',
      '',
      'JSON 格式：',
      '{"summary":"","analysis":"","domain_tags":[],"content_type":"","case_region":"","commentary":"","faqs":[{"question":"","answer":""}]}',
      '',
      `标题：${candidate.title}`,
      `类型：${text(candidate.data.type || candidate.data.file_type || '')}`,
      `内容类型：${text(candidate.data.content_type || candidate.data['内容类型'] || '')}`,
      `年份：${text(candidate.data.year || candidate.data['年份'] || '')}`,
      `出处：${text(candidate.data.source_from || candidate.data['出处'] || '')}`,
      `案例地区：${text(candidate.data.case_region || candidate.data['案例地区'] || '')}`,
      `资源文件：${text(candidate.data.resource || '')}`,
      '',
      'Markdown正文：',
      body,
    ].join('\n');

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90000);
    let response;
    try {
      response = await fetch(`${this.baseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          model: this.model,
          stream: false,
          messages: [
            { role: 'system', content: '你只输出严格 JSON，不能输出解释文字。' },
            { role: 'user', content: prompt },
          ],
          options: { temperature: 0.2, num_predict: 500 },
        }),
      });
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('Ollama analyze timeout after 90s');
      throw err;
    } finally {
      clearTimeout(timeout);
    }

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`Ollama ${response.status}: ${errText.slice(0, 200)}`);
    }
    const data = await response.json();
    const raw = data?.message?.content || data?.response || '';
    const jsonText = extractJson(raw);
    if (!jsonText) return this._fallbackAnalysis(candidate, body);
    try {
      return JSON.parse(jsonText);
    } catch {
      return this._fallbackAnalysis(candidate, body);
    }
  }

  _fallbackAnalysis(candidate, body) {
    const summary = plainExcerpt(candidate.data.summary || body, 60) || candidate.title;
    const analysis = plainExcerpt(
      extractSectionSafe(body, '解析结果（qwen-vl-plus）') ||
      extractSectionSafe(body, '解析结果') ||
      body,
      180
    ) || summary;
    const commentary = plainExcerpt(
      extractSectionSafe(body, '讲解词') ||
      extractSectionSafe(body, '讲解词草稿') ||
      body,
      180
    ) || summary;
    return {
      summary,
      analysis,
      domain_tags: normalizeTags(candidate.data.domain_tags || candidate.data.tags).slice(0, 6),
      content_type: text(candidate.data.content_type || candidate.data.file_type || candidate.data.type || ''),
      case_region: text(candidate.data.case_region || candidate.data.region || ''),
      commentary,
      faqs: [],
    };
  }

  _writeAnalysis(candidate, analysis) {
    const body = stripAiSection(candidate.body);
    const content = candidate.frontmatter
      ? `---\n${candidate.frontmatter}\n---\n\n${body}\n\n${buildAiSection(analysis)}\n`
      : `${body}\n\n${buildAiSection(analysis)}\n`;
    fs.writeFileSync(candidate.file, content, 'utf8');
  }
}

export const obsidianAnalyzer = new ObsidianAnalyzer();
export default obsidianAnalyzer;
