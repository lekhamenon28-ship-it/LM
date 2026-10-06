/* The application shell contains no local simulator state. */
let state={features:{},incidents:[],policies:{},audit:[]};
function persist(){}
function audit(action,detail){window.dispatchEvent(new CustomEvent('aether:statechange',{detail:{action,detail}}))}
const dialog=document.createElement('dialog');dialog.id='detailDialog';dialog.className='detail-dialog';dialog.setAttribute('aria-labelledby','detailTitle');document.body.append(dialog);
let focusBeforeDialog;
function detail(title,content){focusBeforeDialog=document.activeElement;dialog.innerHTML=`<header class="detail-header"><h2 id="detailTitle">${escapeHtml(title)}</h2><button aria-label="Close details" onclick="closeDetail()">✕</button></header><div class="detail-body">${content}</div>`;if(!dialog.open)dialog.showModal();dialog.querySelector('button').focus()}
function closeDetail(){dialog.close();focusBeforeDialog?.focus()}
dialog.addEventListener('click',ev=>{if(ev.target===dialog){const r=dialog.getBoundingClientRect();if(ev.clientX<r.left||ev.clientX>r.right||ev.clientY<r.top||ev.clientY>r.bottom)closeDetail()}});
function kv(label,value){return `<div class="detail-kv"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value??'Not reported')}</strong></div>`}
window.addEventListener('popstate',()=>switchTab(location.hash.slice(1)));
window.addEventListener('DOMContentLoaded',()=>{$('mobileMenuBtn').onclick=()=>{const open=$('sidebar').classList.toggle('mobile-open');$('mobileMenuBtn').setAttribute('aria-expanded',String(open))};try{for(const key of Object.keys(localStorage)){if(key==='aether-ops-v1'||key.startsWith('aether-ops-v1:'))localStorage.removeItem(key)}}catch{}});
