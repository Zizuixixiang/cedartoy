(() => {
  'use strict';

  const labels = {
    'gemini-3.5-flash': ['Flash', 'Gemini 3.5 Flash'],
    'gemini-3.1-pro-preview': ['Pro', 'Gemini 3.1 Pro'],
  };
  let scheduled = false;

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function selectedModel() {
    const select = document.querySelector('#providerList select');
    return labels[select?.value] ? select.value : 'gemini-3.5-flash';
  }

  function render() {
    scheduled = false;
    const orb = document.getElementById('providerOrb');
    if (orb) orb.title = '选择本站塔罗模型';
    const current = selectedModel();
    setText(document.getElementById('providerLabel'), `本站 ${labels[current][0]}`);

    const list = document.getElementById('providerList');
    const item = list?.querySelector('.provider-item');
    const select = item?.querySelector('select');
    if (!item || !select) return;

    item.querySelectorAll('input, .p-remove').forEach(node => node.remove());
    select.classList.add('managed-model-source');
    select.setAttribute('aria-hidden', 'true');
    for (const option of [...select.options]) {
      if (labels[option.value]) setText(option, labels[option.value][1]);
      else option.remove();
    }

    let choices = item.querySelector('.managed-model-options');
    if (!choices) {
      choices = document.createElement('div');
      choices.className = 'managed-model-options';
      choices.setAttribute('role', 'radiogroup');
      choices.setAttribute('aria-label', '解读模型');
      for (const [model, names] of Object.entries(labels)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'managed-model-option';
        button.dataset.model = model;
        button.setAttribute('role', 'radio');
        const name = document.createElement('span');
        name.textContent = names[0];
        const detail = document.createElement('small');
        detail.textContent = names[1];
        button.append(name, detail);
        button.addEventListener('click', event => {
          event.stopPropagation();
          select.value = model;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          queueRender();
        });
        choices.appendChild(button);
      }
      item.appendChild(choices);
    }
    for (const button of choices.querySelectorAll('.managed-model-option')) {
      const active = button.dataset.model === select.value;
      button.classList.toggle('selected', active);
      button.setAttribute('aria-checked', String(active));
    }
    setText(document.getElementById('providerLabel'), `本站 ${labels[selectedModel()][0]}`);
  }

  function queueRender() {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(render);
  }

  new MutationObserver(queueRender).observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });
  queueRender();
})();
