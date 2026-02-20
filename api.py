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

def norm_email(x):
    return (x or "").strip().lower()

def slugify_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-zÀ-ÿ_]+", "", s)
    return s[:80] or "Cliente"

def normalize_phone_pt9(raw: str) -> str:
    """
    ✅ Normaliza SEMPRE para PT 9 dígitos (chave única):
    - '916 634 329' -> '916634329'
    - '+351916634329' -> '916634329'
    - '00351916634329' -> '916634329'
    """
    s = (raw or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if digits.startswith("351") and len(digits) >= 12:
        digits = digits[3:]
    # Fica com últimos 9 (para casos com prefixos)
    if len(digits) > 9:
        digits = digits[-9:]
    if len(digits) != 9:
        return ""
    return digits

def phone9_to_e164(phone9: str) -> str:
    p = normalize_phone_pt9(phone9)
    return f"+351{p}" if p else ""

# -------------------------
# ✅ STORAGE: escolher diretório escrevível
# -------------------------
def pick_data_dir():
    cand = (os.environ.get("DATA_DIR", "") or "").strip()
    candidates = []
    if cand:
        candidates.append(cand)

    # fallback local / tmp
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
CLIENT_INDEX_FILE = os.path.join(DATA_DIR, "clients_index.json") if DATA_DIR else None  # phone9 -> client_id

# ✅ LOGIN CHALLENGES persistente (para não falhar em restart)
LOGIN_CHALLENGES_FILE = os.path.join(DATA_DIR, "login_challenges.json") if DATA_DIR else None

def jload(path, default):
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def jsave(path, obj):
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_bookings():
    global BOOKINGS
    if not BOOKINGS_FILE:
        BOOKINGS = {}
        return
    BOOKINGS = jload(BOOKINGS_FILE, {})
    if not isinstance(BOOKINGS, dict):
        BOOKINGS = {}

def save_bookings():
    if not BOOKINGS_FILE:
        return
    tmp = BOOKINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(BOOKINGS, f, ensure_ascii=False)
    os.replace(tmp, BOOKINGS_FILE)

def ensure_clients_dir():
    if CLIENTS_DIR:
        os.makedirs(CLIENTS_DIR, exist_ok=True)

def load_client_index():
    if not CLIENT_INDEX_FILE:
        return {}
    idx = jload(CLIENT_INDEX_FILE, {})
    return idx if isinstance(idx, dict) else {}

def save_client_index(idx: dict):
    if CLIENT_INDEX_FILE:
        jsave(CLIENT_INDEX_FILE, idx)

def list_existing_client_ids() -> set:
    ensure_clients_dir()
    ids = set()
    if CLIENTS_DIR and os.path.isdir(CLIENTS_DIR):
        for fn in os.listdir(CLIENTS_DIR):
            m = re.match(r"^(\d{6})_", fn)
            if m:
                ids.add(m.group(1))
    return ids

def next_client_id(existing_ids: set) -> str:
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

def find_client_by_id(client_id: str) -> dict:
    ensure_clients_dir()
    if not CLIENTS_DIR or not os.path.isdir(CLIENTS_DIR):
        return {}

    prefix = f"{client_id}_"
    for fn in os.listdir(CLIENTS_DIR):
        if fn.startswith(prefix):
            p = os.path.join(CLIENTS_DIR, fn, "cliente.txt")
            data = parse_cliente_txt(p)
            if data.get("id") == client_id:
                # ✅ telefone guardado como 9 dígitos
                tel9 = normalize_phone_pt9(data.get("telefone", ""))
                return {
                    "id": data.get("id",""),
                    "nome": data.get("nome",""),
                    "telefone": tel9,
                    "phone_e164": phone9_to_e164(tel9),
                    "email": data.get("email",""),
                    "profissao": data.get("profissao",""),
                    "idade": data.get("idade",""),
                    "notas": (data.get("notas","") or "").replace("\\n","\n"),
                    "foto_antes": data.get("foto_antes",""),
                    "foto_depois": data.get("foto_depois",""),
                }
    return {}

def write_cliente_txt(client: dict):
    """
    TXT igual ao C:
      telefone = 9 dígitos
    """
    cid = client.get("id") or ""
    nome = (client.get("nome") or "Cliente").strip()

    tel9 = normalize_phone_pt9(client.get("telefone") or client.get("phone") or "")
    email = norm_email(client.get("email"))

    path = client_txt_path(cid, nome)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    notas = (client.get("notas") or "").replace("\r", "").replace("\n", "\\n")

    lines = [
        f"id={cid}",
        f"nome={nome}",
        f"telefone={tel9}",
        f"email={email}",
        f"profissao={(client.get('profissao','') or '').strip()}",
        f"idade={(client.get('idade','') or '').strip()}",
        f"notas={notas}",
        f"foto_antes={(client.get('foto_antes','') or '').strip()}",
        f"foto_depois={(client.get('foto_depois','') or '').strip()}",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def get_or_create_client_id_by_phone9(phone9: str) -> tuple[str, bool]:
    """
    ✅ phone9 é a chave (916634329)
    devolve (client_id, existing)
    """
    phone9 = normalize_phone_pt9(phone9)
    if not phone9:
        return "", False

    idx = load_client_index()
    existing_id = idx.get(phone9)
    if existing_id:
        return existing_id, True

    cid = next_client_id(list_existing_client_ids())
    idx[phone9] = cid
    save_client_index(idx)
    return cid, False

def upsert_client_profile(client_id: str, phone9: str, name: str, email: str = "") -> tuple[dict, bool, str]:
    phone9 = normalize_phone_pt9(phone9)
    if not phone9:
        return {}, False, "Telefone inválido"

    name = (name or "").strip()
    if not name:
        return {}, False, "Nome obrigatório"

    email = norm_email(email)

    idx = load_client_index()

    # se o telefone já está associado a outro id -> bloquear duplicado
    cur_id = idx.get(phone9)
    if cur_id and cur_id != client_id:
        return {}, False, "Este telemóvel já existe noutro cliente"

    # gravar no índice
    idx[phone9] = client_id
    save_client_index(idx)

    cur = find_client_by_id(client_id)
    created = not bool(cur)

    client = cur or {
        "id": client_id,
        "nome": "",
        "telefone": "",
        "email": "",
        "profissao": "",
        "idade": "",
        "notas": "",
        "foto_antes": "",
        "foto_depois": "",
    }

    # atualizar sem apagar
    client["id"] = client_id
    client["telefone"] = phone9
    client["nome"] = name
    if email:
        client["email"] = email

    write_cliente_txt(client)

    # devolver já com phone_e164
    out = dict(client)
    out["telefone"] = phone9
    out["phone_e164"] = phone9_to_e164(phone9)
    return out, created, ""

# =========================
# LOGIN CHALLENGES (persist)
# =========================
def load_login_challenges():
    ch = jload(LOGIN_CHALLENGES_FILE, {})
    if not isinstance(ch, dict):
        return {}
    # limpar expirados
    now = int(time.time())
    out = {}
    for k, v in ch.items():
        try:
            if int(v.get("exp", 0)) > now:
                out[k] = v
        except Exception:
            continue
    if out != ch:
        jsave(LOGIN_CHALLENGES_FILE, out)
    return out

def save_login_challenges(ch: dict):
    jsave(LOGIN_CHALLENGES_FILE, ch)

# =========================
# EMAIL (igual ao teu)
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

        server = smtp_connect_ipv4(SMTP_HOST, SMTP_PORT, timeout=20)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        return True, "Email enviado"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

# =========================
# BOOKINGS STORAGE LOAD
# =========================
load_bookings()
CHANGES.clear()
for b in BOOKINGS.values():
    push_change("upsert", b)

# =========================
# HOME / HEALTH
# =========================
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
        "clients_index": CLIENT_INDEX_FILE,
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
# CLIENT: LOGIN (OTP)
# =========================
@app.post("/client/start_login")
def client_start_login():
    data = request.get_json(silent=True) or {}
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    if not phone9:
        return bad("Telefone inválido (usa 9 dígitos PT)")

    otp = f"{secrets.randbelow(1000000):06d}"
    challenge_id = now_id()
    exp = int(time.time()) + 5 * 60

    ch = load_login_challenges()
    ch[challenge_id] = {"phone9": phone9, "otp": otp, "exp": exp}
    save_login_challenges(ch)

    return jsonify({"ok": True, "challenge_id": challenge_id, "otp_debug": otp})

@app.post("/client/verify_login")
def client_verify_login():
    data = request.get_json(silent=True) or {}
    challenge_id = norm_str(data.get("challenge_id"))
    code = norm_str(data.get("code"))

    if not challenge_id or not code:
        return bad("challenge_id e code são obrigatórios")

    ch = load_login_challenges()
    rec = ch.get(challenge_id)
    if not rec:
        return bad("challenge inválido", 401)

    if int(time.time()) > int(rec.get("exp", 0)):
        ch.pop(challenge_id, None)
        save_login_challenges(ch)
        return bad("código expirou", 401)

    if code != rec.get("otp"):
        return bad("código errado", 401)

    phone9 = rec["phone9"]
    client_id, existing = get_or_create_client_id_by_phone9(phone9)

    # limpar challenge
    ch.pop(challenge_id, None)
    save_login_challenges(ch)

    if existing:
        client = find_client_by_id(client_id)
        # ✅ se por algum motivo faltar o txt, ainda assim devolve estrutura
        if not client:
            client = {
                "id": client_id,
                "nome": "",
                "telefone": phone9,
                "phone_e164": phone9_to_e164(phone9),
                "email": "",
                "profissao": "",
                "idade": "",
                "notas": "",
                "foto_antes": "",
                "foto_depois": "",
            }
        return jsonify({"ok": True, "existing": True, "client": client})

    # novo: ainda não tem nome
    client = {
        "id": client_id,
        "nome": "",
        "telefone": phone9,
        "phone_e164": phone9_to_e164(phone9),
        "email": "",
        "profissao": "",
        "idade": "",
        "notas": "",
        "foto_antes": "",
        "foto_depois": "",
    }
    return jsonify({"ok": True, "existing": False, "client": client})

@app.get("/client/<client_id>")
def client_get(client_id):
    cid = norm_str(client_id)
    if not cid:
        return bad("id inválido")
    c = find_client_by_id(cid)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "client": c})

@app.get("/client/by_phone")
def client_by_phone():
    """
    Útil para debug/bridge: /client/by_phone?phone=916...
    """
    p9 = normalize_phone_pt9(request.args.get("phone",""))
    if not p9:
        return bad("telefone inválido")
    idx = load_client_index()
    cid = idx.get(p9, "")
    if not cid:
        return bad("not found", 404)
    c = find_client_by_id(cid)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "client": c})

@app.post("/client/upsert")
def client_upsert():
    data = request.get_json(silent=True) or {}
    cid = norm_str(data.get("id"))
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    name = norm_str(data.get("name"))
    email = norm_email(data.get("email"))

    if not cid:
        return bad("id obrigatório")
    if not phone9:
        return bad("phone obrigatório")
    if not name:
        return bad("name obrigatório")

    client, created, err = upsert_client_profile(cid, phone9, name, email=email)
    if err:
        return bad(err)
    return jsonify({"ok": True, "created": created, "client": client})

# =========================
# BOOKINGS: merge preservando contacto
# =========================
def merge_preserving_contact(old: dict, incoming: dict) -> dict:
    merged = {**(old or {}), **(incoming or {})}

    for key in ["phone", "email", "client_id", "name", "client"]:
        inc = norm_str((incoming or {}).get(key))
        if inc:
            merged[key] = inc
        else:
            merged[key] = norm_str((old or {}).get(key))

    merged["service"] = norm_str(merged.get("service"))
    merged["barber"] = norm_str(merged.get("barber"))
    merged["date"] = norm_str(merged.get("date"))
    merged["time"] = norm_str(merged.get("time"))[:5]
    merged["status"] = norm_str(merged.get("status")) or "Marcado"
    return merged

def hydrate_contact_from_client(booking: dict) -> dict:
    b = dict(booking or {})
    cid = norm_str(b.get("client_id"))
    if not cid:
        return b

    c = find_client_by_id(cid)
    if not c:
        return b

    # phone guardado sempre como 9 dígitos
    if not norm_str(b.get("phone")) and norm_str(c.get("telefone")):
        b["phone"] = norm_str(c.get("telefone"))
    if not norm_str(b.get("email")) and norm_str(c.get("email")):
        b["email"] = norm_str(c.get("email"))
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

    # ✅ agora phone é sempre 9 dígitos (podes mandar e164 na mesma)
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    if not phone9:
        return bad("Telefone inválido")

    required = ["service", "barber", "date", "time", "dur", "client_id"]
    for k in required:
        if not norm_str(str(data.get(k, ""))):
            return bad(f"Campo obrigatório em falta: {k}")

    cid = norm_str(data.get("client_id"))
    c = find_client_by_id(cid)
    if not c or not norm_str(c.get("nome")):
        return bad("Cliente sem perfil. Cria primeiro (nome).")

    bid = norm_str(data.get("id")) or now_id()
    t = norm_str(str(data["time"]))[:5]

    item = {
        "id": bid,
        "name": norm_str(data.get("name")) or norm_str(c.get("nome")),
        "phone": phone9,  # ✅ 9 dígitos
        "email": norm_email(data.get("email")) or norm_email(c.get("email")),
        "service": norm_str(data.get("service")),
        "barber": norm_str(data.get("barber")),
        "date": norm_str(data.get("date")),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": norm_str(data.get("notes")),
        "status": "Marcado",
        "client_id": cid,
        "client": norm_str(c.get("nome")),
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

            # normalizar phone para 9 dígitos sempre
            if merged.get("phone"):
                merged["phone"] = normalize_phone_pt9(merged.get("phone"))

            merged = hydrate_contact_from_client(merged)

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

        if merged.get("phone"):
            merged["phone"] = normalize_phone_pt9(merged.get("phone"))

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

# =========================
# ADMIN (fica igual ao teu – cortei para não ficar gigante)
# =========================
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
