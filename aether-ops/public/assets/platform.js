/* Public extension API. Features are trusted source modules loaded by the build. */
(() => {
  const features = new Map();
  const services = new Map();
  const hashRoute=location.hash.slice(1);
  const initialRoute=({garden:'agents',creator:'agent-builder'})[hashRoute]||hashRoute;
  let ready = false;
  const validId = id => typeof id === 'string' && /^[a-z][a-z0-9-]{1,63}$/.test(id);
  const snapshot = value => structuredClone(value);

  function featureStore(id) {
    return Object.freeze({
      get(fallback = {}) {
        return snapshot(state.features?.[id] ?? fallback);
      },
      set(value) {
        const encoded = JSON.stringify(value);
        if (encoded === undefined) throw new TypeError('Feature data must be JSON serializable.');
        state.features ??= {};
        state.features[id] = JSON.parse(encoded);
        persist();
        window.dispatchEvent(new CustomEvent('aether:featurechange', {detail: {id}}));
      }
    });
  }

  function mountFeature(feature) {
    const nav = document.querySelector('#sidebar > div');
    let navButton = nav.querySelector(`[data-tab="${feature.id}"]`);
    if (!navButton) {
      let group = document.getElementById('featureNavigation');
      if (!group) {
        group = document.createElement('div');
        group.id = 'featureNavigation';
        const heading = document.createElement('div');
        heading.className = 'pt-3 px-3 py-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 font-bold';
        heading.textContent = 'Workspace';
        group.append(heading);
        nav.append(group);
      }
      navButton = document.createElement('button');
      navButton.className = 'nav-item w-full flex items-center gap-3 px-3 py-3 rounded-lg text-sm font-medium transition';
      navButton.dataset.tab = feature.id;
      const icon = document.createElement('i');
      icon.dataset.lucide = feature.icon || 'puzzle';
      icon.className = 'w-4 h-4 text-blue-600';
      const label = document.createElement('span');
      label.textContent = feature.label;
      navButton.append(icon, label);
      group.append(navButton);
    }
    navButton.removeAttribute('onclick');
    navButton.addEventListener('click', () => switchTab(feature.id));

    const section = document.createElement('section');
    section.id = 'view-' + feature.id;
    section.className = 'tab-view hidden space-y-6';
    const header = document.createElement('header');
    header.className = 'workspace-heading';
    const title = document.createElement('h1');
    title.className = 'text-2xl font-semibold text-navy-950';
    title.textContent = feature.label;
    const description = document.createElement('p');
    description.className = 'text-sm text-slate-500 mt-2';
    description.textContent = feature.description || '';
    const headingContent=document.createElement('div');
    headingContent.append(title,description);
    header.append(headingContent);
    const content = document.createElement('div');
    content.dataset.featureContent = feature.id;
    section.append(header, content);
    document.getElementById('mainWorkspace').append(section);
    try {
      feature.mount(Object.freeze({
        root: content,
        store: featureStore(feature.id),
        services: Aether.services,
        navigate: Aether.navigate,
        notify: Aether.notify,
        getAgents: Aether.getAgents,
        getIncidents: Aether.getIncidents,
        onStateChange: Aether.onStateChange,
        audit: (action, detail) => audit(action, detail),
        escapeHtml
      }));
    } catch (error) {
      console.error('Feature failed to mount:', feature.id, error);
      content.textContent = 'This feature could not load. Your other app sections are still available.';
    }
    lucide.createIcons();
  }

  const Aether = Object.freeze({
    version: '1.0.0',
    registerFeature(feature) {
      if (!feature || !validId(feature.id) || typeof feature.label !== 'string' || !feature.label.trim() || typeof feature.mount !== 'function') {
        throw new TypeError('A feature needs an id, label, and mount function.');
      }
      if (features.has(feature.id) || document.getElementById('view-' + feature.id)) {
        throw new Error('Feature id already registered: ' + feature.id);
      }
      features.set(feature.id, Object.freeze({...feature}));
      if (ready) mountFeature(features.get(feature.id));
    },
    listFeatures: () => Array.from(features.values(), ({id, label, description}) => ({id, label, description})),
    navigate: id => switchTab(id),
    notify: (message, type = 'info') => showToast(message, type),
    getAgents: () => snapshot(window.Workspace?.get()?.agents||[]),
    getIncidents: () => snapshot(state.incidents),
    getPolicies: () => snapshot(state.policies),
    onStateChange(callback) {
      if (typeof callback !== 'function') throw new TypeError('Listener must be a function.');
      const listener = event => callback(event.detail);
      window.addEventListener('aether:statechange', listener);
      return () => window.removeEventListener('aether:statechange', listener);
    },
    services: Object.freeze({
      register(name, handler) {
        if (!validId(name.replaceAll('.', '-')) || typeof handler !== 'function') throw new TypeError('A service needs a valid name and handler.');
        if (services.has(name)) throw new Error('Service already registered: ' + name);
        services.set(name, handler);
      },
      async call(name, input, options = {}) {
        const handler = services.get(name);
        if (!handler) throw new Error('Service is not configured: ' + name);
        return handler(input, options);
      },
      has: name => services.has(name)
    })
  });
  window.Aether = Aether;
  function start() {
    ready = true;
    for (const feature of features.values()) mountFeature(feature);
    const order=['dashboard','agents','agent-builder','orchestrator','tools','knowledge-fabric','conversations','model-management','executions','observability','infrastructure','python-agents','credentials','personas','users'];
    const navigation=document.getElementById('featureNavigation');for(const id of order){const button=navigation?.querySelector(`[data-tab="${id}"]`);if(button)navigation.append(button)}
    if (features.has(initialRoute)) switchTab(initialRoute);
    else if (features.has('dashboard')) switchTab('dashboard');
  }
  if (document.readyState === 'loading') window.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
