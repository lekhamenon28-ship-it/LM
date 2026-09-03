const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const modal = $('#modal');
const toast = message => { const el = $('#toast'); el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2600); };
const openModal = html => { $('#modal-content').innerHTML = `<div class="modal-body">${html}</div>`; modal.showModal(); };

$$('.nav-trigger').forEach(button => button.addEventListener('click', event => {
  event.stopPropagation();
  const target = $(`#${button.dataset.menu}-menu`);
  $$('.mega-menu').filter(menu => menu !== target).forEach(menu => menu.classList.remove('show'));
  target.classList.toggle('show');
}));
document.addEventListener('click', () => $$('.mega-menu').forEach(menu => menu.classList.remove('show')));
$$('.mega-menu').forEach(menu => menu.addEventListener('click', event => event.stopPropagation()));

$('.menu-toggle').addEventListener('click', event => {
  const open = $('#primary-nav').classList.toggle('show');
  event.currentTarget.setAttribute('aria-expanded', open);
});
$$('#primary-nav a, #primary-nav button').forEach(item => item.addEventListener('click', () => {
  if (window.innerWidth <= 900) { $('#primary-nav').classList.remove('show'); $('.menu-toggle').setAttribute('aria-expanded', 'false'); }
}));

$$('[data-scroll]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.scroll}`).scrollIntoView({ behavior: 'smooth' })));
$('#signin-button').addEventListener('click', () => openModal('<span class="eyebrow">MEMBER ACCESS</span><h2>Welcome back</h2><p>Sign in to manage your benefits, claims, care, and digital ID card.</p><form id="signin-form"><label>Member ID<input required autocomplete="username" placeholder="Enter your member ID"></label><label>Password<input required type="password" autocomplete="current-password" placeholder="Enter your password"></label><button class="button primary">Sign in securely</button></form><button class="text-link" data-demo-action="register">Create an account</button>'));

const showCard = () => openModal('<span class="eyebrow">DIGITAL ID CARD</span><h2>Sign in to view your card</h2><p>Your member ID card is available anytime through your secure Aurevia account.</p><button class="button primary" data-demo-action="signin">Continue to sign in</button>');
$('#id-card-action').addEventListener('click', showCard);
$$('[data-plan]').forEach(button => button.addEventListener('click', () => openModal(`<span class="eyebrow">PLAN EXPLORER</span><h2>${button.dataset.plan} plans</h2><p>Tell us where you live to see available plan options and estimated costs.</p><form class="zip-form"><label>ZIP code<input required inputmode="numeric" maxlength="5" pattern="[0-9]{5}" placeholder="5-digit ZIP code"></label><button class="button primary">See plan options</button></form>`)));
$$('[data-resource]').forEach(button => button.addEventListener('click', () => openModal(`<span class="eyebrow">GUIDE</span><h2>${button.dataset.resource}</h2><p>This short guide breaks down the essentials into clear, practical next steps.</p><button class="button primary" data-demo-action="guide">Open the guide</button>`)));
$('#wellness-button').addEventListener('click', () => openModal('<span class="eyebrow">HEALTH & WELLNESS</span><h2>Build healthier momentum</h2><p>Explore preventive care checklists, movement goals, nutrition tips, and member rewards.</p><button class="button primary" data-demo-action="wellness">Browse wellness topics</button>'));

$('#care-form').addEventListener('submit', event => {
  event.preventDefault();
  const query = $('#care-query').value.trim() || 'Primary care';
  const zip = $('#zip').value.trim();
  if (zip && !/^\d{5}$/.test(zip)) { toast('Please enter a valid 5-digit ZIP code.'); return; }
  openModal(`<span class="eyebrow">CARE NEAR ${zip || 'YOU'}</span><h2>${query} results</h2><p>Sample in-network results are shown for this demonstration.</p><div class="result-list"><div><b>Dr. Priya Raman</b><small>Primary care · 1.2 miles · Next visit today</small></div><div><b>Willow Creek Medical Group</b><small>Multi-specialty clinic · 2.5 miles · Accepting patients</small></div><div><b>Aurevia Virtual Care</b><small>Video visit · Available in about 15 minutes</small></div></div>`);
});

$('.search-button').addEventListener('click', () => openModal('<span class="eyebrow">SEARCH AUREVIA</span><h2>How can we help?</h2><form id="site-search"><label>Search<input autofocus required placeholder="Plans, benefits, doctors..."></label><button class="button primary">Search</button></form>'));
$('.modal-close').addEventListener('click', () => modal.close());
modal.addEventListener('click', event => { if (event.target === modal) modal.close(); });
$('#modal-content').addEventListener('submit', event => { event.preventDefault(); modal.close(); toast('Thanks! This demo action was completed.'); });
$('#modal-content').addEventListener('click', event => { if (!event.target.dataset.demoAction) return; modal.close(); if (event.target.dataset.demoAction === 'signin') $('#signin-button').click(); else toast('This feature is ready to connect to your live member experience.'); });
$$('[data-footer-action]').forEach(button => button.addEventListener('click', () => button.dataset.footerAction === 'card' ? showCard() : $('#signin-button').click()));
