import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceBundlePath = path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v14.js');
const outputBundlePath = path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v22.js');
const sourceCssPath = path.join(root, 'admin-ui', 'assets', 'upload-real-v6.css');
const outputCssPath = path.join(root, 'admin-ui', 'assets', 'upload-real-v12.css');
const indexPath = path.join(root, 'admin-ui', 'index.html');

function replaceOnce(source, needle, replacement, label) {
  const first = source.indexOf(needle);
  const last = source.lastIndexOf(needle);
  if (first < 0 || first !== last) {
    throw new Error(`${label}: expected one stable build anchor, found ${first < 0 ? 0 : 'multiple'}`);
  }
  return source.slice(0, first) + replacement + source.slice(first + needle.length);
}

let bundle = fs.readFileSync(sourceBundlePath, 'utf8');

bundle = replaceOnce(
  bundle,
  'async function _(){let I=await p("/api/qa");return(Array.isArray(I.items)?I.items:[]).filter(je=>!String(je?.id||"").toLowerCase().endsWith("_faq_package")).map(je=>({...je,source:"exhibition-legacy"}))}',
  'async function _(){let I=await p("/api/qa");return(Array.isArray(I.items)?I.items:[]).filter(je=>!String(je?.id||"").toLowerCase().endsWith("_faq_package")).filter(je=>!["reviewing","rejected"].includes(String(je?.status||"").toLowerCase())).map(je=>({...je,source:je.source||"exhibition-legacy",sourceFile:je.sourceFile||(je.source==="robot-teaching"?"机器人现场补充":je.source==="manual"?"手动上传":"")}))}',
  'FAQ library publication and association normalization',
);

bundle = replaceOnce(
  bundle,
  'Ae(ke.map(Ie=>I==="FAQ库"?[Ie.question,Ie.answer,(Ie.keywords||[]).slice(0,4).join("、")||"默认知识库",Ie.enabled?"已上架":"审核中"]:[Ie.name,Ie.answer,Ie.category||"通用",Ie.enabled!==false?"启用":"停用"]))',
  'Ae(ke.map(Ie=>I==="FAQ库"?[Ie.question,Ie.answer,Ie.sourceFile||(Ie.source==="robot-teaching"?"机器人现场补充":Ie.source==="manual"?"手动上传":"未关联展项"),Ie.enabled&&String(Ie.status||"published")==="published"?"已上架":"审核中"]:[Ie.name,Ie.answer,Ie.category||"通用",Ie.enabled!==false?"启用":"停用"]))',
  'FAQ association table rows',
);

bundle = replaceOnce(
  bundle,
  'Zn=()=>{qe(null),mn(true),Me(true),Mn(["","","",I==="FAQ库"?"审核中":"启用"])}',
  'Zn=()=>{qe(null),mn(true),Me(true),Mn(["","",I==="FAQ库"?"手动上传":"",I==="FAQ库"?"审核中":"启用"])}',
  'manual FAQ association default',
);

bundle = replaceOnce(
  bundle,
  'keywords:an[2].split(/[,，、;；\\n]/).map(ke=>ke.trim()).filter(Boolean),enabled:an[3]==="已上架"',
  'keywords:!Le&&Ue!==null?Be[Ue]?.keywords||[]:[],sourceFile:!Le&&Ue!==null?Be[Ue]?.sourceFile||an[2]||"手动上传":"手动上传",enabled:an[3]==="已上架"',
  'FAQ association save payload',
);

bundle = replaceOnce(
  bundle,
  'Ne?(0,w.jsx)("input",{value:an[2],onChange:G=>Mn(an.map((ke,Ie)=>Ie===2?G.target.value:ke))}):(0,w.jsx)("p",{children:Ce[2]})',
  'Ne&&I!=="FAQ库"?(0,w.jsx)("input",{value:an[2],onChange:G=>Mn(an.map((ke,Ie)=>Ie===2?G.target.value:ke))}):(0,w.jsx)("div",{className:I==="FAQ库"?"readonly faq-association-readonly":"",children:Ce[2]||"手动上传"})',
  'read-only FAQ association detail',
);

const authShellStart = bundle.indexOf('function se(){');
const authShellEnd = bundle.indexOf('function ce(', authShellStart);
if (authShellStart < 0 || authShellEnd < 0) throw new Error('admin shell: stable component anchors changed');
const authShell = `const uaAdminTokenKey="exhibition-admin-token";function uaAdminToken(){return window.localStorage.getItem(uaAdminTokenKey)||""}function uaHasPermission(I,X){return Boolean(I?.permissions?.includes(X))}async function uaAdminRequest(I,X={}){let je=new Headers(X.headers||{}),ve=uaAdminToken();ve&&je.set("Authorization","Bearer "+ve),X.body&&!je.has("Content-Type")&&je.set("Content-Type","application/json");let U=await fetch(uaApi(I),{...X,headers:je,cache:"no-store"}),xe=await U.json().catch(()=>null);if(!U.ok)throw Error(xe?.error||"账号服务请求失败（HTTP "+U.status+"）");return xe}function uaRoleName(I){return{super_admin:"超级管理员",admin:"管理员",reviewer:"审核员",operator:"现场运维",viewer:"只读用户"}[I]||"用户"}function uaLoginDialog({account:I,onLogin:X,onLogout:je,onClose:ve}){let[U,xe]=(0,l.useState)(""),[Re,Ae]=(0,l.useState)(""),[Be,_e]=(0,l.useState)(false),[be,Pe]=(0,l.useState)("");async function Ee(ln){ln.preventDefault(),_e(true),Pe("");try{let Ve=await uaAdminRequest("/api/admin/auth/login",{method:"POST",body:JSON.stringify({username:U,password:Re})});window.localStorage.setItem(uaAdminTokenKey,Ve.token),X(Ve.account)}catch(Ve){Pe(Ve instanceof Error?Ve.message:String(Ve))}finally{_e(false)}}return(0,w.jsx)("div",{className:"account-modal-backdrop",children:(0,w.jsxs)("form",{className:"account-login-dialog",onSubmit:Ee,children:[(0,w.jsxs)("div",{className:"account-dialog-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("h2",{children:I?"切换账号":"登录管理后台"}),(0,w.jsx)("p",{children:I?"输入其他账号和密码完成切换。":"请使用管理账号登录。"})]}),I&&(0,w.jsx)("button",{type:"button",className:"account-dialog-close",onClick:ve,"aria-label":"关闭",children:"×"})]}),I&&(0,w.jsxs)("div",{className:"current-account-strip",children:[(0,w.jsx)("span",{children:String(I.displayName||I.username).slice(0,1)}),(0,w.jsxs)("div",{children:[(0,w.jsx)("strong",{children:I.displayName||I.username}),(0,w.jsxs)("small",{children:[uaRoleName(I.role)," · ",I.username]})]}),(0,w.jsx)("button",{type:"button",className:"ghost",onClick:je,children:"退出当前账号"})]}),(0,w.jsxs)("label",{className:"account-field",children:[(0,w.jsx)("span",{children:"账号"}),(0,w.jsx)("input",{value:U,onChange:ln=>xe(ln.target.value),autoComplete:"username",placeholder:"请输入账号",autoFocus:true})]}),(0,w.jsxs)("label",{className:"account-field",children:[(0,w.jsx)("span",{children:"密码"}),(0,w.jsx)("input",{type:"password",value:Re,onChange:ln=>Ae(ln.target.value),autoComplete:"current-password",placeholder:"请输入密码"})]}),be&&(0,w.jsx)("div",{className:"account-form-error",children:be}),(0,w.jsx)("button",{type:"submit",className:"primary full",disabled:Be||!U.trim()||!Re,children:Be?"正在登录…":I?"切换账号":"登录"})]})})}function se(){let[I,X]=(0,l.useState)("knowledge"),[je,ve]=(0,l.useState)("scripts"),[U,xe]=(0,l.useState)(0),[Re,Ae]=(0,l.useState)(false),[Be,_e]=(0,l.useState)(0),[be,Pe]=(0,l.useState)(""),[Ee,ln]=(0,l.useState)({state:"checking",label:"正在连接中控服务"}),[Ve,Se]=(0,l.useState)(null),[Qe,dn]=(0,l.useState)(false),[Ye,Sn]=(0,l.useState)(false);(0,l.useEffect)(()=>{let Ue=new AbortController,qe=true,Ne=window.setTimeout(()=>Ue.abort(),3e3);return f(Ue.signal).then(Me=>{qe&&ln(Me)}).catch(()=>{qe&&ln({state:"offline",label:"中控服务暂不可用"})}),()=>{qe=false,window.clearTimeout(Ne),Ue.abort()}},[]),(0,l.useEffect)(()=>{uaAdminToken()?uaAdminRequest("/api/admin/auth/me").then(Ue=>Se(Ue.account)).catch(()=>{window.localStorage.removeItem(uaAdminTokenKey),Se(null)}).finally(()=>Sn(true)):Sn(true)},[]),(0,l.useEffect)(()=>{if(!Ve)return;let Ue=uaHasPermission(Ve,"knowledge.view")?"knowledge":uaHasPermission(Ve,"records.view")?"records":"technology";if(I==="knowledge"&&!uaHasPermission(Ve,"knowledge.view")||I==="records"&&!uaHasPermission(Ve,"records.view"))X(Ue)},[Ve]);function mn(Ue){Pe(Ue),window.setTimeout(()=>Pe(""),2200)}function En(){window.localStorage.removeItem(uaAdminTokenKey),Se(null),dn(false)}if(!Ye)return(0,w.jsx)("div",{className:"account-loading",children:"正在读取账号信息…"});if(!Ve)return(0,w.jsx)(uaLoginDialog,{account:null,onLogin:Ue=>Se(Ue),onLogout:En});let $e=uaHasPermission(Ve,"knowledge.view"),Jn=uaHasPermission(Ve,"records.view"),Ot=["clients.manage","avatar.manage","publish.manage","accounts.manage"].some(Ue=>uaHasPermission(Ve,Ue));return(0,w.jsxs)("div",{className:"app-shell",children:[(0,w.jsxs)("aside",{className:"sidebar",children:[(0,w.jsxs)("div",{className:"brand",children:[(0,w.jsx)("span",{children:"◆"})," 智能讲解平台"]}),(0,w.jsxs)("nav",{children:[$e&&(0,w.jsx)(ce,{active:I==="knowledge",icon:"book",label:"知识管理",onClick:()=>X("knowledge")}),Jn&&(0,w.jsx)(ce,{active:I==="records",icon:"chart",label:"讲解记录",onClick:()=>X("records")}),Ot&&(0,w.jsx)(ce,{active:I==="technology",icon:"gear",label:"系统设置",onClick:()=>X("technology")})]}),(0,w.jsxs)("button",{className:"sidebar-foot account-switch-button",onClick:()=>dn(true),children:[(0,w.jsx)("span",{className:"avatar",children:String(Ve.displayName||Ve.username).slice(0,1)}),(0,w.jsxs)("div",{children:[(0,w.jsx)("strong",{children:Ve.displayName||Ve.username}),(0,w.jsx)("small",{children:Ve.username})]})]})]}),(0,w.jsxs)("main",{className:"workspace",children:[(0,w.jsxs)("header",{className:"topbar",children:[(0,w.jsx)("strong",{children:I==="knowledge"?"知识管理":I==="records"?"讲解记录":"系统设置"}),(0,w.jsxs)("div",{className:"top-actions",children:[(0,w.jsxs)("span",{className:"runtime-status "+Ee.state,children:[(0,w.jsx)("span",{className:"status-dot"}),Ee.label]}),(0,w.jsx)("a",{className:"developer-link",href:"/developer/",children:"开发者界面"}),(0,w.jsx)("button",{className:"top-user account-top-button",onClick:()=>dn(true),children:Ve.displayName||Ve.username})]})]}),I==="knowledge"&&$e&&(0,w.jsx)(D,{tab:je,setTab:ve,selectedUnit:U,setSelectedUnit:xe,onUpload:()=>uaHasPermission(Ve,"knowledge.manage")?Ae(true):mn("当前账号没有上传权限"),notify:mn,dataRevision:Be}),I==="records"&&Jn&&(0,w.jsx)(de,{notify:mn}),I==="technology"&&Ot&&(0,w.jsx)(A,{notify:mn,account:Ve})]}),Re&&(0,w.jsx)(pe,{close:()=>Ae(false),done:Ue=>{Ae(false),_e(qe=>qe+1),mn("已上传 "+Ue.count+" 个文件；后台正在解析，可在全部文件中查看进度")}}),Qe&&(0,w.jsx)(uaLoginDialog,{account:Ve,onLogin:Ue=>{Se(Ue),dn(false)},onLogout:En,onClose:()=>dn(false)}),be&&(0,w.jsxs)("div",{className:"toast",children:["✓ ",be]})]})}`;
bundle = bundle.slice(0, authShellStart) + authShell + bundle.slice(authShellEnd);

bundle = replaceOnce(
  bundle,
  '(0,w.jsx)("div",{className:"tabs",children:[["scripts","内容浏览"],["faq","FAQ库"],["sensitive","敏感口径"],["review","待审核"]].map(([Be,_e])=>(0,w.jsxs)("button",{className:I===Be?"active":"",onClick:()=>X(Be),children:[_e,Be==="review"&&reviewCount>0&&(0,w.jsx)("span",{className:"review-tab-badge",children:reviewCount>99?"99+":reviewCount})]},Be))})',
  '(0,w.jsx)("div",{className:"tabs",children:[["scripts","内容浏览"],["faq","FAQ库"],["sensitive","敏感口径"],["review","待审核"]].map(([Be,_e])=>(0,w.jsxs)("button",{className:I===Be?"active":"",onClick:()=>X(Be),children:[_e,Be==="review"&&reviewCount>0&&(0,w.jsx)("span",{className:"review-tab-badge",children:reviewCount>99?"99+":reviewCount})]},Be))})',
  'remove standalone robot draft entry',
);

bundle = replaceOnce(
  bundle,
  '(0,l.useEffect)(()=>{let Be=true,_e=async()=>{try{let be2=await p("/api/files/review");Be&&setReviewCount(Array.isArray(be2.items)?be2.items.length:0)}catch{}};_e();let be=window.setInterval(_e,15e3);return()=>{Be=false,window.clearInterval(be)}},[Re]);',
  '(0,l.useEffect)(()=>{let Be=true,_e=async()=>{try{let[be2,Pe2]=await Promise.all([p("/api/files/review"),uaAdminRequest("/api/knowledge/qa-drafts")]),Ee2=Array.isArray(Pe2.items)?Pe2.items.filter(item=>["PENDING_REVIEW","INDEX_FAILED"].includes(String(item.status||""))).length:0;Be&&setReviewCount((Array.isArray(be2.items)?be2.items.length:0)+Ee2)}catch{}};_e();let be=window.setInterval(_e,15e3);return()=>{Be=false,window.clearInterval(be)}},[Re]);',
  'pending review total count',
);

bundle = replaceOnce(
  bundle,
  '[faqSaving,setFaqSaving]=(0,l.useState)(false);(0,l.useEffect)',
  '[faqSaving,setFaqSaving]=(0,l.useState)(false),[thumbnailState,setThumbnailState]=(0,l.useState)({name:"",status:"IDLE",slideCount:0,pages:[],error:""}),[thumbnailRetry,setThumbnailRetry]=(0,l.useState)(0);(0,l.useEffect)',
  'thumbnail state'
);

const listEffectEnd = '},[U,X]);let en=qe[I],Zn=en?Math.max(1,en.slideCount||Object.keys(en.commentaryPages||{}).length||1):1,Rn=Array.from';
const thumbnailEffect = `},[U,X]);let thumbnailFile=qe[I],thumbnailName=String(thumbnailFile?.path||thumbnailFile?.name||"").replace(/\\\\/g,"/"),isPpt=Boolean(thumbnailFile&&T(thumbnailFile)==="PPT");(0,l.useEffect)(()=>{if(!isPpt||!thumbnailName){setThumbnailState({name:thumbnailName,status:"IDLE",slideCount:0,pages:[],error:""});return}let active=true,timer=null,controller=null,forceNext=thumbnailRetry>0;setThumbnailState({name:thumbnailName,status:"GENERATING",slideCount:0,pages:[],error:""});async function loadThumbnails(){controller=new AbortController;try{let response=await fetch(uaApi("/api/ppt-thumbnails?name="+encodeURIComponent(thumbnailName)),{method:forceNext?"POST":"GET",cache:"no-store",signal:controller.signal,headers:{Accept:"application/json"}});forceNext=false;let payload=await response.json().catch(()=>null);if(!response.ok&&response.status!==202)throw Error(payload?.error||"缩略图请求失败（HTTP "+response.status+"）");if(!active)return;let status=String(payload?.status||response.status===202?"GENERATING":"FAILED").toUpperCase(),pages=Array.isArray(payload?.pages)?payload.pages:[];setThumbnailState({name:thumbnailName,status,slideCount:Number(payload?.slideCount||pages.length||0),pages,error:payload?.error||""});if(status==="GENERATING"||response.status===202)timer=window.setTimeout(loadThumbnails,1000);else if(status==="READY"&&pages.length)Ee(page=>Math.min(page,pages.length-1))}catch(error){if(active&&error?.name!=="AbortError")setThumbnailState({name:thumbnailName,status:"FAILED",slideCount:0,pages:[],error:error instanceof Error?error.message:String(error)})}}loadThumbnails();return()=>{active=false,controller?.abort(),timer&&window.clearTimeout(timer)}},[thumbnailName,isPpt,thumbnailRetry]);let en=thumbnailFile,thumbnailCurrent=thumbnailState.name===thumbnailName?thumbnailState:{status:"GENERATING",slideCount:0,pages:[],error:""},thumbnailPages=thumbnailCurrent.status==="READY"?thumbnailCurrent.pages:[],Zn=en?Math.max(1,thumbnailCurrent.status==="READY"?thumbnailCurrent.slideCount||thumbnailPages.length:en.slideCount||Object.keys(en.commentaryPages||{}).length||1):1,Rn=Array.from`;
const normalizedThumbnailEffect = thumbnailEffect.replace(
  'payload?.status||response.status===202?"GENERATING":"FAILED"',
  'payload?.status||(response.status===202?"GENERATING":"FAILED")'
);
bundle = replaceOnce(bundle, listEffectEnd, normalizedThumbnailEffect, 'thumbnail polling effect');

bundle = replaceOnce(
  bundle,
  '[thumbnailRetry,setThumbnailRetry]=(0,l.useState)(0);(0,l.useEffect)',
  '[thumbnailRetry,setThumbnailRetry]=(0,l.useState)(0),[previewOpen,setPreviewOpen]=(0,l.useState)(false),[fullPreviewState,setFullPreviewState]=(0,l.useState)({status:"IDLE",page:null,error:""}),[fullPreviewRetry,setFullPreviewRetry]=(0,l.useState)(0),[previewZoom,setPreviewZoom]=(0,l.useState)(1),[filePanelWidth,setFilePanelWidth]=(0,l.useState)(()=>{try{return Math.max(260,Math.min(620,Number(window.localStorage.getItem("exhibition-file-panel-width"))||300))}catch{return 300}}),fileResizeStart=event=>{event.preventDefault();let startX=event.clientX,startWidth=filePanelWidth,nextWidth=startWidth,move=pointerEvent=>{nextWidth=Math.max(260,Math.min(620,startWidth+pointerEvent.clientX-startX)),setFilePanelWidth(nextWidth)},finish=()=>{window.removeEventListener("pointermove",move),window.removeEventListener("pointerup",finish),window.removeEventListener("pointercancel",finish),document.body.classList.remove("resizing-file-browser");try{window.localStorage.setItem("exhibition-file-panel-width",String(nextWidth))}catch{}};document.body.classList.add("resizing-file-browser"),window.addEventListener("pointermove",move),window.addEventListener("pointerup",finish),window.addEventListener("pointercancel",finish)};(0,l.useEffect)',
  'full preview state'
);

bundle = replaceOnce(
  bundle,
  '(0,w.jsxs)("div",{className:"prototype-browser",children:[',
  '(0,w.jsxs)("div",{className:"prototype-browser resizable-file-browser",style:{"--file-browser-width":filePanelWidth+"px"},children:[',
  'resizable file browser grid',
);

const thumbnailEffectEnd = '},[thumbnailName,isPpt,thumbnailRetry]);let en=thumbnailFile';
const fullPreviewEffect = `},[thumbnailName,isPpt,thumbnailRetry]);(0,l.useEffect)(()=>{if(!previewOpen||!thumbnailName){setFullPreviewState({status:"IDLE",page:null,error:""});return}let active=true,timer=null,controller=null,forceNext=fullPreviewRetry>0;setFullPreviewState({status:"GENERATING",page:null,error:""});async function loadFullPreview(){controller=new AbortController;try{let response=await fetch(uaApi("/api/ppt-thumbnails?name="+encodeURIComponent(thumbnailName)+"&page="+(Pe+1)+"&quality=full"),{method:forceNext?"POST":"GET",cache:"no-store",signal:controller.signal,headers:{Accept:"application/json"}});forceNext=false;let payload=await response.json().catch(()=>null);if(!response.ok&&response.status!==202)throw Error(payload?.error||"高清页图请求失败（HTTP "+response.status+"）");if(!active)return;let status=String(payload?.status||(response.status===202?"GENERATING":"FAILED")).toUpperCase(),page=payload?.page||(Array.isArray(payload?.pages)?payload.pages[0]:null);setFullPreviewState({status,page,error:payload?.error||""});if(status==="GENERATING"||response.status===202)timer=window.setTimeout(loadFullPreview,1000)}catch(error){if(active&&error?.name!=="AbortError")setFullPreviewState({status:"FAILED",page:null,error:error instanceof Error?error.message:String(error)})}}loadFullPreview();return()=>{active=false,controller?.abort(),timer&&window.clearTimeout(timer)}},[previewOpen,thumbnailName,Pe,fullPreviewRetry]);(0,l.useEffect)(()=>{if(!previewOpen)return;let closeOnEscape=event=>{event.key==="Escape"&&setPreviewOpen(false)};return window.addEventListener("keydown",closeOnEscape),()=>window.removeEventListener("keydown",closeOnEscape)},[previewOpen]);let en=thumbnailFile`;
bundle = replaceOnce(bundle, thumbnailEffectEnd, fullPreviewEffect, 'full preview polling effect');

bundle = replaceOnce(
  bundle,
  '),G=Rn[Math.min(Pe,Rn.length-1)],ke=',
  '),thumbnailPage=thumbnailPages.find(ze=>Number(ze.page)===Pe+1)||thumbnailPages[Pe],G=Rn[Math.min(Pe,Rn.length-1)],ke=',
  'selected thumbnail page'
);

const previewStart = '(0,w.jsxs)("div",{className:"panel page-preview",children:';
const previewEnd = '}),(0,w.jsxs)("div",{className:"panel content-maintenance",children:';
const start = bundle.indexOf(previewStart);
const end = bundle.indexOf(previewEnd, start);
if (start < 0 || end < 0 || bundle.indexOf(previewStart, start + 1) >= 0) {
  throw new Error('page preview: stable section anchors changed');
}
const preview = `(0,w.jsxs)("div",{className:"panel page-preview",children:[(0,w.jsxs)("div",{className:"preview-head",children:[(0,w.jsx)("strong",{children:"页面预览"}),(0,w.jsxs)("small",{children:[en?.name||"请选择文件","　·　第 ",Pe+1," / ",Zn," 页"]})]}),isPpt?(0,w.jsxs)(w.Fragment,{children:[thumbnailCurrent.status==="READY"&&thumbnailPage?(0,w.jsx)("div",{className:"ppt-page-preview",children:(0,w.jsx)("img",{className:"ppt-page-preview-image",src:thumbnailPage.url,alt:"第 "+(Pe+1)+"页缩略图"})}):thumbnailCurrent.status==="FAILED"?(0,w.jsxs)("div",{className:"thumbnail-state failed",children:[(0,w.jsx)("strong",{children:"逐页预览生成失败"}),(0,w.jsx)("span",{children:thumbnailCurrent.error||"缩略图服务未返回有效结果"}),(0,w.jsx)("button",{className:"thumbnail-retry",onClick:()=>setThumbnailRetry(Date.now()),children:"重新生成"})]}):(0,w.jsxs)("div",{className:"thumbnail-state generating",children:[(0,w.jsx)("i",{}),(0,w.jsx)("strong",{children:"正在生成逐页预览"}),(0,w.jsx)("span",{children:"首次打开需要一点时间，完成后会自动显示"})]}),(0,w.jsxs)("div",{className:"pages-title",children:[(0,w.jsx)("strong",{children:"全部页面"}),(0,w.jsxs)("span",{children:[thumbnailCurrent.status==="READY"?Zn:"生成中"," 页"]})]}),thumbnailCurrent.status==="READY"?(0,w.jsx)("div",{className:"page-thumbnails ppt-thumbnail-grid",children:thumbnailPages.map((ze,Q)=>(0,w.jsxs)("button",{className:Q===Pe?"active":"",onClick:()=>sn(Q),children:[(0,w.jsx)("img",{loading:"lazy",src:ze.url,alt:"第 "+ze.page+"页"}),(0,w.jsxs)("span",{children:["第 ",ze.page," 页"]})]},ze.page))}):thumbnailCurrent.status==="FAILED"?null:(0,w.jsx)("div",{className:"thumbnail-grid-state",children:"缩略图生成完成后将在这里按页展示"})]}):(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"slide-preview",children:[(0,w.jsx)("div",{className:"slide-title",children:G?.heading||"暂无资料"}),(0,w.jsx)("h2",{children:G?.title||"请选择左侧真实文件"}),(0,w.jsx)("p",{children:G?.lead||"中控服务尚未返回文件数据"}),(0,w.jsx)("div",{className:"metric-cards",children:[["文件类型",en?T(en):"-"],["文件大小",en?E(en.size):"-"],["讲解页数",String(Zn)]].map(([ze,Q])=>(0,w.jsxs)("div",{children:[(0,w.jsx)("b",{children:ze}),(0,w.jsx)("strong",{children:Q})]},ze))})]}),(0,w.jsxs)("div",{className:"pages-title",children:[(0,w.jsx)("strong",{children:"全部页面"}),(0,w.jsxs)("span",{children:[Zn," 页"]})]}),(0,w.jsx)("div",{className:"page-thumbnails",children:Rn.map((ze,Q)=>(0,w.jsxs)("button",{className:Q===Pe?"active":"",onClick:()=>sn(Q),children:[(0,w.jsx)("b",{children:ze.no}),(0,w.jsx)("strong",{children:ze.title}),(0,w.jsx)("small",{children:ze.subtitle}),(0,w.jsx)("i",{}),(0,w.jsx)("i",{})]},ze.no))})]})]}`;
const completePreview = `${preview})`;
bundle = bundle.slice(0, start) + completePreview + bundle.slice(end + 2);

bundle = replaceOnce(
  bundle,
  '}),(0,w.jsxs)("div",{className:"panel page-preview",children:',
  '}),(0,w.jsx)("button",{type:"button",className:"file-browser-resizer","aria-label":"调整文件列表宽度",title:"拖动调整文件列表宽度，双击恢复默认",onPointerDown:fileResizeStart,onDoubleClick:()=>{setFilePanelWidth(300);try{window.localStorage.setItem("exhibition-file-panel-width","300")}catch{}},onKeyDown:event=>{if(event.key!=="ArrowLeft"&&event.key!=="ArrowRight")return;event.preventDefault(),setFilePanelWidth(width=>{let next=Math.max(260,Math.min(620,width+(event.key==="ArrowRight"?20:-20)));try{window.localStorage.setItem("exhibition-file-panel-width",String(next))}catch{}return next})}}),(0,w.jsxs)("div",{className:"panel page-preview",children:',
  'file browser resize handle',
);

bundle = replaceOnce(
  bundle,
  '(0,w.jsx)("div",{className:"ppt-page-preview",children:(0,w.jsx)("img",{className:"ppt-page-preview-image",src:thumbnailPage.url,alt:"第 "+(Pe+1)+"页缩略图"})})',
  '(0,w.jsxs)("button",{type:"button",className:"ppt-page-preview",title:"点击查看高清大图",onClick:()=>{setPreviewZoom(1),setFullPreviewRetry(0),setPreviewOpen(true)},children:[(0,w.jsx)("img",{className:"ppt-page-preview-image",src:thumbnailPage.url,alt:"第 "+(Pe+1)+"页缩略图"}),(0,w.jsx)("span",{className:"ppt-preview-hint",children:"点击查看高清大图"})]})',
  'clickable PPT preview'
);

const modalAnchor = ']}),deleteConfirmFile&&';
const fullPreviewModal = `]}),previewOpen&&(0,w.jsx)("div",{className:"ppt-lightbox-backdrop",role:"presentation",onMouseDown:()=>setPreviewOpen(false),children:(0,w.jsxs)("div",{className:"ppt-lightbox",role:"dialog","aria-modal":"true","aria-label":"PPT高清页图",onMouseDown:event=>event.stopPropagation(),children:[(0,w.jsxs)("div",{className:"ppt-lightbox-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("strong",{children:en?.name||"PPT高清预览"}),(0,w.jsxs)("span",{children:["第 ",Pe+1," 页 · ",fullPreviewState.page?.width||1920," × ",fullPreviewState.page?.height||1080]})]}),(0,w.jsxs)("div",{className:"ppt-lightbox-tools",children:[(0,w.jsx)("button",{title:"缩小","aria-label":"缩小",disabled:previewZoom<=.5,onClick:()=>setPreviewZoom(value=>Math.max(.5,Number((value-.25).toFixed(2)))),children:"−"}),(0,w.jsx)("button",{className:"zoom-value",title:"恢复原始缩放",onClick:()=>setPreviewZoom(1),children:Math.round(previewZoom*100)+"%"}),(0,w.jsx)("button",{title:"放大","aria-label":"放大",disabled:previewZoom>=3,onClick:()=>setPreviewZoom(value=>Math.min(3,Number((value+.25).toFixed(2)))),children:"+"}),(0,w.jsx)("button",{title:"关闭","aria-label":"关闭",onClick:()=>setPreviewOpen(false),children:"×"})]})]}),(0,w.jsx)("div",{className:"ppt-lightbox-stage",children:fullPreviewState.status==="READY"&&fullPreviewState.page?(0,w.jsx)("img",{src:fullPreviewState.page.url,alt:"第 "+(Pe+1)+"页高清图",style:{transform:"scale("+previewZoom+")"},onClick:()=>setPreviewZoom(value=>value===1?1.5:1)}):fullPreviewState.status==="FAILED"?(0,w.jsxs)("div",{className:"thumbnail-state failed",children:[(0,w.jsx)("strong",{children:"高清页图生成失败"}),(0,w.jsx)("span",{children:fullPreviewState.error||"请稍后重试"}),(0,w.jsx)("button",{className:"thumbnail-retry",onClick:()=>setFullPreviewRetry(Date.now()),children:"重新生成"})]}):(0,w.jsxs)("div",{className:"thumbnail-state generating",children:[(0,w.jsx)("i",{}),(0,w.jsx)("strong",{children:"正在生成当前页高清图"}),(0,w.jsx)("span",{children:"首次打开需要一点时间，生成后会自动显示"})]})})]})}),deleteConfirmFile&&`;
bundle = replaceOnce(bundle, modalAnchor, fullPreviewModal, 'full preview modal');

bundle = replaceOnce(
  bundle,
  '}finally{window.clearTimeout(ve)}}var C=',
  '}finally{window.clearTimeout(ve)}}async function uaStartBatchReview(I){let X=await p("/api/files/review/batch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"approve",fileNames:I})});if(!X?.operation)throw Error("批量上架任务创建失败");return X.operation}async function uaGetBatchReview(I){let X=await p("/api/files/review/operations/"+encodeURIComponent(I));if(!X?.operation)throw Error("读取批量上架进度失败");return X.operation}var C=',
  'batch review API helpers'
);

const reviewStart = 'function ue({notify:I,onCountChange:reviewCountChanged}){';
const reviewEnd = '}function uaApi(I)';
const reviewStartIndex = bundle.indexOf(reviewStart);
const reviewEndIndex = bundle.indexOf(reviewEnd, reviewStartIndex);
if (reviewStartIndex < 0 || reviewEndIndex < 0 || bundle.indexOf(reviewStart, reviewStartIndex + 1) >= 0) {
  throw new Error('review UI: stable section anchors changed');
}
const reviewComponent = `function ue({notify:I,onCountChange:reviewCountChanged}){let[X,je]=(0,l.useState)([]),[ve,U]=(0,l.useState)(0),[xe,Re]=(0,l.useState)(true),[Ae,Be]=(0,l.useState)(""),[_e,be]=(0,l.useState)(false),[selectedNames,setSelectedNames]=(0,l.useState)([]),[reviewOperation,setReviewOperation]=(0,l.useState)(null),Pe=async()=>{Re(true),Be("");try{let Ve=await p("/api/files/review"),Se=Array.isArray(Ve.items)?Ve.items:[];je(Se),reviewCountChanged?.(Se.length),setSelectedNames(names=>names.filter(name=>Se.some(file=>file.name===name))),U(Qe=>Math.min(Qe,Math.max(0,Se.length-1)))}catch(Ve){Be(Ve instanceof Error?Ve.message:String(Ve))}finally{Re(false)}};(0,l.useEffect)(()=>{Pe()},[]);(0,l.useEffect)(()=>{if(!reviewOperation?.id||["COMPLETED","FAILED"].includes(reviewOperation.status))return;let active=true,timer=null;async function pollReview(){try{let operation=await uaGetBatchReview(reviewOperation.id);if(!active)return;setReviewOperation(operation);if(operation.status==="COMPLETED"){be(false),setSelectedNames([]),I(operation.message||"所选资料已正式上架"),await Pe();return}if(operation.status==="FAILED"){be(false),I("批量上架失败："+(operation.error||"未知错误"));return}timer=window.setTimeout(pollReview,800)}catch(error){if(!active)return;setReviewOperation(operation=>({...operation,status:"FAILED",phase:"failed",message:"读取进度失败",error:error instanceof Error?error.message:String(error)})),be(false)}}pollReview();return()=>{active=false,timer&&window.clearTimeout(timer)}},[reviewOperation?.id]);let Ee=X[ve]||null,allSelected=X.length>0&&selectedNames.length===X.length,toggleSelection=name=>setSelectedNames(names=>names.includes(name)?names.filter(item=>item!==name):[...names,name]),startBatch=async()=>{if(_e||!selectedNames.length)return;be(true),setReviewOperation(null);try{let operation=await uaStartBatchReview(selectedNames);setReviewOperation(operation)}catch(error){be(false),I("批量上架失败："+(error instanceof Error?error.message:String(error)))}},rejectCurrent=async()=>{if(!Ee||_e)return;be(true);try{await uaReviewAction("reject",Ee.name),I("资料已退回，不进入正式知识库"),setSelectedNames(names=>names.filter(name=>name!==Ee.name)),await Pe()}catch(error){I("审核操作失败："+(error instanceof Error?error.message:String(error)))}finally{be(false)}},progress=Math.max(0,Math.min(100,Number(reviewOperation?.percent||0)));return(0,w.jsxs)("div",{className:"three-column review-layout",children:[(0,w.jsxs)("div",{className:"panel review-list",children:[(0,w.jsxs)("div",{className:"review-list-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("div",{className:"panel-title",children:"待审核资料"}),(0,w.jsxs)("small",{children:["已选择 ",selectedNames.length," / ",X.length," 份"]})]}),(0,w.jsxs)("label",{className:"review-select-all",children:[(0,w.jsx)("input",{type:"checkbox",checked:allSelected,disabled:_e||!X.length,onChange:event=>setSelectedNames(event.target.checked?X.map(file=>file.name):[])}),(0,w.jsx)("span",{children:"全选"})]})]}),xe&&(0,w.jsx)("div",{className:"no-files",children:"正在读取真实待审核资料…"}),Ae&&(0,w.jsx)("div",{className:"no-files",children:"读取失败："+Ae}),!xe&&!Ae&&!X.length&&(0,w.jsx)("div",{className:"no-files",children:"当前没有待审核资料"}),X.map((Ve,Se)=>(0,w.jsxs)("div",{className:ve===Se?"review-item active":"review-item",children:[(0,w.jsx)("label",{className:"review-checkbox",title:"选择 "+Ve.name,children:(0,w.jsx)("input",{type:"checkbox",checked:selectedNames.includes(Ve.name),disabled:_e,onChange:()=>toggleSelection(Ve.name)})}),(0,w.jsxs)("button",{className:"review-item-open",onClick:()=>U(Se),children:[(0,w.jsx)("span",{className:"doc-icon",children:"▤"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:Ve.name}),(0,w.jsxs)("small",{children:["AI分析完成 · ",Ve.generatedFaqCount||0," 条FAQ"]})]}),(0,w.jsx)("i",{children:"›"})]})]},Ve.analysisJobId||Ve.name))]}),(0,w.jsxs)("div",{className:"panel review-main",children:Ee?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsx)("div",{className:"section-kicker",children:"真实待审核资料"}),(0,w.jsx)("h2",{children:Ee.name}),(0,w.jsxs)("p",{className:"muted",children:Ee.faqOnly?"FAQ资料：已完成 · 内容片段："+(Ee.analysisUnitCount||0)+" · FAQ："+(Ee.generatedFaqCount||0):["AI分析：已完成　·　页面：",Ee.slideCount||Object.keys(Ee.commentaryPages||{}).length||0,"　·　FAQ：",Ee.generatedFaqCount||0]}),(0,w.jsxs)("div",{className:"review-detail-card",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"上架状态"}),(0,w.jsx)("strong",{children:selectedNames.includes(Ee.name)?"已选中，等待批量上架":"等待人工审核"})]}),(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"生成时间"}),(0,w.jsx)("span",{children:Ee.analysisCompletedAt?new Date(Ee.analysisCompletedAt).toLocaleString("zh-CN"):"—"})]}),(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:Ee.faqOnly?"资料用途":"讲解词预览"}),(0,w.jsx)("p",{children:Ee.faqOnly?"该 PDF/Word 仅用于FAQ问答，不生成讲解词，也不会下发给展示客户端。":String(Ee.commentary||"暂无讲解词").slice(0,800)})]})]})]}):(0,w.jsx)("div",{className:"no-files",children:"请选择待审核资料"})}),(0,w.jsxs)("div",{className:"panel review-side",children:[(0,w.jsx)("h3",{children:"审核操作"}),(0,w.jsx)("div",{className:"check-row good",children:"勾选资料后统一上架，知识库和向量索引只同步一次。"}),reviewOperation&&(0,w.jsxs)("div",{className:"review-progress",children:[(0,w.jsxs)("div",{className:"review-progress-head",children:[(0,w.jsx)("strong",{children:reviewOperation.message||"正在处理"}),(0,w.jsx)("span",{children:progress+"%"})]}),(0,w.jsx)("div",{className:"review-progress-track",children:(0,w.jsx)("i",{style:{width:progress+"%"}})}),(0,w.jsxs)("small",{children:["已处理 ",reviewOperation.current||0," / ",reviewOperation.total||selectedNames.length," 份",reviewOperation.status==="FAILED"?" · "+(reviewOperation.error||"任务失败"):""]})]}),(0,w.jsx)("button",{className:"primary full",disabled:_e||!selectedNames.length,onClick:startBatch,children:_e?"批量上架处理中…":"审核并上架所选资料（"+selectedNames.length+"）"}),(0,w.jsx)("button",{className:"danger full",disabled:!Ee||_e,onClick:rejectCurrent,children:_e?"处理中…":"退回当前资料"})]})]})}`;
const reviewHelpers = String.raw`function uaReviewTierText(I){let X=String(I||"").replace(/&&/g,"\n\n").trim();if(!X)return "暂无讲解词";let je=["精炼版","标准版","详细版"],ve=je.map(U=>{let xe=X.match(new RegExp("【"+U+"】\\s*([\\s\\S]*?)(?=\\n\\s*【(?:精炼版|标准版|详细版)】|$)"));return xe?.[1]?.trim()?"【"+U+"】 "+xe[1].trim():""}).filter(Boolean);return ve.length?ve.join("\n\n"):X}function uaReviewCommentaryText(I){let X=I?.commentaryPages||{},je=Object.keys(X).map(Number).filter(Number.isFinite).sort((ve,U)=>ve-U);return je.length?je.map(ve=>"第 "+ve+" 页\n"+uaReviewTierText(X[String(ve)])).join("\n\n"):uaReviewTierText(I?.commentary)}`;
let formattedReviewComponent = reviewComponent
  .replace(
    '[reviewOperation,setReviewOperation]=(0,l.useState)(null),Pe=async',
    '[reviewOperation,setReviewOperation]=(0,l.useState)(null),[reviewDetailTab,setReviewDetailTab]=(0,l.useState)("faq"),[reviewFaqPage,setReviewFaqPage]=(0,l.useState)(0),Pe=async'
  )
  .replace(
    'let Ee=X[ve]||null,allSelected=',
    'let Ee=X[ve]||null,reviewFaqs=Array.isArray(Ee?.faqs)?Ee.faqs:[],allSelected='
  )
  .replace(
    'className:"review-item-open",onClick:()=>U(Se)',
    'className:"review-item-open",onClick:()=>{U(Se),setReviewDetailTab("faq"),setReviewFaqPage(0)}'
  )
  .replace(
    '(0,w.jsx)("p",{children:Ee.faqOnly?',
    '(0,w.jsx)("p",{className:Ee.faqOnly?"":"review-commentary-preview",children:Ee.faqOnly?'
  )
  .replace(
    'String(Ee.commentary||"暂无讲解词").slice(0,800)',
    'uaReviewCommentaryText(Ee)'
  );
const reviewMainStart = formattedReviewComponent.indexOf('(0,w.jsxs)("div",{className:"panel review-main",children:Ee?');
const reviewSideStart = formattedReviewComponent.indexOf(',(0,w.jsxs)("div",{className:"panel review-side"', reviewMainStart);
if (reviewMainStart < 0 || reviewSideStart < 0) throw new Error('review details: stable panel anchors changed');
const reviewMain = `(0,w.jsxs)("div",{className:"panel review-main",children:Ee?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"review-main-heading",children:[(0,w.jsx)("h2",{title:Ee.name,children:Ee.name}),(0,w.jsxs)("p",{className:"muted",children:["AI分析完成 · ",Ee.slideCount||Ee.analysisUnitCount||0," 个内容单元 · ",reviewFaqs.length||Ee.generatedFaqCount||0," 条FAQ"]})]}),(0,w.jsxs)("div",{className:"review-detail-tabs",children:[!Ee.faqOnly&&(0,w.jsx)("button",{className:reviewDetailTab==="script"?"active":"",onClick:()=>setReviewDetailTab("script"),children:"讲解词"}),(0,w.jsxs)("button",{className:reviewDetailTab==="faq"||Ee.faqOnly?"active":"",onClick:()=>setReviewDetailTab("faq"),children:["生成FAQ ",(0,w.jsx)("span",{children:reviewFaqs.length||Ee.generatedFaqCount||0})]})]}),(0,w.jsx)("div",{className:"review-detail-scroll",children:reviewDetailTab==="script"&&!Ee.faqOnly?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"review-detail-card compact",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"上架状态"}),(0,w.jsx)("strong",{children:selectedNames.includes(Ee.name)?"已选中，等待批量上架":"等待人工审核"})]}),(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"生成时间"}),(0,w.jsx)("span",{children:Ee.analysisCompletedAt?new Date(Ee.analysisCompletedAt).toLocaleString("zh-CN"):"—"})]})]}),(0,w.jsx)("pre",{className:"review-commentary-preview",children:uaReviewCommentaryText(Ee)})]}):(0,w.jsxs)(w.Fragment,{children:[reviewFaqs.length?reviewFaqs.slice(reviewFaqPage*8,reviewFaqPage*8+8).map((faq,index)=>(0,w.jsxs)("article",{className:"review-faq-card",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("b",{children:String(reviewFaqPage*8+index+1).padStart(2,"0")}),(0,w.jsx)("strong",{children:faq.question}),(0,w.jsx)("span",{children:"第 "+(faq.sourcePage||1)+" 页"})]}),(0,w.jsx)("p",{children:faq.answer})]},faq.id||faq.question)):(0,w.jsx)("div",{className:"no-files",children:"当前资料尚未读取到生成的FAQ"}),reviewFaqs.length>8&&(0,w.jsxs)("div",{className:"review-faq-pagination",children:[(0,w.jsx)("button",{disabled:reviewFaqPage===0,onClick:()=>setReviewFaqPage(page=>Math.max(0,page-1)),children:"上一页"}),(0,w.jsxs)("span",{children:["第 ",reviewFaqPage+1," / ",Math.ceil(reviewFaqs.length/8)," 页"]}),(0,w.jsx)("button",{disabled:(reviewFaqPage+1)*8>=reviewFaqs.length,onClick:()=>setReviewFaqPage(page=>Math.min(Math.ceil(reviewFaqs.length/8)-1,page+1)),children:"下一页"})]})]})})]}):(0,w.jsx)("div",{className:"no-files",children:"请选择待审核资料"})})`;
formattedReviewComponent = formattedReviewComponent.slice(0, reviewMainStart)
  + reviewMain
  + formattedReviewComponent.slice(reviewSideStart);
const integratedReviewComponent = `function ue({notify:I,onCountChange:reviewCountChanged}){
let[files,setFiles]=(0,l.useState)([]),[fileIndex,setFileIndex]=(0,l.useState)(0),[robotDrafts,setRobotDrafts]=(0,l.useState)([]),[robotDraftId,setRobotDraftId]=(0,l.useState)(""),[reviewMode,setReviewMode]=(0,l.useState)("files"),[robotExpanded,setRobotExpanded]=(0,l.useState)(false),[loading,setLoading]=(0,l.useState)(true),[loadError,setLoadError]=(0,l.useState)(""),[busy,setBusy]=(0,l.useState)(false),[selectedNames,setSelectedNames]=(0,l.useState)([]),[reviewOperation,setReviewOperation]=(0,l.useState)(null),[reviewDetailTab,setReviewDetailTab]=(0,l.useState)("faq"),[reviewFaqPage,setReviewFaqPage]=(0,l.useState)(0),[robotForm,setRobotForm]=(0,l.useState)({question:"",answer:""});
let refresh=async()=>{setLoading(true),setLoadError("");try{let[filePayload,draftPayload]=await Promise.all([p("/api/files/review"),uaAdminRequest("/api/knowledge/qa-drafts")]),nextFiles=Array.isArray(filePayload.items)?filePayload.items:[],nextDrafts=(Array.isArray(draftPayload.items)?draftPayload.items:[]).filter(item=>["PENDING_REVIEW","INDEX_FAILED"].includes(String(item.status||"")));setFiles(nextFiles),setRobotDrafts(nextDrafts),reviewCountChanged?.(nextFiles.length+nextDrafts.length),setSelectedNames(names=>names.filter(name=>nextFiles.some(file=>file.name===name))),setFileIndex(index=>Math.min(index,Math.max(0,nextFiles.length-1))),setRobotDraftId(id=>nextDrafts.some(item=>item.draftId===id)?id:nextDrafts[0]?.draftId||"")}catch(error){setLoadError(error instanceof Error?error.message:String(error))}finally{setLoading(false)}};
(0,l.useEffect)(()=>{refresh()},[]);
(0,l.useEffect)(()=>{if(!reviewOperation?.id||["COMPLETED","FAILED"].includes(reviewOperation.status))return;let active=true,timer=null;async function pollReview(){try{let operation=await uaGetBatchReview(reviewOperation.id);if(!active)return;setReviewOperation(operation);if(operation.status==="COMPLETED"){setBusy(false),setSelectedNames([]),I(operation.message||"所选资料已正式上架"),await refresh();return}if(operation.status==="FAILED"){setBusy(false),I("批量上架失败："+(operation.error||"未知错误"));return}timer=window.setTimeout(pollReview,800)}catch(error){if(!active)return;setReviewOperation(operation=>({...operation,status:"FAILED",phase:"failed",message:"读取进度失败",error:error instanceof Error?error.message:String(error)})),setBusy(false)}}pollReview();return()=>{active=false,timer&&window.clearTimeout(timer)}},[reviewOperation?.id]);
let selectedFile=files[fileIndex]||null,selectedRobot=robotDrafts.find(item=>item.draftId===robotDraftId)||robotDrafts[0]||null,isRobot=reviewMode==="robot",reviewFaqs=Array.isArray(selectedFile?.faqs)?selectedFile.faqs:[],allSelected=files.length>0&&selectedNames.length===files.length,progress=Math.max(0,Math.min(100,Number(reviewOperation?.percent||0)));
(0,l.useEffect)(()=>{if(!selectedRobot){setRobotForm({question:"",answer:""});return}setRobotForm({question:String(selectedRobot.reviewQuestion||selectedRobot.question||""),answer:String(selectedRobot.reviewAnswer||selectedRobot.suggestedAnswer||selectedRobot.rawAnswer||"")})},[selectedRobot?.draftId,selectedRobot?.updatedAt]);
let toggleSelection=name=>setSelectedNames(names=>names.includes(name)?names.filter(item=>item!==name):[...names,name]),selectFile=index=>{setReviewMode("files"),setRobotExpanded(false),setFileIndex(index),setReviewDetailTab("faq"),setReviewFaqPage(0)},selectRobot=id=>{setReviewMode("robot"),setRobotExpanded(true),id&&setRobotDraftId(id)},toggleRobotList=()=>{if(!isRobot){setReviewMode("robot"),setRobotExpanded(true),!robotDraftId&&robotDrafts[0]?.draftId&&setRobotDraftId(robotDrafts[0].draftId)}else setRobotExpanded(expanded=>!expanded)},startBatch=async()=>{if(busy||!selectedNames.length)return;setBusy(true),setReviewOperation(null);try{let operation=await uaStartBatchReview(selectedNames);setReviewOperation(operation)}catch(error){setBusy(false),I("批量上架失败："+(error instanceof Error?error.message:String(error)))}},rejectCurrent=async()=>{if(!selectedFile||busy)return;setBusy(true);try{await uaReviewAction("reject",selectedFile.name),I("资料已退回，不进入正式知识库"),setSelectedNames(names=>names.filter(name=>name!==selectedFile.name)),await refresh()}catch(error){I("审核操作失败："+(error instanceof Error?error.message:String(error)))}finally{setBusy(false)}},validateRobot=()=>{if(!robotForm.question.trim()||!robotForm.answer.trim()){I("问题和答案不能为空");return false}return true},saveRobot=async()=>{if(!selectedRobot||busy||!validateRobot())return;setBusy(true);try{let result=await uaAdminRequest("/api/knowledge/qa-drafts/"+encodeURIComponent(selectedRobot.draftId),{method:"PATCH",body:JSON.stringify({question:robotForm.question.trim(),answer:robotForm.answer.trim()})});setRobotDrafts(items=>items.map(item=>item.draftId===selectedRobot.draftId?result.item:item)),I("现场问题和答案已保存")}catch(error){I("保存失败："+(error instanceof Error?error.message:String(error)))}finally{setBusy(false)}},publishRobot=async()=>{if(!selectedRobot||busy||!validateRobot())return;setBusy(true);try{await uaAdminRequest("/api/knowledge/qa-drafts/"+encodeURIComponent(selectedRobot.draftId)+"/publish",{method:"POST",body:JSON.stringify({question:robotForm.question.trim(),answer:robotForm.answer.trim()})}),I("机器人现场FAQ已审核发布"),await refresh()}catch(error){I("发布失败："+(error instanceof Error?error.message:String(error)))}finally{setBusy(false)}},rejectRobot=async()=>{if(!selectedRobot||busy)return;setBusy(true);try{await uaAdminRequest("/api/knowledge/qa-drafts/"+encodeURIComponent(selectedRobot.draftId)+"/reject",{method:"POST",body:"{}"}),I("机器人现场录入已退回"),await refresh()}catch(error){I("退回失败："+(error instanceof Error?error.message:String(error)))}finally{setBusy(false)}};
return(0,w.jsxs)("div",{className:"three-column review-layout",children:[
(0,w.jsxs)("div",{className:"panel review-list",children:[
(0,w.jsxs)("div",{className:"review-list-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("div",{className:"panel-title",children:"待审核"}),(0,w.jsxs)("small",{children:["共 ",files.length+robotDrafts.length," 项"]})]}),(0,w.jsxs)("label",{className:"review-select-all",children:[(0,w.jsx)("input",{type:"checkbox",checked:allSelected,disabled:busy||!files.length,onChange:event=>setSelectedNames(event.target.checked?files.map(file=>file.name):[])}),(0,w.jsx)("span",{children:"全选资料"})]})]}),
(0,w.jsxs)("div",{className:isRobot?"review-item robot-review-item active":"review-item robot-review-item",children:[(0,w.jsx)("span",{className:"review-checkbox robot-review-marker",children:(0,w.jsx)("i",{})}),(0,w.jsxs)("button",{className:"review-item-open",onClick:toggleRobotList,"aria-expanded":isRobot&&robotExpanded,children:[(0,w.jsx)("span",{className:"doc-icon",children:"问"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:"机器人现场录入"}),(0,w.jsx)("small",{children:robotDrafts.length?robotDrafts.length+" 条待审核":"暂无待审核记录"})]}),(0,w.jsx)("i",{className:"review-expand-arrow",children:isRobot&&robotExpanded?"⌃":"›"})]})]}),
isRobot&&robotExpanded&&robotDrafts.length>0&&(0,w.jsx)("div",{className:"robot-draft-list",children:robotDrafts.map((draft,index)=>(0,w.jsxs)("button",{className:selectedRobot?.draftId===draft.draftId?"active":"",onClick:()=>selectRobot(draft.draftId),children:[(0,w.jsx)("i",{children:String(index+1).padStart(2,"0")}),(0,w.jsx)("span",{title:draft.reviewQuestion||draft.question,children:draft.reviewQuestion||draft.question||"未命名问题"})]},draft.draftId))}),
(0,w.jsx)("div",{className:"review-group-label",children:"待审核资料"}),loading&&(0,w.jsx)("div",{className:"no-files",children:"正在读取待审核内容…"}),loadError&&(0,w.jsx)("div",{className:"no-files",children:"读取失败："+loadError}),!loading&&!loadError&&!files.length&&(0,w.jsx)("div",{className:"no-files compact",children:"当前没有待审核资料"}),files.map((file,index)=>(0,w.jsxs)("div",{className:!isRobot&&fileIndex===index?"review-item active":"review-item",children:[(0,w.jsx)("label",{className:"review-checkbox",title:"选择 "+file.name,children:(0,w.jsx)("input",{type:"checkbox",checked:selectedNames.includes(file.name),disabled:busy,onChange:()=>toggleSelection(file.name)})}),(0,w.jsxs)("button",{className:"review-item-open",onClick:()=>selectFile(index),children:[(0,w.jsx)("span",{className:"doc-icon",children:"▤"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:file.name}),(0,w.jsxs)("small",{children:["AI分析完成 · ",file.generatedFaqCount||0," 条FAQ"]})]}),(0,w.jsx)("i",{children:"›"})]})]},file.analysisJobId||file.name))]}),
(0,w.jsxs)("div",{className:"panel review-main",children:isRobot?selectedRobot?(0,w.jsxs)("div",{className:"robot-review-form",children:[(0,w.jsx)("h2",{children:"机器人现场录入"}),(0,w.jsxs)("label",{className:"robot-review-field",children:[(0,w.jsx)("span",{children:"问题"}),(0,w.jsx)("input",{value:robotForm.question,maxLength:1000,onChange:event=>setRobotForm(form=>({...form,question:event.target.value})),placeholder:"请输入现场问题"})]}),(0,w.jsxs)("label",{className:"robot-review-field answer",children:[(0,w.jsx)("span",{children:"答案"}),(0,w.jsx)("textarea",{value:robotForm.answer,maxLength:4000,onChange:event=>setRobotForm(form=>({...form,answer:event.target.value})),placeholder:"请输入现场答案"})]})]}):(0,w.jsx)("div",{className:"no-files",children:"当前没有机器人现场录入待审核记录"}):selectedFile?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"review-main-heading",children:[(0,w.jsx)("h2",{title:selectedFile.name,children:selectedFile.name}),(0,w.jsxs)("p",{className:"muted",children:["AI分析完成 · ",selectedFile.slideCount||selectedFile.analysisUnitCount||0," 个内容单元 · ",reviewFaqs.length||selectedFile.generatedFaqCount||0," 条FAQ"]})]}),(0,w.jsxs)("div",{className:"review-detail-tabs",children:[!selectedFile.faqOnly&&(0,w.jsx)("button",{className:reviewDetailTab==="script"?"active":"",onClick:()=>setReviewDetailTab("script"),children:"讲解词"}),(0,w.jsxs)("button",{className:reviewDetailTab==="faq"||selectedFile.faqOnly?"active":"",onClick:()=>setReviewDetailTab("faq"),children:["生成FAQ ",(0,w.jsx)("span",{children:reviewFaqs.length||selectedFile.generatedFaqCount||0})]})]}),(0,w.jsx)("div",{className:"review-detail-scroll",children:reviewDetailTab==="script"&&!selectedFile.faqOnly?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"review-detail-card compact",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"上架状态"}),(0,w.jsx)("strong",{children:selectedNames.includes(selectedFile.name)?"已选中，等待批量上架":"等待人工审核"})]}),(0,w.jsxs)("div",{children:[(0,w.jsx)("label",{children:"生成时间"}),(0,w.jsx)("span",{children:selectedFile.analysisCompletedAt?new Date(selectedFile.analysisCompletedAt).toLocaleString("zh-CN"):"—"})]})]}),(0,w.jsx)("pre",{className:"review-commentary-preview",children:uaReviewCommentaryText(selectedFile)})]}):(0,w.jsxs)(w.Fragment,{children:[reviewFaqs.length?reviewFaqs.slice(reviewFaqPage*8,reviewFaqPage*8+8).map((faq,index)=>(0,w.jsxs)("article",{className:"review-faq-card",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("b",{children:String(reviewFaqPage*8+index+1).padStart(2,"0")}),(0,w.jsx)("strong",{children:faq.question}),(0,w.jsx)("span",{children:"第 "+(faq.sourcePage||1)+" 页"})]}),(0,w.jsx)("p",{children:faq.answer})]},faq.id||faq.question)):(0,w.jsx)("div",{className:"no-files",children:"当前资料尚未读取到生成的FAQ"}),reviewFaqs.length>8&&(0,w.jsxs)("div",{className:"review-faq-pagination",children:[(0,w.jsx)("button",{disabled:reviewFaqPage===0,onClick:()=>setReviewFaqPage(page=>Math.max(0,page-1)),children:"上一页"}),(0,w.jsxs)("span",{children:["第 ",reviewFaqPage+1," / ",Math.ceil(reviewFaqs.length/8)," 页"]}),(0,w.jsx)("button",{disabled:(reviewFaqPage+1)*8>=reviewFaqs.length,onClick:()=>setReviewFaqPage(page=>Math.min(Math.ceil(reviewFaqs.length/8)-1,page+1)),children:"下一页"})]})]})})]}):(0,w.jsx)("div",{className:"no-files",children:"请选择待审核资料"})}),
(0,w.jsxs)("div",{className:"panel review-side",children:isRobot?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsx)("h3",{children:"审核操作"}),(0,w.jsx)("button",{className:"ghost full",disabled:!selectedRobot||busy,onClick:saveRobot,children:busy?"处理中…":"保存修改"}),(0,w.jsx)("button",{className:"primary full",disabled:!selectedRobot||busy,onClick:publishRobot,children:busy?"处理中…":"审核并发布"}),(0,w.jsx)("button",{className:"danger full",disabled:!selectedRobot||busy,onClick:rejectRobot,children:busy?"处理中…":"退回当前录入"})]}):(0,w.jsxs)(w.Fragment,{children:[(0,w.jsx)("h3",{children:"审核操作"}),(0,w.jsx)("div",{className:"check-row good",children:"勾选资料后统一上架，知识库和向量索引只同步一次。"}),reviewOperation&&(0,w.jsxs)("div",{className:"review-progress",children:[(0,w.jsxs)("div",{className:"review-progress-head",children:[(0,w.jsx)("strong",{children:reviewOperation.message||"正在处理"}),(0,w.jsx)("span",{children:progress+"%"})]}),(0,w.jsx)("div",{className:"review-progress-track",children:(0,w.jsx)("i",{style:{width:progress+"%"}})}),(0,w.jsxs)("small",{children:["已处理 ",reviewOperation.current||0," / ",reviewOperation.total||selectedNames.length," 份",reviewOperation.status==="FAILED"?" · "+(reviewOperation.error||"任务失败"):""]})]}),(0,w.jsx)("button",{className:"primary full",disabled:busy||!selectedNames.length,onClick:startBatch,children:busy?"批量上架处理中…":"审核并上架所选资料（"+selectedNames.length+"）"}),(0,w.jsx)("button",{className:"danger full",disabled:!selectedFile||busy,onClick:rejectCurrent,children:busy?"处理中…":"退回当前资料"})]})})]})}`;
formattedReviewComponent = integratedReviewComponent;
bundle = bundle.slice(0, reviewStartIndex) + reviewHelpers + formattedReviewComponent + bundle.slice(reviewEndIndex + 1);

const settingsStart = 'function A({notify:I}){';
const settingsEnd = '}function uaLegacyAvatarManager';
const settingsStartIndex = bundle.indexOf(settingsStart);
const settingsEndIndex = bundle.indexOf(settingsEnd, settingsStartIndex);
if (settingsStartIndex < 0 || settingsEndIndex < 0 || bundle.indexOf(settingsStart, settingsStartIndex + 1) >= 0) {
  throw new Error('system settings: stable component anchors changed');
}
const settingsComponent = `function A({notify:I,account:X}){let[je,ve]=(0,l.useState)(""),[U,xe]=(0,l.useState)(null),Re=[["client","客户端管理","clients.manage"],["avatar","数字人管理","avatar.manage"],["permission","权限与上架方式","settings"]].filter(([Be,_e,be])=>be==="settings"?uaHasPermission(X,"publish.manage")||uaHasPermission(X,"accounts.manage"):uaHasPermission(X,be)),Ae={client:{title:"客户端管理",desc:"查看展示客户端实时状态，配置客户端背景和当前客户端的数字人位置。"},avatar:{title:"数字人管理",desc:"控制数字人服务、运行状态、显示屏幕和播报。"},permission:{title:"系统设置",desc:"管理账号权限、密码与资料上架方式。"}}[je]||{title:"系统设置",desc:"当前账号没有可用的系统设置权限。"};return(0,l.useEffect)(()=>{b().then(xe).catch(()=>xe(null))},[]),(0,l.useEffect)(()=>{Re.length&&!Re.some(([Be])=>Be===je)&&ve(Re[0][0])},[X,je]),(0,w.jsxs)("section",{className:"page system-settings-page",children:[(0,w.jsx)("div",{className:"page-heading",children:(0,w.jsxs)("div",{children:[(0,w.jsx)("h1",{children:Ae.title}),(0,w.jsxs)("p",{children:[Ae.desc,je==="permission"&&U?.counts&&\` · 真实知识对象 \${Number(U.counts.knowledge_objects||0).toLocaleString()} 条\`]})]})}),(0,w.jsx)("div",{className:"tabs system-settings-tabs",children:Re.map(([Be,_e])=>(0,w.jsx)("button",{className:je===Be?"active":"",onClick:()=>ve(Be),children:_e},Be))}),je==="client"?(0,w.jsx)(j,{notify:I}):je==="avatar"?(0,w.jsx)(uaDigitalHumanManager,{notify:I}):je==="permission"?(0,w.jsx)(fe,{notify:I,account:X}):(0,w.jsx)("div",{className:"panel no-settings-access",children:"当前账号没有系统设置权限"})]})}`;
bundle = bundle.slice(0, settingsStartIndex) + settingsComponent + bundle.slice(settingsEndIndex + 1);

bundle = replaceOnce(
  bundle,
  'async function uaSaveScreenSettings(I,X){return p("/api/screens/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({deviceId:I,...X})})}function uaAvatarBase',
  'async function uaSaveScreenSettings(I,X){return p("/api/screens/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({deviceId:I,...X})})}function uaUploadScreenBackground(I,X,je){return new Promise((ve,U)=>{let xe=new XMLHttpRequest;xe.open("POST",uaApi("/api/screens/background?deviceId="+encodeURIComponent(I)+"&name="+encodeURIComponent(X.name))),xe.setRequestHeader("Content-Type",X.type||"application/octet-stream"),xe.upload.onprogress=Re=>{Re.lengthComputable&&je(Math.round(Re.loaded/Re.total*100))},xe.onerror=()=>U(Error("背景图片上传连接中断")),xe.onload=()=>{let Re=null;try{Re=JSON.parse(xe.responseText)}catch{}xe.status>=200&&xe.status<300&&Re?.screen?ve(Re):U(Error(Re?.error||"背景图片上传失败（HTTP "+xe.status+"）"))},xe.send(X)})}function uaAvatarBase',
  'screen background upload helper',
);

const clientStart = 'function j({notify:I}){';
const clientEnd = '}function fe({notify:I}){';
const clientStartIndex = bundle.indexOf(clientStart);
const clientEndIndex = bundle.indexOf(clientEnd, clientStartIndex);
if (clientStartIndex < 0 || clientEndIndex < 0 || bundle.indexOf(clientStart, clientStartIndex + 1) >= 0) {
  throw new Error('client manager: stable component anchors changed');
}
const clientComponent = `function j({notify:I}){let[X,je]=(0,l.useState)([]),[ve,U]=(0,l.useState)(""),[xe,Re]=(0,l.useState)({width:960,height:1080,x:2880,y:0}),[Ae,Be]=(0,l.useState)(""),[_e,be]=(0,l.useState)(""),[Pe,Ee]=(0,l.useState)(0),[Se,Qe]=(0,l.useState)("image"),[Ct,Xt]=(0,l.useState)("cover"),[Bt,Ht]=(0,l.useState)(false),Lt=(0,l.useRef)(false),Wt=(0,l.useRef)(false);async function dn(){if(Wt.current)return;Wt.current=true;try{let Nn=(await uaGetScreens()).slice().sort((Vn,Gn)=>Number(Vn.screenNumber)-Number(Gn.screenNumber));je(Nn),U(Vn=>Nn.some(Gn=>String(Gn.screenNumber)===String(Vn))?String(Vn):String(Nn[0]?.screenNumber||"")),Be(""),Ee(Date.now())}catch(Nn){Be(Nn instanceof Error?Nn.message:String(Nn))}finally{Wt.current=false}}(0,l.useEffect)(()=>{dn();let Nn=window.setInterval(dn,3e3);return()=>window.clearInterval(Nn)},[]);let ft=X.find(Nn=>String(Nn.screenNumber)===String(ve)),Ye=ft?.snapshot,Sn=uaSnapshotUrl(Ye?.url),Ue=Number(Ye?.width||ft?.settings?.displayWidth||3840),qe=Number(Ye?.height||ft?.settings?.displayHeight||2160),Ne=ft?.status,Me=Number(Ne?.memoryUsedMb),Le=Number(Ne?.memoryTotalMb),mn=Number.isFinite(Me)&&Number.isFinite(Le)&&Le>0?\`\${(Me/1024).toFixed(1)} / \${(Le/1024).toFixed(0)} GB\`:uaMetric(Ne?.memoryPercent),En=[{label:"在线状态",value:ft?.online?"在线":"离线",tone:ft?.online?"green":"red"},{label:"IP地址",value:ft?.ipAddress||"--",tone:"blue"},{label:"CPU",value:uaMetric(Ne?.cpuPercent),tone:"blue"},{label:"内存",value:mn,tone:"purple"},{label:"分辨率",value:\`\${Ue} × \${qe}\`,tone:"orange"},{label:"最近心跳",value:uaAge(ft?.lastSeenAt),tone:"green"}];(0,l.useEffect)(()=>{let Nn=ft?.settings?.avatarBounds||{width:Math.max(1,Math.round(Ue*.25)),height:qe,x:Math.max(0,Math.round(Ue*.75)),y:0};Lt.current=false;Re({width:Number(Nn.width)||960,height:Number(Nn.height)||1080,x:Number(Nn.x)||0,y:Number(Nn.y)||0}),Qe(ft?.settings?.transparentBackground?"transparent":"image"),Xt(ft?.settings?.backgroundFit==="contain"?"contain":"cover")},[ft?.deviceId,ft?.settings?.avatarBounds?.width,ft?.settings?.avatarBounds?.height,ft?.settings?.avatarBounds?.x,ft?.settings?.avatarBounds?.y,ft?.settings?.transparentBackground,ft?.settings?.backgroundFit]);function $e(Nn,Vn){Lt.current=true;Re(Gn=>({...Gn,[Nn]:Number(Vn)||0}))}async function Jn(){for(let Nn=0;Nn<20;Nn+=1){try{let Vn=await uaAvatarRequest("/health",void 0,"GET");if(Vn?.online)return Vn}catch{}await new Promise(Vn=>window.setTimeout(Vn,750))}throw Error("等待当前客户端数字人连接超时")}async function Ot(Nn){if(!ft?.deviceId){I("请先选择一台展示客户端");return}Ee(Nn);let Vn=Nn==="reset"?{width:Math.max(1,Math.round(Ue*.25)),height:qe,x:Math.max(0,Math.round(Ue*.75)),y:0}:{...xe};try{await uaSaveScreenSettings(ft.deviceId,{avatarBounds:Vn});await uaSetAvatarTarget(String(ft.screenNumber));await Jn();await uaAvatarRequest(Nn==="reset"?"/api/avatar/reset":"/api/avatar/set-bounds",Vn);Re(Vn),Lt.current=false;await dn();I(\`\${ft.screenNumber}号屏数字人位置已保存并应用\`)}catch(Gn){I(\`位置设置失败：\${Gn instanceof Error?Gn.message:String(Gn)}\`)}finally{Ee("")}}async function Kn(Nn={mode:Se,fit:Ct}){if(!ft?.deviceId){I("请先选择一台展示客户端");return}Ht(true);try{await uaSaveScreenSettings(ft.deviceId,{transparentBackground:Nn.mode==="transparent",backgroundFit:Nn.fit});Qe(Nn.mode),Xt(Nn.fit),await dn(),I(Nn.mode==="transparent"?\`\${ft.screenNumber}号屏透明背景已下发\`:\`\${ft.screenNumber}号屏图片背景已下发\`)}catch(Vn){I(\`背景设置失败：\${Vn instanceof Error?Vn.message:String(Vn)}\`)}finally{Ht(false)}}return(0,w.jsxs)("div",{className:"avatar-console client-manager",children:[(0,w.jsxs)("div",{className:"avatar-console-intro",children:[(0,w.jsx)("h2",{children:"客户端管理"}),(0,w.jsx)("p",{children:"选择展示客户端后查看实时画面、运行状态，并配置该客户端的背景和数字人位置。"})]}),(0,w.jsxs)("div",{className:"avatar-status-row",children:[(0,w.jsxs)("label",{className:"avatar-screen-select",children:[(0,w.jsx)("span",{children:"选择客户端"}),(0,w.jsx)("select",{value:ve,onChange:Nn=>U(Nn.target.value),children:X.map(Nn=>(0,w.jsx)("option",{value:String(Nn.screenNumber),children:\`\${Nn.screenNumber}号屏（\${Nn.name||Nn.ipAddress||"展示客户端"}）\`},Nn.deviceId||Nn.screenNumber))})]}),(0,w.jsx)("div",{className:"avatar-metrics",children:En.map(Nn=>(0,w.jsxs)("div",{className:"avatar-metric",children:[(0,w.jsx)("span",{children:Nn.label}),(0,w.jsx)("strong",{className:Nn.tone,children:Nn.value})]},Nn.label))})]}),_e?(0,w.jsx)("div",{className:"avatar-console-error",children:\`客户端数据读取失败：\${_e}\`}):null,!X.length&&!_e?(0,w.jsx)("div",{className:"panel client-manager-empty",children:"暂无已注册的展示客户端"}):ft?(0,w.jsxs)("div",{className:"avatar-workspace",children:[(0,w.jsxs)("section",{className:"panel avatar-monitor-panel",children:[(0,w.jsxs)("div",{className:"avatar-panel-heading",children:[(0,w.jsxs)("h3",{children:["客户端实时监控 · ",ft.screenNumber,"号屏"]}),(0,w.jsx)("span",{children:\`最近更新：\${Ye?.updatedAt?uaAge(Ye.updatedAt):"暂无截图"}\`})]}),(0,w.jsx)("div",{className:"avatar-live-preview",children:Sn?(0,w.jsx)("img",{src:\`\${Sn}\${Sn.includes("?")?"&":"?"}refresh=\${Pe}\`,alt:\`\${ft.screenNumber}号屏实时画面\`}):(0,w.jsxs)("div",{className:"avatar-preview-empty",children:[(0,w.jsx)("strong",{children:"暂未收到客户端截图"}),(0,w.jsx)("span",{children:"客户端在线后会自动回传画面。"})]})}),(0,w.jsxs)("div",{className:"avatar-preview-footer",children:[(0,w.jsx)("button",{className:"ghost",onClick:dn,children:"刷新画面"}),(0,w.jsxs)("span",{children:[ft.name||"展示客户端"," · ",ft.ipAddress||"IP未上报"," · ",Ue," × ",qe]})]})]}),(0,w.jsxs)("section",{className:"panel avatar-layout-panel",children:[(0,w.jsxs)("div",{className:"avatar-panel-heading",children:[(0,w.jsxs)("h3",{children:["客户端数字人位置 · ",ft.screenNumber,"号屏"]}),(0,w.jsx)("span",{className:ft.online?"online":"offline",children:ft.online?"在线":"离线"})]}),(0,w.jsx)("p",{className:"avatar-layout-note",children:"位置按客户端单独保存；应用时会将数字人切换到当前客户端。"}),(0,w.jsx)("div",{className:"avatar-bounds-fields",children:[["x","水平位置 X"],["y","垂直位置 Y"],["width","数字人宽度"],["height","数字人高度"]].map(([Nn,Vn])=>(0,w.jsxs)("label",{children:[(0,w.jsx)("span",{children:Vn}),(0,w.jsxs)("div",{children:[(0,w.jsx)("input",{type:"number",value:xe[Nn],onChange:Gn=>$e(Nn,Gn.target.value)}),(0,w.jsx)("i",{children:"px"})]})]},Nn))}),(0,w.jsxs)("div",{className:"avatar-layout-actions",children:[(0,w.jsx)("button",{className:"ghost",disabled:!ft.online||Boolean(Ae),onClick:()=>Ot("reset"),children:Ae==="reset"?"恢复中…":"恢复默认"}),(0,w.jsx)("button",{className:"primary",disabled:!ft.online||Boolean(Ae),onClick:()=>Ot("bounds"),children:Ae==="bounds"?"应用中…":"应用到当前客户端"})]})]}),(0,w.jsxs)("section",{className:"panel background-card avatar-background-card",children:[(0,w.jsxs)("div",{className:"background-copy",children:[(0,w.jsx)("h2",{children:"客户端默认背景"}),(0,w.jsxs)("p",{children:["用于",ft.screenNumber,"号屏的首页、输入和回答状态。"]})]}),(0,w.jsx)("div",{className:\`background-preview \${Se==="transparent"?"transparent":""}\`,children:Se==="transparent"?(0,w.jsx)("strong",{children:"透明背景"}):(0,w.jsx)("div",{className:"city-silhouette"})}),(0,w.jsxs)("div",{className:"file-info background-settings",children:[(0,w.jsx)("label",{children:"背景类型"}),(0,w.jsxs)("div",{className:"background-type-options",children:[(0,w.jsx)("button",{type:"button",className:Se==="image"?"active":"",disabled:Bt,onClick:()=>Qe("image"),children:"图片背景"}),(0,w.jsx)("button",{type:"button",className:Se==="transparent"?"active":"",disabled:Bt,onClick:()=>Qe("transparent"),children:"透明背景"})]}),(0,w.jsx)("small",{children:Se==="transparent"?"透明区域会透出客户端下层画面。":"使用客户端现有默认背景图。"}),(0,w.jsxs)("select",{value:Ct,disabled:Bt||Se==="transparent",onChange:Nn=>Xt(Nn.target.value),children:[(0,w.jsx)("option",{value:"cover",children:"铺满并居中裁切"}),(0,w.jsx)("option",{value:"contain",children:"完整显示"})]})]}),(0,w.jsxs)("div",{className:"background-actions",children:[(0,w.jsx)("button",{className:"ghost",disabled:Bt,onClick:()=>Kn({mode:"image",fit:"cover"}),children:"恢复默认"}),(0,w.jsx)("button",{className:"primary",disabled:Bt,onClick:()=>Kn(),children:Bt?"应用中…":"应用背景设置"})]})]})]}):null]})}function uaDigitalHumanManager({notify:I}){let[X,je]=(0,l.useState)(null),[ve,U]=(0,l.useState)(null),[xe,Re]=(0,l.useState)(null),[Ae,Be]=(0,l.useState)(""),[_e,be]=(0,l.useState)("欢迎来到思德科技展厅。"),[Pe,Ee]=(0,l.useState)(""),Se=(0,l.useRef)(false);async function Qe(){if(Se.current)return;Se.current=true;try{let[Ct,Xt,Bt,Ht]=await Promise.allSettled([p("/api/avatar-service"),uaGetAvatarTarget(),uaAvatarRequest("/health",void 0,"GET"),x()]);Ct.status==="fulfilled"&&je(Ct.value);Xt.status==="fulfilled"&&U(Xt.value);Bt.status==="fulfilled"?Re(Bt.value):Re(null);Ht.status==="fulfilled"&&Be(Ht.value);let Lt=[Ct,Xt,Ht].find(Wt=>Wt.status==="rejected");Ee(Lt?Lt.reason?.message||String(Lt.reason):"")}finally{Se.current=false}}(0,l.useEffect)(()=>{Qe();let Ct=window.setInterval(Qe,3e3);return()=>window.clearInterval(Ct)},[]);async function dn(Ct){Ee(Ct);try{let Xt=await p("/api/avatar-service",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:Ct})});je(Xt),I(Ct==="start"?"数字人控制服务已启动，开机自启动仍保持关闭":"数字人控制服务已停止"),await Qe()}catch(Xt){I(\`控制服务操作失败：\${Xt instanceof Error?Xt.message:String(Xt)}\`)}finally{Ee("")}}async function ft(Ct,Xt,Lt){Ee(Ct);try{await uaAvatarRequest(Xt,Ct==="say"?{text:_e}:{}),I(Lt),await Qe()}catch(Bt){I(\`数字人操作失败：\${Bt instanceof Error?Bt.message:String(Bt)}\`)}finally{Ee("")}}async function Ye(Ct){Ee("target");try{let Xt=await uaSetAvatarTarget(Ct);U(Xt),I(\`数字人已切换到\${Xt.label}\`)}catch(Xt){I(\`切换失败：\${Xt instanceof Error?Xt.message:String(Xt)}\`)}finally{Ee("")}}async function Sn(Ct){if(!Ae)return;let Xt={...Ae,avatarEnabled:Ct};Ee("tts");try{let Bt=await ee(Xt);Be(Bt),I(Ct?"已启用数字人播报":"已关闭数字人播报") }catch(Bt){I(\`播报设置失败：\${Bt instanceof Error?Bt.message:String(Bt)}\`)}finally{Ee("")}}let Ue=X?.running===true,qe=xe?.online===true,Ne=xe?.desiredOpen===true,Me=ve?.label||"61主机",Le=xe?.lastCommand;return(0,w.jsxs)("div",{className:"digital-human-console",children:[(0,w.jsxs)("div",{className:"service-status-grid",children:[(0,w.jsxs)("article",{className:"service-status-card",children:[(0,w.jsx)("span",{children:"控制服务"}),(0,w.jsx)("strong",{className:Ue?"online":"offline",children:Ue?"在线":"离线"}),(0,w.jsx)("small",{children:Ue?"19200 控制接口正常":"控制接口未启动"})]}),(0,w.jsxs)("article",{className:"service-status-card",children:[(0,w.jsx)("span",{children:"数字人客户端"}),(0,w.jsx)("strong",{className:qe?"online":"offline",children:qe?Ne?"已打开":"已连接":"未连接"}),(0,w.jsx)("small",{children:qe?xe?.session?.deviceId||"客户端会话在线":"等待数字人客户端连接"})]}),(0,w.jsxs)("article",{className:"service-status-card",children:[(0,w.jsx)("span",{children:"显示屏幕"}),(0,w.jsx)("strong",{children:Me}),(0,w.jsx)("small",{children:ve?.online===false?"目标客户端离线":"当前播报目标"})]}),(0,w.jsxs)("article",{className:"service-status-card",children:[(0,w.jsx)("span",{children:"最近指令"}),(0,w.jsx)("strong",{children:Le?.channel||"暂无"}),(0,w.jsx)("small",{children:Le?.at?new Date(Le.at).toLocaleTimeString("zh-CN",{hour12:false}):"暂无执行记录"})]})]}),(0,w.jsxs)("section",{className:"panel digital-human-service",children:[(0,w.jsxs)("div",{className:"digital-human-section-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("h2",{children:"控制服务"}),(0,w.jsx)("p",{children:"只控制独立数字人桥接服务，不影响中控、知识库和管理网页。"})]}),(0,w.jsxs)("div",{className:"digital-human-actions",children:[(0,w.jsx)("button",{className:"ghost",disabled:Boolean(Pe),onClick:Qe,children:"刷新"}),Ue?(0,w.jsx)("button",{className:"danger",disabled:Boolean(Pe),onClick:()=>dn("stop"),children:Pe==="stop"?"停止中…":"停止服务"}):(0,w.jsx)("button",{className:"primary",disabled:Boolean(Pe),onClick:()=>dn("start"),children:Pe==="start"?"启动中…":"启动服务"})]})]}),(0,w.jsxs)("div",{className:"avatar-broadcast-switch",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("strong",{children:"使用数字人播报"}),(0,w.jsx)("small",{children:Ae?.avatarEnabled?"回答统一由数字人客户端播报。":"回答继续使用普通 TTS。"})]}),(0,w.jsx)("input",{type:"checkbox",checked:Ae?.avatarEnabled===true,disabled:Pe==="tts",onChange:Ct=>Sn(Ct.target.checked)})]})]}),(0,w.jsxs)("section",{className:"panel digital-human-runtime",children:[(0,w.jsxs)("div",{className:"digital-human-section-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("h2",{children:"数字人运行控制"}),(0,w.jsx)("p",{children:"选择数字人所在屏幕，再控制打开、退出和画面显隐。"})]}),(0,w.jsx)("label",{className:"digital-human-target",children:(0,w.jsxs)("select",{value:String(ve?.target||"61"),disabled:Pe==="target",onChange:Ct=>Ye(Ct.target.value),children:[(0,w.jsx)("option",{value:"61",children:"61主机"}),...(ve?.targets||[]).filter(Ct=>Ct.target!=="61").map(Ct=>(0,w.jsx)("option",{value:Ct.target,disabled:!Ct.online,children:\`\${Ct.label}\${Ct.name?\` · \${Ct.name}\`:""}\`},Ct.target))]})})]}),(0,w.jsxs)("div",{className:"digital-human-command-row",children:[(0,w.jsx)("button",{className:"primary",disabled:!Ue||Boolean(Pe),onClick:()=>ft("open","/api/avatar/open","数字人已打开"),children:Pe==="open"?"打开中…":"打开数字人"}),(0,w.jsx)("button",{className:"danger",disabled:!Ue||Boolean(Pe),onClick:()=>ft("exit","/api/avatar/exit","数字人已退出"),children:Pe==="exit"?"退出中…":"退出数字人"}),(0,w.jsx)("button",{className:"ghost",disabled:!qe||Boolean(Pe),onClick:()=>ft("show","/api/avatar/show","数字人画面已显示"),children:"显示画面"}),(0,w.jsx)("button",{className:"ghost",disabled:!qe||Boolean(Pe),onClick:()=>ft("hide","/api/avatar/hide","数字人画面已隐藏"),children:"隐藏画面"})]})]}),(0,w.jsxs)("section",{className:"panel digital-human-test",children:[(0,w.jsxs)("div",{className:"digital-human-section-head",children:(0,w.jsxs)("div",{children:[(0,w.jsx)("h2",{children:"播报测试"}),(0,w.jsx)("p",{children:"文本直接发送到当前数字人客户端。"})]})}),(0,w.jsx)("textarea",{value:_e,onChange:Ct=>be(Ct.target.value),placeholder:"输入数字人需要播报的内容"}),(0,w.jsxs)("div",{className:"digital-human-command-row",children:[(0,w.jsx)("button",{className:"primary",disabled:!qe||!_e.trim()||Boolean(Pe),onClick:()=>ft("say","/api/avatar/say","数字人播报指令已发送"),children:Pe==="say"?"发送中…":"开始播报"}),(0,w.jsx)("button",{className:"danger",disabled:!qe||Boolean(Pe),onClick:()=>ft("interrupt","/api/avatar/interrupt","已打断数字人播报"),children:"打断播报"})]}),_e?(0,w.jsx)("div",{className:"avatar-console-error",children:_e}):null]})]})}`;
const fixedClientComponent = clientComponent
  .replace(
    '[Pe,Ee]=(0,l.useState)(""),Se=(0,l.useRef)(false);',
    '[Pe,Ee]=(0,l.useState)(""),[avatarError,setAvatarError]=(0,l.useState)(""),Se=(0,l.useRef)(false);'
  )
  .replace(
    'let Lt=[Ct,Xt,Ht].find(Wt=>Wt.status==="rejected");Ee(Lt?Lt.reason?.message||String(Lt.reason):"")',
    'let Lt=[Ct,Xt,Ht].find(Wt=>Wt.status==="rejected");setAvatarError(Lt?Lt.reason?.message||String(Lt.reason):"")'
  )
  .replace(
    '),_e?(0,w.jsx)("div",{className:"avatar-console-error",children:_e}):null]})]})}',
    '),avatarError?(0,w.jsx)("div",{className:"avatar-console-error",children:avatarError}):null]})]})}'
  );
const enhancedClientComponent = fixedClientComponent
  .replace(
    '[Bt,Ht]=(0,l.useState)(false),Lt=(0,l.useRef)(false)',
    '[Bt,Ht]=(0,l.useState)(false),[backgroundAsset,setBackgroundAsset]=(0,l.useState)({file:null,preview:"",name:""}),[backgroundProgress,setBackgroundProgress]=(0,l.useState)(0),backgroundInput=(0,l.useRef)(null),Lt=(0,l.useRef)(false)'
  )
  .replace(
    'Qe(ft?.settings?.transparentBackground?"transparent":"image"),Xt(ft?.settings?.backgroundFit==="contain"?"contain":"cover")',
    'Qe(ft?.settings?.transparentBackground?"transparent":"image"),Xt(ft?.settings?.backgroundFit==="contain"?"contain":"cover"),setBackgroundAsset({file:null,preview:uaSnapshotUrl(ft?.settings?.backgroundImageUrl),name:ft?.settings?.backgroundImageName||""})'
  )
  .replace(
    'async function Kn(Nn={mode:Se,fit:Ct}){if(!ft?.deviceId){I("请先选择一台展示客户端");return}Ht(true);try{await uaSaveScreenSettings(ft.deviceId,{transparentBackground:Nn.mode==="transparent",backgroundFit:Nn.fit});Qe(Nn.mode),Xt(Nn.fit),await dn(),I(Nn.mode==="transparent"?`${ft.screenNumber}号屏透明背景已下发`:`${ft.screenNumber}号屏图片背景已下发`)}catch(Vn){I(`背景设置失败：${Vn instanceof Error?Vn.message:String(Vn)}`)}finally{Ht(false)}}',
    'async function selectBackgroundFile(Nn){let Vn=Nn?.target?.files?.[0];if(!Vn)return;Nn.target.value="";if(!/\\.(png|jpe?g|webp)$/i.test(Vn.name)){I("请选择 PNG、JPG、JPEG 或 WEBP 图片");return}if(Vn.size>20*1024*1024){I("背景图片不能超过 20MB");return}let Gn=await new Promise((On,Fn)=>{let Yn=new FileReader;Yn.onload=()=>On(String(Yn.result||"")),Yn.onerror=()=>Fn(Error("图片预览读取失败")),Yn.readAsDataURL(Vn)});setBackgroundAsset({file:Vn,preview:Gn,name:Vn.name}),Qe("image")}async function Kn(Nn={mode:Se,fit:Ct,reset:false}){if(!ft?.deviceId){I("请先选择一台展示客户端");return}Ht(true),setBackgroundProgress(0);try{let Vn=Nn.reset?"":ft?.settings?.backgroundImageUrl||"",Gn=Nn.reset?"":ft?.settings?.backgroundImageName||"";if(backgroundAsset.file&&!Nn.reset){let On=await uaUploadScreenBackground(ft.deviceId,backgroundAsset.file,setBackgroundProgress);Vn=On.screen?.settings?.backgroundImageUrl||Vn,Gn=On.screen?.settings?.backgroundImageName||backgroundAsset.name}await uaSaveScreenSettings(ft.deviceId,{transparentBackground:Nn.mode==="transparent",backgroundFit:Nn.fit,backgroundImageUrl:Vn,backgroundImageName:Gn});Qe(Nn.mode),Xt(Nn.fit),setBackgroundAsset({file:null,preview:uaSnapshotUrl(Vn),name:Gn}),await dn(),I(Nn.mode==="transparent"?`${ft.screenNumber}号屏透明背景已下发`:`${ft.screenNumber}号屏图片背景已保存并下发`)}catch(Vn){I(`背景设置失败：${Vn instanceof Error?Vn.message:String(Vn)}`)}finally{Ht(false),setBackgroundProgress(0)}}'
  )
  .replace(
    '(0,w.jsx)("div",{className:`background-preview ${Se==="transparent"?"transparent":""}`,children:Se==="transparent"?(0,w.jsx)("strong",{children:"透明背景"}):(0,w.jsx)("div",{className:"city-silhouette"})})',
    '(0,w.jsx)("div",{className:`background-preview ${Se==="transparent"?"transparent":""}`,style:Se==="image"&&backgroundAsset.preview?{backgroundImage:`linear-gradient(rgba(7,35,61,.08),rgba(7,35,61,.08)),url("${backgroundAsset.preview}")`,backgroundSize:Ct==="contain"?"contain":"cover",backgroundPosition:"center",backgroundRepeat:"no-repeat"}:void 0,children:Se==="transparent"?(0,w.jsx)("strong",{children:"透明背景"}):backgroundAsset.preview?(0,w.jsx)("span",{className:"background-preview-badge",children:"当前图片"}):(0,w.jsx)("div",{className:"city-silhouette"})})'
  )
  .replace(
    '(0,w.jsx)("small",{children:Se==="transparent"?"透明区域会透出客户端下层画面。":"使用客户端现有默认背景图。"}),(0,w.jsxs)("select",{value:Ct',
    '(0,w.jsxs)("div",{className:"background-upload-control",children:[(0,w.jsx)("input",{ref:backgroundInput,type:"file",accept:".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp",hidden:true,onChange:selectBackgroundFile}),(0,w.jsx)("button",{type:"button",className:"ghost",disabled:Bt||Se==="transparent",onClick:()=>backgroundInput.current?.click(),children:"选择图片"}),(0,w.jsxs)("span",{title:backgroundAsset.name,children:[backgroundAsset.name||"尚未选择自定义图片",backgroundProgress>0?` · 上传 ${backgroundProgress}%`:""]})]}),(0,w.jsx)("small",{children:Se==="transparent"?"透明区域会透出客户端下层画面。":"支持 PNG、JPG、WEBP，建议 3840 × 2160，最大 20MB。"}),(0,w.jsxs)("select",{value:Ct'
  )
  .replace(
    'onClick:()=>Kn({mode:"image",fit:"cover"}),children:"恢复默认"',
    'onClick:()=>Kn({mode:"image",fit:"cover",reset:true}),children:"恢复默认"'
  );
bundle = bundle.slice(0, clientStartIndex) + enhancedClientComponent + bundle.slice(clientEndIndex + 1);

const permissionStart = 'function fe({notify:I}){';
const permissionEnd = '}var uaAccept=';
const permissionStartIndex = bundle.indexOf(permissionStart);
const permissionEndIndex = bundle.indexOf(permissionEnd, permissionStartIndex);
if (permissionStartIndex < 0 || permissionEndIndex < 0) throw new Error('account settings: stable component anchors changed');
const permissionComponent = `function fe({notify:I,account:X}){let[je,ve]=(0,l.useState)("review"),[U,xe]=(0,l.useState)(false),[Re,Ae]=(0,l.useState)({accounts:[],permissions:[],roleDefaults:{}}),[Be,_e]=(0,l.useState)(""),[be,Pe]=(0,l.useState)(null),[Ee,ln]=(0,l.useState)(false),Ve=uaHasPermission(X,"accounts.manage"),Se=uaHasPermission(X,"publish.manage"),Qe={"knowledge.view":"知识查看","knowledge.manage":"知识维护","records.view":"讲解记录","clients.manage":"客户端管理","avatar.manage":"数字人管理","publish.manage":"上架审批","accounts.manage":"账号管理"},dn={super_admin:"超级管理员",admin:"管理员",reviewer:"审核员",operator:"现场运维",viewer:"只读用户"};async function Ye(){if(!Ve)return;try{let Sn=await uaAdminRequest("/api/admin/accounts");Ae(Sn),_e(Ue=>Ue==="__new__"?Ue:Sn.accounts.some(qe=>qe.id===Ue)?Ue:Sn.accounts[0]?.id||"")}catch(Sn){I("账号列表读取失败："+(Sn instanceof Error?Sn.message:String(Sn)))}}(0,l.useEffect)(()=>{Se&&uaGetPublishSettings().then(Sn=>ve(Sn.mode==="AUTO_PUBLISH"?"auto":"review")).catch(Sn=>I("读取上架设置失败："+(Sn instanceof Error?Sn.message:String(Sn)))),Ye()},[X]);let Sn=Re.accounts.find(Ue=>Ue.id===Be)||null;(0,l.useEffect)(()=>{Be==="__new__"||Pe(Sn?{id:Sn.id,username:Sn.username,displayName:Sn.displayName,role:Sn.role,permissions:[...Sn.permissions],enabled:Sn.enabled,password:"",protected:Sn.protected}:null)},[Be,Re.accounts]);function Ue(){Pe({id:"",username:"",displayName:"",role:"viewer",permissions:[...(Re.roleDefaults.viewer||["knowledge.view","records.view"])],enabled:true,password:"",protected:false}),_e("__new__")}function qe(Me,Le){Pe(mn=>({...mn,[Me]:Le}))}function Ne(Me){let Le=be.permissions.includes(Me);qe("permissions",Le?be.permissions.filter(En=>En!==Me):[...be.permissions,Me])}async function Me(){if(!be)return;ln(true);try{let Le={username:be.username,displayName:be.displayName,role:be.role,permissions:be.permissions,enabled:be.enabled,...be.password?{password:be.password}:{}};if(be.id){let mn=await uaAdminRequest("/api/admin/accounts/"+encodeURIComponent(be.id),{method:"PATCH",body:JSON.stringify(Le)});I("账号“"+mn.account.displayName+"”已保存"),await Ye(),_e(mn.account.id)}else{let mn=await uaAdminRequest("/api/admin/accounts",{method:"POST",body:JSON.stringify(Le)});I("账号“"+mn.account.displayName+"”已创建"),await Ye(),_e(mn.account.id)}}catch(Le){I("账号保存失败："+(Le instanceof Error?Le.message:String(Le)))}finally{ln(false)}}async function Le(){if(!Sn||Sn.protected||!window.confirm("确定删除账号“"+Sn.displayName+"”吗？"))return;ln(true);try{await uaAdminRequest("/api/admin/accounts/"+encodeURIComponent(Sn.id),{method:"DELETE"}),I("账号已删除"),_e(""),await Ye()}catch(mn){I("删除失败："+(mn instanceof Error?mn.message:String(mn)))}finally{ln(false)}}return(0,w.jsxs)("div",{className:"permission-console",children:[Ve&&(0,w.jsxs)("section",{className:"panel account-management-panel",children:[(0,w.jsxs)("div",{className:"account-section-head",children:[(0,w.jsxs)("div",{children:[(0,w.jsx)("h2",{children:"账号与权限"}),(0,w.jsx)("p",{children:"新增或停用账号，设置角色、功能权限和登录密码。"})]}),(0,w.jsx)("button",{className:"primary",onClick:Ue,children:"新增账号"})]}),(0,w.jsxs)("div",{className:"account-workspace",children:[(0,w.jsx)("div",{className:"account-list",children:Re.accounts.map(mn=>(0,w.jsxs)("button",{className:Be===mn.id?"account-list-item active":"account-list-item",onClick:()=>_e(mn.id),children:[(0,w.jsx)("span",{className:"account-list-avatar",children:String(mn.displayName||mn.username).slice(0,1)}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:mn.displayName||mn.username}),(0,w.jsxs)("small",{children:[mn.username," · ",dn[mn.role]||mn.role]})]}),(0,w.jsx)("i",{className:mn.enabled?"enabled":"disabled",children:mn.enabled?"启用":"停用"})]},mn.id))}),(0,w.jsx)("div",{className:"account-editor",children:be?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsxs)("div",{className:"account-editor-title",children:[(0,w.jsx)("strong",{children:be.id?"编辑账号":"新增账号"}),be.protected&&(0,w.jsx)("span",{children:"系统保护账号"})]}),(0,w.jsxs)("div",{className:"account-form-grid",children:[(0,w.jsxs)("label",{children:[(0,w.jsx)("span",{children:"登录账号"}),(0,w.jsx)("input",{value:be.username,disabled:Boolean(be.id),onChange:mn=>qe("username",mn.target.value),placeholder:"例如 operator01"})]}),(0,w.jsxs)("label",{children:[(0,w.jsx)("span",{children:"显示名称"}),(0,w.jsx)("input",{value:be.displayName,onChange:mn=>qe("displayName",mn.target.value),placeholder:"例如 现场运维"})]}),(0,w.jsxs)("label",{children:[(0,w.jsx)("span",{children:"账号角色"}),(0,w.jsx)("select",{value:be.role,disabled:be.protected,onChange:mn=>{let En=mn.target.value;Pe(Ot=>({...Ot,role:En,permissions:[...(Re.roleDefaults[En]||[])]}))},children:Object.entries(dn).map(([mn,En])=>(0,w.jsx)("option",{value:mn,children:En},mn))})]}),(0,w.jsxs)("label",{children:[(0,w.jsx)("span",{children:be.id?"重置密码":"初始密码"}),(0,w.jsx)("input",{type:"password",value:be.password,onChange:mn=>qe("password",mn.target.value),placeholder:be.id?"不修改请留空":"至少6位"})]})]}),(0,w.jsx)("div",{className:"account-permission-title",children:"功能权限"}),(0,w.jsx)("div",{className:"account-permission-grid",children:Re.permissions.map(mn=>(0,w.jsxs)("label",{className:be.permissions.includes(mn)?"checked":"",children:[(0,w.jsx)("input",{type:"checkbox",checked:be.permissions.includes(mn),disabled:be.protected,onChange:()=>Ne(mn)}),(0,w.jsx)("span",{children:Qe[mn]||mn})]},mn))}),(0,w.jsxs)("label",{className:"account-enabled-switch",children:[(0,w.jsx)("input",{type:"checkbox",checked:be.enabled,disabled:be.protected,onChange:mn=>qe("enabled",mn.target.checked)}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:"允许登录"}),(0,w.jsx)("small",{children:"关闭后该账号立即无法再次登录"})]})]}),(0,w.jsxs)("div",{className:"account-editor-actions",children:[be.id&&!be.protected&&(0,w.jsx)("button",{className:"danger",disabled:Ee,onClick:Le,children:"删除账号"}),(0,w.jsx)("button",{className:"primary",disabled:Ee||!be.username.trim()||!be.displayName.trim()||!be.id&&be.password.length<6,onClick:Me,children:Ee?"保存中…":"保存账号"})]})]}):(0,w.jsx)("div",{className:"account-editor-empty",children:"选择左侧账号或新增账号"})})]})]}),Se&&(0,w.jsxs)("section",{className:"panel publish-card account-publish-card",children:[(0,w.jsx)("h2",{children:"内容上架方式"}),(0,w.jsx)("p",{className:"muted",children:"设置资料上传后的审核流程。"}),(0,w.jsx)("label",{children:"作品范围"}),(0,w.jsx)("div",{className:"readonly",children:"默认知识库（全部讲解词）"}),(0,w.jsx)("label",{children:"请选择"}),(0,w.jsxs)("label",{className:je==="review"?"radio checked":"radio",children:[(0,w.jsx)("input",{type:"radio",checked:je==="review",onChange:()=>ve("review"),name:"publish"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:"需要审核"}),(0,w.jsx)("small",{children:"上传后进入待审核列表，确认后统一上架"})]})]}),(0,w.jsxs)("label",{className:je==="auto"?"radio checked":"radio",children:[(0,w.jsx)("input",{type:"radio",checked:je==="auto",onChange:()=>ve("auto"),name:"publish"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:"自动通过"}),(0,w.jsx)("small",{children:"分析完成后立即进入正式知识库"})]})]}),(0,w.jsx)("button",{className:"primary full",disabled:U,onClick:async()=>{xe(true);try{await uaSavePublishSettings(je==="auto"?"AUTO_PUBLISH":"REVIEW_REQUIRED"),I("上架设置已保存到61真实后台")}catch(Re){I("保存失败："+(Re instanceof Error?Re.message:String(Re)))}finally{xe(false)}},children:U?"保存中…":"保存设置"})]})]})}`;
bundle = bundle.slice(0, permissionStartIndex) + permissionComponent + bundle.slice(permissionEndIndex + 1);

bundle = replaceOnce(
  bundle,
  'function uaUploadFile(I,X){return new Promise((je,ve)=>{let U=new XMLHttpRequest;U.open("POST",uaApi(`/api/files/upload?name=${encodeURIComponent(I.name)}`))',
  'async function uaCheckDuplicate(I){let X=await fetch(uaApi(`/api/files/duplicate?name=${encodeURIComponent(I.name)}&size=${I.size}`),{cache:"no-store"}),je=await X.json().catch(()=>null);if(!X.ok)throw Error(je?.error||"重复文件检查失败");return je}function uaShowDuplicateNotice(I){return new Promise(X=>{let je=document.createElement("div"),ve=document.createElement("div");je.className="file-delete-modal-backdrop duplicate-notice-backdrop",ve.className="file-delete-dialog duplicate-notice-dialog",ve.setAttribute("role","dialog"),ve.setAttribute("aria-modal","true"),ve.setAttribute("aria-labelledby","duplicate-notice-title"),ve.innerHTML=`<div class="file-delete-dialog-head"><span>!</span><div><h3 id="duplicate-notice-title">文件已存在</h3><p>系统已阻止重复上传，无需再次添加相同资料。</p></div></div><div class="file-delete-preview"><span class="file-doc">文</span><span><strong data-duplicate-name></strong><small data-duplicate-status></small></span></div><div class="file-delete-dialog-actions"><button type="button" class="primary duplicate-notice-close">关闭</button></div>`,ve.querySelector("[data-duplicate-name]").textContent=I.fileName||"重复文件",ve.querySelector("[data-duplicate-status]").textContent=I.status?`当前状态：${I.status}`:"已存在于资料库";let U=()=>{document.removeEventListener("keydown",xe),je.remove(),X()},xe=Re=>{Re.key==="Escape"&&U()};ve.querySelector(".duplicate-notice-close").addEventListener("click",U),document.addEventListener("keydown",xe),je.appendChild(ve),document.body.appendChild(je),ve.querySelector(".duplicate-notice-close").focus()})}function uaUploadFile(I,X,je=false){return new Promise((ve,U)=>{let xe=new XMLHttpRequest;xe.open("POST",uaApi(`/api/files/upload?name=${encodeURIComponent(I.name)}${je?"&replace=true":""}`))',
  'add duplicate upload preflight and replacement flag',
);

bundle = replaceOnce(
  bundle,
  'U.setRequestHeader("Content-Type",I.type||"application/octet-stream"),U.upload.onprogress=xe=>{xe.lengthComputable&&X(Math.round(xe.loaded/xe.total*100))},U.onerror=()=>ve(Error("网络连接中断")),U.onabort=()=>ve(Error("上传已取消")),U.onload=()=>{let xe=null;try{xe=JSON.parse(U.responseText)}catch{}U.status>=200&&U.status<300&&xe?.analysisJob?je({analysisJob:xe.analysisJob}):U.status>=200&&U.status<300?ve(Error("上传成功，但服务端未创建本地AI分析任务")):ve(Error(xe?.error||`上传失败（HTTP ${U.status}）`))},U.send(I)})}',
  'xe.setRequestHeader("Content-Type",I.type||"application/octet-stream"),xe.upload.onprogress=Re=>{Re.lengthComputable&&X(Math.round(Re.loaded/Re.total*100))},xe.onerror=()=>U(Error("网络连接中断")),xe.onabort=()=>U(Error("上传已取消")),xe.onload=()=>{let Re=null;try{Re=JSON.parse(xe.responseText)}catch{}xe.status>=200&&xe.status<300&&Re?.analysisJob?ve({analysisJob:Re.analysisJob}):xe.status>=200&&xe.status<300?U(Error("上传成功，但服务端未创建本地AI分析任务")):U(Error(Re?.error||`上传失败（HTTP ${xe.status}）`))},xe.send(I)})}',
  'finish duplicate-aware upload helper variable rename',
);

bundle = replaceOnce(
  bundle,
  'else Ne=(await uaUploadFile(qe.file,Me=>ln(qe.id,{progress:Me}))).analysisJob.id,ln(qe.id,{jobId:Ne});',
  'else{let Me=await uaCheckDuplicate(qe.file);if(Me?.duplicate){await uaShowDuplicateNotice({fileName:qe.file.name,status:Me.existing?.status||""}),ln(qe.id,{state:"duplicate",error:"文件已存在，未重复上传"});continue}Ne=(await uaUploadFile(qe.file,Ot=>ln(qe.id,{progress:Ot}),false)).analysisJob.id,ln(qe.id,{jobId:Ne})};',
  'show an in-product duplicate notice before upload',
);

bundle = replaceOnce(
  bundle,
  'Se.state==="waiting"||Se.state==="error"',
  'Se.state==="waiting"||Se.state==="error"||Se.state==="duplicate"',
  'allow duplicate item to be reconsidered',
);

bundle = replaceOnce(
  bundle,
  'let Se=je.filter(qe=>qe.state==="waiting"||qe.state==="error");',
  'let Se=je.filter(qe=>qe.state==="waiting"||qe.state==="error"||qe.state==="duplicate");',
  'include duplicate items in the next confirmed upload attempt',
);

bundle = replaceOnce(
  bundle,
  'Se.state==="waiting"&&"等待上传",Se.state==="success"',
  'Se.state==="waiting"&&"等待上传",Se.state==="duplicate"&&"文件已存在，未重复上传",Se.state==="success"',
  'render duplicate upload state',
);

bundle = replaceOnce(
  bundle,
  '(0,w.jsx)("small",{children:Me?"正在读取真实资料…":`原5180真实数据 · ${qe.length} 个`})',
  'null',
  'remove all-files legacy data subtitle',
);

bundle = replaceOnce(
  bundle,
  '(I==="FAQ库"||I==="敏感口径")&&` · 真实后台 ${Re.length} 条`',
  '(I==="FAQ库"||I==="敏感口径")&&` · 后台 ${Re.length} 条`',
  'remove real wording from knowledge row counts',
);

bundle = replaceOnce(
  bundle,
  '(0,w.jsx)("p",{children:"基于 61 中控持久化事件统计；动作数据表示“已下发”，不代表终端已执行完成。"})',
  'null',
  'remove operations implementation subtitle',
);

bundle = replaceOnce(
  bundle,
  '(0,w.jsx)("button",{className:"operation-refresh",onClick:()=>Ve(Ne=>Ne+1),disabled:_e,children:_e?"读取中…":"刷新真实数据"})',
  '(0,w.jsx)("button",{className:"ghost operation-refresh",onClick:()=>Ve(Ne=>Ne+1),disabled:_e,children:_e?"读取中…":"刷新数据"})',
  'align operations refresh button copy and style',
);

bundle = replaceOnce(
  bundle,
  '(Ae?.topQuestions||[]).map(',
  '(Ae?.topQuestions||[]).slice(0,5).map(',
  'cap hot questions at five rows',
);

bundle = replaceOnce(
  bundle,
  '(Ae?.filters.targets||[]).map(Ne=>(0,w.jsx)("option",{value:Ne,children:Ne},Ne))',
  '(Ae?.filters.targets||[]).map(Ne=>(0,w.jsx)("option",{value:Ne,children:Ae?.targetLabels?.[Ne]||(Ne==="all"?"整馆展示":/^\\d+$/.test(Ne)?Ne+"号屏":Ne)},Ne))',
  'target filter client labels',
);

bundle = replaceOnce(
  bundle,
  'Sn.slice(0,5).map(({name:Ne,count:Me},Le)=>{let mn=Ue?Math.round(Me/Ue*100):0;return(0,w.jsxs)("button",{onClick:()=>{Ae?.targetDistribution.length?Re(Ne):U(Ne),I(`已筛选 ${Ne}`)},children:[(0,w.jsx)("span",{children:Ne}),(0,w.jsx)("i",{children:(0,w.jsx)("u",{className:Le===1?"green":Le===2?"purple":"blue",style:{width:`${mn}%`}})}),(0,w.jsxs)("b",{children:[mn,"%"]})]},Ne)})',
  'Sn.slice(0,5).map(({name:Ne,count:Me,label:targetLabel},Le)=>{let mn=Ue?Math.round(Me/Ue*100):0,displayName=targetLabel||Ae?.targetLabels?.[Ne]||(Ne==="all"?"整馆展示":/^\\d+$/.test(Ne)?Ne+"号屏":Ne);return(0,w.jsxs)("button",{onClick:()=>{Ae?.targetDistribution.length?Re(Ne):U(Ne),I(`已筛选 ${displayName}`)},children:[(0,w.jsx)("span",{title:displayName,children:displayName}),(0,w.jsx)("i",{children:(0,w.jsx)("u",{className:Le===1?"green":Le===2?"purple":"blue",style:{width:`${mn}%`}})}),(0,w.jsxs)("b",{children:[mn,"%"]})]},Ne)})',
  'target distribution client labels',
);

bundle = replaceOnce(
  bundle,
  'children:[(0,w.jsx)("span",{className:"file-doc",children:"文"}),(0,w.jsxs)("span",{children:[(0,w.jsx)("strong",{children:ze.name})',
  'children:[(0,w.jsx)("span",{className:"file-cover file-cover-"+(T(ze)==="PPT"?"ppt":T(ze)==="PDF"?"pdf":"document"),children:ze.thumbnailUrl||I===Q&&thumbnailPages[0]?.url?(0,w.jsx)("img",{src:ze.thumbnailUrl||thumbnailPages[0].url,alt:ze.name+"缩略图"}):(0,w.jsx)("b",{children:T(ze)==="PPT"?"PPT":T(ze)==="PDF"?"PDF":"DOC"})}),(0,w.jsxs)("span",{className:"file-row-copy",children:[(0,w.jsx)("strong",{title:ze.name,children:ze.name})',
  'show a thumbnail or file-type cover in the file list',
);
fs.writeFileSync(outputBundlePath, bundle);

const css = fs.readFileSync(sourceCssPath, 'utf8') + `

/* File covers and compact operations UI */
.file-cover {
  display: grid;
  flex: 0 0 64px;
  width: 64px;
  aspect-ratio: 16 / 9;
  place-items: center;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(135deg, #5876d8, #7558be);
  border: 1px solid rgba(43, 65, 121, .18);
  border-radius: 5px;
  box-shadow: 0 2px 6px rgba(34, 57, 98, .12);
}
.file-cover img { display: block; width: 100%; height: 100%; object-fit: cover; }
.file-cover b { font-size: 11px; letter-spacing: .04em; }
.file-cover-pdf { background: linear-gradient(135deg, #db4c55, #ad2738); }
.file-cover-document { background: linear-gradient(135deg, #3d7bd8, #235cae); }
.file-row.active .file-cover { border-color: #1ba78b; box-shadow: 0 0 0 2px rgba(27, 167, 139, .15); }
.prototype-browser.resizable-file-browser {
  grid-template-columns: clamp(260px, var(--file-browser-width, 300px), 620px) 8px minmax(390px, 1.12fr) minmax(280px, .9fr);
  gap: 10px;
}
.prototype-browser.resizable-file-browser > .page-preview { min-width: 390px; }
.prototype-browser.resizable-file-browser > .content-maintenance { min-width: 0; }
.file-browser-resizer {
  position: relative;
  align-self: stretch;
  min-width: 8px;
  margin: 8px -2px;
  padding: 0;
  touch-action: none;
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: col-resize;
}
.file-browser-resizer::before {
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 50%;
  width: 2px;
  content: "";
  background: #d9e3ef;
  border-radius: 2px;
  transform: translateX(-50%);
  transition: width .15s ease, background .15s ease;
}
.file-browser-resizer:hover::before,
.file-browser-resizer:focus-visible::before {
  width: 4px;
  background: #4b8fee;
}
.file-browser-resizer:focus-visible { outline: 2px solid rgba(75, 143, 238, .2); }
body.resizing-file-browser,
body.resizing-file-browser * { cursor: col-resize !important; user-select: none !important; }
.file-row-shell .file-row {
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  min-height: 94px;
  padding-left: 13px;
  padding-right: 48px;
}
.file-row .file-row-copy {
  display: grid;
  min-width: 0;
  gap: 5px;
  align-content: center;
  overflow: hidden;
}
.file-row .file-row-copy > strong {
  display: -webkit-box;
  width: 100%;
  min-width: 0;
  overflow: hidden;
  white-space: normal;
  line-height: 1.4;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.file-row .file-row-tags {
  display: flex;
  width: 100%;
  min-width: 0;
  margin-top: 0;
  overflow: hidden;
  flex-wrap: nowrap;
}
.file-row .file-row-tags em { flex: 0 0 auto; }
.file-row .file-progress {
  display: block;
  width: 100%;
  min-width: 0;
  margin-top: 0;
  padding-right: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}
@media (max-width: 1280px) {
  .prototype-browser.resizable-file-browser {
    grid-template-columns: clamp(250px, var(--file-browser-width, 280px), 400px) 8px minmax(360px, 1fr) minmax(220px, .72fr);
    gap: 8px;
  }
  .prototype-browser.resizable-file-browser > .page-preview { min-width: 360px; }
  .file-row-shell .file-row {
    grid-template-columns: 52px minmax(0, 1fr);
    gap: 8px;
    min-height: 88px;
    padding-left: 10px;
    padding-right: 42px;
  }
  .file-cover { width: 52px; flex-basis: 52px; }
  .file-row-shell .file-delete { right: 8px; }
  .file-row-shell:has(.file-row.active) .file-delete { right: 14px; }
}
.review-commentary-preview {
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.75;
}
.review-main {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.review-main-heading {
  min-width: 0;
  padding-bottom: 14px;
  border-bottom: 1px solid #edf1f6;
}
.review-main-heading h2 {
  margin: 0;
  overflow: hidden;
  color: #1b2d45;
  font-size: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.review-main-heading p { margin: 7px 0 0; }
.review-detail-tabs {
  display: flex;
  gap: 4px;
  padding: 12px 0 10px;
}
.review-detail-tabs button {
  min-height: 34px;
  padding: 0 13px;
  color: #65758a;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
}
.review-detail-tabs button.active {
  color: #216fd8;
  background: #eef6ff;
  border-color: #cfe2fa;
  font-weight: 700;
}
.review-detail-tabs button span {
  display: inline-grid;
  min-width: 19px;
  height: 19px;
  margin-left: 5px;
  padding: 0 5px;
  place-items: center;
  color: inherit;
  background: rgba(33, 111, 216, .09);
  border-radius: 10px;
  font-size: 10px;
}
.review-detail-scroll {
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding-right: 5px;
  scrollbar-gutter: stable;
}
.review-detail-card.compact {
  margin-bottom: 12px;
}
.review-faq-card {
  margin-bottom: 9px;
  padding: 13px 14px;
  background: #f8fafc;
  border: 1px solid #e1e8f0;
  border-radius: 7px;
}
.review-faq-card > div {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: start;
}
.review-faq-card b {
  display: grid;
  width: 27px;
  height: 27px;
  place-items: center;
  color: #2378ee;
  background: #eaf3ff;
  border-radius: 6px;
  font-size: 10px;
}
.review-faq-card strong {
  min-width: 0;
  color: #24364d;
  font-size: 13px;
  line-height: 1.55;
  word-break: break-word;
}
.review-faq-card > div > span {
  padding-top: 4px;
  color: #8b97a7;
  font-size: 10px;
  white-space: nowrap;
}
.review-faq-card p {
  margin: 9px 0 0 37px;
  color: #65758a;
  font-size: 12px;
  line-height: 1.65;
  word-break: break-word;
}
.review-faq-pagination {
  position: sticky;
  bottom: 0;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 72px;
  gap: 10px;
  align-items: center;
  padding: 10px 0 2px;
  background: #fff;
  box-shadow: 0 -8px 14px rgba(255, 255, 255, .94);
}
.review-faq-pagination span {
  color: #738196;
  text-align: center;
  font-size: 11px;
}
.review-faq-pagination button {
  height: 30px;
  color: #2878ee;
  background: #f3f7fd;
  border: 1px solid #cfe0f6;
  border-radius: 5px;
  cursor: pointer;
}
.review-faq-pagination button:disabled { color: #aeb7c2; cursor: not-allowed; }
.operation-filters .operation-refresh {
  min-height: 40px;
  padding: 0 18px;
  color: #2378ee;
  font-weight: 600;
  background: #fff;
  border: 1px solid #cbd8e6;
  border-radius: 6px;
}
.hot-ranking { min-width: 0; }
.hot-ranking > button {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  width: 100%;
  min-width: 0;
}
.hot-ranking > button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.terminal-panel { min-width: 0; }
.terminal-panel > button {
  display: grid;
  grid-template-columns: minmax(126px, 1.15fr) minmax(72px, 1fr) 42px;
  width: 100%;
  min-width: 0;
}
.terminal-panel > button > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* On-demand PowerPoint page thumbnails */
.page-preview .ppt-page-preview {
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: #0b1e32;
  border: 1px solid #d8e2ee;
}
.page-preview .ppt-page-preview-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.page-preview .thumbnail-state {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  min-height: 280px;
  padding: 24px;
  text-align: center;
  background: #f7f9fc;
  border: 1px solid #d8e2ee;
}
.page-preview .thumbnail-state span,
.page-preview .thumbnail-grid-state { color: #718096; }
.page-preview .thumbnail-state.generating i {
  width: 26px;
  height: 26px;
  border: 3px solid #cbd9e8;
  border-top-color: #1cae91;
  border-radius: 50%;
  animation: ppt-thumbnail-spin .8s linear infinite;
}
.page-preview .thumbnail-state.failed strong { color: #b42318; }
.page-preview .thumbnail-retry {
  min-height: 36px;
  padding: 0 18px;
  color: #fff;
  background: #1677ff;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}
.page-preview .ppt-thumbnail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(128px, 1fr));
  align-content: start;
  gap: 10px;
}
.page-preview .ppt-thumbnail-grid button {
  display: grid;
  grid-template-rows: auto 28px;
  min-width: 0;
  height: auto;
  padding: 4px;
  overflow: hidden;
}
.page-preview .ppt-thumbnail-grid img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  background: #0b1e32;
}
.page-preview .ppt-thumbnail-grid span {
  align-self: center;
  font-size: 12px;
  color: #526174;
}
.page-preview .thumbnail-grid-state {
  padding: 20px 8px;
  text-align: center;
}
@keyframes ppt-thumbnail-spin { to { transform: rotate(360deg); } }
.upload-status.duplicate { color: #b7791f; }
.duplicate-notice-backdrop { z-index: 1400; }
.duplicate-notice-dialog .file-delete-dialog-head > span {
  color: #9a6a00;
  background: #fff5d6;
}
.duplicate-notice-dialog .file-delete-preview { margin-bottom: 22px; }
.duplicate-notice-close {
  min-width: 84px;
  color: #fff;
  background: #2878ee;
  border: 1px solid #2878ee;
}
.duplicate-notice-close:hover { background: #1f67d6; border-color: #1f67d6; }
.page-preview button.ppt-page-preview {
  position: relative;
  padding: 0;
  border: 0;
  cursor: zoom-in;
}
.page-preview .ppt-preview-hint {
  position: absolute;
  right: 12px;
  bottom: 12px;
  padding: 6px 10px;
  color: #fff;
  background: rgba(8, 30, 52, .78);
  border-radius: 5px;
  opacity: 0;
  transition: opacity .16s ease;
}
.page-preview .ppt-page-preview:hover .ppt-preview-hint { opacity: 1; }
.ppt-lightbox-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(4, 12, 22, .88);
}
.ppt-lightbox {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  width: min(96vw, 1800px);
  height: min(94vh, 1100px);
  overflow: hidden;
  background: #101923;
  border: 1px solid #405168;
  border-radius: 8px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, .45);
}
.ppt-lightbox-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 62px;
  padding: 10px 14px 10px 20px;
  color: #fff;
  background: #162536;
}
.ppt-lightbox-head > div:first-child { min-width: 0; }
.ppt-lightbox-head strong,
.ppt-lightbox-head span { display: block; }
.ppt-lightbox-head strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ppt-lightbox-head span { margin-top: 3px; color: #9fb2c7; font-size: 12px; }
.ppt-lightbox-tools { display: flex; gap: 6px; }
.ppt-lightbox-tools button {
  width: 38px;
  height: 38px;
  color: #fff;
  background: #26394e;
  border: 1px solid #405168;
  border-radius: 5px;
  cursor: pointer;
}
.ppt-lightbox-tools button:disabled { opacity: .4; cursor: default; }
.ppt-lightbox-tools .zoom-value { width: 64px; font-size: 12px; }
.ppt-lightbox-stage {
  display: grid;
  place-items: center;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 24px;
  background: #08111b;
}
.ppt-lightbox-stage > img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transform-origin: center;
  transition: transform .12s ease;
  cursor: zoom-in;
}
.ppt-lightbox-stage .thumbnail-state { color: #fff; background: transparent; border: 0; }

/* Batch review selection and observable publishing progress */
.review-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.review-list-head small { color: #718096; }
.review-select-all,
.review-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #526174;
  cursor: pointer;
}
.review-select-all input,
.review-checkbox input {
  width: 17px;
  height: 17px;
  accent-color: #1677ff;
}
.review-list .review-item {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  align-items: stretch;
  padding: 0;
}
.review-checkbox {
  justify-content: center;
  min-height: 66px;
}
.review-item-open {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px 12px 12px 0;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.review-item-open > span:nth-child(2) { min-width: 0; }
.review-item-open strong,
.review-item-open small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.review-progress {
  display: grid;
  gap: 9px;
  padding: 14px;
  background: #f5f9ff;
  border: 1px solid #cfe0f5;
  border-radius: 6px;
}
.review-progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.review-progress-head strong { min-width: 0; color: #17324d; }
.review-progress-head span { color: #1677ff; font-weight: 700; }
.review-progress-track {
  height: 8px;
  overflow: hidden;
  background: #dce8f5;
  border-radius: 4px;
}
.review-progress-track i {
  display: block;
  height: 100%;
  background: #1677ff;
  border-radius: inherit;
  transition: width .2s ease;
}
.review-progress small { color: #526174; line-height: 1.45; }

/* Robot teaching drafts now share the pending-review workspace. */
.review-list .robot-review-item { margin-bottom: 8px; }
.robot-review-marker { pointer-events: none; }
.robot-review-marker i {
  display: block;
  width: 8px;
  height: 8px;
  background: #23a67a;
  border: 2px solid #dff5ec;
  border-radius: 50%;
  box-sizing: content-box;
}
.robot-review-item .doc-icon { color: #2878d5; background: #eaf3ff; }
.review-expand-arrow {
  color: #8795a8;
  font-size: 18px;
  font-style: normal;
  transition: transform .16s ease;
}
.robot-review-item.active .review-expand-arrow { color: #2878d5; }
.robot-draft-list {
  display: grid;
  gap: 5px;
  max-height: 220px;
  margin: -1px 0 10px 15px;
  padding: 7px 0 2px 14px;
  overflow-y: auto;
  border-left: 1px solid #d7e3f2;
}
.robot-draft-list button {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 6px 9px;
  color: #53657a;
  text-align: left;
  background: #fff;
  border: 1px solid #edf1f6;
  border-radius: 6px;
  cursor: pointer;
}
.robot-draft-list button:hover,
.robot-draft-list button.active { color: #185fac; background: #f2f7fd; border-color: #b8d3f2; }
.robot-draft-list i { color: #8a9aaf; font-size: 10px; font-style: normal; }
.robot-draft-list span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.review-group-label {
  margin: 12px 0 7px;
  padding-top: 12px;
  color: #8491a3;
  border-top: 1px solid #edf1f6;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .05em;
}
.no-files.compact { padding: 18px 8px; }
.robot-review-form {
  display: grid;
  grid-row: 1 / -1;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 20px;
  min-width: 0;
  min-height: 0;
}
.robot-review-form h2 {
  margin: 0;
  padding-bottom: 16px;
  color: #1b2d45;
  border-bottom: 1px solid #edf1f6;
  font-size: 18px;
}
.robot-review-field { display: grid; gap: 8px; min-width: 0; }
.robot-review-field > span { color: #40526a; font-size: 13px; font-weight: 700; }
.robot-review-field input,
.robot-review-field textarea {
  width: 100%;
  min-width: 0;
  padding: 12px 13px;
  color: #24364d;
  background: #fbfcfe;
  border: 1px solid #d9e1ea;
  border-radius: 7px;
  outline: 0;
  font: inherit;
  line-height: 1.65;
  resize: none;
}
.robot-review-field input:focus,
.robot-review-field textarea:focus { background: #fff; border-color: #65a1eb; box-shadow: 0 0 0 3px rgba(38, 121, 224, .1); }
.robot-review-field.answer { min-height: 0; }
.robot-review-field.answer textarea { height: 100%; min-height: 220px; }

/* System settings information architecture */
.system-settings-page {
  width: min(100%, 1500px);
  margin: 0 auto;
  padding-bottom: 32px;
}
.system-settings-tabs {
  margin-bottom: 20px;
  border-bottom: 1px solid #dfe6ef;
}
.client-manager-empty { padding: 42px; text-align: center; color: #718096; }
.service-status-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-column: 1 / -1;
  gap: 12px;
}
.service-status-card {
  display: grid;
  align-content: center;
  gap: 5px;
  min-height: 94px;
  padding: 15px 18px;
  background: #fff;
  border: 1px solid #dfe5ec;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(25, 49, 79, .035);
}
.service-status-card span,
.service-status-card small { color: #748399; }
.service-status-card span { font-size: 13px; font-weight: 600; }
.service-status-card small { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.service-status-card strong { overflow: hidden; color: #18263b; font-size: 22px; line-height: 1.2; text-overflow: ellipsis; white-space: nowrap; }
.service-status-card strong.online { color: #159b70; }
.service-status-card strong.offline { color: #d94b55; }
.digital-human-console {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr);
  gap: 16px;
  align-items: stretch;
}
.digital-human-console > .panel {
  padding: 22px 24px;
  border: 1px solid #dfe6ef;
  border-radius: 8px;
  box-shadow: 0 3px 14px rgba(25, 49, 79, .04);
}
.digital-human-service { grid-column: 1; }
.digital-human-runtime { grid-column: 1; }
.digital-human-test {
  display: flex;
  grid-column: 2;
  grid-row: 2 / span 2;
  flex-direction: column;
}
.digital-human-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}
.digital-human-section-head h2 { margin: 0 0 5px; color: #17263b; font-size: 18px; line-height: 1.35; }
.digital-human-section-head p { margin: 0; color: #718096; font-size: 13px; line-height: 1.55; }
.digital-human-actions,
.digital-human-command-row { display: flex; flex-wrap: wrap; gap: 10px; }
.digital-human-actions button,
.digital-human-command-row button {
  min-height: 38px;
  padding: 0 17px;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  border-radius: 6px;
  cursor: pointer;
}
.digital-human-actions button:disabled,
.digital-human-command-row button:disabled { cursor: not-allowed; opacity: .48; }
.digital-human-actions .danger,
.digital-human-command-row .danger { color: #c53843; background: #fff5f5; border: 1px solid #f3c5c9; }
.avatar-broadcast-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 16px;
  background: #f7faff;
  border: 1px solid #dbe8f6;
  border-radius: 6px;
}
.avatar-broadcast-switch strong,
.avatar-broadcast-switch small { display: block; }
.avatar-broadcast-switch small { margin-top: 5px; color: #718096; }
.avatar-broadcast-switch input[type="checkbox"] {
  position: relative;
  flex: 0 0 auto;
  width: 44px;
  height: 24px;
  margin: 0;
  appearance: none;
  background: #b9c5d4;
  border: 0;
  border-radius: 12px;
  cursor: pointer;
  transition: background .18s ease;
}
.avatar-broadcast-switch input[type="checkbox"]::after {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  content: "";
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 4px rgba(22, 40, 64, .24);
  transition: transform .18s ease;
}
.avatar-broadcast-switch input[type="checkbox"]:checked { background: #2378ee; }
.avatar-broadcast-switch input[type="checkbox"]:checked::after { transform: translateX(20px); }
.digital-human-target select {
  width: clamp(220px, 24vw, 340px);
  min-height: 40px;
  padding: 0 34px 0 12px;
  color: #24364d;
  font: inherit;
  font-size: 13px;
  background: #fff;
  border: 1px solid #d5dfeb;
  border-radius: 6px;
}
.digital-human-test textarea {
  box-sizing: border-box;
  flex: 1 1 auto;
  width: 100%;
  min-height: 168px;
  margin-bottom: 14px;
  padding: 14px 15px;
  color: #20324a;
  font: inherit;
  font-size: 14px;
  line-height: 1.65;
  resize: vertical;
  border: 1px solid #d8e2ee;
  border-radius: 6px;
  outline: none;
}
.digital-human-test textarea:focus {
  border-color: #438cf4;
  box-shadow: 0 0 0 3px rgba(35, 120, 238, .1);
}
.client-manager .avatar-workspace { align-items: start; }
.client-manager .avatar-layout-note { margin: 0 0 14px; }
.avatar-background-card .background-preview {
  position: relative;
  overflow: hidden;
}
.background-preview-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 4px 7px;
  color: #fff;
  background: rgba(17, 39, 65, .72);
  border-radius: 4px;
  font-size: 10px;
}
.background-upload-control {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.background-upload-control button {
  min-width: 82px;
  height: 34px;
  padding: 0 11px;
}
.background-upload-control > span {
  min-width: 0;
  overflow: hidden;
  color: #6f7e91;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Admin accounts and permissions */
.account-loading {
  display: grid;
  min-height: 100vh;
  place-items: center;
  color: #66758a;
  background: #f4f7fb;
}
.account-modal-backdrop {
  position: fixed;
  z-index: 1000;
  inset: 0;
  display: grid;
  padding: 24px;
  place-items: center;
  background: rgba(20, 35, 55, .42);
  backdrop-filter: blur(3px);
}
.account-login-dialog {
  width: min(420px, 100%);
  padding: 28px;
  background: #fff;
  border: 1px solid #dbe4ef;
  border-radius: 8px;
  box-shadow: 0 22px 60px rgba(22, 42, 68, .2);
}
.account-dialog-head,
.current-account-strip,
.account-section-head,
.account-editor-title,
.account-editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.account-dialog-head { margin-bottom: 20px; }
.account-dialog-head h2,
.account-section-head h2 { margin: 0 0 5px; color: #17263b; }
.account-dialog-head p,
.account-section-head p { margin: 0; color: #718096; }
.account-dialog-close {
  width: 34px;
  height: 34px;
  color: #68778b;
  font-size: 24px;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.current-account-strip {
  margin-bottom: 18px;
  padding: 12px;
  background: #f5f8fc;
  border: 1px solid #e0e7f0;
  border-radius: 7px;
}
.current-account-strip > span,
.account-list-avatar {
  display: grid;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #fff;
  font-style: normal;
  font-weight: 700;
  background: #2378ee;
  border-radius: 50%;
}
.current-account-strip > div { min-width: 0; flex: 1; }
.current-account-strip strong,
.current-account-strip small { display: block; }
.current-account-strip small { margin-top: 3px; color: #718096; }
.account-field { display: block; margin-bottom: 15px; }
.account-field span,
.account-form-grid label > span {
  display: block;
  margin-bottom: 7px;
  color: #42536a;
  font-size: 13px;
  font-weight: 600;
}
.account-field input,
.account-form-grid input,
.account-form-grid select {
  box-sizing: border-box;
  width: 100%;
  min-height: 40px;
  padding: 0 12px;
  color: #21334a;
  font: inherit;
  background: #fff;
  border: 1px solid #d5dfeb;
  border-radius: 6px;
  outline: none;
}
.account-field input:focus,
.account-form-grid input:focus,
.account-form-grid select:focus {
  border-color: #438cf4;
  box-shadow: 0 0 0 3px rgba(35, 120, 238, .1);
}
.account-form-error {
  margin: -3px 0 14px;
  padding: 10px 12px;
  color: #b4232d;
  background: #fff3f4;
  border-radius: 6px;
}
.account-switch-button {
  width: calc(100% - 32px);
  color: inherit;
  font: inherit;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.account-top-button {
  padding: 5px 9px;
  color: #53657a;
  font: inherit;
  background: #f3f6fa;
  border: 1px solid #e0e6ee;
  border-radius: 5px;
  cursor: pointer;
}
.permission-console {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}
.account-management-panel { padding: 22px 24px; }
.account-section-head { margin-bottom: 18px; }
.account-section-head p { font-size: 13px; }
.account-workspace {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  min-height: 420px;
  overflow: hidden;
  border: 1px solid #dfe6ef;
  border-radius: 7px;
}
.account-list {
  padding: 8px;
  background: #f6f8fb;
  border-right: 1px solid #dfe6ef;
}
.account-list-item {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 11px 10px;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
}
.account-list-item.active {
  background: #fff;
  border-color: #cfe0f7;
  box-shadow: 0 2px 8px rgba(25, 62, 104, .06);
}
.account-list-item > span:nth-child(2) { min-width: 0; }
.account-list-item strong,
.account-list-item small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.account-list-item small { margin-top: 3px; color: #718096; }
.account-list-item i { font-size: 12px; font-style: normal; }
.account-list-item i.enabled { color: #159b70; }
.account-list-item i.disabled { color: #c34b55; }
.account-editor { padding: 20px; }
.account-editor-title { margin-bottom: 17px; }
.account-editor-title > span {
  padding: 3px 7px;
  color: #856400;
  font-size: 12px;
  background: #fff8dd;
  border-radius: 4px;
}
.account-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.account-permission-title { margin: 20px 0 10px; color: #42536a; font-size: 13px; font-weight: 700; }
.account-permission-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.account-permission-grid label {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  color: #53657a;
  background: #f7f9fc;
  border: 1px solid #e0e6ee;
  border-radius: 6px;
}
.account-permission-grid label.checked { color: #1768d2; background: #f1f7ff; border-color: #bcd6f8; }
.account-enabled-switch {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
}
.account-enabled-switch strong,
.account-enabled-switch small { display: block; }
.account-enabled-switch small { margin-top: 3px; color: #718096; }
.account-editor-actions { justify-content: flex-end; margin-top: 20px; }
.account-editor-actions button { min-height: 38px; padding: 0 17px; }
.account-editor-actions .danger { color: #c53843; background: #fff5f5; border: 1px solid #f3c5c9; }
.account-editor-empty { display: grid; height: 100%; place-items: center; color: #718096; }
.account-publish-card { position: sticky; top: 18px; }
.no-settings-access { padding: 42px; color: #718096; text-align: center; }
@media (max-width: 1180px) {
  .service-status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .digital-human-console { grid-template-columns: 1fr; }
  .digital-human-service,
  .digital-human-runtime,
  .digital-human-test { grid-column: 1; grid-row: auto; }
  .permission-console { grid-template-columns: 1fr; }
  .account-publish-card { position: static; }
}
@media (max-width: 760px) {
  .service-status-grid { grid-template-columns: 1fr; }
  .digital-human-console > .panel { padding: 18px; }
  .digital-human-section-head { align-items: flex-start; flex-direction: column; }
  .digital-human-target, .digital-human-target select { width: 100%; }
  .account-workspace { grid-template-columns: 1fr; }
  .account-list { border-right: 0; border-bottom: 1px solid #dfe6ef; }
  .account-form-grid,
  .account-permission-grid { grid-template-columns: 1fr; }
}
`;
fs.writeFileSync(outputCssPath, css);

let indexHtml = fs.readFileSync(indexPath, 'utf8');
indexHtml = indexHtml
  .replace(/index-real-analysis-v\d+\.js\?v=[^"']+/, 'index-real-analysis-v22.js?v=robot-review-toggle-3')
  .replace(/upload-real-v\d+\.css\?v=[^"']+/, 'upload-real-v12.css?v=robot-review-toggle-3');
fs.writeFileSync(indexPath, indexHtml);

console.log('Built admin UI: v22 JS + v12 CSS');
