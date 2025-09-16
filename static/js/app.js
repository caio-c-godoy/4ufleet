// static/js/app.js
(function () {
  function ensureModal() {
    let modalEl = document.getElementById('globalModal');
    if (!modalEl) {
      modalEl = document.createElement('div');
      modalEl.id = 'globalModal';
      modalEl.className = 'modal fade';
      modalEl.tabIndex = -1;
      modalEl.setAttribute('aria-hidden', 'true');
      modalEl.innerHTML = `
        <div class="modal-dialog modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="globalModalTitle">Modal</h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body" id="globalModalBody"></div>
          </div>
        </div>`;
      document.body.appendChild(modalEl);
    }
    return modalEl;
  }

  function initTooltips(ctx) {
    (ctx || document).querySelectorAll('[data-bs-toggle="tooltip"]').forEach((el) => {
      try { new bootstrap.Tooltip(el); } catch {}
    });
  }

  async function openModal(url, title) {
    const modalEl = ensureModal();
    const modalBody = modalEl.querySelector('#globalModalBody');
    const modalTitle = modalEl.querySelector('#globalModalTitle');
    if (title) modalTitle.textContent = title;

    const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl, { backdrop: 'static' });
    modalBody.innerHTML = '<div class="p-4 text-center">Carregando…</div>';
    bsModal.show();

    try {
      const res = await fetch(url, { headers: { 'X-Requested-With': 'fetch' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const html = await res.text();
      modalBody.innerHTML = html;
      initTooltips(modalBody);
    } catch (err) {
      modalBody.innerHTML = `<div class="p-4 text-danger">Falha ao carregar (${err.message})</div>`;
    }
  }

  // Intercepta QUALQUER submit dentro da #globalModal
  function bindModalFormSubmit() {
    const modalEl = ensureModal();
    modalEl.addEventListener('submit', async (ev) => {
      const form = ev.target.closest('form');
      if (!form) return;
      ev.preventDefault();

      const body = modalEl.querySelector('#globalModalBody');
      const submitBtn = form.querySelector('[type="submit"], button:not([type]), button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;

      try {
        const res = await fetch(form.getAttribute('action') || window.location.href, {
          method: (form.getAttribute('method') || 'POST').toUpperCase(),
          body: new FormData(form),
          headers: { 'X-Requested-With': 'fetch' },
        });

        const ctype = (res.headers.get('content-type') || '').toLowerCase();

        if (ctype.includes('application/json')) {
          const data = await res.json();
          if (data.ok) {
            const inst = bootstrap.Modal.getInstance(modalEl);
            if (inst) inst.hide();
            if (data.redirect) window.location.href = data.redirect;
            else window.location.reload();
            return;
          }
          if (data.html) {
            body.innerHTML = data.html;
            initTooltips(body);
            return;
          }
          body.innerHTML = `<div class="alert alert-danger m-0">Erro ao salvar.</div>`;
        } else {
          const html = await res.text();
          body.innerHTML = html;
          initTooltips(body);
        }
      } catch (err) {
        body.innerHTML = `<div class="alert alert-danger m-0">Falha no envio: ${err.message}</div>`;
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  // Delegação: qualquer elemento com data-modal-url abre a modal
  function initModalDelegation() {
    document.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-modal-url]');
      if (!btn) return;
      ev.preventDefault();
      openModal(btn.getAttribute('data-modal-url'), btn.getAttribute('data-modal-title') || 'Editar');
    });
  }

  window.addEventListener('DOMContentLoaded', () => {
    initTooltips(document);
    initModalDelegation();
    bindModalFormSubmit();
  });
})();
