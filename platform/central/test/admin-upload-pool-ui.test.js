import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const indexHtml = fs.readFileSync(path.join(root, 'admin-ui', 'index.html'), 'utf8');
const bundle = fs.readFileSync(path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v11.js'), 'utf8');
const avatarBundle = fs.readFileSync(path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v22.js'), 'utf8');
const thumbnailBundle = avatarBundle;
const uploadCss = fs.readFileSync(path.join(root, 'admin-ui', 'assets', 'upload-real-v6.css'), 'utf8');
const thumbnailCss = fs.readFileSync(path.join(root, 'admin-ui', 'assets', 'upload-real-v12.css'), 'utf8');
const fileServerSource = fs.readFileSync(path.join(root, 'src', 'files', 'FileServer.js'), 'utf8');
const analysisSource = fs.readFileSync(path.join(root, 'src', 'analysis', 'UploadAnalysisService.js'), 'utf8');
const deviceManagerSource = fs.readFileSync(path.join(root, 'src', 'devices', 'DeviceManager.js'), 'utf8');

test('admin entry selects the upload-pool UI assets', () => {
  const entry = indexHtml.match(/<script[^>]+src="(\/assets\/[^"?]+\.js)(?:\?[^\"]*)?"/);
  assert.ok(entry, 'admin entry must reference an existing JavaScript bundle');
  const activeBundle = fs.readFileSync(path.join(root, 'admin-ui', entry[1].slice(1)), 'utf8');
  assert.match(activeBundle, /function uaLocalVoiceSettings\(/, 'active UI must retain local voice settings');
  assert.match(activeBundle, /机器人现场补充/, 'active UI must retain robot teaching integration');
  assert.match(activeBundle, /children:"批量删除"/, 'active UI must retain the latest FAQ batch controls');
  assert.match(indexHtml, /avatar-layout-v14\.css/);
  assert.match(indexHtml, /upload-real-v12\.css\?v=faq-batch-delete-1/);
});

test('upload modal submits a real batch queue and hands progress to the file list', () => {
  assert.match(bundle, /上传池/);
  assert.match(bundle, /multiple:!0/);
  assert.match(bundle, /后台正在解析，可在全部文件中查看进度/);
  assert.doesNotMatch(bundle, /await Promise\.all\(Ye\.map/);
  assert.match(bundle, /function uaFileStatus/);
  assert.match(bundle, /function uaFileSnapshots/);
  assert.match(uploadCss, /\.upload-message\.success/);
});

test('upload pool uses an in-product duplicate notice and rejects identical content', () => {
  assert.match(avatarBundle, /\/api\/files\/duplicate/);
  assert.match(avatarBundle, /文件已存在，未重复上传/);
  assert.match(avatarBundle, /uaShowDuplicateNotice/);
  assert.match(avatarBundle, /duplicate-notice-dialog/);
  assert.match(avatarBundle, /duplicate-notice-close/);
  assert.match(avatarBundle, /系统已阻止重复上传/);
  assert.doesNotMatch(avatarBundle, /window\.confirm\(`文件/);
  assert.match(thumbnailCss, /\.duplicate-notice-backdrop/);
  assert.match(thumbnailCss, /\.duplicate-notice-close/);
  assert.match(fileServerSource, /FILE_ALREADY_EXISTS/);
  assert.match(fileServerSource, /FILE_CONTENT_DUPLICATE/);
  assert.match(fileServerSource, /contentSha256/);
});

test('all files shows parsing and FAQ generation progress from backend jobs', () => {
  assert.match(bundle, /X\.phase==="faq_generate"/);
  assert.match(bundle, /`FAQ构建 \$\{Number\(I\.generatedFaqCount\|\|je\)\}/);
  assert.match(bundle, /`页面解析 \$\{je\}\/\$\{ve\|\|"—"\}`/);
  assert.match(bundle, /uaFileIsProcessing\(ze\)/);
  assert.match(bundle, /window\.setTimeout\(ye,1500\)/);
  assert.match(bundle, /"开始上传"/);
  assert.doesNotMatch(bundle, /开始真实上传/);
  assert.match(bundle, /publicationStatus==="PENDING_REVIEW"\)return "等待审核发布"/);
  assert.match(bundle, /filter\(X=>X\.publicationStatus!=="REJECTED"\)/);
  assert.doesNotMatch(bundle, /publicationStatus!=="PENDING_REVIEW"&&/);
  assert.match(bundle, /"file-progress pending"/);
  assert.match(uploadCss, /\.file-row \.file-progress\.pending\s*\{[^}]*color:\s*#b7791f/s);
  assert.match(uploadCss, /\.file-row \.file-progress\.pending i\s*\{[^}]*background:\s*#e7a928/s);
});

test('re-upload keeps one logical PPT and resets it to one review lifecycle', () => {
  assert.match(fileServerSource, /const finalName = safeName/);
  assert.match(fileServerSource, /removeFileArtifacts\(finalName/);
  assert.doesNotMatch(fileServerSource, /const finalName = this\._uniqueFileName/);
  assert.match(analysisSource, /publicationStatus: supported \? 'PROCESSING' : 'FAILED'/);
  assert.match(analysisSource, /reviewStatus: supported \? 'NOT_STARTED' : 'FAILED'/);
  assert.match(analysisSource, /publicationStatus: 'FAILED'/);
  assert.match(analysisSource, /existingQuestions: pageFaqs\.map\(item => item\.question\)\.slice\(-FAQ_TARGET_COUNT\)/);
});

test('published current-page FAQ list shows its total and remains independently scrollable', () => {
  assert.match(bundle, /className:"faq-count"/);
  assert.match(bundle, /"第 ",Pe\+1," 页 · 共 ",pageFaqs\.length," 条"/);
  assert.match(bundle, /sourcePage\|\|ze\.raw_json\?\.sourcePage/);
  assert.match(uploadCss, /\.content-maintenance \.page-faq\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(uploadCss, /\.content-maintenance \.faq-toolbar\s*\{[^}]*position:\s*sticky/s);
});

test('published per-file FAQ list paginates ten cards at a time', () => {
  assert.match(bundle, /pageFaqs\.slice\(faqPageState\*10,faqPageState\*10\+10\)/);
  assert.match(bundle, /Math\.ceil\(pageFaqs\.length\/10\)/);
  assert.match(bundle, /className:"faq-pagination"/);
  assert.match(bundle, /children:"上一页"/);
  assert.match(bundle, /children:"下一页"/);
  assert.match(bundle, /setFaqPageState\(0\)/);
  assert.match(uploadCss, /\.content-maintenance \.faq-pagination\s*\{/);
});

test('file list uses one neutral base color and a blue-green current-file state', () => {
  assert.doesNotMatch(bundle, /just-uploaded/);
  assert.match(uploadCss, /\.file-row\.active\s*\{[^}]*background:\s*#e7f8f4/s);
  assert.match(uploadCss, /\.file-row\.active \.file-doc\s*\{/);
});

test('admin copy, hot ranking and file covers follow the current UI contract', () => {
  assert.doesNotMatch(thumbnailBundle, /原5180真实数据/);
  assert.doesNotMatch(thumbnailBundle, /刷新真实数据/);
  assert.doesNotMatch(thumbnailBundle, /基于 61 中控持久化事件统计/);
  assert.doesNotMatch(thumbnailBundle, / · 真实后台 \$\{Re\.length\} 条/);
  assert.match(thumbnailBundle, /children:_e\?"读取中…":"刷新数据"/);
  assert.match(thumbnailBundle, /topQuestions\|\|\[\]\)\.slice\(0,5\)\.map/);
  assert.match(thumbnailBundle, /className:"file-cover file-cover-"/);
  assert.match(thumbnailBundle, /className:"file-row-copy"/);
  assert.match(thumbnailBundle, /className:"file-browser-resizer"/);
  assert.match(thumbnailBundle, /exhibition-file-panel-width/);
  assert.match(thumbnailBundle, /onPointerDown:fileResizeStart/);
  assert.match(thumbnailBundle, /title:ze\.name,children:ze\.name/);
  assert.match(thumbnailBundle, /thumbnailPages\[0\]\?\.url/);
  assert.match(thumbnailBundle, /uaReviewCommentaryText/);
  assert.match(thumbnailBundle, /review-commentary-preview/);
  assert.match(thumbnailCss, /\.file-cover\s*\{/);
  assert.match(thumbnailCss, /\.file-row-shell \.file-row\s*\{[^}]*grid-template-columns:\s*64px minmax\(0, 1fr\)[^}]*padding-right:\s*48px/s);
  assert.match(thumbnailCss, /\.file-row \.file-row-copy\s*\{[^}]*min-width:\s*0[^}]*overflow:\s*hidden/s);
  assert.match(thumbnailCss, /\.prototype-browser\.resizable-file-browser\s*\{[^}]*var\(--file-browser-width[^}]*minmax\(390px, 1\.12fr\)[^}]*minmax\(280px, \.9fr\)/s);
  assert.match(thumbnailCss, /\.file-browser-resizer::before\s*\{/);
  assert.match(thumbnailCss, /\.file-row \.file-row-copy > strong\s*\{[^}]*white-space:\s*normal[^}]*-webkit-line-clamp:\s*2/s);
  assert.match(thumbnailCss, /\.file-row \.file-progress\s*\{[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/s);
  assert.match(thumbnailCss, /@media \(max-width: 1280px\)[\s\S]*?grid-template-columns:\s*52px minmax\(0, 1fr\)/);
  assert.match(thumbnailCss, /\.operation-filters \.operation-refresh\s*\{/);
  assert.match(thumbnailCss, /\.hot-ranking > button span\s*\{[^}]*text-overflow:\s*ellipsis/s);
  assert.match(thumbnailCss, /\.review-commentary-preview\s*\{[^}]*white-space:\s*pre-wrap/s);
});

test('each file exposes a guarded complete-delete action', () => {
  assert.match(bundle, /async function uaDeleteFile/);
  assert.match(bundle, /method:"DELETE"/);
  assert.match(bundle, /className:"file-delete"/);
  assert.doesNotMatch(bundle, /window\.confirm/);
  assert.match(bundle, /className:"file-delete-modal-backdrop"/);
  assert.match(bundle, /children:"确定删除"/);
  assert.match(bundle, /className:"file-delete-preview"/);
  assert.match(bundle, /删除后将同时移除文件、讲解词、FAQ和检索索引，且不可恢复/);
  assert.match(bundle, /children:deletingFile\?"删除中…":"确定"/);
  assert.match(uploadCss, /\.file-row-shell \.file-delete\s*\{/);
  assert.match(bundle, /deletingFile===ze\.name\?"…":"×"/);
  assert.match(uploadCss, /\.file-row-shell \.file-delete\s*\{[^}]*width:\s*24px[^}]*height:\s*24px/s);
  assert.match(uploadCss, /\.file-row-shell \.file-delete\s*\{[^}]*color:\s*#7c899a[^}]*background:\s*#f7f9fc[^}]*border:\s*1px solid #d9e1eb/s);
  assert.match(uploadCss, /\.file-row-shell:has\(\.file-row\.active\) \.file-delete\s*\{[^}]*color:\s*#187d69[^}]*background:\s*#f3fbf8/s);
  assert.match(uploadCss, /\.file-delete-modal-backdrop\s*\{[^}]*position:\s*fixed[^}]*place-items:\s*center/s);
  assert.match(uploadCss, /\.file-delete-dialog\s*\{[^}]*width:\s*min\(420px,[^}]*border-radius:\s*12px/s);
});

test('current-page generated FAQs can be edited through the shared FAQ API', () => {
  assert.match(bundle, /function m\(\).*sourceFile===U\.name/s);
  assert.match(bundle, /savePageFaq=async/);
  assert.match(bundle, /sourcePage:Number/);
  assert.match(bundle, /sourceFile:en\?\.name/);
  assert.match(bundle, /FAQ已保存，FAQ库与当前页面已同步/);
  assert.match(uploadCss, /\.content-maintenance \.faq-card \.faq-edit\s*\{[^}]*height:\s*24px[^}]*font-size:\s*11px/s);
  assert.match(uploadCss, /\.content-maintenance \.faq-card\.editing \.faq-question-editor,[\s\S]*?grid-column:\s*2/s);
  assert.match(uploadCss, /\.content-maintenance \.faq-card\.editing \.faq-question-editor\s*\{[^}]*height:\s*34px/s);
  assert.match(uploadCss, /\.content-maintenance \.faq-card\.editing \.faq-answer-editor\s*\{[^}]*min-height:\s*62px/s);
});

test('page preview handles long file names, headings and metric values generically', () => {
  assert.match(uploadCss, /\.page-preview \.slide-title\s*\{[^}]*-webkit-line-clamp:\s*2/s);
  assert.match(uploadCss, /\.page-preview \.slide-preview h2\s*\{[^}]*white-space:\s*normal/s);
  assert.match(uploadCss, /\.page-preview \.metric-cards strong\s*\{[^}]*white-space:\s*nowrap/s);
  assert.match(uploadCss, /\.page-preview \.page-thumbnails strong,[^{]*\.page-preview \.page-thumbnails small\s*\{/s);
});

test('review UI batches selected files and polls visible publishing progress', () => {
  assert.match(thumbnailBundle, /\/api\/files\/review\/batch/);
  assert.match(thumbnailBundle, /\/api\/files\/review\/operations\//);
  assert.match(thumbnailBundle, /审核并上架所选资料/);
  assert.match(thumbnailBundle, /全选资料/);
  assert.match(analysisSource, /正在重建向量索引/);
  assert.match(fileServerSource, /startBatchReview/);
  assert.match(fileServerSource, /getBatchReviewOperation/);
  assert.match(thumbnailBundle, /review-progress-track/);
  assert.match(thumbnailCss, /\.review-progress-track/);
  assert.match(thumbnailBundle, /className:"review-detail-tabs"/);
  assert.match(thumbnailBundle, /className:"review-faq-card"/);
  assert.match(thumbnailBundle, /reviewFaqs\.slice\(reviewFaqPage\*8,reviewFaqPage\*8\+8\)/);
  assert.match(thumbnailBundle, /children:\["生成FAQ "/);
  assert.doesNotMatch(thumbnailBundle, /section-kicker",children:"真实待审核资料"/);
  assert.match(thumbnailCss, /\.review-detail-scroll\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(thumbnailCss, /\.review-faq-card\s*\{/);
  assert.match(fileServerSource, /const reviewFaqs = knowledgeDb\.listFaqs\(\)\.filter\(item => item\.status === 'reviewing'\)/);
  assert.match(fileServerSource, /faqs = reviewFaqs\.filter\(item => item\.sourceFile === file\.name\)/);
});

test('robot teaching drafts are integrated into pending review without a standalone tab', () => {
  assert.match(thumbnailBundle, /机器人现场录入/);
  assert.match(thumbnailBundle, /review-item robot-review-item active/);
  assert.match(thumbnailBundle, /onClick:toggleRobotList/);
  assert.match(thumbnailBundle, /setRobotExpanded\(expanded=>!expanded\)/);
  assert.match(thumbnailBundle, /"aria-expanded":isRobot&&robotExpanded/);
  assert.match(thumbnailBundle, /className:"robot-review-form"/);
  assert.match(thumbnailBundle, /\/api\/knowledge\/qa-drafts/);
  assert.match(thumbnailBundle, /method:"PATCH"/);
  assert.match(thumbnailBundle, /\/publish"/);
  assert.match(thumbnailBundle, /\/reject"/);
  assert.doesNotMatch(thumbnailBundle, /robot-drafts\.html/);
  assert.doesNotMatch(thumbnailBundle, /robot-draft-tab-link/);
  assert.match(thumbnailCss, /\.review-list \.robot-review-item\s*\{/);
  assert.match(thumbnailCss, /\.robot-review-item \.doc-icon\s*\{/);
  assert.match(thumbnailCss, /\.robot-review-form\s*\{/);
  assert.match(thumbnailCss, /\.robot-review-field textarea\s*\{/);
});

test('PPT page preview polls on demand, uses returned URLs and cancels cleanly', () => {
  assert.match(thumbnailBundle, /\/api\/ppt-thumbnails\?name=/);
  assert.match(thumbnailBundle, /encodeURIComponent\(thumbnailName\)/);
  assert.match(thumbnailBundle, /status==="GENERATING"/);
  assert.match(thumbnailBundle, /status==="READY"/);
  assert.match(thumbnailBundle, /status==="FAILED"/);
  assert.match(thumbnailBundle, /window\.setTimeout\(loadThumbnails,1000\)/);
  assert.match(thumbnailBundle, /controller\?\.abort\(\)/);
  assert.match(thumbnailBundle, /window\.clearTimeout\(timer\)/);
  assert.match(thumbnailBundle, /src:thumbnailPage\.url/);
  assert.match(thumbnailBundle, /src:ze\.url/);
  assert.match(thumbnailBundle, /method:forceNext\?"POST":"GET"/);
  assert.match(thumbnailBundle, /children:"重新生成"/);
  assert.match(thumbnailCss, /\.ppt-thumbnail-grid/);
});

test('clicking the large PPT preview opens a cancellable high-resolution page viewer', () => {
  assert.match(thumbnailBundle, /quality=full/);
  assert.match(thumbnailBundle, /正在生成当前页高清图/);
  assert.match(thumbnailBundle, /className:"ppt-lightbox-backdrop"/);
  assert.match(thumbnailBundle, /className:"ppt-lightbox-tools"/);
  assert.match(thumbnailBundle, /setPreviewOpen\(true\)/);
  assert.match(thumbnailBundle, /setPreviewOpen\(false\)/);
  assert.match(thumbnailBundle, /event\.key==="Escape"/);
  assert.match(thumbnailBundle, /setFullPreviewRetry\(Date\.now\(\)\)/);
  assert.match(thumbnailBundle, /window\.clearTimeout\(timer\)/);
  assert.match(thumbnailCss, /\.ppt-lightbox-stage/);
});

test('pending review count is visible on the top review tab', () => {
  assert.match(bundle, /className:"review-tab-badge"/);
  assert.match(bundle, /Be==="review"&&reviewCount>0/);
  assert.match(bundle, /onCountChange:setReviewCount/);
  assert.match(bundle, /reviewCountChanged\?\.\(Se\.length\)/);
  assert.doesNotMatch(bundle, /children:\["待审核资料 ",\(0,w\.jsx\)\("span",\{className:"badge"/);
  assert.match(uploadCss, /\.review-tab-badge\s*\{/);
});

test('sensitive wording shares the developer fallback rule API', () => {
  assert.match(bundle, /async function uaFallbackRules/);
  assert.match(bundle, /p\("\/api\/fallback-rules"\)/);
  assert.match(bundle, /async function uaSaveFallbackRule/);
  assert.match(bundle, /敏感口径已同步到兜底回答库/);
  assert.doesNotMatch(bundle, /当前61后台没有敏感口径管理接口/);
});

test('FAQ library paginates using a page size derived from viewport height', () => {
  assert.match(bundle, /window\.innerHeight-330/);
  assert.match(bundle, /faqListPageSize/);
  assert.match(bundle, /className:"library-pagination"/);
  assert.match(bundle, /visibleRows\.map/);
  assert.match(bundle, /每页 ",faqListPageSize," 条/);
  assert.match(uploadCss, /\.library-pagination\s*\{/);
});

test('FAQ library supports current-page batch selection, deletion, latest-first ordering and scoped search', () => {
  assert.match(thumbnailBundle, /children:"批量删除"/);
  assert.match(thumbnailBundle, /children:"全选当前页"/);
  assert.match(thumbnailBundle, /删除所选（/);
  assert.match(thumbnailBundle, /action:"delete",ids:selectedFaqIds/);
  assert.match(thumbnailBundle, /搜索问题或关联展项/);
  assert.match(thumbnailBundle, /function uaFaqPage/);
  assert.match(thumbnailBundle, /X\.set\("q",I\.query\)/);
  assert.match(thumbnailBundle, /X\.set\("library","1"\)/);
  assert.match(thumbnailCss, /\.faq-heading-actions\s*\{/);
  assert.match(thumbnailCss, /\.faq-batch-table \.faq-select-column\s*\{/);
  assert.match(thumbnailCss, /\.faq-select-all-current\s*\{/);
  assert.match(fileServerSource, /\[item\?\.question, item\?\.sourceFile\]/);
  assert.match(fileServerSource, /pageSize/);
  assert.match(fileServerSource, /faqAddedAt/);
  assert.match(fileServerSource, /\.sort\(\(left, right\) => faqAddedAt\(right\.item\) - faqAddedAt\(left\.item\)/);
  assert.match(fileServerSource, /qaStore\.hideMany\(imported\)/);
});

test('system settings separates client, digital-human and publishing responsibilities', () => {
  assert.match(avatarBundle, /\["client","客户端管理","clients\.manage"\]/);
  assert.match(avatarBundle, /\["avatar","数字人管理","avatar\.manage"\]/);
  assert.match(avatarBundle, /\["permission","权限与上架方式","settings"\]/);
  assert.match(avatarBundle, /function uaDigitalHumanManager/);
  assert.match(avatarBundle, /p\("\/api\/avatar-service"/);
  assert.match(avatarBundle, /\/api\/avatar\/open/);
  assert.match(avatarBundle, /\/api\/avatar\/exit/);
  assert.match(avatarBundle, /\/api\/avatar\/show/);
  assert.match(avatarBundle, /\/api\/avatar\/hide/);
  assert.match(avatarBundle, /\/api\/avatar\/say/);
  assert.match(avatarBundle, /使用数字人播报/);
  assert.match(thumbnailCss, /grid-template-columns: minmax\(0, 1\.08fr\) minmax\(360px, \.92fr\)/);
  assert.match(thumbnailCss, /\.digital-human-test\s*\{[\s\S]*grid-row: 2 \/ span 2/);
  assert.match(thumbnailCss, /input\[type="checkbox"\][\s\S]*appearance: none/);
});

test('formal admin UI has real account login, switching and permission management', () => {
  assert.match(avatarBundle, /exhibition-admin-token/);
  assert.match(avatarBundle, /登录管理后台/);
  assert.match(avatarBundle, /切换账号/);
  assert.match(avatarBundle, /退出当前账号/);
  assert.match(avatarBundle, /账号与权限/);
  assert.match(avatarBundle, /新增账号/);
  assert.match(avatarBundle, /重置密码/);
  assert.match(avatarBundle, /accounts\.manage/);
  assert.match(avatarBundle, /publish\.manage/);
  assert.match(fileServerSource, /\/api\/admin\/auth/);
  assert.match(fileServerSource, /\/api\/admin\/accounts/);
  assert.match(fileServerSource, /adminAccountStore\.list/);
  assert.match(thumbnailCss, /\.account-login-dialog\s*\{/);
  assert.match(thumbnailCss, /\.account-permission-grid\s*\{/);
});

test('client management owns monitoring, background and per-screen avatar position', () => {
  assert.match(avatarBundle, /function uaGetScreens/);
  assert.match(avatarBundle, /p\("\/api\/screens"\)/);
  assert.match(avatarBundle, /p\("\/api\/avatar-target"/);
  assert.match(avatarBundle, /function uaSnapshotUrl/);
  assert.match(avatarBundle, /\/api\/avatar\/set-bounds/);
  assert.match(avatarBundle, /\/api\/avatar\/reset/);
  assert.match(avatarBundle, /children:"客户端管理"/);
  assert.match(avatarBundle, /客户端数字人位置/);
  assert.match(avatarBundle, /avatarBounds/);
  assert.match(avatarBundle, /className:"avatar-metrics"/);
  assert.match(avatarBundle, /className:"avatar-live-preview"/);
  assert.match(uploadCss, /\.avatar-workspace\s*\{/);
  assert.match(uploadCss, /\.avatar-live-preview\s*\{/);
  assert.match(uploadCss, /\.avatar-bounds-fields\s*\{/);
  assert.match(avatarBundle, /function uaSaveScreenSettings/);
  assert.match(avatarBundle, /p\("\/api\/screens\/settings"/);
  assert.match(avatarBundle, /transparentBackground/);
  assert.match(avatarBundle, /backgroundFit/);
  assert.match(avatarBundle, /children:"透明背景"/);
  assert.match(avatarBundle, /className:"panel background-card avatar-background-card"/);
  assert.doesNotMatch(avatarBundle, /new-client-background\.png/);
  assert.match(deviceManagerSource, /transparentBackground: input\.transparentBackground === true/);
  assert.match(deviceManagerSource, /backgroundFit: 'cover'/);
  assert.match(deviceManagerSource, /backgroundImageUrl:/);
  assert.match(deviceManagerSource, /backgroundImageName:/);
  assert.match(deviceManagerSource, /avatarBounds/);
  assert.match(uploadCss, /\.avatar-background-card\s*\{/);
  assert.match(uploadCss, /\.background-type-options\s*\{/);
  assert.match(avatarBundle, /function uaUploadScreenBackground/);
  assert.match(avatarBundle, /\/api\/screens\/background\?deviceId=/);
  assert.match(avatarBundle, /children:"选择图片"/);
  assert.match(avatarBundle, /背景图片不能超过 20MB/);
  assert.match(thumbnailCss, /\.background-upload-control\s*\{/);
  assert.match(fileServerSource, /_handleScreenBackgroundUpload/);
  assert.match(fileServerSource, /SCREEN_BACKGROUND_MAX_BYTES/);
  assert.match(fileServerSource, /backgroundImageUrl/);
});

test('FAQ associations and target screen labels use business names', () => {
  assert.match(thumbnailBundle, /sourceFile:[^\n]*je\.source==="manual"\?"手动上传"/);
  assert.match(thumbnailBundle, /机器人现场补充/);
  assert.match(thumbnailBundle, /\["reviewing","rejected"\]\.includes/);
  assert.match(thumbnailBundle, /targetLabels/);
  assert.match(thumbnailBundle, /整馆展示/);
  assert.match(fileServerSource, /\['all', '整馆展示'\]/);
  assert.match(fileServerSource, /return \[number, `\$\{number\}号屏（\$\{clientName\}）`\]/);
  assert.match(thumbnailCss, /\.terminal-panel > button > span\s*\{[^}]*text-overflow:\s*ellipsis/s);
});
