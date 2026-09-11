import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bundlePath = path.join(root, 'admin-ui', 'assets', 'index-real-analysis-v9.js');
let bundle = fs.readFileSync(bundlePath, 'utf8');

function replaceOnce(before, after, label) {
  if (bundle.includes(after)) return;
  const first = bundle.indexOf(before);
  if (first < 0) throw new Error(`UI patch target missing: ${label}`);
  if (bundle.indexOf(before, first + before.length) >= 0) throw new Error(`UI patch target is ambiguous: ${label}`);
  bundle = `${bundle.slice(0, first)}${after}${bundle.slice(first + before.length)}`;
}

const duplicateEditState = '[editingFaq,setEditingFaq]=(0,l.useState)(null),[faqDraft,setFaqDraft]=(0,l.useState)({question:"",answer:""}),[faqSaving,setFaqSaving]=(0,l.useState)(!1),[editingFaq,setEditingFaq]=(0,l.useState)(null),[faqDraft,setFaqDraft]=(0,l.useState)({question:"",answer:""}),[faqSaving,setFaqSaving]=(0,l.useState)(!1)';
if (bundle.includes(duplicateEditState)) {
  bundle = bundle.replace(duplicateEditState, '[editingFaq,setEditingFaq]=(0,l.useState)(null),[faqDraft,setFaqDraft]=(0,l.useState)({question:"",answer:""}),[faqSaving,setFaqSaving]=(0,l.useState)(!1)');
}

function replaceBetween(start, end, replacement, label) {
  const startIndex = bundle.indexOf(start);
  if (startIndex < 0 && bundle.includes(replacement)) return;
  if (startIndex < 0) throw new Error(`UI patch start missing: ${label}`);
  const endIndex = bundle.indexOf(end, startIndex + start.length);
  if (endIndex < 0) throw new Error(`UI patch end missing: ${label}`);
  bundle = `${bundle.slice(0, startIndex)}${replacement}${bundle.slice(endIndex)}`;
}

replaceOnce(
  'faqs:je.filter(xe=>xe.raw_json?.frontmatter?.resource===U.name)',
  'faqs:je.filter(xe=>xe.sourceFile===U.name||xe.raw_json?.frontmatter?.resource===U.name)',
  'associate manual FAQ overrides with their source file',
);

replaceOnce(
  '[faqPageState,setFaqPageState]=(0,l.useState)(0),[deletingFile,setDeletingFile]=(0,l.useState)("")',
  '[faqPageState,setFaqPageState]=(0,l.useState)(0),[deletingFile,setDeletingFile]=(0,l.useState)(""),[editingFaq,setEditingFaq]=(0,l.useState)(null),[faqDraft,setFaqDraft]=(0,l.useState)({question:"",answer:""}),[faqSaving,setFaqSaving]=(0,l.useState)(!1)',
  'add page FAQ editing state',
);

replaceOnce(
  'ke=dn?Sn:ie(G?.text||"",Se),Ie=ze=>',
  'ke=dn?Sn:ie(G?.text||"",Se),pageFaqs=(en?.faqs||[]).filter(ze=>Number(ze.sourcePage||ze.raw_json?.sourcePage||ze.raw_json?.page_number||1)===Pe+1),Ie=ze=>',
  'select FAQs for the current PPT page',
);

replaceOnce(
  'Rn=Array.from({length:Math.min(Zn,80)}',
  'Rn=Array.from({length:Zn}',
  'show every PPT page in the page selector',
);

replaceOnce(
  'sn=ze=>{Ee(ze),Ue(""),Ye(!1)},deleteFile=',
  'sn=ze=>{Ee(ze),Ue(""),Ye(!1),setFaqPageState(0),setEditingFaq(null)},deleteFile=',
  'reset FAQ pagination when switching PPT pages',
);

replaceOnce(
  'children:deletingFile===ze.name?"…":"删"',
  'children:deletingFile===ze.name?"…":"×"',
  'render file deletion as a small cross',
);

replaceOnce(
  'finally{setDeletingFile("")}};return(0,w.jsxs)("div",{className:"prototype-browser"',
  'finally{setDeletingFile("")}},editPageFaq=ze=>{setEditingFaq(ze.id),setFaqDraft({question:ze.question||"",answer:ze.answer||""})},savePageFaq=async ze=>{if(!faqDraft.question.trim()||!faqDraft.answer.trim()){je("问题和答案不能为空");return}setFaqSaving(!0);try{let Q=await v({id:ze.id,source:ze.source,question:faqDraft.question,answer:faqDraft.answer,keywords:ze.keywords||[],enabled:ze.enabled!==!1,sourcePage:Number(ze.sourcePage||ze.raw_json?.sourcePage||Pe+1),sourceFile:en?.name||""});Ne(ye=>ye.map((Te,Fe)=>Fe===I?{...Te,faqs:(Te.faqs||[]).map(Ge=>Ge.id===ze.id?Q:Ge)}:Te)),setEditingFaq(null),je("FAQ已保存，FAQ库与当前页面已同步")}catch(Q){je(`保存失败：${Q instanceof Error?Q.message:String(Q)}`)}finally{setFaqSaving(!1)}};return(0,w.jsxs)("div",{className:"prototype-browser"',
  'add current-page FAQ edit/save actions',
);

const pageFaqReplacement = `):(0,w.jsxs)("div",{className:"page-faq",children:[(0,w.jsxs)("div",{className:"faq-toolbar",children:[(0,w.jsxs)("strong",{children:["当前页 FAQ",(0,w.jsxs)("em",{className:"faq-count",children:["第 ",Pe+1," 页 · 共 ",pageFaqs.length," 条"]})]}),(0,w.jsx)("span",{className:"pill success",children:"真实知识库"})]}),pageFaqs.length?(0,w.jsxs)(w.Fragment,{children:[pageFaqs.slice(faqPageState*10,faqPageState*10+10).map((ze,Q)=>{let ye=editingFaq===ze.id;return(0,w.jsxs)("div",{className:ye?"faq-card editing":"faq-card",children:[(0,w.jsx)("b",{className:"faq-sequence",children:String(faqPageState*10+Q+1).padStart(2,"0")}),ye?(0,w.jsxs)(w.Fragment,{children:[(0,w.jsx)("textarea",{className:"faq-question-editor","aria-label":"编辑FAQ问题",value:faqDraft.question,onChange:Te=>setFaqDraft(Fe=>({...Fe,question:Te.target.value}))}),(0,w.jsx)("textarea",{className:"faq-answer-editor","aria-label":"编辑FAQ答案",value:faqDraft.answer,onChange:Te=>setFaqDraft(Fe=>({...Fe,answer:Te.target.value}))}),(0,w.jsxs)("div",{className:"faq-edit-actions",children:[(0,w.jsx)("button",{className:"ghost",disabled:faqSaving,onClick:()=>setEditingFaq(null),children:"取消"}),(0,w.jsx)("button",{className:"primary",disabled:faqSaving,onClick:()=>savePageFaq(ze),children:faqSaving?"保存中…":"保存"})]})]}):(0,w.jsxs)(w.Fragment,{children:[(0,w.jsx)("strong",{children:ze.question}),(0,w.jsx)("span",{children:ze.answer}),(0,w.jsx)("i",{children:"已发布"}),(0,w.jsx)("button",{className:"faq-edit",onClick:()=>editPageFaq(ze),children:"编辑"})]})]},ze.id||ze.question)}),(0,w.jsxs)("div",{className:"faq-pagination",children:[(0,w.jsx)("button",{disabled:faqPageState===0,onClick:()=>setFaqPageState(ze=>Math.max(0,ze-1)),children:"上一页"}),(0,w.jsxs)("span",{children:["第 ",faqPageState+1," / ",Math.ceil(pageFaqs.length/10)," 页"]}),(0,w.jsx)("button",{disabled:(faqPageState+1)*10>=pageFaqs.length,onClick:()=>setFaqPageState(ze=>Math.min(Math.ceil(pageFaqs.length/10)-1,ze+1)),children:"下一页"})]})]}):(0,w.jsx)("div",{className:"no-files",children:"当前页面暂无真实FAQ"})]})]})]})}`;

replaceBetween(
  '):(0,w.jsxs)("div",{className:"page-faq"',
  'function k(',
  pageFaqReplacement,
  'render and edit the selected page FAQ collection',
);

fs.writeFileSync(bundlePath, bundle, 'utf8');
