/* Shared presentation helpers. All operational data is loaded from authenticated APIs. */
const $=id=>document.getElementById(id);
const agentGardenData=[];
function escapeHtml(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function showToast(message,type='info'){const item=document.createElement('div');item.className='app-toast '+type;item.textContent=String(message);$('toastContainer').append(item);setTimeout(()=>item.remove(),6000)}
function switchTab(id){const target=$('view-'+id);if(!target)return;document.querySelectorAll('.tab-view').forEach(x=>x.classList.toggle('hidden',x!==target));document.querySelectorAll('[data-tab]').forEach(x=>{x.classList.toggle('active',x.dataset.tab===id);x.setAttribute('aria-current',x.dataset.tab===id?'page':'false')});if(location.hash!=='#'+id)history.pushState(null,'','#'+id);$('sidebar').classList.remove('mobile-open');$('mobileMenuBtn').setAttribute('aria-expanded','false');window.scrollTo(0,0)}
