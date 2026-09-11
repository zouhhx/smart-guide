import crypto from 'crypto';
import fs from 'fs';
import path from 'path';

function parseArgs(argv) {
  const args = { apply: false, verifyHashes: true };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--apply') args.apply = true;
    else if (arg === '--no-verify-hashes') args.verifyHashes = false;
    else if (arg.startsWith('--')) args[arg.slice(2)] = argv[++i];
  }
  return args;
}

function fail(message) {
  throw new Error(message);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
}

function findPrefixedDirectory(root, prefix) {
  const rows = fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && entry.name.startsWith(prefix));
  if (rows.length !== 1) fail(`Expected one ${prefix} directory under ${root}, found ${rows.length}`);
  return path.join(root, rows[0].name);
}

function findPrefixedFile(root, prefix) {
  const rows = fs.readdirSync(root, { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.startsWith(prefix) && !entry.name.includes('.bak-'));
  if (rows.length !== 1) fail(`Expected one ${prefix} file under ${root}, found ${rows.length}`);
  return path.join(root, rows[0].name);
}

function parseFrontmatter(markdown) {
  const match = String(markdown || '').replace(/^\uFEFF/, '').match(/^---\s*\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return {};
  const result = {};
  for (const line of match[1].split(/\r?\n/)) {
    const index = line.indexOf(':');
    if (index <= 0 || /^\s/.test(line)) continue;
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, '');
    if (value) result[key] = value;
  }
  return result;
}

function parseCommentary(markdown) {
  const markers = [...String(markdown || '').matchAll(/^##\s+(精炼版|标准版|详细版)\s*$/gm)];
  const keyMap = { 精炼版: 'concise', 标准版: 'standard', 详细版: 'detailed' };
  const levels = {};
  markers.forEach((marker, index) => {
    const start = marker.index + marker[0].length;
    const end = markers[index + 1]?.index ?? markdown.length;
    levels[keyMap[marker[1]]] = markdown.slice(start, end).trim();
  });
  for (const key of ['concise', 'standard', 'detailed']) {
    if (!levels[key]) fail(`Missing ${key} commentary section`);
  }
  return levels;
}

function buildCommentary(levels) {
  return [
    `【精炼版】\n${levels.concise}`,
    `【标准版】\n${levels.standard}`,
    `【详细版】\n${levels.detailed}`,
  ].join('\n\n');
}

function sha256(file) {
  const hash = crypto.createHash('sha256');
  const fd = fs.openSync(file, 'r');
  const buffer = Buffer.allocUnsafe(4 * 1024 * 1024);
  try {
    let bytesRead = 0;
    do {
      bytesRead = fs.readSync(fd, buffer, 0, buffer.length, null);
      if (bytesRead) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead);
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest('hex');
}

function loadExpectedHashes(packageRoot) {
  const file = path.join(packageRoot, '文件清单-SHA256.txt');
  if (!fs.existsSync(file)) return new Map();
  const result = new Map();
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([a-f0-9]{64})\s+\.\/(.+)$/i);
    if (match) result.set(match[2].replace(/\\/g, '/'), match[1].toLowerCase());
  }
  return result;
}

function atomicWriteJson(file, value) {
  const temp = `${file}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  fs.copyFileSync(temp, file);
  fs.rmSync(temp, { force: true });
}

function copyAtomic(source, destination) {
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  const temp = `${destination}.incoming-${process.pid}`;
  fs.copyFileSync(source, temp);
  if (fs.existsSync(destination)) {
    fs.copyFileSync(temp, destination);
    fs.rmSync(temp, { force: true });
  } else {
    fs.renameSync(temp, destination);
  }
}

function safeTimestamp() {
  return new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
}

function unitRecords(packageRoot, verifyHashes) {
  const contentRoot = findPrefixedDirectory(packageRoot, '02-');
  const pptRoot = findPrefixedDirectory(packageRoot, '07-');
  const expectedHashes = loadExpectedHashes(packageRoot);
  const pptFiles = fs.readdirSync(pptRoot, { withFileTypes: true })
    .filter(entry => entry.isFile() && /\.pptx$/i.test(entry.name))
    .map(entry => entry.name)
    .sort((a, b) => a.localeCompare(b, 'zh-CN'));
  const unitDirs = fs.readdirSync(contentRoot, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && !entry.name.startsWith('.'));

  if (pptFiles.length !== 31) fail(`Expected 31 PPTX files, found ${pptFiles.length}`);
  if (unitDirs.length !== 31) fail(`Expected 31 content units, found ${unitDirs.length}`);

  const records = unitDirs.map(entry => {
    const unitDir = path.join(contentRoot, entry.name);
    const l0File = findPrefixedFile(unitDir, '00-L0');
    const commentaryFile = findPrefixedFile(unitDir, '04-三档讲解词');
    const metadata = parseFrontmatter(fs.readFileSync(l0File, 'utf8'));
    const unitId = metadata.unit_id;
    const unitTitle = metadata.unit_title;
    if (!unitId || !unitTitle) fail(`Missing unit metadata in ${l0File}`);

    const matches = pptFiles.filter(name => {
      const title = path.basename(name, path.extname(name)).replace(/^\d{2}-/, '');
      return title === unitTitle;
    });
    if (matches.length !== 1) fail(`Expected one PPTX for ${unitId} ${unitTitle}, found ${matches.length}`);
    const pptName = matches[0];
    const pptFile = path.join(pptRoot, pptName);
    const packageRelative = `${path.basename(pptRoot)}/${pptName}`.replace(/\\/g, '/');
    const expectedHash = expectedHashes.get(packageRelative) || '';
    const actualHash = verifyHashes ? sha256(pptFile) : '';
    if (verifyHashes && expectedHash && expectedHash !== actualHash) {
      fail(`SHA256 mismatch for ${pptName}: expected ${expectedHash}, got ${actualHash}`);
    }
    const levels = parseCommentary(fs.readFileSync(commentaryFile, 'utf8'));
    return {
      unitDir,
      unitDirName: entry.name,
      unitId,
      unitTitle,
      sourceFile: metadata.source_file || '',
      sourcePage: Number(metadata.source_page || 0) || null,
      pageIndex: Number(pptName.match(/^(\d{2})-/)?.[1] || 0),
      pptName,
      pptFile,
      pptSize: fs.statSync(pptFile).size,
      sha256: actualHash || expectedHash,
      levels,
      commentary: buildCommentary(levels),
      titleOnly: unitId === 'UNIT-DC-ZJ-005',
    };
  }).sort((a, b) => a.pageIndex - b.pageIndex);

  const ids = new Set(records.map(item => item.unitId));
  const names = new Set(records.map(item => item.pptName.toLowerCase()));
  if (ids.size !== 31 || names.size !== 31 || records.some((item, index) => item.pageIndex !== index + 1)) {
    fail('Unit IDs, PPT names, or page indexes are not unique and sequential');
  }
  return records;
}

const runtimeDocPattern = /^(?:00-L0|01-L1|02-L2|03-L3|04-三档讲解词|05-FAQ问答口径|05-百问不倒FAQ库|06-敏感问题兜底|08-一客一策客户适配卡|09-内容调取与组合规则|10-内容对象标签库|README\.md)/;

function syncRuntimeDocuments(records, vaultUnitsRoot, apply) {
  const summary = { copied: 0, changed: 0, unchanged: 0, skipped: 0 };
  for (const record of records) {
    const targetDir = path.join(vaultUnitsRoot, record.unitDirName);
    for (const entry of fs.readdirSync(record.unitDir, { withFileTypes: true })) {
      if (!entry.isFile() || entry.name.includes('.bak-') || !runtimeDocPattern.test(entry.name)) {
        summary.skipped += 1;
        continue;
      }
      const source = path.join(record.unitDir, entry.name);
      const target = path.join(targetDir, entry.name);
      const exists = fs.existsSync(target);
      const changed = !exists || sha256(source) !== sha256(target);
      if (!changed) {
        summary.unchanged += 1;
        continue;
      }
      if (apply) copyAtomic(source, target);
      summary.copied += 1;
      if (exists) summary.changed += 1;
    }
  }
  return summary;
}

function publicBaseUrl(libraryRows) {
  const sample = libraryRows.find(item => /^https?:\/\//i.test(String(item.url || '')));
  if (!sample) return 'http://192.168.16.61:3001';
  try {
    return new URL(String(sample.url)).origin;
  } catch {
    return 'http://192.168.16.61:3001';
  }
}

function mediaLocalBase(libraryRows, mediaDir) {
  const sample = libraryRows.find(item => item.localPath && item.name);
  if (!sample) return mediaDir;
  return path.win32.dirname(String(sample.localPath));
}

function updateLibrary(records, libraryFile, mediaDir, vaultUnitsRoot, apply) {
  const raw = readJson(libraryFile);
  const rows = Array.isArray(raw) ? raw : raw.files || raw.items || [];
  const baseUrl = publicBaseUrl(rows);
  const localBase = mediaLocalBase(rows, mediaDir);
  const importedAt = new Date().toISOString();
  let added = 0;
  let updated = 0;

  for (const record of records) {
    const entry = {
      id: `halltest:${record.unitId}`,
      title: record.unitTitle,
      name: record.pptName,
      originalName: record.pptName,
      description: `数字中国一客一策智能讲解展厅测试包 1.2${record.titleOnly ? '；仅标题证据，限制扩展回答' : ''}`,
      keywords: [record.unitTitle, record.unitId, '数字中国', '一客一策'].join('、'),
      commentary: record.commentary,
      commentaryPages: { '1': record.commentary },
      slideCount: 1,
      status: 'published',
      ext: '.pptx',
      type: 'ppt',
      size: record.pptSize,
      url: `${baseUrl}/${encodeURIComponent(record.pptName)}`,
      localPath: path.win32.join(localBase, record.pptName),
      source: 'hall-test-1.2.0-20260720',
      programTitle: '数字中国一客一策智能讲解',
      unitId: record.unitId,
      sourceFile: record.sourceFile,
      sourcePage: record.sourcePage,
      contentStatus: record.titleOnly ? 'title_only_restricted' : 'content_ready',
      sha256: record.sha256,
      knowledgeUnitDirs: [path.join(vaultUnitsRoot, record.unitDirName)],
      importedAt,
    };
    const index = rows.findIndex(item => item.id === entry.id || item.name === entry.name || item.originalName === entry.name);
    if (index >= 0) {
      rows[index] = { ...rows[index], ...entry };
      updated += 1;
    } else {
      rows.push(entry);
      added += 1;
    }
  }
  rows.forEach((item, index) => { item.index = index + 1; });
  if (!Array.isArray(raw)) {
    if (raw.files) raw.files = rows;
    else if (raw.items) raw.items = rows;
    else raw.files = rows;
    raw.updatedAt = importedAt;
  }
  if (apply) atomicWriteJson(libraryFile, Array.isArray(raw) ? rows : raw);
  return { added, updated, total: rows.length };
}

function placeMedia(records, mediaDir, apply) {
  const summary = { copied: 0, unchanged: 0, conflicts: 0 };
  for (const record of records) {
    const target = path.join(mediaDir, record.pptName);
    if (fs.existsSync(target)) {
      const targetSize = fs.statSync(target).size;
      const targetHash = record.sha256 ? sha256(target) : '';
      if (targetSize !== record.pptSize || (record.sha256 && targetHash !== record.sha256)) {
        summary.conflicts += 1;
        fail(`Existing media differs from the package: ${target}`);
      }
      summary.unchanged += 1;
      continue;
    }
    if (apply) {
      fs.mkdirSync(mediaDir, { recursive: true });
      copyAtomic(record.pptFile, target);
    }
    summary.copied += 1;
  }
  return summary;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const packageRoot = path.resolve(args['package-root'] || '');
  const targetRoot = path.resolve(args['target-root'] || process.cwd());
  if (!packageRoot || !fs.existsSync(packageRoot)) fail('--package-root is required');
  if (!fs.existsSync(targetRoot)) fail(`Target root does not exist: ${targetRoot}`);

  const mediaDir = path.resolve(args['media-dir'] || path.join(targetRoot, 'media'));
  const vaultDir = path.resolve(args['vault-dir'] || path.join(targetRoot, 'data', 'obsidian-vault'));
  const vaultUnitsRoot = path.join(vaultDir, '10-units');
  const libraryFile = path.resolve(args['library-file'] || path.join(mediaDir, 'library.json'));
  if (!fs.existsSync(libraryFile)) fail(`Library file does not exist: ${libraryFile}`);
  if (!fs.existsSync(vaultUnitsRoot)) fail(`Vault units directory does not exist: ${vaultUnitsRoot}`);

  const records = unitRecords(packageRoot, args.verifyHashes);
  const titleOnly = records.filter(item => item.titleOnly).map(item => item.unitId);

  let backup = '';
  if (args.apply) {
    const backupDir = path.join(targetRoot, 'backups', `hall-test-import-${safeTimestamp()}`);
    fs.mkdirSync(backupDir, { recursive: true });
    backup = path.join(backupDir, 'library.json');
    fs.copyFileSync(libraryFile, backup);
  }

  const documentSummary = syncRuntimeDocuments(records, vaultUnitsRoot, args.apply);
  const mediaSummary = placeMedia(records, mediaDir, args.apply);
  const librarySummary = updateLibrary(records, libraryFile, mediaDir, vaultUnitsRoot, args.apply);
  const result = {
    ok: true,
    mode: args.apply ? 'apply' : 'dry-run',
    packageRoot,
    targetRoot,
    units: records.length,
    pptx: records.length,
    totalPptBytes: records.reduce((sum, item) => sum + item.pptSize, 0),
    titleOnlyRestrictedUnits: titleOnly,
    documents: documentSummary,
    media: mediaSummary,
    library: librarySummary,
    libraryBackup: backup,
  };
  console.log(JSON.stringify(result, null, 2));
}

try {
  main();
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message, stack: error.stack }, null, 2));
  process.exitCode = 1;
}
