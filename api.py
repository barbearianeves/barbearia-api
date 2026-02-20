from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets, json, re

# ✅ email
import smtplib, ssl, socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# =========================
# CONFIG / SECRETS
# =========================
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345").strip()
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "barbeiro-2026").strip()

# ✅ Email (Render envs)
FROM_EMAIL = (os.environ.get("FROM_EMAIL", "") or "").strip() or (os.environ.get("SMTP_USER", "") or "").strip()
SMTP_HOST  = (os.environ.get("SMTP_HOST", "smtp.gmail.com") or "").strip()
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER  = (os.environ.get("SMTP_USER", "") or "").strip() or FROM_EMAIL
SMTP_PASS  = (os.environ.get("SMTP_PASS", "") or "").strip()

BOOKINGS = {}                    # id -> booking dict (persistente)
CHANGES  = deque(maxlen=20000)   # eventos para bridge (memória)

# =========================
# Helpers
# =========================
def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({"op": op, "payload": payload, "ts": int(time.time())})

def is_admin(req):
    return (req.headers.get("X-Admin-Token", "") or "").strip() == ADMIN_TOKEN

def norm_str(x):
    return (x or "").strip()

def slugify_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-zÀ-ÿ_]+", "", s)
    return s[:80] or "Cliente"

def normalize_phone_e164_pt(raw: str) -> str:
    """
    Normaliza PT:
    - '916 634 329' -> '+351916634329'
    - '00351...' -> '+351...'
    - '+351...' mantém
    """
    s = (raw or "").strip()
    s = re.sub(r"[^\d+]", "", s)
    if not s:
        return ""
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        return "+" + digits
    digits = re.sub(r"\D", "", s)
    if len(digits) == 9:
        return "+351" + digits
    # fallback genérico
    return "+" + digits

# -------------------------
# ✅ STORAGE: escolher diretório escrevível
# -------------------------
def pick_data_dir():
    cand = (os.environ.get("DATA_DIR", "") or "").strip()
    candidates = []
    if cand:
        candidates.append(cand)

    candidates.append(os.path.join(os.path.dirname(__file__), "data"))
    candidates.append("/tmp/barbearia_data")

    for p in candidates:
        try:
            os.makedirs(p, exist_ok=True)
            testf = os.path.join(p, ".write_test")
            with open(testf, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(testf)
            return p
        except Exception:
            continue
    return None

DATA_DIR = pick_data_dir()
BOOKINGS_FILE = os.path.join(DATA_DIR, "bookings.json") if DATA_DIR else None

# ✅ CLIENTES (TXT igual ao C)
CLIENTS_DIR = os.path.join(DATA_DIR, "clientes") if DATA_DIR else None
CLIENT_INDEX_FILE = os.path.join(DATA_DIR, "clients_index.json") if DATA_DIR else None  # phone_e164 -> client_id
LOGIN_CHALLENGES = {}  # challenge_id -> {phone, otp, exp_ts}

def load_bookings():
    global BOOKINGS
    if not BOOKINGS_FILE:
        BOOKINGS = {}
        return
    try:
        if os.path.exists(BOOKINGS_FILE):
            with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            BOOKINGS = data if isinstance(data, dict) else {}
        else:
            BOOKINGS = {}
    except Exception:
        BOOKINGS = {}

def save_bookings():
    if not BOOKINGS_FILE:
        return
    tmp = BOOKINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(BOOKINGS, f, ensure_ascii=False)
    os.replace(tmp, BOOKINGS_FILE)

def jload(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def jsave(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

load_bookings()

# ✅ snapshot em memória (para bridge puxar após restart)
CHANGES.clear()
for b in BOOKINGS.values():
    push_change("upsert", b)

# =========================
# CLIENTES: storage + index
# =========================
def ensure_clients_dir():
    if not CLIENTS_DIR:
        return
    os.makedirs(CLIENTS_DIR, exist_ok=True)

def load_client_index():
    if not CLIENT_INDEX_FILE:
        return {}
    return jload(CLIENT_INDEX_FILE, {})

def save_client_index(idx: dict):
    if not CLIENT_INDEX_FILE:
        return
    jsave(CLIENT_INDEX_FILE, idx)

def next_client_id(existing_ids: set) -> str:
    # ids tipo "000001"
    n = 1
    while True:
        cid = f"{n:06d}"
        if cid not in existing_ids:
            return cid
        n += 1

def client_folder_path(client_id: str, name: str) -> str:
    ensure_clients_dir()
    folder = f"{client_id}_{slugify_name(name)}"
    return os.path.join(CLIENTS_DIR, folder)

def client_txt_path(client_id: str, name: str) -> str:
    return os.path.join(client_folder_path(client_id, name), "cliente.txt")

def parse_cliente_txt(path: str) -> dict:
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out

def write_cliente_txt(client: dict):
    """
    Escreve no formato:
    id=000001
    nome=...
    telefone=...
    email=...
    profissao=...
    idade=...
    notas=... (pode ter \n)
    foto_antes=
    foto_depois=
    """
    cid = client.get("id")
    nome = client.get("nome") or "Cliente"
    path = client_txt_path(cid, nome)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # mantém \n escapado como no teu exemplo
    notas = (client.get("notas") or "").replace("\r", "").replace("\n", "\\n")

    lines = [
        f"id={cid}",
        f"nome={client.get('nome','')}",
        f"telefone={client.get('telefone','')}",
        f"email={client.get('email','')}",
        f"profissao={client.get('profissao','')}",
        f"idade={client.get('idade','')}",
        f"notas={notas}",
        f"foto_antes={client.get('foto_antes','')}",
        f"foto_depois={client.get('foto_depois','')}",
    ]
    content = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def find_client_by_id(client_id: str, idx: dict) -> dict:
    """
    Procura em disco o cliente.txt pelo id (pasta começa por '{id}_')
    """
    ensure_clients_dir()
    if not CLIENTS_DIR or not os.path.isdir(CLIENTS_DIR):
        return {}

    prefix = f"{client_id}_"
    for fn in os.listdir(CLIENTS_DIR):
        if fn.startswith(prefix):
            p = os.path.join(CLIENTS_DIR, fn, "cliente.txt")
            data = parse_cliente_txt(p)
            if data.get("id") == client_id:
                return {
                    "id": data.get("id",""),
                    "nome": data.get("nome",""),
                    "telefone": data.get("telefone",""),
                    "email": data.get("email",""),
                    "profissao": data.get("profissao",""),
                    "idade": data.get("idade",""),
                    "notas": (data.get("notas","") or "").replace("\\n","\n"),
                    "foto_antes": data.get("foto_antes",""),
                    "foto_depois": data.get("foto_depois",""),
                }
    return {}

def upsert_client(phone_raw: str, name: str = "", email: str = "", client_id: str = "", extra: dict = None):
    """
    Regras:
    - chave principal: telefone normalizado (e164)
    - se phone existir no index -> usa esse id
    - se vier client_id (ex: sessão), respeita e atualiza index
    - nunca cria duplicado do mesmo telefone
    """
    ensure_clients_dir()
    idx = load_client_index()

    phone = normalize_phone_e164_pt(phone_raw)
    if not phone:
        return None, "Telefone inválido"

    # existing by phone
    existing_id = idx.get(phone)

    # carregar conjunto de ids existentes
    existing_ids = set()
    if CLIENTS_DIR and os.path.isdir(CLIENTS_DIR):
        for fn in os.listdir(CLIENTS_DIR):
            m = re.match(r"^(\d{6})_", fn)
            if m:
                existing_ids.add(m.group(1))

    cid = client_id or existing_id
    created = False

    if not cid:
        cid = next_client_id(existing_ids)
        created = True

    # buscar cliente atual (se existir)
    cur = find_client_by_id(cid, idx) or {
        "id": cid,
        "nome": "",
        "telefone": "",
        "email": "",
        "profissao": "",
        "idade": "",
        "notas": "",
        "foto_antes": "",
        "foto_depois": "",
    }

    # atualizar campos (sem apagar com vazio)
    if name and name.strip():
        cur["nome"] = name.strip()
    if email and email.strip():
        cur["email"] = email.strip()
    if phone:
        cur["telefone"] = phone

    if extra:
        # extra sem apagar
        for k, v in extra.items():
            if v is None:
                continue
            if isinstance(v, str):
                if v.strip():
                    cur[k] = v.strip()
            else:
                cur[k] = v

    # gravar e indexar
    write_cliente_txt(cur)
    idx[phone] = cid
    save_client_index(idx)

    return {"client": cur, "created": created}, None

# =========================
# ✅ EMAIL: SMTP por IPv4 + logs
# =========================
def smtp_connect_ipv4(host: str, port: int, timeout: int = 20) -> smtplib.SMTP:
    ipv4 = None
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            ipv4 = infos[0][4][0]
    except Exception:
        ipv4 = None

    target = ipv4 or host
    return smtplib.SMTP(target, port, timeout=timeout)

def send_validation_email(booking, subject=None, body=None):
    to_email = norm_str(booking.get("email"))
    if not to_email:
        return False, "Cliente sem email"

    if not SMTP_USER or not FROM_EMAIL:
        return False, "SMTP_USER/FROM_EMAIL não definidos"

    if not SMTP_PASS:
        return False, "SMTP_PASS não definido"

    try:
        subj = subject or "Confirmação da sua marcação - Barbearia"

        if body is None:
            body = f"""Olá {booking.get("name","")},

A sua marcação foi validada com sucesso!

Data: {booking.get("date")}
Hora: {booking.get("time")}
Serviço: {booking.get("service")}
Barbeiro: {booking.get("barber")}

Obrigado pela preferência!
Barbearia
"""

        msg = MIMEMultipart()
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subj
        msg.attach(MIMEText(body, "plain", "utf-8"))

        print(f"[EMAIL] to={to_email} host={SMTP_HOST} port={SMTP_PORT} user={SMTP_USER}", flush=True)

        server = smtp_connect_ipv4(SMTP_HOST, SMTP_PORT, timeout=20)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        print("[EMAIL] starttls OK", flush=True)

        server.login(SMTP_USER, SMTP_PASS)
        print("[EMAIL] login OK", flush=True)

        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        print("[EMAIL] sendmail OK", flush=True)

        server.quit()
        return True, "Email enviado"
    except Exception as e:
        print(f"[EMAIL] ERRO: {type(e).__name__}: {e}", flush=True)
        return False, f"{type(e).__name__}: {e}"

# -------------------------
# HOME / HEALTH
# -------------------------
@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "barbearia-api",
        "bookings": len(BOOKINGS),
        "changes": len(CHANGES),
        "persist": BOOKINGS_FILE or "NO_PERSIST",
        "data_dir": DATA_DIR,
        "clients_dir": CLIENTS_DIR,
        "smtp": {
            "from": FROM_EMAIL,
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "user_set": bool(SMTP_USER),
            "pass_set": bool(SMTP_PASS)
        }
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

# =========================
# CLIENT: LOGIN (OTP simples)
# =========================
@app.post("/client/start_login")
def client_start_login():
    data = request.get_json(silent=True) or {}
    phone = normalize_phone_e164_pt(norm_str(data.get("phone")))
    if not phone:
        return bad("Telefone inválido")

    # gera otp
    otp = f"{secrets.randbelow(1000000):06d}"
    challenge_id = now_id()
    exp = int(time.time()) + 5 * 60  # 5 min

    LOGIN_CHALLENGES[challenge_id] = {
        "phone": phone,
        "otp": otp,
        "exp": exp
    }

    # para já devolvemos otp_debug para testes
    return jsonify({"ok": True, "challenge_id": challenge_id, "otp_debug": otp})

@app.post("/client/verify_login")
def client_verify_login():
    data = request.get_json(silent=True) or {}
    challenge_id = norm_str(data.get("challenge_id"))
    code = norm_str(data.get("code"))
    name = norm_str(data.get("name"))
    email = norm_str(data.get("email"))

    if not challenge_id or not code:
        return bad("challenge_id e code são obrigatórios")

    ch = LOGIN_CHALLENGES.get(challenge_id)
    if not ch:
        return bad("challenge inválido", 401)

    if int(time.time()) > int(ch.get("exp", 0)):
        LOGIN_CHALLENGES.pop(challenge_id, None)
        return bad("código expirou", 401)

    if code != ch.get("otp"):
        return bad("código errado", 401)

    phone = ch["phone"]
    # ok: devolve cliente existente ou cria novo
    r, err = upsert_client(phone_raw=phone, name=name or "", email=email or "", client_id="")
    LOGIN_CHALLENGES.pop(challenge_id, None)
    if err:
        return bad(err)

    client = r["client"]
    return jsonify({"ok": True, "created": r["created"], "client": client})

@app.get("/client/<client_id>")
def client_get(client_id):
    cid = norm_str(client_id)
    if not cid:
        return bad("id inválido")
    idx = load_client_index()
    c = find_client_by_id(cid, idx)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "client": c})

@app.post("/client/upsert")
def client_upsert():
    data = request.get_json(silent=True) or {}
    cid = norm_str(data.get("id"))
    phone = norm_str(data.get("phone"))
    name = norm_str(data.get("name"))
    email = norm_str(data.get("email"))

    # phone é obrigatório para não haver duplicados
    if not phone:
        return bad("phone obrigatório")

    r, err = upsert_client(phone_raw=phone, name=name, email=email, client_id=cid or "")
    if err:
        return bad(err)
    return jsonify({"ok": True, "created": r["created"], "client": r["client"]})

# -------------------------
# ✅ ADMIN: TESTE EMAIL
# -------------------------
@app.post("/admin/test_email")
def admin_test_email():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    to_email = norm_str(data.get("to"))
    if not to_email:
        return bad("Campo obrigatório: to")

    fake_booking = {
        "email": to_email,
        "name": "Teste",
        "date": time.strftime("%Y-%m-%d"),
        "time": time.strftime("%H:%M"),
        "service": "Teste SMTP",
        "barber": "Sistema",
    }

    ok, msg = send_validation_email(fake_booking, subject="✅ Teste SMTP - Barbearia")
    return jsonify({"ok": ok, "message": msg})

# =========================
# BOOKINGS: merge preservando contacto + puxando do cliente
# =========================
def merge_preserving_contact(old: dict, incoming: dict) -> dict:
    """
    ✅ NÃO deixa apagar phone/email se vierem vazios no incoming.
    (mas permite atualizar se vierem preenchidos)
    """
    merged = {**(old or {}), **(incoming or {})}

    for key in ["phone", "email"]:
        inc = norm_str((incoming or {}).get(key))
        if inc:
            merged[key] = inc
        else:
            merged[key] = norm_str((old or {}).get(key))

    # normaliza sempre
    merged["name"] = norm_str(merged.get("name"))
    merged["service"] = norm_str(merged.get("service"))
    merged["barber"] = norm_str(merged.get("barber"))
    merged["date"] = norm_str(merged.get("date"))
    merged["time"] = norm_str(merged.get("time"))[:5]
    merged["status"] = norm_str(merged.get("status")) or "Marcado"

    return merged

def hydrate_contact_from_client(booking: dict) -> dict:
    """
    ✅ Se booking vem com client_id mas sem phone/email (ou vazios),
    vai buscar ao cliente.txt e completa.
    """
    b = dict(booking or {})
    cid = norm_str(b.get("client_id"))
    if not cid:
        return b

    need_phone = not norm_str(b.get("phone"))
    need_email = not norm_str(b.get("email"))
    if not (need_phone or need_email):
        return b

    idx = load_client_index()
    c = find_client_by_id(cid, idx)
    if not c:
        return b

    if need_phone and norm_str(c.get("telefone")):
        b["phone"] = norm_str(c.get("telefone"))
    if need_email and norm_str(c.get("email")):
        b["email"] = norm_str(c.get("email"))

    # nome também
    if not norm_str(b.get("name")) and norm_str(c.get("nome")):
        b["name"] = norm_str(c.get("nome"))
    if not norm_str(b.get("client")) and norm_str(c.get("nome")):
        b["client"] = norm_str(c.get("nome"))

    return b

# -------------------------
# CLIENTE: CRIAR MARCAÇÃO
# -------------------------
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}
    required = ["name", "phone", "service", "barber", "date", "time", "dur"]
    for k in required:
        if not norm_str(str(data.get(k, ""))):
            return bad(f"Campo obrigatório em falta: {k}")

    t = norm_str(str(data["time"]))[:5]
    bid = norm_str(data.get("id")) or now_id()

    phone_norm = normalize_phone_e164_pt(norm_str(data.get("phone")))
    if not phone_norm:
        return bad("Telefone inválido")

    # ✅ upsert cliente para garantir que existe e evitar duplicado
    client_id_in = norm_str(data.get("client_id"))
    r, err = upsert_client(
        phone_raw=phone_norm,
        name=norm_str(data.get("name")),
        email=norm_str(data.get("email")),
        client_id=client_id_in
    )
    if err:
        return bad(err)
    client = r["client"]

    item = {
        "id": bid,
        "name": norm_str(data.get("name")) or norm_str(client.get("nome")),
        "phone": phone_norm,
        "email": norm_str(data.get("email")) or norm_str(client.get("email")),
        "service": norm_str(data.get("service")),
        "barber": norm_str(data.get("barber")),
        "date": norm_str(data.get("date")),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": norm_str(data.get("notes")),
        "status": "Marcado",
        "client_id": norm_str(client.get("id")),
        "client": norm_str(data.get("client")) or norm_str(client.get("nome")),
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid, "client_id": item["client_id"]})

# -------------------------
# BRIDGE: PULL / SYNC
# -------------------------
@app.get("/pull")
def pull():
    secret = request.args.get("secret", "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    cursor = int(request.args.get("cursor", "0"))
    limit  = int(request.args.get("limit", "200"))

    changes_list = list(CHANGES)
    out = changes_list[cursor: cursor + limit]
    new_cursor = min(cursor + len(out), len(changes_list))
    return jsonify({"ok": True, "cursor": new_cursor, "items": out})

@app.post("/sync")
def sync():
    secret = request.args.get("secret", "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes", [])
    if not isinstance(changes, list):
        return bad("changes inválido")

    applied = 0
    for ch in changes:
        op = ch.get("op")
        payload = ch.get("payload") or {}
        bid = payload.get("id")
        if not bid:
            continue

        if op == "delete":
            existed = bid in BOOKINGS
            BOOKINGS.pop(bid, None)
            if existed:
                save_bookings()
                push_change("delete", {"id": bid})
            applied += 1

        elif op == "upsert":
            old = BOOKINGS.get(bid, {})
            merged = merge_preserving_contact(old, payload)
            merged = hydrate_contact_from_client(merged)  # ✅ completa contacto se faltar
            BOOKINGS[bid] = merged
            save_bookings()
            push_change("upsert", BOOKINGS[bid])
            applied += 1

    return jsonify({"ok": True, "applied": applied})

@app.post("/replace_all")
def replace_all():
    secret = request.args.get("secret", "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return bad("items inválido")

    global BOOKINGS
    old_all = BOOKINGS
    BOOKINGS = {}

    for b in items:
        bid = norm_str(b.get("id"))
        if not bid:
            continue
        merged = merge_preserving_contact(old_all.get(bid, {}), b)
        merged = hydrate_contact_from_client(merged)
        BOOKINGS[bid] = merged

    save_bookings()

    CHANGES.clear()
    for b in BOOKINGS.values():
        push_change("upsert", b)

    return jsonify({"ok": True, "count": len(BOOKINGS)})

# -------------------------
# CLIENTE: VER OCUPADOS/DIA (SEM dados pessoais)
# -------------------------
def _day_items_for_clients(date: str = "", barber: str = ""):
    out = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue

        st = b.get("status", "Marcado")
        if st in ["Cancelado", "Desbloqueado"]:
            continue

        is_block = (st == "Bloqueado") or (b.get("service") == "INDISPONIVEL")
        out.append({
            "id": b.get("id", ""),
            "time": b.get("time", ""),
            "dur": b.get("dur", 30),
            "status": st,
            "label": "INDISPONÍVEL" if is_block else "OCUPADO",
            "service": b.get("service", ""),
            "notes": b.get("notes", "")
        })
    return out

@app.get("/day")
def day():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _day_items_for_clients(date, barber)})

@app.get("/busy")
def busy():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _day_items_for_clients(date, barber)})

# ==========================
# ADMIN: LISTAR
# ==========================
@app.get("/admin/bookings")
def admin_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date = norm_str(request.args.get("date"))
    barber = norm_str(request.args.get("barber"))

    out = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        out.append(b)

    out.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
    return jsonify({"ok": True, "items": out})

# ✅ ADMIN: OBTER 1 BOOKING COMPLETO
@app.get("/admin/booking/<bid>")
def admin_get_booking(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)

    # garante contacto caso venha vazio
    b2 = hydrate_contact_from_client(b)
    BOOKINGS[bid] = b2
    save_bookings()
    return jsonify({"ok": True, "item": b2})

# ✅ ADMIN: atualizar contacto
@app.post("/admin/update_contact/<bid>")
def admin_update_contact(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    email = norm_str(data.get("email"))
    phone = norm_str(data.get("phone"))

    if phone:
        p = normalize_phone_e164_pt(phone)
        if not p:
            return bad("telefone inválido")
        BOOKINGS[bid]["phone"] = p
    if email:
        BOOKINGS[bid]["email"] = email

    save_bookings()
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True, "item": BOOKINGS[bid]})

# ==========================
# ADMIN: CANCELAR
# ==========================
@app.post("/admin/cancel/<bid>")
def admin_cancel(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    BOOKINGS[bid]["status"] = "Cancelado"
    save_bookings()
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True})

# ==========================
# ADMIN: BLOQUEAR
# ==========================
@app.post("/admin/block")
def admin_block():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    date = norm_str(data.get("date"))
    time_ = norm_str(data.get("time"))[:5]
    barber = norm_str(data.get("barber"))

    if not date or not time_ or not barber:
        return bad("Campos obrigatórios: date, time, barber")

    bid = now_id()
    item = {
        "id": bid,
        "name": "INDISPONÍVEL",
        "phone": "",
        "email": "",
        "service": "INDISPONIVEL",
        "barber": barber,
        "date": date,
        "time": time_,
        "dur": 30,
        "notes": "",
        "status": "Bloqueado",
        "client_id": "",
        "client": "INDISPONÍVEL",
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})

# ==========================
# ADMIN: DESBLOQUEAR
# ==========================
@app.post("/admin/unblock/<bid>")
def admin_unblock(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    BOOKINGS[bid]["status"] = "Desbloqueado"
    save_bookings()
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True})

# ==========================
# ✅ ADMIN: VALIDAR + EMAIL
# ==========================
@app.post("/admin/validate_and_email/<bid>")
def admin_validate_and_email(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    new_status = norm_str(data.get("status")) or "Chegou"

    incoming_email = norm_str(data.get("email"))
    incoming_phone = norm_str(data.get("phone"))
    if incoming_email:
        BOOKINGS[bid]["email"] = incoming_email
    if incoming_phone:
        p = normalize_phone_e164_pt(incoming_phone)
        if p:
            BOOKINGS[bid]["phone"] = p

    BOOKINGS[bid]["status"] = new_status

    # ✅ garante contacto pelo cliente_id se faltar
    BOOKINGS[bid] = hydrate_contact_from_client(BOOKINGS[bid])

    save_bookings()
    push_change("upsert", BOOKINGS[bid])

    ok, msg_email = send_validation_email(BOOKINGS[bid])

    return jsonify({
        "ok": True,
        "status": new_status,
        "email_sent": ok,
        "message": msg_email
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
