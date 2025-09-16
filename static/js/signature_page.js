// static/js/signature_page.js
(() => {
  const DEBUG = false;

  const canvas = document.getElementById('signature-canvas');
  const slot   = document.getElementById('sign-slot');
  const errBox = document.getElementById('sign-error');

  if (!canvas || !slot) {
    console.error('[signature] Canvas ou slot não encontrado.');
    return;
  }

  // --- Garantias de interação ---
  // Se algum ancestral estiver com pointer-events:none, habilita:
  (function fixPointerAncestry(el) {
    let n = el;
    while (n) {
      const cs = getComputedStyle(n);
      if (cs.pointerEvents === 'none') n.style.pointerEvents = 'auto';
      n = n.parentElement;
    }
  })(canvas);

  canvas.style.pointerEvents = 'auto';
  canvas.style.touchAction   = 'none';
  canvas.style.zIndex        = '5';

  const ctx = canvas.getContext('2d');

  function sizeCanvas() {
    // A largura visual = largura de exibição (CSS).
    // A altura visual fixa em ~156px (definida no CSS inline do template).
    const DPR  = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width  = Math.max(1, Math.round(rect.width  * DPR));
    canvas.height = Math.max(1, Math.round(rect.height * DPR));

    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.lineWidth   = 4;
    ctx.lineJoin    = 'round';
    ctx.lineCap     = 'round';
    ctx.strokeStyle = '#0d6efd';

    if (DEBUG) console.log('[signature] resized', { w: canvas.width, h: canvas.height, DPR });
  }

  sizeCanvas();
  window.addEventListener('resize', sizeCanvas);

  // --- util de posição ---
  function posFromEvent(e) {
    const r = canvas.getBoundingClientRect();
    const x = ('touches' in e && e.touches[0]) ? e.touches[0].clientX : e.clientX;
    const y = ('touches' in e && e.touches[0]) ? e.touches[0].clientY : e.clientY;
    return { x: x - r.left, y: y - r.top };
  }

  function isInsideSlot(p) {
    const cr = canvas.getBoundingClientRect();
    const sr = slot.getBoundingClientRect();
    const x0 = sr.left - cr.left;
    const y0 = sr.top  - cr.top;
    return p.x >= x0 && p.x <= x0 + sr.width && p.y >= y0 && p.y <= y0 + sr.height;
  }

  // --- desenho ---
  let drawing = false;
  let hasDrawn = false;

  function down(e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const p = posFromEvent(e);
    if (!isInsideSlot(p)) {
      if (DEBUG) console.log('[signature] down fora do slot', p);
      return;
    }
    drawing = true; hasDrawn = true;
    ctx.beginPath(); ctx.moveTo(p.x, p.y);
    e.preventDefault();
    if (DEBUG) console.log('[signature] down', p);
  }

  function move(e) {
    if (!drawing) return;
    const p = posFromEvent(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    e.preventDefault();
  }

  function up() { drawing = false; }

  canvas.addEventListener('pointerdown', down, { passive:false });
  canvas.addEventListener('pointermove',  move, { passive:false });
  window.addEventListener('pointerup',    up,   { passive:true  });

  // --- limpar / enviar ---
  const err = (msg) => {
    errBox.textContent = msg;
    errBox.classList.remove('d-none');
    setTimeout(() => errBox.classList.add('d-none'), 4000);
  };

  const btnClear = document.getElementById('clear-signature');
  const btnSave  = document.getElementById('save-signature');

  btnClear?.addEventListener('click', () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    hasDrawn = false;
  });

  btnSave?.addEventListener('click', () => {
    if (!hasDrawn) return err('Desenhe sua assinatura antes de confirmar.');

    const dataURL = canvas.toDataURL('image/png');
    // A rota e os parâmetros vêm do template do Flask:
    const url = document.location.pathname.replace(/\/sign$/, '/apply-signature');

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataURL })
    })
    .then(r => r.json())
    .then(res => {
      if (res && res.ok && res.redirect_url) {
        window.location.href = res.redirect_url;
      } else {
        err(res?.error || 'Falha ao aplicar assinatura.');
      }
    })
    .catch(e => err('Erro de rede: ' + e.message));
  });

  // --- “SOS” automático: se nada desenhar em 1s, reforça z-index/pointer-events ---
  setTimeout(() => {
    if (!hasDrawn) {
      canvas.style.zIndex      = '9999';
      canvas.style.pointerEvents = 'auto';
      if (DEBUG) console.log('[signature] SOS: reforçou z-index/pointer-events');
    }
  }, 1000);
})();
