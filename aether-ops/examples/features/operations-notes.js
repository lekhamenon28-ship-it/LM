/* Copy this file into app/features/ to enable Operations Notes. */
Aether.registerFeature({
  id: 'operations-notes',
  label: 'Operations Notes',
  icon: 'notebook-pen',
  description: 'Save notes alongside your agents and incidents.',
  mount({root, store, notify, audit}) {
    const form = document.createElement('form');
    form.className = 'bg-white border border-navy-100 rounded-xl p-5 shadow-card space-y-4';
    const label = document.createElement('label');
    label.htmlFor = 'operations-notes-text';
    label.textContent = 'Your notes';
    label.className = 'block text-sm font-semibold';
    const input = document.createElement('textarea');
    input.id = label.htmlFor;
    input.rows = 8;
    input.className = 'detail-input';
    input.value = store.get({text: ''}).text;
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'detail-button';
    save.textContent = 'Save notes';
    form.append(label, input, save);
    form.addEventListener('submit', event => {
      event.preventDefault();
      store.set({text: input.value, updatedAt: new Date().toISOString()});
      audit('Operations notes saved', 'Updated local workspace notes');
      notify('Notes saved.', 'success');
    });
    root.append(form);
  }
});
