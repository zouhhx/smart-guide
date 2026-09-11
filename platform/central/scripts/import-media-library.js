import fs from 'fs';
import path from 'path';

const sourcePath = process.argv[2] || process.env.JSON_PATH;
const outPath = process.argv[3] || path.resolve('media', 'library.json');
const allowed = new Set(['.ppt', '.pptx', '.mp4', '.webm', '.avi', '.mov', '.mkv', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg']);

if (!sourcePath) {
  console.error('Usage: node scripts/import-media-library.js <source-json> [out-json]');
  process.exit(1);
}

const raw = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
const records = Array.isArray(raw) ? raw : Object.values(raw).flat().filter(x => x && typeof x === 'object');

const usedNames = new Set();
const files = [];

for (const item of records) {
  const url = pickUrl(item);
  if (!url) continue;

  const ext = path.extname(new URL(url).pathname).toLowerCase();
  if (!allowed.has(ext)) continue;

  const type = ['.ppt', '.pptx'].includes(ext)
    ? 'ppt'
    : ['.mp4', '.webm', '.avi', '.mov', '.mkv'].includes(ext)
      ? 'video'
      : 'image';
  const title = clean(item.exhibit_title || item.program_title || item.original_file_name || `media-${item.id || files.length + 1}`);
  const name = uniqueName(`${String(files.length + 1).padStart(3, '0')}_${safeFileName(title)}${ext}`);

  files.push({
    index: files.length + 1,
    id: item.id,
    title,
    name,
    originalName: clean(item.original_file_name || path.basename(new URL(url).pathname)),
    description: clean(item.exhibit_description || item.creation_description || ''),
    keywords: clean(item.keywords || ''),
    commentary: clean(item.commentary || item.all_commentary || item.ppt_commentary || ''),
    programTitle: clean(item.program_title || item.program_name || ''),
    ext,
    type,
    size: item.file_size || 0,
    url,
    thumbnailUrl: pickThumbnail(item),
  });
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify({ generatedAt: new Date().toISOString(), files }, null, 2), 'utf8');
console.log(`Imported ${files.length} PPT/video/image files into ${outPath}`);

function pickUrl(item) {
  return clean(item.minio_url || item.file_url || item.program_path || '');
}

function pickThumbnail(item) {
  return clean(item.minio_thumbnail_url || item.thumbnail_url || item.cover_url || '');
}

function clean(value) {
  return String(value ?? '').trim();
}

function safeFileName(value) {
  return clean(value)
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .slice(0, 80) || 'media';
}

function uniqueName(name) {
  let next = name;
  let counter = 2;
  while (usedNames.has(next.toLowerCase())) {
    const ext = path.extname(name);
    next = `${path.basename(name, ext)}_${counter}${ext}`;
    counter += 1;
  }
  usedNames.add(next.toLowerCase());
  return next;
}
