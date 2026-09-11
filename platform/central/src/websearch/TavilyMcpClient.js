import { spawn } from 'child_process';

function splitCommand(value) {
  const input = String(value || '').trim();
  if (!input) return [];
  const parts = [];
  let current = '';
  let quote = '';
  for (let i = 0; i < input.length; i += 1) {
    const ch = input[i];
    if (quote) {
      if (ch === quote) quote = '';
      else current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        parts.push(current);
        current = '';
      }
      continue;
    }
    current += ch;
  }
  if (current) parts.push(current);
  return parts;
}

class TavilyMcpClient {
  constructor() {
    this.commandSpec = process.env.TAVILY_MCP_COMMAND || 'npx -y tavily-mcp@0.2.21';
    this.toolName = process.env.TAVILY_MCP_TOOL || '';
    this.timeoutMs = Number.parseInt(process.env.TAVILY_MCP_TIMEOUT_MS || '12000', 10);
    this.totalTimeoutMs = Number.parseInt(process.env.TAVILY_TOTAL_TIMEOUT_MS || '15000', 10);
    this.maxExtractVerifications = Math.max(0, Math.min(3,
      Number.parseInt(process.env.TAVILY_MCP_VERIFY_RESULTS || '0', 10),
    ));
    this.extractCacheTtlMs = Math.max(0, Number.parseInt(
      process.env.TAVILY_MCP_EXTRACT_CACHE_TTL_MS || '300000', 10,
    ));
    this.extractCache = new Map();
    this.searchCache = new Map();
    this.session = null;
    this.tools = [];
    this.connecting = null;
  }

  async search(query, options = {}) {
    const cacheKey = this._searchCacheKey(query, options);
    const cached = this.searchCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return structuredClone(cached.result);
    if (cached) this.searchCache.delete(cacheKey);
    const result = await this._searchWithSession(query, options);
    const liveQuery = /(最新|刚刚|今天|目前|当前|实时|赛果|比分|latest|current|today|live)/i.test(query);
    const ttl = liveQuery ? 30000 : 120000;
    this.searchCache.set(cacheKey, { result, expiresAt: Date.now() + ttl });
    if (this.searchCache.size > 100) this.searchCache.delete(this.searchCache.keys().next().value);
    return structuredClone(result);
  }

  close() {
    this._resetSession();
  }

  async _searchWithSession(query, options = {}) {
    const commandParts = splitCommand(this.commandSpec);
    if (!commandParts.length) {
      throw new Error('TAVILY_MCP_COMMAND is empty');
    }
    if (!process.env.TAVILY_API_KEY) {
      throw new Error('TAVILY_API_KEY is required for tavily-mcp');
    }

    const deadline = Date.now() + this.totalTimeoutMs;
    const { session, tools } = await this._ensureSession(commandParts, deadline);
    try {
      const tool = this._selectSearchTool(tools);
      if (!tool) throw new Error('No Tavily search tool found in MCP server');
      const extractTool = this._selectExtractTool(tools);
      const preferredPromise = Promise.all((options.preferredUrls || []).slice(0, 2).map(url =>
        this._fetchPreferredResult(url, session, extractTool, deadline).catch(() => null),
      ));
      const searchPromise = session.request('tools/call', {
        name: tool.name,
        arguments: this._buildSearchArgs(query, options, tool.inputSchema),
      }, this._remaining(deadline));
      const [called, preferredRows] = await Promise.all([searchPromise, preferredPromise]);
      if (called?.isError) {
        const message = called?.content?.map(item => item.text || '').filter(Boolean).join('\n');
        throw new Error(message || 'Tavily MCP search failed');
      }
      const normalized = this._normalizeToolResult(called, { keepUntrusted: true });
      const mergedRows = [];
      const seenUrls = new Set();
      for (const row of [...preferredRows.filter(Boolean), ...normalized.results]) {
        const key = this._canonicalUrl(row.url);
        if (!key || seenUrls.has(key)) continue;
        seenUrls.add(key);
        mergedRows.push(row);
      }
      normalized.results = mergedRows;
      const untrusted = normalized.results.filter(row => row.metadataTrusted === false);

      if (!extractTool || this.maxExtractVerifications === 0) {
        return { ...normalized, results: normalized.results.filter(row => row.metadataTrusted !== false) };
      }
      const extractLimit = Math.max(0, Math.min(
        Number(options.extractResults ?? this.maxExtractVerifications),
        this.maxExtractVerifications,
      ));
      const officialDomains = new Set((options.includeDomains || []).map(item => String(item).toLowerCase()));
      const trustedOfficial = normalized.results.filter(row => {
        // 固定官方链接已由 HTTPS 直取并完成正文清洗，不再用 Tavily extract 覆盖，避免其格式化损坏小数。
        if (row.metadataTrusted === false || row.preferredDirect === true) return false;
        try {
          const hostname = new URL(row.url).hostname.toLowerCase();
          return [...officialDomains].some(domain => hostname === domain || hostname.endsWith(`.${domain}`));
        } catch { return false; }
      });
      const candidates = this._rankExtractCandidates(
        [...trustedOfficial, ...untrusted], query, options,
      ).slice(0, extractLimit);
      if (!candidates.length) return normalized;
      const verified = await Promise.all(candidates.map(row =>
        this._verifyResultWithExtract(session, extractTool, row, query, deadline).catch(() => null),
      ));
      const verifiedByUrl = new Map(verified.filter(Boolean).map(row => [this._canonicalUrl(row.url), row]));
      return {
        ...normalized,
        results: normalized.results.map(row => {
          const replacement = verifiedByUrl.get(this._canonicalUrl(row.url));
          if (replacement) return replacement;
          return row.metadataTrusted === false ? null : row;
        }).filter(Boolean),
      };
    } catch (err) {
      this._resetSession(session);
      throw err;
    }
  }

  async _ensureSession(commandParts, deadline) {
    if (this.session?.isAlive() && this.tools.length) return { session: this.session, tools: this.tools };
    if (this.connecting) return this.connecting;
    this.connecting = (async () => {
      const isWindows = process.platform === 'win32';
      const session = new McpStdioSession(
        isWindows ? (process.env.ComSpec || 'cmd.exe') : commandParts[0],
        isWindows ? ['/d', '/s', '/c', this.commandSpec] : commandParts.slice(1), {
          timeoutMs: this.timeoutMs,
          env: process.env,
        },
      );
      try {
        await session.start();
        await session.request('initialize', {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'exhibition-hall-websearch', version: '2.0.0' },
        }, this._remaining(deadline));
        session.notify('notifications/initialized', {});
        const listed = await session.request('tools/list', {}, this._remaining(deadline));
        this.session = session;
        this.tools = listed?.tools || [];
        return { session, tools: this.tools };
      } catch (err) {
        session.stop();
        throw err;
      }
    })();
    try {
      return await this.connecting;
    } finally {
      this.connecting = null;
    }
  }

  _resetSession(session = this.session) {
    if (session) session.stop();
    if (!session || this.session === session) {
      this.session = null;
      this.tools = [];
    }
  }

  _remaining(deadline) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error('Tavily total deadline exceeded');
    return Math.min(this.timeoutMs, remaining);
  }

  _rankExtractCandidates(rows, query, options) {
    const official = new Set((options.includeDomains || []).map(item => String(item).toLowerCase()));
    const terms = String(query || '').toLowerCase().match(/[a-z]{3,}|[\u4e00-\u9fff]{2,}/g) || [];
    return [...rows].sort((a, b) => {
      const score = row => {
        let hostname = '';
        try { hostname = new URL(row.url).hostname.toLowerCase(); } catch {}
        const officialBoost = [...official].some(domain => hostname === domain || hostname.endsWith(`.${domain}`)) ? 10 : 0;
        const haystack = `${row.title} ${row.snippet}`.toLowerCase();
        return officialBoost + terms.filter(term => haystack.includes(term)).length;
      };
      return score(b) - score(a);
    });
  }

  _searchCacheKey(query, options) {
    return `${String(query || '').trim().toLowerCase()}\n${JSON.stringify({
      maxResults: options.maxResults,
      recencyDays: options.recencyDays,
      searchDepth: options.searchDepth,
      includeDomains: options.includeDomains || [],
      preferredUrls: options.preferredUrls || [],
    })}`;
  }

  _selectSearchTool(tools) {
    if (this.toolName) return tools.find(tool => tool.name === this.toolName) || { name: this.toolName };
    return tools.find(tool => /tavily.*search/i.test(tool.name || ''))
      || tools.find(tool => /search/i.test(tool.name || ''))
      || tools[0]
      || null;
  }

  _selectExtractTool(tools) {
    return tools.find(tool => /tavily.*extract/i.test(tool.name || ''))
      || tools.find(tool => /extract/i.test(tool.name || ''))
      || null;
  }

  async _verifyResultWithExtract(session, tool, row, query, deadline = Date.now() + this.timeoutMs) {
    const key = this._extractCacheKey(row.url, query);
    const cached = this.extractCache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      return this._replaceUntrustedMetadata(row, cached.content);
    }
    if (cached) this.extractCache.delete(key);
    const called = await session.request('tools/call', {
      name: tool.name,
      arguments: this._buildExtractArgs(row.url, query, tool.inputSchema),
    }, this._remaining(deadline));
    if (called?.isError) return null;
    const text = called?.content
      ?.filter(item => item.type === 'text' && item.text)
      ?.map(item => item.text)
      ?.join('\n') || '';
    const rawContent = this._parseSingleExtract(text, row.url);
    if (!rawContent) return null;
    const liveQuery = /(最新|刚刚|今天|目前|当前|实时|赛果|比分|latest|current|today|live)/i.test(query);
    const ttl = liveQuery ? Math.min(this.extractCacheTtlMs, 60000) : this.extractCacheTtlMs;
    if (ttl > 0) this.extractCache.set(key, { content: rawContent, expiresAt: Date.now() + ttl });
    if (this.extractCache.size > 100) this.extractCache.delete(this.extractCache.keys().next().value);
    return this._replaceUntrustedMetadata(row, rawContent);
  }

  async _fetchPreferredResult(url, session, extractTool, deadline) {
    const direct = await this._fetchOfficialPage(url, deadline).catch(() => null);
    if (direct) return direct;
    if (!extractTool) return null;
    return this._verifyResultWithExtract(session, extractTool, {
      title: '', url, snippet: '', rawContent: '', metadataTrusted: false,
    }, '', deadline);
  }

  async _fetchOfficialPage(url, deadline) {
    const parsed = new URL(String(url || '').trim());
    if (parsed.protocol !== 'https:') return null;
    const timeoutMs = Math.min(7000, this._remaining(deadline));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(parsed.toString(), {
        signal: controller.signal,
        redirect: 'follow',
        headers: {
          accept: 'text/html,application/xhtml+xml,text/plain;q=0.9',
          'user-agent': 'Mozilla/5.0 ExhibitionHallWebSearch/1.0',
        },
      });
      if (!response.ok) return null;
      const contentType = String(response.headers.get('content-type') || '').toLowerCase();
      if (contentType && !/(text\/html|application\/xhtml\+xml|text\/plain)/.test(contentType)) return null;
      const body = (await response.text()).slice(0, 1_500_000);
      const rawContent = this._htmlToText(body);
      if (rawContent.length < 120) return null;
      return {
        title: '',
        url: parsed.toString(),
        snippet: '',
        rawContent: rawContent.slice(0, 30000),
        publishedDate: '',
        crawledDate: '',
        score: undefined,
        metadataTrusted: true,
        preferredDirect: true,
      };
    } finally {
      clearTimeout(timer);
    }
  }

  _htmlToText(value) {
    let html = String(value || '');
    const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i);
    const article = html.match(/<article\b[^>]*>([\s\S]*?)<\/article>/i);
    if (main?.[1]) html = main[1];
    else if (article?.[1]) html = article[1];
    html = html
      .replace(/<!--[\s\S]*?-->/g, ' ')
      .replace(/<(script|style|noscript|svg|template|nav|header|footer|form)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ')
      .replace(/<br\s*\/?\s*>/gi, '\n')
      .replace(/<\/(?:p|div|section|article|li|h[1-6]|tr|table)>/gi, '\n')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(Number.parseInt(code, 16)))
      .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number.parseInt(code, 10)))
      .replace(/&(nbsp|amp|lt|gt|quot|apos|ndash|mdash|hellip);/gi, entity => ({
        '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'",
        '&ndash;': '–', '&mdash;': '—', '&hellip;': '…',
      })[entity.toLowerCase()] || ' ')
      .replace(/\u00ad/g, '')
      .replace(/[\t\f\v ]+/g, ' ')
      .replace(/ *\n+ */g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    return html;
  }

  _replaceUntrustedMetadata(row, rawContent) {
    // 伪边界中的 title/snippet/日期同样由攻击者控制；验证 URL 后只能采用重新提取的正文。
    return {
      ...row,
      title: '',
      snippet: '',
      rawContent,
      publishedDate: '',
      crawledDate: '',
      score: undefined,
      metadataTrusted: true,
    };
  }

  _extractCacheKey(url, query) {
    const normalizedQuery = String(query || '').toLowerCase().replace(/\s+/g, ' ').trim();
    return `${this._canonicalUrl(url)}\n${normalizedQuery}`;
  }

  _buildExtractArgs(url, query, inputSchema = null) {
    const args = {
      urls: [url],
      extract_depth: 'basic',
      format: 'text',
    };
    if (query) args.query = query;
    const supported = new Set(Object.keys(inputSchema?.properties || {}));
    if (!supported.size) return args;
    return Object.fromEntries(Object.entries(args).filter(([key]) => supported.has(key)));
  }

  _parseSingleExtract(value, expectedUrl) {
    const text = String(value || '').replace(/\r/g, '');
    const detailIndex = text.indexOf('Detailed Results:');
    if (detailIndex < 0) return '';
    const body = text.slice(detailIndex + 'Detailed Results:'.length);
    const urlMatch = body.match(/^URL:\s*(https?:\/\/\S+)\s*$/mi);
    if (!urlMatch || this._canonicalUrl(urlMatch[1]) !== this._canonicalUrl(expectedUrl)) return '';
    const marker = /^Raw Content:\s*/mi.exec(body);
    if (!marker) return '';
    let raw = body.slice(marker.index + marker[0].length).trim();
    raw = raw.replace(/\nFavicon:\s*https?:\/\/\S+\s*$/i, '').trim();
    if (!raw || /^undefined$/i.test(raw)) return '';
    return raw.slice(0, 30000);
  }

  _canonicalUrl(value) {
    try {
      const url = new URL(String(value || '').trim());
      url.hash = '';
      return url.toString().replace(/\/$/, '');
    } catch {
      return String(value || '').trim().replace(/\/$/, '');
    }
  }

  _buildSearchArgs(query, options, inputSchema = null) {
    const args = {
      query,
      max_results: Math.max(5, Math.min(20, Number(options.maxResults || 8))),
      include_raw_content: options.includeRawContent === true,
      search_depth: options.searchDepth || 'advanced',
    };
    if (Array.isArray(options.includeDomains) && options.includeDomains.length) {
      args.include_domains = options.includeDomains.map(item => String(item || '').trim()).filter(Boolean);
    }
    if (Array.isArray(options.excludeDomains) && options.excludeDomains.length) {
      args.exclude_domains = options.excludeDomains.map(item => String(item || '').trim()).filter(Boolean);
    }
    if (options.timeRange) args.time_range = options.timeRange;
    if (options.topic) args.topic = options.topic;
    if (options.recencyDays) {
      const startDate = new Date(Date.now() - options.recencyDays * 24 * 60 * 60 * 1000);
      args.start_date = startDate.toISOString().slice(0, 10);
    }
    const supported = new Set(Object.keys(inputSchema?.properties || {}));
    if (!supported.size) return args;
    return Object.fromEntries(Object.entries(args).filter(([key]) => supported.has(key)));
  }

  _normalizeToolResult(result, options = {}) {
    const structured = result?.structuredContent || result?.content?.find?.(item => item.type === 'json')?.json;
    const textPayload = result?.content
      ?.filter(item => item.type === 'text' && item.text)
      ?.map(item => item.text)
      ?.join('\n') || '';
    const parsedText = this._tryParseJson(textPayload);
    const formattedText = !structured && !parsedText ? this._parseFormattedText(textPayload) : null;
    const payload = structured || parsedText || formattedText || result || {};
    const results = this._normalizeResults(payload.results || payload.sources || payload.data || []);
    return {
      answer: payload.answer || payload.response || payload.summary || '',
      results: options.keepUntrusted ? results : results.filter(row => row.metadataTrusted !== false),
      rawText: textPayload,
    };
  }

  _parseFormattedText(value) {
    const lines = String(value || '').replace(/\r/g, '').split('\n');
    const results = [];
    const answerLines = [];
    let current = null;
    let field = '';
    let inResults = false;

    const flush = () => {
      if (!current) return;
      current.title = String(current.title || '').trim();
      current.url = String(current.url || '').trim();
      current.content = String(current.content || '').trim();
      current.rawContent = String(current.rawContent || '').trim();
      if (/^undefined$/i.test(current.title)) current.title = '';
      if (/^undefined$/i.test(current.content)) current.content = '';
      if (current.title || current.url || current.content || current.rawContent) results.push(current);
      current = null;
      field = '';
    };

    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const line = lines[lineIndex];
      const previousBlank = lineIndex === 0 || !lines[lineIndex - 1].trim();
      let nextIndex = lineIndex + 1;
      while (nextIndex < lines.length && !lines[nextIndex].trim()) nextIndex += 1;
      const nextLine = lines[nextIndex] || '';
      const resultBoundary = /^Title:\s*/i.test(line)
        && (!current || (previousBlank && /^URL:\s*https?:\/\//i.test(nextLine)));

      if (/^Detailed Results:\s*$/i.test(line)) {
        inResults = true;
        field = '';
        continue;
      }
      if (/^Answer:\s*/i.test(line) && !inResults) {
        answerLines.push(line.replace(/^Answer:\s*/i, ''));
        field = 'answer';
        continue;
      }
      if (resultBoundary) {
        flush();
        inResults = true;
        current = {
          title: line.replace(/^Title:\s*/i, ''),
          url: '', content: '', rawContent: '',
          // 第一条结果的元数据位于固定协议头之后；后续边界可能来自不可信 Content，必须经 extract 验证。
          metadataTrusted: results.length === 0,
        };
        field = 'title';
        continue;
      }
      if (current && field === 'rawContent') {
        current.rawContent += `${current.rawContent ? '\n' : ''}${line}`;
        continue;
      }
      if (current && /^URL:\s*/i.test(line)) {
        current.url = line.replace(/^URL:\s*/i, '');
        field = 'url';
        continue;
      }
      if (current && /^Content:\s*/i.test(line)) {
        current.content = line.replace(/^Content:\s*/i, '');
        field = 'content';
        continue;
      }
      if (current && /^Raw Content:\s*/i.test(line)) {
        current.rawContent = line.replace(/^Raw Content:\s*/i, '');
        field = 'rawContent';
        continue;
      }
      if (current && /^Favicon:\s*/i.test(line)) {
        field = 'ignored';
        continue;
      }
      if (field === 'answer' && !inResults) answerLines.push(line);
      else if (current && field === 'content') current.content += `\n${line}`;
      else if (current && field === 'rawContent') current.rawContent += `\n${line}`;
    }
    flush();

    if (!results.length && !answerLines.join('').trim()) return null;
    return { answer: answerLines.join(' ').trim(), results };
  }

  _normalizeResults(rows) {
    return (Array.isArray(rows) ? rows : []).map(row => ({
      title: String(row.title || row.name || '').trim(),
      url: String(row.url || row.link || '').trim(),
      publishedDate: row.published_date || row.publishedDate || row.date || '',
      crawledDate: row.crawled_date || row.crawledDate || row.crawl_date || '',
      snippet: String(row.content || row.snippet || row.description || row.summary || '').trim(),
      rawContent: String(row.raw_content || row.rawContent || '').trim(),
      score: row.score,
      metadataTrusted: row.metadataTrusted !== false,
    })).filter(row => row.title || row.url || row.snippet || row.rawContent);
  }

  _tryParseJson(value) {
    const text = String(value || '').trim();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      const start = text.indexOf('{');
      const end = text.lastIndexOf('}');
      if (start >= 0 && end > start) {
        try {
          return JSON.parse(text.slice(start, end + 1));
        } catch {}
      }
    }
    return null;
  }
}

class McpStdioSession {
  constructor(command, args, { timeoutMs, env }) {
    this.command = command;
    this.args = args;
    this.timeoutMs = timeoutMs;
    this.env = env;
    this.nextId = 1;
    this.pending = new Map();
    this.buffer = '';
    this.child = null;
    this.stderr = '';
  }

  start() {
    this.child = spawn(this.command, this.args, {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: this.env,
      windowsHide: true,
    });
    this.child.stdout.setEncoding('utf8');
    this.child.stderr.setEncoding('utf8');
    this.child.stdout.on('data', chunk => this._onStdout(chunk));
    this.child.stderr.on('data', chunk => {
      this.stderr += chunk;
    });
    this.child.on('exit', () => {
      for (const [, entry] of this.pending) {
        clearTimeout(entry.timer);
        entry.reject(new Error(`Tavily MCP process exited: ${this.stderr.trim()}`));
      }
      this.pending.clear();
      this.child = null;
    });
    return new Promise((resolve, reject) => {
      this.child.once('spawn', resolve);
      this.child.once('error', reject);
    });
  }

  stop() {
    if (this.child && !this.child.killed) this.child.kill();
    this.child = null;
  }

  isAlive() {
    return Boolean(this.child && !this.child.killed && this.child.exitCode === null);
  }

  request(method, params, timeoutMs = this.timeoutMs) {
    const id = this.nextId++;
    const message = { jsonrpc: '2.0', id, method, params };
    const promise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Tavily MCP timeout on ${method}`));
      }, Math.max(1, Number(timeoutMs || this.timeoutMs)));
      this.pending.set(id, { resolve, reject, timer });
    });
    this._send(message);
    return promise;
  }

  notify(method, params) {
    this._send({ jsonrpc: '2.0', method, params });
  }

  _send(message) {
    if (!this.child?.stdin?.writable) throw new Error('Tavily MCP process is not writable');
    this.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  _onStdout(chunk) {
    this.buffer += chunk;
    let index;
    while ((index = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (!line) continue;
      this._handleLine(line);
    }
  }

  _handleLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    if (!message.id || !this.pending.has(message.id)) return;
    const entry = this.pending.get(message.id);
    this.pending.delete(message.id);
    clearTimeout(entry.timer);
    if (message.error) entry.reject(new Error(message.error.message || JSON.stringify(message.error)));
    else entry.resolve(message.result);
  }
}

export const tavilyMcpClient = new TavilyMcpClient();
export default tavilyMcpClient;
