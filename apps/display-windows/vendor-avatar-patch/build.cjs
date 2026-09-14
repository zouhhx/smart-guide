const fs=require('node:fs');const crypto=require('node:crypto');const esbuild=require('esbuild');
const [input,output]=process.argv.slice(2);if(!input||!output)throw Error('Usage: node build.cjs ORIGINAL_APP_ASAR_OR_JS OUTPUT_JS');
let raw=fs.readFileSync(input);
if(input.endsWith('.asar')){const header=JSON.parse(raw.subarray(16,16+raw.readUInt32LE(12)).toString('utf8'));const meta=header.files.out.files.renderer.files.assets.files['index-CPRpiHM5.js'];const offset=8+raw.readUInt32LE(4)+Number(meta.offset);raw=raw.subarray(offset,offset+meta.size);}
const hash=crypto.createHash('sha256').update(raw).digest('hex');
if(hash!=='1331d61d1c66de76155cceb572863008b97090f533fe188d4edba97fcc8f2f34')throw Error('Unsupported original renderer; use the unpatched vendor backup');
let text=raw.toString('utf8');const size=raw.length;
function change(a,b){if(text.split(a).length!==2)throw Error('not unique '+a);text=text.replace(a,b)}
change('Qe=new Map,nn=uo=>{const{proxy_address:', 'Qe=new Map,stopEpoch={value:0},nn=uo=>{const{proxy_address:');
change('un=()=>{Ge.value!==SdkPageState.CHAT&&Je()}', 'un=()=>{stopEpoch.value++;Je();Ge.value=SdkPageState.IDLE}');
change('cn=uo=>{Ge.value!==SdkPageState.CHAT&&$e().then(()=>{Ge.value!==SdkPageState.CHAT&&', 'cn=uo=>{const epoch=stopEpoch.value;Ge.value!==SdkPageState.CHAT&&$e().then(()=>{epoch===stopEpoch.value&&Ge.value!==SdkPageState.CHAT&&');
change('hn=uo=>{Ge.value!==SdkPageState.CHAT&&$e(!0).then(()=>{Ge.value!==SdkPageState.CHAT&&', 'hn=uo=>{const epoch=stopEpoch.value;Ge.value!==SdkPageState.CHAT&&$e(!0).then(()=>{epoch===stopEpoch.value&&Ge.value!==SdkPageState.CHAT&&');
// Enable the SDK's coherent path: interrupt, stale-event filtering, and face reset on new voice_start.
// Do not separately call the low-level interrupt method.
change('this._env=m.env||"production",this.enableClientInterrupt=m.enableClientInterrupt||!1', 'this._env=m.env||"production",this.enableClientInterrupt=!0');

text=text.replaceAll(':::[SDK ', '[SDK ').replaceAll(':::[AgentConsole]', '[Agent]');
const result=esbuild.transformSync(text,{loader:'js',charset:'utf8',minifyWhitespace:true,minifyIdentifiers:true,minifySyntax:true,legalComments:'inline',target:'esnext'}).code;
const bytes=Buffer.from(result);if(bytes.length>size)throw Error('too large '+bytes.length+' vs '+size);const final=Buffer.alloc(size,32);bytes.copy(final);fs.writeFileSync(output,final);console.log({original:size,minified:bytes.length,padded:final.length});
