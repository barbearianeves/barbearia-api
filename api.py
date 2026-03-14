<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Área do Barbeiro</title>
<style>
  :root{
    --bg:#0b0d10; --card:#12161c; --line:#2a3240;
    --txt:#fff; --muted:#aeb6c2;

    --freeBg:#0f1319; --freeLine:#2a3240;
    --busyBg:#241216; --busyLine:#7a2a3a;
    --blockBg:#142017; --blockLine:#2bd66a;

    --barberBg:#111c2d; --barberLine:#4ea1ff;

    --btn:#ffffff; --btnTxt:#000;
    --danger:#ff5c5c;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:var(--bg);color:var(--txt);padding:16px}
  .wrap{max-width:1100px;margin:0 auto}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin:12px 0}
  h1{font-size:20px;margin:6px 0}
  small{color:var(--muted)}
  label{display:block;margin:10px 0 6px;font-weight:850;color:#d9deea;font-size:13px}
  input,select,textarea{width:100%;padding:12px;border-radius:12px;border:1px solid var(--line);background:#0f1319;color:#fff;outline:none}
  textarea{min-height:84px;resize:vertical}
  button{width:100%;padding:12px;border-radius:12px;border:0;cursor:pointer;font-weight:950;background:var(--btn);color:var(--btnTxt)}
  button.danger{background:var(--danger);color:#000}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
  @media(max-width:900px){ .row3{grid-template-columns:1fr} }
  @media(max-width:700px){ .row{grid-template-columns:1fr} }

  .status{margin-top:10px;color:var(--muted);font-size:13px}

  .slots{
    display:grid; grid-template-columns:repeat(4,1fr);
    gap:10px; margin-top:12px; max-height:72vh; overflow:auto; padding-right:6px;
  }
  @media(max-width:980px){ .slots{grid-template-columns:repeat(2,1fr)} }
  @media(max-width:520px){ .slots{grid-template-columns:1fr} }

  .slot{
    border:1px solid var(--freeLine); background:var(--freeBg);
    border-radius:14px; padding:12px; cursor:pointer; user-select:none;
    display:flex; flex-direction:column; gap:6px;
  }
  .slot .t{font-weight:950}
  .slot .d{font-size:12px;color:var(--muted)}
  .slot.busy{background:var(--busyBg);border-color:var(--busyLine)}
  .slot.block{background:var(--blockBg);border-color:var(--blockLine)}
  .slot.barber-made{background:var(--barberBg);border-color:var(--barberLine)}
  .actions{display:flex;gap:10px;margin-top:6px;flex-wrap:wrap}
  .actions button{width:auto;padding:10px;border-radius:12px;font-size:12px}

  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px}
  .loginBox{width:100%;max-width:420px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px}
  .hide{display:none}

  .dbg{
    margin-top:8px; padding:10px; border:1px dashed var(--line);
    border-radius:12px; font-size:12px; color:var(--muted); white-space:pre-wrap;
  }

  .tabs{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap}
  .tabbtn{
    width:auto; padding:10px 12px; border-radius:14px; border:1px solid var(--line);
    background:#0f1319; color:#fff; font-weight:900; cursor:pointer;
  }
  .tabbtn.active{background:#fff;color:#000;border-color:#fff}

  .clientGrid{display:grid;grid-template-columns:340px 1fr;gap:12px}
  @media(max-width:900px){ .clientGrid{grid-template-columns:1fr} }

  .photos{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  @media(max-width:700px){ .photos{grid-template-columns:1fr} }
  .ph{border:1px solid var(--line);border-radius:14px;background:#0f1319;padding:10px}
  .ph img{width:100%;height:auto;border-radius:12px;border:1px solid var(--line);display:block}
  .ph .t{font-weight:950;margin-bottom:8px}
  .mini{font-size:12px;color:var(--muted)}

  .searchList{
    display:grid;
    gap:8px;
    max-height:65vh;
    overflow:auto;
    margin-top:10px;
  }
  .searchItem{
    border:1px solid var(--line);
    background:#0f1319;
    border-radius:12px;
    padding:10px;
    cursor:pointer;
  }
  .searchItem:hover{border-color:#fff}
  .searchItem .n{font-weight:900}
  .searchItem .m{font-size:12px;color:var(--muted);margin-top:4px}

  .bookBox{
    display:grid;
    grid-template-columns:1fr;
    gap:12px;
  }

  .pill{
    display:inline-block;
    font-size:11px;
    padding:4px 8px;
    border-radius:999px;
    border:1px solid var(--line);
    color:#fff;
    background:#0f1319;
  }
  .pill.barber{
    border-color:var(--barberLine);
    color:#d7ebff;
    background:#102238;
  }
</style>
</head>
<body>
<div class="wrap">

  <div id="app" class="hide">

    <div class="tabs">
      <button class="tabbtn active" id="tabMap" onclick="showTab('map')">🗓️ Mapa</button>
      <button class="tabbtn" id="tabPhotos" onclick="showTab('photos')">📸 Fotos</button>
      <button class="tabbtn" id="tabBooking" onclick="showTab('booking')">➕ Marcar</button>
    </div>

    <div id="viewMap">
      <div class="card">
        <h1>Mapa do Barbeiro</h1>
        <div class="row" style="margin-top:10px">
          <div>
            <label>Barbeiro</label>
            <select id="barber">
              <option value="Pedro">Pedro</option>
              <option value="Joao">Joao</option>
            </select>
          </div>
          <div>
            <label>Data</label>
            <input type="date" id="date">
          </div>
        </div>

        <div class="status" id="status">—</div>
        <div class="slots" id="slots"></div>
      </div>
    </div>

    <div id="viewPhotos" class="hide">
      <div class="card">
        <h1>Fotos dos Clientes</h1>
        <small>Pesquisa simples por nome ou telefone. Ao clicar num cliente, as fotos aparecem à direita.</small>

        <div class="clientGrid" style="margin-top:12px">
          <div class="card" style="margin:0">
            <label>Pesquisar cliente</label>
            <input id="photoSearch" placeholder="Nome ou telefone" oninput="filterPhotoClients()" />
            <div class="status" id="clientsStatus">—</div>
            <div id="photoResults" class="searchList"></div>
          </div>

          <div class="card" style="margin:0">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap">
              <div>
                <div style="font-weight:950;font-size:16px" id="selName">Seleciona um cliente</div>
                <div class="mini" id="selMeta">—</div>
              </div>
              <div style="display:flex;gap:10px;flex-wrap:wrap">
                <button style="width:auto" onclick="newClient()">➕ Novo</button>
                <button style="width:auto" onclick="saveClient()">💾 Guardar</button>
                <button style="width:auto" class="danger" onclick="deleteClientById((c_id.value||'').trim())">🗑️ Apagar</button>
              </div>
            </div>

            <div class="row" style="margin-top:10px">
              <div>
                <label>ID</label>
                <input id="c_id" placeholder="(auto)" />
              </div>
              <div>
                <label>Nome</label>
                <input id="c_name" placeholder="ex: Rui Silva" />
              </div>
            </div>

            <div class="row">
              <div>
                <label>Telefone</label>
                <input id="c_phone" placeholder="9xxxxxxxx" />
              </div>
              <div>
                <label>Email</label>
                <input id="c_email" placeholder="email@..." />
              </div>
            </div>

            <label>Notas</label>
            <textarea id="c_notes" placeholder="Notas do cliente..."></textarea>

            <div class="photos" style="margin-top:12px">
              <div class="ph">
                <div class="t">📷 Antes</div>
                <img id="imgBefore" src="" alt="Antes" style="display:none" />
                <div class="mini" id="beforeInfo">—</div>
                <div style="height:10px"></div>
                <input id="fileBefore" type="file" accept="image/*" />
                <div style="height:10px"></div>
                <button onclick="uploadPhoto('before')">⬆️ Enviar Antes</button>
              </div>

              <div class="ph">
                <div class="t">📷 Depois</div>
                <img id="imgAfter" src="" alt="Depois" style="display:none" />
                <div class="mini" id="afterInfo">—</div>
                <div style="height:10px"></div>
                <input id="fileAfter" type="file" accept="image/*" />
                <div style="height:10px"></div>
                <button onclick="uploadPhoto('after')">⬆️ Enviar Depois</button>
              </div>
            </div>

            <div class="status" id="clientSaveStatus">—</div>
          </div>
        </div>
      </div>
    </div>

    <div id="viewBooking" class="hide">
      <div class="card">
        <h1>Nova Marcação</h1>
        <small>Podes marcar manualmente a partir daqui. Esta marcação pode cair em cima de horários já ocupados.</small>

        <div class="bookBox" style="margin-top:12px">
          <div class="card" style="margin:0">
            <label>Pesquisar cliente existente</label>
            <input id="bookSearch" placeholder="Nome ou telefone" oninput="filterBookingClients()" />
            <div class="status" id="bookSearchStatus">—</div>
            <div id="bookResults" class="searchList" style="max-height:220px"></div>
          </div>

          <div class="card" style="margin:0">
            <div style="font-weight:950;font-size:16px" id="bookSelTitle">Dados da marcação</div>
            <div class="mini" id="bookSelMeta">Podes escolher cliente existente ou preencher manualmente.</div>

            <div class="row" style="margin-top:10px">
              <div>
                <label>Client ID</label>
                <input id="b_client_id" placeholder="(auto se escolheres cliente)" />
              </div>
              <div>
                <label>Nome</label>
                <input id="b_name" placeholder="Nome do cliente" />
              </div>
            </div>

            <div class="row">
              <div>
                <label>Telefone</label>
                <input id="b_phone" placeholder="9xxxxxxxx" />
              </div>
              <div>
                <label>Email</label>
                <input id="b_email" placeholder="email@..." />
              </div>
            </div>

            <div class="row3">
              <div>
                <label>Barbeiro</label>
                <select id="b_barber">
                  <option value="Pedro">Pedro</option>
                  <option value="Joao">Joao</option>
                </select>
              </div>
              <div>
                <label>Data</label>
                <input type="date" id="b_date" />
              </div>
              <div>
                <label>Hora</label>
                <input type="time" id="b_time" />
              </div>
            </div>

            <div class="row3">
              <div>
                <label>Serviço</label>
                <input id="b_service" placeholder="Ex: Corte" />
              </div>
              <div>
                <label>Duração (min)</label>
                <select id="b_dur">
                  <option value="15">15</option>
                  <option value="30">30</option>
                  <option value="45" selected>45</option>
                  <option value="60">60</option>
                  <option value="75">75</option>
                  <option value="90">90</option>
                  <option value="120">120</option>
                </select>
              </div>
              <div>
                <label>Origem</label>
                <input id="b_status_preview" value="Marcação do barbeiro" disabled />
              </div>
            </div>

            <label>Notas</label>
            <textarea id="b_notes" placeholder="Notas da marcação..."></textarea>

            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
              <button style="width:auto" onclick="clearBookingForm()">Limpar</button>
              <button style="width:auto" onclick="createBookingFromAdmin()">✅ Criar Marcação</button>
            </div>

            <div class="status" id="bookCreateStatus">—</div>
          </div>
        </div>
      </div>
    </div>

  </div>

  <div id="loginOverlay" class="overlay">
    <div class="loginBox">
      <h1>Entrar</h1>
      <small>Senha do barbeiro (ADMIN_TOKEN).</small>
      <label>Senha</label>
      <input type="password" id="token" placeholder="ex: barbeiro-2026">
      <div style="height:10px"></div>
      <button onclick="doLogin()">Entrar</button>
      <div class="status" id="loginStatus">—</div>
    </div>
  </div>
</div>

<script>
const API_BASE = "https://barbearia-api-ki25.onrender.com";
const STEP_MIN = 45;
const DEBUG_ON_MISSING = true;
const TEST_PHONE_FALLBACK = "916634329";

function getScheduleForDate(dateISO){
  const [y,m,d] = dateISO.split("-").map(Number);
  const dt = new Date(y, m-1, d);
  const dow = dt.getDay();

  if(dow === 0 || dow === 1) return { closed:true, segments: [] };

  if(dow === 6){
    return { closed:false, segments: [{ start:"09:00", end:"14:00" }] };
  }

  return {
    closed:false,
    segments: [
      { start:"10:00", end:"12:30" },
      { start:"14:00", end:"20:00" }
    ]
  };
}

const appEl = document.getElementById("app");
const overlayEl = document.getElementById("loginOverlay");
const tokenEl = document.getElementById("token");
const loginStatusEl = document.getElementById("loginStatus");

const barberEl = document.getElementById("barber");
const dateEl = document.getElementById("date");
const statusEl = document.getElementById("status");
const slotsEl = document.getElementById("slots");

let currentItemsById = new Map();
let clientsCache = [];
let selectedClient = null;
let bookingSelectedClient = null;

function token(){ return (localStorage.getItem("admin_token") || "").trim(); }
function pad2(n){ return (n<10?"0":"")+n; }
function toMin(hhmm){ const [h,m]=hhmm.split(":").map(Number); return h*60+m; }
function toHHMM(min){ const h=Math.floor(min/60), m=min%60; return pad2(h)+":"+pad2(m); }
function overlaps(s1,e1,s2,e2){ return (s1<e2) && (s2<e1); }
function todayISO(){ const d=new Date(); return d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate()); }
function escapeHtml(s){
  return (s||"").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}
function normPhone(p){ return (p||"").replace(/\D+/g,"").replace(/^351/,""); }
function normEmail(e){ return (e||"").trim().toLowerCase(); }
function normName(n){ return (n||"").trim().toLowerCase().replace(/\s+/g," "); }

function isBarberMadeBooking(x){
  const createdBy = String(x?.created_by || "").trim().toLowerCase();
  const createdVia = String(x?.created_via || "").trim().toLowerCase();
  return createdBy === "barber_admin" || createdVia === "barber_page";
}

async function apiGet(url){
  const res = await fetch(url, { headers: { "X-Admin-Token": token() }});
  const data = await res.json().catch(()=> ({}));
  if(!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
async function apiPost(url, body){
  const res = await fetch(url, {
    method:"POST",
    headers:{ "Content-Type":"application/json", "X-Admin-Token": token() },
    body: JSON.stringify(body || {})
  });
  const data = await res.json().catch(()=> ({}));
  if(!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
async function apiPostForm(url, formData){
  const res = await fetch(url, {
    method:"POST",
    headers:{ "X-Admin-Token": token() },
    body: formData
  });
  const data = await res.json().catch(()=> ({}));
  if(!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
async function apiDelete(url){
  const res = await fetch(url, { method:"DELETE", headers:{ "X-Admin-Token": token() }});
  const data = await res.json().catch(()=> ({}));
  if(!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function isBlock(x){
  return (x.status === "Bloqueado") || (x.service === "INDISPONIVEL");
}
function isHiddenFromMap(x){
  return (x.status === "Cancelado") || (x.status === "Desbloqueado");
}

function showTab(which){
  const map = document.getElementById("viewMap");
  const photos = document.getElementById("viewPhotos");
  const booking = document.getElementById("viewBooking");

  const b1 = document.getElementById("tabMap");
  const b2 = document.getElementById("tabPhotos");
  const b3 = document.getElementById("tabBooking");

  map.classList.add("hide");
  photos.classList.add("hide");
  booking.classList.add("hide");

  b1.classList.remove("active");
  b2.classList.remove("active");
  b3.classList.remove("active");

  if(which === "photos"){
    photos.classList.remove("hide");
    b2.classList.add("active");
  }else if(which === "booking"){
    booking.classList.remove("hide");
    b3.classList.add("active");
  }else{
    map.classList.remove("hide");
    b1.classList.add("active");
  }
}

async function doLogin(){
  const t = tokenEl.value.trim();
  if(!t){ loginStatusEl.textContent="Escreve a senha."; return; }
  localStorage.setItem("admin_token", t);

  try{
    await apiGet(new URL(API_BASE + "/admin/bookings").toString());
    loginStatusEl.textContent="✅ OK";
    overlayEl.classList.add("hide");
    appEl.classList.remove("hide");
    if(!dateEl.value) dateEl.value = todayISO();
    if(!document.getElementById("b_date").value) document.getElementById("b_date").value = todayISO();
    await reloadMap();
    await reloadClients();
    filterPhotoClients();
    filterBookingClients();
  }catch(ex){
    localStorage.removeItem("admin_token");
    loginStatusEl.textContent="❌ Senha errada ou API offline.";
  }
}

async function fetchAdminDay(date, barber){
  const url = new URL(API_BASE + "/admin/bookings");
  url.searchParams.set("date", date);
  url.searchParams.set("barber", barber);
  const data = await apiGet(url.toString());
  return (data.items || []);
}

function buildSlots(items, schedule){
  if(schedule?.closed) return [];

  const filtered = items.filter(x => !isHiddenFromMap(x));
  const booked = filtered.map(x => {
    const s = toMin(x.time);
    const e = s + (parseInt(x.dur,10) || STEP_MIN);
    return { ...x, s, e };
  });

  const segments = schedule.segments || [];
  const slots = [];

  for(const seg of segments){
    const start = toMin(seg.start);
    const end   = toMin(seg.end);

    for(let t=start; t + STEP_MIN <= end; t += STEP_MIN){
      const s=t, e=t+STEP_MIN;

      let hit = null;
      for(const b of booked){
        if(overlaps(s,e,b.s,b.e)){ hit=b; break; }
      }
      slots.push({ time: toHHMM(t), hit });
    }
  }

  return slots;
}

function renderSlots(slots){
  slotsEl.innerHTML = "";

  if(!slots.length){
    slotsEl.innerHTML = `<div class="status">Sem horários (domingo fechado ou data inválida).</div>`;
    return;
  }

  for(const s of slots){
    const div = document.createElement("div");
    const hit = s.hit;
    const busy = !!hit && !isBlock(hit);
    const block = !!hit && isBlock(hit);
    const barberMade = !!hit && isBarberMadeBooking(hit);

    div.className = "slot" + (busy ? " busy" : "") + (block ? " block" : "") + (barberMade ? " barber-made" : "");

    if(busy){
      div.innerHTML = `
        <div class="t">${escapeHtml(s.time)} — ${barberMade ? "Marcação do Barbeiro" : "Ocupado"}</div>
        <div class="d">${escapeHtml(hit.name || hit.client || "")}</div>
        <div class="d">${escapeHtml(hit.service || "")}</div>
        ${barberMade ? `<div><span class="pill barber">feito pelo barbeiro</span></div>` : ``}
        <div class="actions">
          <button onclick="openOutlookEmail('${hit.id}')">📧 Outlook</button>
          <button onclick="openSMS('${hit.id}')">📱 SMS (iPhone)</button>
          <button onclick="openPhotosForBooking('${hit.id}')">📸 Fotos</button>
          <button onclick="prefillBookingFromSlot('${hit.id}')">➕ Marcar por cima</button>
          <button class="danger" onclick="cancelBooking('${hit.id}')">Cancelar</button>
        </div>
        <div class="dbg hide" id="dbg_${hit.id}"></div>
      `;
    } else if (block){
      div.innerHTML = `
        <div class="t">${escapeHtml(s.time)} — Indisponível</div>
        <div class="d">Bloqueado</div>
        <div class="actions">
          <button class="danger" onclick="unblock('${hit.id}')">Desbloquear</button>
          <button onclick="prefillBookingByTime('${s.time}')">➕ Marcar aqui</button>
        </div>
      `;
    } else {
      div.innerHTML = `
        <div class="t">${escapeHtml(s.time)} — Livre</div>
        <div class="d">Toque para bloquear</div>
        <div class="actions">
          <button onclick="prefillBookingByTime('${s.time}')">➕ Marcar aqui</button>
        </div>
      `;
      div.addEventListener("click", () => blockAt(s.time));
    }

    slotsEl.appendChild(div);
  }
}

async function reloadMap(){
  const d = dateEl.value;
  const b = barberEl.value;
  if(!d){ statusEl.textContent="Escolhe uma data."; return; }

  const schedule = getScheduleForDate(d);

  if(schedule.closed){
    slotsEl.innerHTML = "";
    statusEl.textContent = `Fechado • ${b} • ${d}`;
    return;
  }

  const segText = (schedule.segments || []).map(s => `${s.start}–${s.end}`).join(" • ");

  statusEl.textContent="A carregar…";
  try{
    const items = await fetchAdminDay(d,b);

    currentItemsById = new Map();
    for(const it of items){
      if(it && it.id) currentItemsById.set(it.id, it);
    }

    const slots = buildSlots(items, schedule);
    renderSlots(slots);

    statusEl.textContent = `OK • ${b} • ${d} • ${segText}`;
  }catch(ex){
    statusEl.textContent = "Erro: " + ex.message;
  }
}

async function blockAt(timeHHMM){
  const d = dateEl.value;
  const b = barberEl.value;
  try{
    await apiPost(API_BASE + "/admin/block", { date:d, time:timeHHMM, barber:b });
    await reloadMap();
  }catch(ex){
    alert("Erro a bloquear: " + ex.message);
  }
}

async function cancelBooking(id){
  if(!confirm("Cancelar esta marcação? (fica no histórico)")) return;
  try{
    await apiPost(API_BASE + "/admin/cancel/" + id, {});
    await reloadMap();
  }catch(ex){
    alert("Erro a cancelar: " + ex.message);
  }
}

async function unblock(id){
  if(!confirm("Desbloquear este horário? (fica no histórico)")) return;
  try{
    await apiPost(API_BASE + "/admin/unblock/" + id, {});
    await reloadMap();
  }catch(ex){
    alert("Erro a desbloquear: " + ex.message);
  }
}

async function getBookingSafe(id){
  let hit = currentItemsById.get(id) || null;
  if(hit) return hit;

  try{
    const data = await apiGet(API_BASE + "/admin/booking/" + id);
    if(data && data.item) return data.item;
    if(data && data.booking) return data.booking;
    if(data && data.id) return data;
  }catch(e){}
  return null;
}

function showDebug(id, obj){
  if(!DEBUG_ON_MISSING) return;
  const el = document.getElementById("dbg_" + id);
  if(!el) return;
  el.classList.remove("hide");
  el.textContent = "DEBUG booking:\n" + JSON.stringify(obj, null, 2);
}

async function openOutlookEmail(id){
  const hit = await getBookingSafe(id);
  if(!hit){ alert("Não encontrei a marcação."); return; }

  const to = (hit.email || "").trim();
  if(!to){
    showDebug(id, hit);
    alert("Este cliente não tem email guardado (na API).");
    return;
  }

  const subject = `Marcação validada - ${hit.date} ${hit.time}`;
  const body =
`Olá ${hit.name || ""},

A sua marcação foi validada com sucesso!

Data: ${hit.date}
Hora: ${hit.time}
Serviço: ${hit.service}
Barbeiro: ${hit.barber}

Obrigado pela preferência!
Barbearia`;

  const outlookUrl =
    "ms-outlook://compose?to=" + encodeURIComponent(to) +
    "&subject=" + encodeURIComponent(subject) +
    "&body=" + encodeURIComponent(body);

  const mailtoUrl =
    "mailto:" + encodeURIComponent(to) +
    "?subject=" + encodeURIComponent(subject) +
    "&body=" + encodeURIComponent(body);

  window.location.href = outlookUrl;
  setTimeout(() => { window.location.href = mailtoUrl; }, 600);
}

async function openSMS(id){
  const hit = await getBookingSafe(id);
  if(!hit){ alert("Não encontrei a marcação."); return; }

  let phone = (hit.phone || "").trim();
  if(!phone) phone = TEST_PHONE_FALLBACK;

  if(!phone){
    showDebug(id, hit);
    alert("Este cliente não tem telemóvel guardado (na API).");
    return;
  }

  const msg =
`Olá ${hit.name || ""}, a sua marcação foi validada ✅

Data: ${hit.date}
Hora: ${hit.time}
Serviço: ${hit.service}
Barbeiro: ${hit.barber}

Obrigado!`;

  const smsUrl = "sms:" + encodeURIComponent(phone) + "&body=" + encodeURIComponent(msg);
  window.location.href = smsUrl;
}

const clientsStatusEl = document.getElementById("clientsStatus");
const clientSaveStatusEl = document.getElementById("clientSaveStatus");
const selNameEl = document.getElementById("selName");
const selMetaEl = document.getElementById("selMeta");

const c_id = document.getElementById("c_id");
const c_name = document.getElementById("c_name");
const c_phone = document.getElementById("c_phone");
const c_email = document.getElementById("c_email");
const c_notes = document.getElementById("c_notes");

const imgBefore = document.getElementById("imgBefore");
const imgAfter  = document.getElementById("imgAfter");
const beforeInfo = document.getElementById("beforeInfo");
const afterInfo  = document.getElementById("afterInfo");
const fileBefore = document.getElementById("fileBefore");
const fileAfter  = document.getElementById("fileAfter");

function clientMatchesExactly(client, name, phone, email){
  const cp = normPhone(client?.phone || "");
  const ce = normEmail(client?.email || "");
  const cn = normName(client?.name || "");

  const p = normPhone(phone || "");
  const e = normEmail(email || "");
  const n = normName(name || "");

  if(e && ce && e === ce) return true;
  if(p && cp && p === cp) return true;
  if(n && cn && n === cn) return true;
  return false;
}

function findClientLocalExact(name, phone, email){
  const list = clientsCache || [];

  if(email){
    const hit = list.find(x => normEmail(x.email) === normEmail(email));
    if(hit) return hit;
  }

  if(phone){
    const hit = list.find(x => normPhone(x.phone) === normPhone(phone));
    if(hit) return hit;
  }

  if(name){
    const hits = list.filter(x => normName(x.name) === normName(name));
    if(hits.length === 1) return hits[0];
  }

  return null;
}

async function searchClientsRemote(name, phone, email){
  const tries = [];
  if(email) tries.push(email);
  if(phone) tries.push(phone);
  if(name) tries.push(name);

  for(const q of tries){
    try{
      const url = new URL(API_BASE + "/admin/clients");
      url.searchParams.set("q", q);
      const data = await apiGet(url.toString());
      const items = data.items || [];
      const exact = items.find(x => clientMatchesExactly(x, name, phone, email));
      if(exact) return exact;
    }catch(e){}
  }

  return null;
}

async function linkBookingToClient(bookingId, clientId){
  if(!bookingId || !clientId) return null;
  try{
    const data = await apiPost(
      API_BASE + "/admin/booking/" + encodeURIComponent(bookingId) + "/link-client",
      { client_id: String(clientId) }
    );
    return data?.item || null;
  }catch(e){
    console.warn("Falhou link booking->client", e);
    return null;
  }
}

function absUrlMaybe(u){
  if(!u) return "";
  if(u.startsWith("http://") || u.startsWith("https://")) return u;
  if(u.startsWith("/")) return API_BASE + u;
  return API_BASE + "/" + u;
}

async function reloadClients(){
  clientsStatusEl.textContent = "A carregar clientes…";
  try{
    const url = new URL(API_BASE + "/admin/clients");
    const data = await apiGet(url.toString());
    clientsCache = data.items || [];
    clientsStatusEl.textContent = `OK • ${clientsCache.length} clientes`;
    filterPhotoClients();
    filterBookingClients();
  }catch(ex){
    clientsStatusEl.textContent = "Erro: " + ex.message;
  }
}

function fillClientForm(c){
  c_id.value = c?.id || "";
  c_name.value = c?.name || "";
  c_phone.value = c?.phone || "";
  c_email.value = c?.email || "";
  c_notes.value = c?.notes || "";
}

function renderClientPhotos(c){
  const b = c?.photo_before_url || "";
  const a = c?.photo_after_url || "";

  if(b){
    imgBefore.src = absUrlMaybe(b);
    imgBefore.style.display = "block";
    beforeInfo.textContent = b;
  }else{
    imgBefore.src = "";
    imgBefore.style.display = "none";
    beforeInfo.textContent = "—";
  }

  if(a){
    imgAfter.src = absUrlMaybe(a);
    imgAfter.style.display = "block";
    afterInfo.textContent = a;
  }else{
    imgAfter.src = "";
    imgAfter.style.display = "none";
    afterInfo.textContent = "—";
  }
}

function selectClient(c){
  selectedClient = c || null;
  selNameEl.textContent = c?.name || "Seleciona um cliente";
  selMetaEl.textContent = [c?.phone||"", c?.email||"", c?.id||""].filter(Boolean).join(" • ") || "—";
  fillClientForm(c || {});
  renderClientPhotos(c || {});
  clientSaveStatusEl.textContent = "—";
  if(c) clientsStatusEl.textContent = "Cliente selecionado.";
}

function renderPhotoResults(items){
  const box = document.getElementById("photoResults");
  box.innerHTML = "";

  if(!items.length){
    box.innerHTML = `<div class="mini">Sem resultados.</div>`;
    return;
  }

  for(const c of items){
    const div = document.createElement("div");
    div.className = "searchItem";
    div.innerHTML = `
      <div class="n">${escapeHtml(c.name || "(sem nome)")}</div>
      <div class="m">${escapeHtml([c.phone||"", c.email||"", c.id||""].filter(Boolean).join(" • "))}</div>
    `;
    div.onclick = () => selectClient(c);
    box.appendChild(div);
  }
}

function filterPhotoClients(){
  const q = (document.getElementById("photoSearch").value || "").trim().toLowerCase();
  let items = [...clientsCache];

  if(q){
    items = items.filter(c => {
      const name = (c.name || "").toLowerCase();
      const phone = normPhone(c.phone || "");
      return name.includes(q) || phone.includes(normPhone(q));
    });
  }

  renderPhotoResults(items.slice(0, 100));
}

function newClient(){
  selectedClient = { id:"", name:"", phone:"", email:"", notes:"" };
  selNameEl.textContent = "Novo cliente";
  selMetaEl.textContent = "—";
  fillClientForm(selectedClient);
  renderClientPhotos({});
  clientSaveStatusEl.textContent = "—";
  clientsStatusEl.textContent = "Novo cliente.";
  showTab("photos");
}

async function saveClient(){
  clientSaveStatusEl.textContent = "A guardar…";
  try{
    const payload = {
      id: (c_id.value || "").trim(),
      name: (c_name.value || "").trim(),
      phone: (c_phone.value || "").trim(),
      email: (c_email.value || "").trim(),
      notes: (c_notes.value || "").trim()
    };

    if(!payload.id){
      if(!(clientsCache||[]).length){
        await reloadClients().catch(()=>{});
      }

      let existing = findClientLocalExact(payload.name, payload.phone, payload.email);
      if(!existing){
        existing = await searchClientsRemote(payload.name, payload.phone, payload.email);
      }
      if(existing && existing.id){
        payload.id = existing.id;
      }
    }

    const data = await apiPost(API_BASE + "/admin/clients", payload);
    const item = data.item || null;
    if(item){
      await reloadClients().catch(()=>{});
      selectClient(item);
    }
    clientSaveStatusEl.textContent = "✅ Guardado";
  }catch(ex){
    clientSaveStatusEl.textContent = "❌ Erro: " + ex.message;
  }
}

async function uploadPhoto(kind){
  const cid = (c_id.value || "").trim();
  if(!cid){
    alert("Primeiro guarda o cliente (precisa de ID).");
    return;
  }

  const file = (kind === "before" ? fileBefore.files[0] : fileAfter.files[0]);
  if(!file){
    alert("Escolhe uma foto primeiro.");
    return;
  }

  clientSaveStatusEl.textContent = "A enviar foto…";
  try{
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);

    const data = await apiPostForm(API_BASE + "/admin/client/" + encodeURIComponent(cid) + "/photo", fd);
    const item = data.item || null;
    if(item){
      await reloadClients().catch(()=>{});
      selectClient(item);
    }
    clientSaveStatusEl.textContent = "✅ Foto enviada";
    if(kind === "before") fileBefore.value = "";
    else fileAfter.value = "";
  }catch(ex){
    clientSaveStatusEl.textContent = "❌ Erro: " + ex.message;
  }
}

async function deleteClientById(cid){
  const id = (cid || "").trim();
  if(!id){ alert("Sem ID."); return; }
  if(!confirm("Apagar este cliente?")) return;

  try{
    await apiDelete(API_BASE + "/admin/client/" + encodeURIComponent(id));
    newClient();
    await reloadClients();
    clientSaveStatusEl.textContent = "✅ Cliente apagado.";
  }catch(e1){
    try{
      await apiPost(API_BASE + "/admin/client/" + encodeURIComponent(id) + "/delete", {});
      newClient();
      await reloadClients();
      clientSaveStatusEl.textContent = "✅ Cliente apagado.";
    }catch(e2){
      alert("Não consegui apagar: " + (e2?.message || e1?.message || "erro"));
    }
  }
}

async function openPhotosForBooking(bid){
  const b = await getBookingSafe(bid);
  if(!b){ alert("Não encontrei a marcação."); return; }

  showTab("photos");
  clientsStatusEl.textContent = "A ligar cliente da marcação…";
  clientSaveStatusEl.textContent = "—";

  const bookingName  = (b.name || b.client || "").trim();
  const bookingPhone = (b.phone || "").trim();
  const bookingEmail = (b.email || "").trim();
  const bookingClientId = (b.client_id || "").trim();

  if(!(clientsCache||[]).length){
    await reloadClients().catch(()=>{});
  }

  if(bookingClientId){
    let c = (clientsCache || []).find(x => String(x.id) === String(bookingClientId)) || null;
    if(!c){
      try{
        const data = await apiGet(API_BASE + "/admin/client/" + encodeURIComponent(bookingClientId));
        c = data.item || null;
      }catch(e){}
    }

    if(c){
      selectClient(c);
      clientsStatusEl.textContent = "Cliente da marcação aberto com sucesso.";
      return;
    }
  }

  let existing = findClientLocalExact(bookingName, bookingPhone, bookingEmail);
  if(!existing){
    existing = await searchClientsRemote(bookingName, bookingPhone, bookingEmail);
  }

  if(existing && existing.id){
    await linkBookingToClient(bid, existing.id);
    await reloadMap().catch(()=>{});
    await reloadClients().catch(()=>{});
    selectClient(existing);
    clientSaveStatusEl.textContent = "✅ Cliente encontrado e ligado à marcação.";
    clientsStatusEl.textContent = "Cliente da marcação aberto com sucesso.";
    return;
  }

  try{
    const payload = {
      id: "",
      name: bookingName,
      phone: bookingPhone,
      email: bookingEmail,
      notes: ""
    };

    const data = await apiPost(API_BASE + "/admin/clients", payload);
    const item = data.item || null;

    if(item && item.id){
      await linkBookingToClient(bid, item.id);
      await reloadMap().catch(()=>{});
      await reloadClients().catch(()=>{});
      selectClient(item);
      clientSaveStatusEl.textContent = "✅ Cliente criado e ligado automaticamente à marcação.";
      clientsStatusEl.textContent = "Cliente da marcação aberto com sucesso.";
      return;
    }
  }catch(e){
    console.warn("Falhou criação automática do cliente", e);
  }

  newClient();
  c_name.value = bookingName;
  c_phone.value = bookingPhone;
  c_email.value = bookingEmail;
  c_id.value = "";

  clientsStatusEl.textContent = "Cliente não encontrado.";
  clientSaveStatusEl.textContent =
    "⚠️ Não encontrei nem consegui criar automaticamente. Carrega 💾 Guardar e depois envia as fotos.";
}

/* -------------------------
   TAB MARCAÇÕES
------------------------- */
const b_client_id = document.getElementById("b_client_id");
const b_name = document.getElementById("b_name");
const b_phone = document.getElementById("b_phone");
const b_email = document.getElementById("b_email");
const b_barber = document.getElementById("b_barber");
const b_date = document.getElementById("b_date");
const b_time = document.getElementById("b_time");
const b_service = document.getElementById("b_service");
const b_dur = document.getElementById("b_dur");
const b_notes = document.getElementById("b_notes");
const bookCreateStatus = document.getElementById("bookCreateStatus");
const bookSearchStatus = document.getElementById("bookSearchStatus");
const bookSelTitle = document.getElementById("bookSelTitle");
const bookSelMeta = document.getElementById("bookSelMeta");

function renderBookingResults(items){
  const box = document.getElementById("bookResults");
  box.innerHTML = "";

  if(!items.length){
    box.innerHTML = `<div class="mini">Sem resultados.</div>`;
    return;
  }

  for(const c of items){
    const div = document.createElement("div");
    div.className = "searchItem";
    div.innerHTML = `
      <div class="n">${escapeHtml(c.name || "(sem nome)")}</div>
      <div class="m">${escapeHtml([c.phone||"", c.email||"", c.id||""].filter(Boolean).join(" • "))}</div>
    `;
    div.onclick = () => selectBookingClient(c);
    box.appendChild(div);
  }
}

function filterBookingClients(){
  const q = (document.getElementById("bookSearch").value || "").trim().toLowerCase();
  let items = [...clientsCache];

  if(q){
    items = items.filter(c => {
      const name = (c.name || "").toLowerCase();
      const phone = normPhone(c.phone || "");
      return name.includes(q) || phone.includes(normPhone(q));
    });
  }

  renderBookingResults(items.slice(0, 100));
  bookSearchStatus.textContent = `${items.length} resultado(s)`;
}

function selectBookingClient(c){
  bookingSelectedClient = c || null;
  b_client_id.value = c?.id || "";
  b_name.value = c?.name || "";
  b_phone.value = c?.phone || "";
  b_email.value = c?.email || "";
  bookSelTitle.textContent = c?.name || "Dados da marcação";
  bookSelMeta.textContent = [c?.phone||"", c?.email||"", c?.id||""].filter(Boolean).join(" • ") || "—";
}

function clearBookingForm(){
  bookingSelectedClient = null;
  b_client_id.value = "";
  b_name.value = "";
  b_phone.value = "";
  b_email.value = "";
  b_service.value = "";
  b_notes.value = "";
  b_dur.value = "45";
  if(!b_date.value) b_date.value = todayISO();
  bookSelTitle.textContent = "Dados da marcação";
  bookSelMeta.textContent = "Podes escolher cliente existente ou preencher manualmente.";
  bookCreateStatus.textContent = "—";
}

async function createBookingFromAdmin(){
  const payload = {
    client_id: (b_client_id.value || "").trim(),
    name: (b_name.value || "").trim(),
    phone: (b_phone.value || "").trim(),
    email: (b_email.value || "").trim(),
    barber: (b_barber.value || "").trim(),
    date: (b_date.value || "").trim(),
    time: (b_time.value || "").trim(),
    service: (b_service.value || "").trim(),
    dur: parseInt(b_dur.value || "45", 10),
    notes: (b_notes.value || "").trim(),
    created_by: "barber_admin",
    created_via: "barber_page"
  };

  if(!payload.name){
    alert("Escreve o nome do cliente.");
    return;
  }
  if(!payload.phone){
    alert("Escreve o telefone do cliente.");
    return;
  }
  if(!payload.barber || !payload.date || !payload.time || !payload.service || !payload.dur){
    alert("Preenche barbeiro, data, hora, serviço e duração.");
    return;
  }

  bookCreateStatus.textContent = "A criar marcação…";
  try{
    const data = await apiPost(API_BASE + "/book", payload);
    const newClientId = data?.client_id || "";
    if(newClientId && !payload.client_id){
      b_client_id.value = newClientId;
    }

    await reloadClients().catch(()=>{});
    await reloadMap().catch(()=>{});

    bookCreateStatus.textContent = `✅ Marcação criada com sucesso${data?.id ? " • ID " + data.id : ""}`;
    showTab("map");
  }catch(ex){
    bookCreateStatus.textContent = "❌ Erro: " + ex.message;
  }
}

async function prefillBookingFromSlot(bid){
  const b = await getBookingSafe(bid);
  if(!b) return;

  showTab("booking");

  b_client_id.value = b.client_id || "";
  b_name.value = b.name || b.client || "";
  b_phone.value = b.phone || "";
  b_email.value = b.email || "";
  b_barber.value = b.barber || "Pedro";
  b_date.value = b.date || todayISO();
  b_time.value = b.time || "";
  b_service.value = b.service || "";
  b_dur.value = String(b.dur || 45);
  b_notes.value = "";
  bookSelTitle.textContent = "Nova marcação por cima de um slot existente";
  bookSelMeta.textContent = "Podes alterar a hora, serviço e duração antes de gravar.";
  bookCreateStatus.textContent = "—";
}

function prefillBookingByTime(hhmm){
  showTab("booking");
  b_barber.value = barberEl.value || "Pedro";
  b_date.value = dateEl.value || todayISO();
  b_time.value = hhmm || "";
  if(!b_dur.value) b_dur.value = "45";
  bookCreateStatus.textContent = "—";
}

(function boot(){
  const t = token();
  if(t){
    overlayEl.classList.add("hide");
    appEl.classList.remove("hide");
    if(!dateEl.value) dateEl.value = todayISO();
    if(!b_date.value) b_date.value = todayISO();
    reloadMap().catch(()=>{});
    reloadClients().catch(()=>{});
  }
})();

dateEl.addEventListener("change", reloadMap);
barberEl.addEventListener("change", reloadMap);
setInterval(() => {
  if(!overlayEl.classList.contains("hide")) return;
  reloadMap();
}, 60000);
</script>
</body>
</html>
