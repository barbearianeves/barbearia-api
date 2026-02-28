from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from collections import deque
import os, time, secrets, json

# ✅ email
import smtplib, ssl, socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ✅ http p/ chamar o bridge no PC
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# =========================
# CONFIG / SECRETS
# =========================
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345").strip()

# ✅ IMPORTANTE: Admin token NÃO deve ser igual ao BRIDGE_SECRET
# (mas se quiseres manter igual, mete no Render env ADMIN_TOKEN=neves-12345)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "neves-12345").strip()

# ✅ Onde está o bridge HTTP no PC (para listar clientes reais da app C)
BRIDGE_PC_BASE = (os.environ.get("BRIDGE_PC_BASE", "") or "").strip().rstrip("/")

# ✅ Email (Render envs)
FROM_EMAIL = (os.environ.get("FROM_EMAIL", "") or "").strip() or (os.environ.get("SMTP_USER", "") or "").strip()
SMTP_HOST  = (os.environ.get("SMTP_HOST", "smtp.gmail.com") or "").strip()
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER  = (os.environ.get("SMTP_USER", "") or "").strip() or FROM_EMAIL
SMTP_PASS  = (os.environ.get("SMTP_PASS", "") or "").strip()

BOOKINGS = {}                    # id -> booking dict (persistente)
CHANGES  = deque(maxlen=20000)   # eventos para bridge (memória)

# ✅ clientes (persistente) - mantém para fotos + admin
CLIENTS = {}                     # client_id -> client dict


# -------------------------
# IDS
# -------------------------
def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def now_client_id():
    return "C-" + str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(2)


# -------------------------
# Helpers
# -------------------------
def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({"op": op, "payload": payload, "ts": int(time.time())})

def is_admin(req):
    return (req.headers.get("X-Admin-Token", "") or "").strip() == ADMIN_TOKEN

def norm_phone(p: str) -> str:
    p = (p or "").strip()
    if not p: return ""
    digits = "".join([c for c in p if c.isdigit()])
    if len(digits) == 12 and digits.startswith("351"):
        digits = digits[3:]
    return digits

def norm_email(e: str) -> str:
    return (e or "").strip().lower()

def abs_url(u: str) -> str:
    """Transforma /files/... em URL absoluta com base no host atual."""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    # base do request (Render)
    base = request.host_url.rstrip("/")
    if u.startswith("/"):
        return base + u
    return base + "/" + u


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
CLIENTS_FILE  = os.path.join(DATA_DIR, "clients.json")  if DATA_DIR else None

# ✅ pasta de uploads (fotos)
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads") if DATA_DIR else None
if UPLOADS_DIR:
    os.makedirs(UPLOADS_DIR, exist_ok=True)

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

def load_clients():
    global CLIENTS
    if not CLIENTS_FILE:
        CLIENTS = {}
        return
    try:
        if os.path.exists(CLIENTS_FILE):
            with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            CLIENTS = data if isinstance(data, dict) else {}
        else:
            CLIENTS = {}
    except Exception:
        CLIENTS = {}

def save_clients():
    if not CLIENTS_FILE:
        return
    tmp = CLIENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CLIENTS, f, ensure_ascii=False)
    os.replace(tmp, CLIENTS_FILE)

load_bookings()
load_clients()

# ✅ snapshot em memória (para bridge puxar após restart)
CHANGES.clear()
for b in BOOKINGS.values():
    push_change("upsert", b)


# -------------------------
# ✅ EMAIL: SMTP por IPv4 + logs
# -------------------------
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
    to_email = (booking.get("email") or "").strip()
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
        "clients": len(CLIENTS),
        "persist": BOOKINGS_FILE or "NO_PERSIST",
        "data_dir": DATA_DIR or "NO_DATA_DIR",
        "uploads_dir": UPLOADS_DIR or "NO_UPLOADS",
        "bridge_pc_base": BRIDGE_PC_BASE or "",
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


# -------------------------
# ✅ DEBUG: ver rotas (mata o 404 fantasma)
# -------------------------
@app.get("/debug/routes")
def debug_routes():
    out = []
    for r in app.url_map.iter_rules():
        methods = sorted([m for m in r.methods if m not in ("HEAD", "OPTIONS")])
        out.append({"rule": str(r), "methods": methods, "endpoint": r.endpoint})
    out.sort(key=lambda x: x["rule"])
    return jsonify({"ok": True, "routes": out})

@app.get("/debug/client/<cid>")
def debug_client(cid):
    c = CLIENTS.get(cid)
    return jsonify({"ok": True, "item": c or None})


# -------------------------
# ✅ servir uploads por URL (a bridge vai buscar daqui)
# -------------------------
@app.get("/files/<path:filepath>")
def files(filepath):
    if not UPLOADS_DIR:
        return bad("uploads disabled", 500)
    safe = filepath.replace("..", "").lstrip("/\\")
    return send_from_directory(UPLOADS_DIR, safe, as_attachment=False)


# -------------------------
# ✅ BRIDGE: CLIENTES (para bridge puxar URLs das fotos)
# devolve SEMPRE id + photo_before_url + photo_after_url (ABSOLUTOS)
# -------------------------
@app.get("/bridge/clients")
def bridge_clients():
    secret = (request.args.get("secret") or "").strip()
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    items = []
    for cid, c in CLIENTS.items():
        before_u = (c.get("photo_before_url") or "").strip()
        after_u  = (c.get("photo_after_url") or "").strip()

        item = {
            "id": (c.get("id") or cid),
            "name": (c.get("name") or ""),
            "phone": norm_phone(c.get("phone") or ""),
            "email": norm_email(c.get("email") or ""),
            # ✅ aqui vai ABSOLUTO para a bridge não falhar
            "photo_before_url": abs_url(before_u) if before_u else "",
            "photo_after_url": abs_url(after_u) if after_u else "",
        }
        items.append(item)

    items.sort(key=lambda x: (x.get("name",""), x.get("id","")))
    return jsonify({"ok": True, "items": items})


# -------------------------
# ✅ PUBLIC: clientes (SÓ os do PC)
# -------------------------
@app.get("/public/clients")
def public_clients():
    secret = (request.args.get("secret") or "").strip()
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    if not BRIDGE_PC_BASE:
        return bad("BRIDGE_PC_BASE não definido no servidor", 500)

    try:
        url = f"{BRIDGE_PC_BASE}/clients"
        r = requests.get(url, params={"secret": BRIDGE_SECRET}, timeout=20)
        if not r.ok:
            return bad(f"bridge pc http error {r.status_code}", 502)
        data = r.json()
        if not data.get("ok"):
            return bad("bridge pc respondeu erro", 502)
        return jsonify({"ok": True, "items": data.get("items") or []})
    except Exception as e:
        return bad(f"bridge pc offline: {type(e).__name__}: {e}", 502)


# -------------------------
# ✅ ADMIN: TESTE EMAIL
# -------------------------
@app.post("/admin/test_email")
def admin_test_email():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    to_email = (data.get("to") or "").strip()
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


# -------------------------
# CLIENTE: CRIAR MARCAÇÃO
# -------------------------
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}
    required = ["name", "phone", "service", "barber", "date", "time", "dur"]
    for k in required:
        if not str(data.get(k, "")).strip():
            return bad(f"Campo obrigatório em falta: {k}")

    t = str(data["time"]).strip()[:5]
    bid = data.get("id") or now_id()

    phone = norm_phone(str(data.get("phone", "")).strip())
    email = norm_email(str(data.get("email", "")).strip())
    client_id = str(data.get("client_id", "")).strip()

    item = {
        "id": bid,
        "name": str(data.get("name", "")).strip(),
        "phone": phone,
        "email": email,
        "service": str(data.get("service", "")).strip(),
        "barber": str(data.get("barber", "")).strip(),
        "date": str(data.get("date", "")).strip(),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": str(data.get("notes", "")).strip(),
        "status": "Marcado",
        "client_id": client_id,
        "client": str(data.get("client", "")).strip(),
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)

    # ✅ se vier client_id, garante que existe um cliente
    if client_id:
        c = CLIENTS.get(client_id) or {}
        if not c.get("id"):
            c["id"] = client_id
        if item.get("name") and not c.get("name"):
            c["name"] = item["name"]
        if item.get("phone") and not c.get("phone"):
            c["phone"] = item["phone"]
        if item.get("email") and not c.get("email"):
            c["email"] = item["email"]
        c["updated_at"] = int(time.time())
        if not c.get("created_at"):
            c["created_at"] = int(time.time())
        CLIENTS[client_id] = c
        save_clients()

    return jsonify({"ok": True, "id": bid})


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
            BOOKINGS[bid] = {**BOOKINGS.get(bid, {}), **payload}
            save_bookings()
            push_change("upsert", BOOKINGS[bid])
            applied += 1

            # ✅ se bridge enviar client_id, mantém clientes em dia
            cid = (BOOKINGS[bid].get("client_id") or "").strip()
            if cid:
                c = CLIENTS.get(cid) or {"id": cid}
                if BOOKINGS[bid].get("name"):
                    c["name"] = BOOKINGS[bid]["name"]
                if BOOKINGS[bid].get("phone"):
                    c["phone"] = norm_phone(BOOKINGS[bid]["phone"])
                if BOOKINGS[bid].get("email"):
                    c["email"] = norm_email(BOOKINGS[bid]["email"])
                c["updated_at"] = int(time.time())
                if not c.get("created_at"):
                    c["created_at"] = int(time.time())
                CLIENTS[cid] = c
                save_clients()

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
    BOOKINGS = {}

    for b in items:
        bid = str(b.get("id", "")).strip()
        if not bid:
            continue
        BOOKINGS[bid] = b

        # ✅ alimentar clientes
        cid = (b.get("client_id") or "").strip()
        if cid:
            c = CLIENTS.get(cid) or {"id": cid}
            if b.get("name"):
                c["name"] = b.get("name")
            if b.get("phone"):
                c["phone"] = norm_phone(b.get("phone"))
            if b.get("email"):
                c["email"] = norm_email(b.get("email"))
            c["updated_at"] = int(time.time())
            if not c.get("created_at"):
                c["created_at"] = int(time.time())
            CLIENTS[cid] = c

    save_bookings()
    save_clients()

    CHANGES.clear()
    for b in BOOKINGS.values():
        push_change("upsert", b)

    return jsonify({"ok": True, "count": len(BOOKINGS)})


# -------------------------
# CLIENTE: VER OCUPADOS/DIA
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
# ADMIN: LISTAR BOOKINGS
# ==========================
@app.get("/admin/bookings")
def admin_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date = (request.args.get("date") or "").strip()
    barber = (request.args.get("barber") or "").strip()

    out = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        out.append(b)

    out.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
    return jsonify({"ok": True, "items": out})

@app.get("/admin/booking/<bid>")
def admin_get_booking(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)
    return jsonify({"ok": True, "item": b})


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
    date = str(data.get("date", "")).strip()
    time_ = str(data.get("time", "")).strip()[:5]
    barber = str(data.get("barber", "")).strip()

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
    new_status = (data.get("status") or "Chegou").strip()

    incoming_email = (data.get("email") or "").strip()
    if incoming_email:
        BOOKINGS[bid]["email"] = incoming_email

    BOOKINGS[bid]["status"] = new_status
    save_bookings()
    push_change("upsert", BOOKINGS[bid])

    ok, msg_email = send_validation_email(BOOKINGS[bid])

    return jsonify({
        "ok": True,
        "status": new_status,
        "email_sent": ok,
        "message": msg_email
    })


# ==========================
# ✅ CLIENTES (ADMIN)
# ==========================
@app.get("/admin/clients")
def admin_clients_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    q = (request.args.get("q") or "").strip().lower()
    items = list(CLIENTS.values())

    if q:
        def hit(c):
            return (q in (c.get("id","").lower())
                or q in (c.get("name","").lower())
                or q in (c.get("phone","").lower())
                or q in (c.get("email","").lower()))
        items = [c for c in items if hit(c)]

    items.sort(key=lambda x: (x.get("name",""), x.get("phone",""), x.get("id","")))
    return jsonify({"ok": True, "items": items})

@app.get("/admin/client/<cid>")
def admin_client_get(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    c = CLIENTS.get(cid)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "item": c})

@app.post("/admin/clients")
def admin_clients_upsert():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    cid = (data.get("id") or "").strip() or now_client_id()

    c = CLIENTS.get(cid) or {"id": cid, "created_at": int(time.time())}
    name  = (data.get("name") or "").strip()
    phone = norm_phone(data.get("phone") or "")
    email = norm_email(data.get("email") or "")

    if name:  c["name"] = name
    if phone: c["phone"] = phone
    if email: c["email"] = email
    if "notes" in data and str(data.get("notes") or "").strip():
        c["notes"] = str(data.get("notes") or "").strip()

    c["updated_at"] = int(time.time())

    # permitir set direto (opcional)
    if data.get("photo_before_url") is not None:
        c["photo_before_url"] = str(data.get("photo_before_url") or "").strip()
    if data.get("photo_after_url") is not None:
        c["photo_after_url"] = str(data.get("photo_after_url") or "").strip()

    CLIENTS[cid] = c
    save_clients()
    return jsonify({"ok": True, "id": cid, "item": c})


# -------------------------
# ✅ ADMIN: upload de foto (gera /files/<cid>/before-xxx.jpg)
# -------------------------
@app.post("/admin/client/<cid>/photo")
def admin_client_upload_photo(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if not UPLOADS_DIR:
        return bad("uploads disabled", 500)

    kind = (request.form.get("kind") or "before").strip().lower()
    if kind not in ["before", "after"]:
        return bad("kind inválido (before|after)")

    if "file" not in request.files:
        return bad("Falta ficheiro: file")

    f = request.files["file"]
    if not f or not f.filename:
        return bad("Ficheiro inválido")

    c = CLIENTS.get(cid) or {"id": cid, "created_at": int(time.time())}

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"]:
        ext = ".jpg"

    client_dir = os.path.join(UPLOADS_DIR, cid)
    os.makedirs(client_dir, exist_ok=True)

    ts = str(int(time.time()))
    filename = f"{kind}-{ts}{ext}"
    save_path = os.path.join(client_dir, filename)
    f.save(save_path)

    url_path = f"{cid}/{filename}"
    url = f"/files/{url_path}"

    if kind == "before":
        c["photo_before_url"] = url
        c["photo_before_at"] = int(time.time())
    else:
        c["photo_after_url"] = url
        c["photo_after_at"] = int(time.time())

    note = (request.form.get("note") or "").strip()
    if note:
        c.setdefault("photo_notes", [])
        c["photo_notes"].append({"kind": kind, "note": note, "ts": int(time.time())})

    c["updated_at"] = int(time.time())
    CLIENTS[cid] = c
    save_clients()

    return jsonify({"ok": True, "url": url, "item": c})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
