const form=document.getElementById('loginForm');
const error=document.getElementById('loginError');
const submit=document.getElementById('loginSubmit');
document.getElementById('showPassword').onclick=()=>{const input=document.getElementById('loginPassword');const visible=input.type==='password';input.type=visible?'text':'password';const btn=document.getElementById('showPassword');btn.textContent=visible?'Hide':'Show';btn.setAttribute('aria-label',visible?'Hide password':'Show password')};
form.addEventListener('submit',async event=>{
 event.preventDefault();error.classList.add('hidden');submit.disabled=true;submit.textContent='Signing in…';
 try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('loginUser').value,password:document.getElementById('loginPassword').value})});const data=await response.json();if(!response.ok)throw Error(data.error||'Unable to sign in.');if(location.pathname==='/index.html')location.reload();else location.replace('/index.html'+(location.hash||'#dashboard'))}catch(e){error.textContent=e.message||'Unable to connect. Please try again.';error.classList.remove('hidden');submit.disabled=false;submit.textContent='Sign in'}
});
