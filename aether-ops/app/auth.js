(() => {
 const user=window.AETHER_USER;
 async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  const data=await response.json();
  if(response.status===401){location.replace('/login');throw Error('Your session has expired. Please sign in again.')}
  if(!response.ok)throw Error(data.error||'Unable to complete the request.');
  return data;
 }
 async function logout(){try{await api('/api/auth/logout',{method:'POST',body:'{}'});location.replace('/login')}catch(e){Aether.notify(e.message,'danger')}}
 function changePassword(){
  detail('Change your password',`<form id="changePasswordForm"><label for="currentPassword">Current password</label><input id="currentPassword" class="detail-input" type="password" autocomplete="current-password" maxlength="128" required><label for="newPassword">New password</label><input id="newPassword" class="detail-input" type="password" autocomplete="new-password" minlength="12" maxlength="128" required><label for="confirmPassword">Confirm new password</label><input id="confirmPassword" class="detail-input" type="password" autocomplete="new-password" minlength="12" maxlength="128" required><p id="passwordError" role="alert" class="text-rose-700"></p><button class="detail-button" type="submit">Update password</button></form>`);
  document.getElementById('changePasswordForm').onsubmit=async event=>{event.preventDefault();const error=document.getElementById('passwordError');const password=document.getElementById('newPassword').value;if(password!==document.getElementById('confirmPassword').value){error.textContent='New passwords do not match.';return}const submit=event.target.querySelector('button');submit.disabled=true;try{await api('/api/auth/password',{method:'POST',body:JSON.stringify({currentPassword:document.getElementById('currentPassword').value,password})});closeDetail();Aether.notify('Password updated.','success')}catch(e){error.textContent=e.message;submit.disabled=false}};
 }
 window.AppAuth=Object.freeze({user,api,logout,changePassword});
 window.addEventListener('DOMContentLoaded',()=>{
  if(!user)return;
  const host=document.createElement('div');host.className='mt-3 pt-3 border-t border-slate-200 text-xs';
  const identity=document.createElement('p');identity.className='text-navy-950 font-semibold mb-2';identity.textContent=user.username+' · '+(user.role==='admin'?'Administrator':'Operator');
  const password=document.createElement('button');password.className='text-blue-600 font-semibold mr-4';password.textContent='Change password';password.onclick=changePassword;
  const signout=document.createElement('button');signout.className='text-slate-600 font-semibold';signout.textContent='Sign out';signout.onclick=logout;
  host.append(identity,password,signout);document.querySelector('#sidebar > div:last-child').append(host);
  const oldLabel=Array.from(document.querySelectorAll('header *')).find(el=>el.children.length===0&&el.textContent.trim()==='Lead SRE Officer');if(oldLabel)oldLabel.textContent=user.username;
  const oldAuth=Array.from(document.querySelectorAll('header *')).find(el=>el.children.length===0&&el.textContent.includes('auth: okta_sso'));if(oldAuth)oldAuth.textContent=user.role==='admin'?'Administrator':'Operator';
 });
})();
