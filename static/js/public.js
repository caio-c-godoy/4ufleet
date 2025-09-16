// static/js/public.js
// ============================================================
//  Público (sem sidebar) – utilidades e UI
//  - Autocomplete de aeroportos dos EUA (IATA) via <datalist>
//  - Abertura de modal de reserva e envio do formulário
//  - Pequenos helpers de UX
//  - Assinatura de contrato em canvas (se a página tiver #signature-canvas)
// ============================================================
(function () {
  "use strict";

  // ------------------------------------------------------------
  // Utils
  // ------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function setDisabled(el, disabled) {
    if (!el) return;
    el.disabled = !!disabled;
    if (disabled) el.setAttribute("aria-busy", "true");
    else el.removeAttribute("aria-busy");
  }

  function spinnerHTML(label) {
    return `
      <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${label || "Carregando"}
    `;
  }

  // ------------------------------------------------------------
  // 1) AUTOCOMPLETE DE AEROPORTOS (EUA, IATA)
  // ------------------------------------------------------------
  async function loadUSAirportsDatalist() {
    const datalist = document.getElementById("us-airports");
    if (!datalist) return;

    const CACHE_KEY = "us_airports_options_v1";
    try {
      const cached = sessionStorage.getItem(CACHE_KEY);
      if (cached) {
        datalist.innerHTML = cached;
        return;
      }
    } catch (_) {}

    let airports = [];

    // Plano A: backend
    try {
      if (window.PUBLIC_AIRPORTS_URL) {
        const r = await fetch(window.PUBLIC_AIRPORTS_URL, { cache: "force-cache" });
        if (r.ok) {
          const data = await r.json();
          const items = (data && data.items) || [];
          if (items.length) {
            const frag = document.createDocumentFragment();
            for (const s of items) {
              const opt = document.createElement("option");
              opt.value = s;
              frag.appendChild(opt);
            }
            datalist.innerHTML = "";
            datalist.appendChild(frag);
            try { sessionStorage.setItem(CACHE_KEY, datalist.innerHTML); } catch (_) {}
            return;
          }
        }
      }
    } catch (_) {}

    // Plano B: arquivo local
    try {
      const r = await fetch("/static/data/airports_us_iata.json", { cache: "force-cache" });
      if (r.ok) airports = await r.json();
    } catch (_) {}

    // Plano C: OpenFlights remoto (fallback)
    if (!airports.length) {
      try {
        const url = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat";
        const resp = await fetch(url, { cache: "force-cache" });
        if (!resp.ok) throw new Error("Falha ao baixar base de aeroportos.");
        const text = await resp.text();

        function parseCSV(line) {
          const out = [];
          let cur = "", inQ = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
              if (inQ && line[i + 1] === '"') { cur += '"'; i++; }
              else { inQ = !inQ; }
            } else if (ch === "," && !inQ) {
              out.push(cur); cur = "";
            } else cur += ch;
          }
          out.push(cur);
          return out;
        }

        const lines = text.split(/\r?\n/);
        for (const line of lines) {
          if (!line.trim()) continue;
          const cols = parseCSV(line);
          const country = cols[3];
          const iata    = cols[4];
          const name    = cols[1];
          const city    = cols[2];
          if (country === "United States" && iata && iata !== "\\N" && iata.length === 3) {
            airports.push({ name, city, state: "", iata });
          }
        }
      } catch (e) {
        console.error("Fallback aeroportos falhou:", e);
      }
    }

    const seen = new Set();
    const dedup = [];
    for (const a of airports) {
      const key = (a.iata || "").toUpperCase();
      if (!key || seen.has(key)) continue;
      seen.add(key); dedup.push(a);
    }
    dedup.sort((a, b) => a.name.localeCompare(b.name));

    const frag = document.createDocumentFragment();
    for (const a of dedup) {
      const opt = document.createElement("option");
      const city = a.city ? ` — ${a.city}` : "";
      opt.value = `${a.name} (${a.iata.toUpperCase()})${city}`;
      frag.appendChild(opt);
    }
    datalist.innerHTML = "";
    datalist.appendChild(frag);
    try { sessionStorage.setItem(CACHE_KEY, datalist.innerHTML); } catch (_) {}
  }

  // ------------------------------------------------------------
  // 2) MODAL DE RESERVA
  // ------------------------------------------------------------
  async function safeJsonOrText(resp) {
    try { return JSON.stringify(await resp.json()); }
    catch (_) { try { return await resp.text(); } catch (_) { return String(resp.status || "erro"); } }
  }

  function setupReserveModal() {
    document.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-reserve-url]");
      if (!btn) return;

      ev.preventDefault();
      const url = btn.getAttribute("data-reserve-url");
      if (!url) return;

      const modalEl = document.getElementById("reserveModal");
      const bodyEl  = document.getElementById("reserveModalBody");
      if (!modalEl || !bodyEl) return;

      try {
        setDisabled(btn, true);
        const res  = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
        const html = await res.text();
        bodyEl.innerHTML = html;

        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        const form = bodyEl.querySelector("form");
        if (form) {
          const submitBtn = form.querySelector('button[type="submit"], .btn-primary');
          form.addEventListener("submit", async (e) => {
            e.preventDefault();
            try {
              if (submitBtn) {
                submitBtn.dataset._label = submitBtn.innerHTML;
                submitBtn.innerHTML = spinnerHTML("Enviando");
              }
              setDisabled(submitBtn, true);

              const fd   = new FormData(form);
              const resp = await fetch(form.action, { method: "POST", body: fd });
              if (!resp.ok) {
                const msg = await safeJsonOrText(resp);
                alert("Não foi possível registrar sua reserva.\n\n" + msg);
                return;
              }
              const data = await resp.json();
              if (data && data.redirect) window.location.href = data.redirect;
              else { alert("Reserva registrada, sem redirecionamento."); modal.hide(); }
            } catch (err) {
              console.error(err); alert("Falha ao enviar reserva. Tente novamente.");
            } finally {
              if (submitBtn) {
                setDisabled(submitBtn, false);
                submitBtn.innerHTML = submitBtn.dataset._label || "Enviar";
                delete submitBtn.dataset._label;
              }
            }
          }, { once: true });
        }
      } catch (err) {
        console.error(err); alert("Falha ao carregar o formulário.");
      } finally {
        setDisabled(btn, false);
      }
    });
  }

  // ------------------------------------------------------------
  // 3) FORM DE FILTROS (results)
  // ------------------------------------------------------------
  function setupResultsFiltersSync() {
    const form = document.getElementById("filtersForm");
    if (!form) return;
    const syncNames = ["pickup_airport", "dropoff_airport"];
    syncNames.forEach((name) => {
      const visible = form.querySelector(`input[name="${name}"]:not([type="hidden"])`);
      const hidden  = form.querySelector(`input[type="hidden"][name="${name}"]`);
      if (visible && hidden) visible.addEventListener("input", () => { hidden.value = visible.value; });
    });
  }

  // ------------------------------------------------------------
  // 4) Assinatura em canvas (ativa só se houver #signature-canvas)
  // ------------------------------------------------------------
  function initSignature() {
    const canvas  = document.getElementById('signature-canvas');
    if (!canvas) return;

    const slot     = document.getElementById('sign-slot');
    const errBox   = document.getElementById('sign-error');
    const clearBtn = document.getElementById('clear-signature');
    const saveBtn  = document.getElementById('save-signature');
    const ctx      = canvas.getContext('2d');

    // interação garantida
    canvas.style.pointerEvents = 'auto';
    canvas.style.touchAction   = 'none';
    canvas.style.userSelect    = 'none';
    canvas.style.webkitUserSelect = 'none';

    function size() {
      const dpr = window.devicePixelRatio || 1;
      const r = canvas.getBoundingClientRect();
      canvas.width  = Math.max(1, Math.round(r.width  * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineWidth = 4; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.strokeStyle = '#0d6efd';
    }
    size();
    window.addEventListener('resize', size);

    const P = e => {
      const r = canvas.getBoundingClientRect();
      const p = (e.touches && e.touches[0]) ? e.touches[0] : e;
      return { x: p.clientX - r.left, y: p.clientY - r.top };
    };

    const insideSlot = pt => {
      if (!slot) return true;
      const rc = canvas.getBoundingClientRect();
      const rs = slot.getBoundingClientRect();
      const sx = rs.left - rc.left;
      const sy = rs.top  - rc.top;
      return pt.x >= sx && pt.x <= sx + rs.width && pt.y >= sy && pt.y <= sy + rs.height;
    };

    const showErr = (msg) => {
      if (!errBox) { alert(msg); return; }
      errBox.textContent = msg;
      errBox.classList.remove('d-none');
      setTimeout(() => errBox.classList.add('d-none'), 3500);
    };

    let drawing = false, drew = false;

    function down(e) {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const p = P(e);
      if (!insideSlot(p)) return;
      drawing = true; drew = true;
      ctx.beginPath(); ctx.moveTo(p.x, p.y);
      e.preventDefault();
    }
    function move(e) {
      if (!drawing) return;
      const p = P(e);
      ctx.lineTo(p.x, p.y); ctx.stroke();
      e.preventDefault();
    }
    function up() { drawing = false; }

    canvas.addEventListener('pointerdown', down, { passive:false });
    canvas.addEventListener('pointermove',  move, { passive:false });
    window.addEventListener('pointerup',    up,   { passive:true  });

    // fallback mouse/touch adicionais (alguns Androids velhos)
    canvas.addEventListener('mousedown', down, { passive:false });
    canvas.addEventListener('mousemove', move, { passive:false });
    window.addEventListener('mouseup',   up,   { passive:true  });
    canvas.addEventListener('touchstart',down, { passive:false });
    canvas.addEventListener('touchmove', move, { passive:false });
    canvas.addEventListener('touchend',  up,   { passive:true  });

    clearBtn?.addEventListener('click', () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      drew = false;
    });

    saveBtn?.addEventListener('click', async (e) => {
      e.preventDefault(); e.stopPropagation();

      if (!drew) return showErr('Desenhe sua assinatura antes de confirmar.');

      const postUrl = saveBtn.dataset.postUrl
        || location.pathname.replace(/\/sign$/, '/apply-signature');

      const old = saveBtn.innerHTML;
      saveBtn.disabled = true;
      saveBtn.innerHTML = spinnerHTML('Salvando');

      try {
        const img = canvas.toDataURL('image/png');
        const res = await fetch(postUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: img })
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok || !json.ok) throw new Error(json.error || `Falha ao salvar (HTTP ${res.status})`);

        if (json.redirect_url) window.location.href = json.redirect_url;
      } catch (err) {
        showErr(err.message || 'Erro ao salvar assinatura.');
      } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = old;
      }
    });

    console.log('[signature] pronto.');
  }

  // ------------------------------------------------------------
  // Init
  // ------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    loadUSAirportsDatalist();
    setupReserveModal();
    setupResultsFiltersSync();
    initSignature(); // <<< ativa a assinatura se existir o canvas
  });
})();

// Popovers
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-bs-toggle="popover"]').forEach(el => {
    new bootstrap.Popover(el, { container: 'body', trigger: 'hover focus' });
  });
});
