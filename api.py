# api.py — Barbearia API (bookings + clientes + fotos)
# Start (Render):
#   gunicorn -w 1 api:app --bind 0.0.0.0:$PORT

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from collections import deque
from datetime import date
import os, time, secrets, json, shutil, re, unicodedata

import smtplib, ssl, socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, allow_headers=["Content-Type", "X-Admin-Token"])

# =========================
# CONFIG / SECRETS
# =========================
BRIDGE_SECRET = (os.environ.get("BRIDGE_SECRET", "neves-12345") or "").strip()
ADMIN_TOKEN   = (os.environ.get("ADMIN_TOKEN", "neves-12345") or "").strip()
BRIDGE_PC_BASE = (os.environ.get("BRIDGE_PC_BASE", "") or "").strip().rstrip("/")

FROM_EMAIL = (os.environ.get("FROM_EMAIL", "") or "").strip() or (os.environ.get("SMTP_USER", "") or "").strip()
SMTP_HOST  = (os.environ.get("SMTP_HOST", "smtp.gmail.com") or "").strip()
SMTP_PORT  = int((os.environ.get("SMTP_PORT", "587") or "587").strip())
SMTP_USER  = (os.environ.get("SMTP_USER", "") or "").strip() or FROM_EMAIL
SMTP_PASS  = (os.environ.get("SMTP_PASS", "") or "").strip()

BOOKINGS = {}
CHANGES  = deque(maxlen=20000)

CLIENTS = {}
CLIENT_CHANGES = deque(maxlen=20000)

# =========================
# HELPERS GERAIS
# =========================
def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({"op": op, "payload": payload, "ts": int(time.time())})

def push_client_change(op, payload):
    CLIENT_CHANGES.append({"op": op, "payload": payload, "ts": int(time.time())})

def is_admin(req):
    return (req.headers.get("X-Admin-Token", "") or "").strip() == ADMIN_TOKEN

def clean_str(v) -> str:
    return str(v or "").replace("\r", "").replace("\n", "").strip()

def norm_phone(p: str) -> str:
    p = clean_str(p)
    if not p:
        return ""
    digits = re.sub(r"\D+", "", p)
    if len(digits) == 12 and digits.startswith("351"):
        digits = digits[3:]
    return digits

def valid_pt_mobile_phone(p: str) -> bool:
    p = norm_phone(p)
    return bool(re.fullmatch(r"9\d{8}", p))

def norm_email(e: str) -> str:
    return clean_str(e).lower()

def norm_client_id(cid: str) -> str:
    s = clean_str(cid)
    if not s:
        return ""
    if s.isdigit():
        n = int(s)
        return str(n) if n > 0 else ""
    return ""

def norm_name(n: str) -> str:
    s = clean_str(n).lower()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip()
    return s

def same_person_name(a: str, b: str) -> bool:
    na = norm_name(a)
    nb = norm_name(b)
    if not na or not nb:
        return False
    return na == nb

def merge_non_empty(dst: dict, src: dict):
    for k, v in (src or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and not clean_str(v):
            continue
        dst[k] = v
    return dst

def _guess_base_url():
    base = clean_str(os.environ.get("RENDER_EXTERNAL_URL", "") or "")
    return base.rstrip("/") if base else ""

def abs_url(u: str) -> str:
    u = clean_str(u)
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    try:
        base = request.host_url.rstrip("/")
    except Exception:
        base = _guess_base_url()
    if not base:
        return u
    if u.startswith("/"):
        return base + u
    return base + "/" + u

# =========================
# STORAGE
# =========================
def pick_data_dir():
    cand = clean_str(os.environ.get("DATA_DIR", "") or "")
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
CLIENTS_FILE  = os.path.join(DATA_DIR, "clients.json") if DATA_DIR else None
CLIENT_ID_COUNTER_FILE = os.path.join(DATA_DIR, "client_id_counter.json") if DATA_DIR else None
RESET_CLIENTS_STATE_FILE = os.path.join(DATA_DIR, "reset_clients_state.json") if DATA_DIR else None

ALLOW_MULTI_RESET_SAME_DAY = int((os.environ.get("ALLOW_MULTI_RESET_SAME_DAY", "0") or "0"))

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

def _load_counter():
    if not CLIENT_ID_COUNTER_FILE:
        return {"next": 1}
    try:
        if os.path.exists(CLIENT_ID_COUNTER_FILE):
            with open(CLIENT_ID_COUNTER_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("next"), int) and d["next"] >= 1:
                return d
    except Exception:
        pass
    return {"next": 1}

def _save_counter(counter):
    if not CLIENT_ID_COUNTER_FILE:
        return
    tmp = CLIENT_ID_COUNTER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(counter, f, ensure_ascii=False)
    os.replace(tmp, CLIENT_ID_COUNTER_FILE)

def _get_max_client_id() -> int:
    max_id = 0
    for cid in CLIENTS.keys():
        s = clean_str(cid)
        if s.isdigit():
            try:
                max_id = max(max_id, int(s))
            except Exception:
                pass
    return max_id

def _recalc_counter_from_clients():
    max_id = _get_max_client_id()
    counter = _load_counter()
    next_id = int(counter.get("next", 1) or 1)
    safe_next = max(max_id + 1, next_id, 1)
    counter["next"] = safe_next
    _save_counter(counter)
    return counter

def _today_iso():
    return date.today().isoformat()

def _load_reset_state():
    if not RESET_CLIENTS_STATE_FILE:
        return {"last_reset_date": ""}
    try:
        if os.path.exists(RESET_CLIENTS_STATE_FILE):
            with open(RESET_CLIENTS_STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return {
                    "last_reset_date": clean_str(d.get("last_reset_date") or "")
                }
    except Exception:
        pass
    return {"last_reset_date": ""}

def _save_reset_state(state: dict):
    if not RESET_CLIENTS_STATE_FILE:
        return
    tmp = RESET_CLIENTS_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, RESET_CLIENTS_STATE_FILE)

def _can_reset_clients_today():
    st = _load_reset_state()
    today = _today_iso()
    last = clean_str(st.get("last_reset_date") or "")
    return last != today, last, today

COUNTER = None

load_bookings()
load_clients()
COUNTER = _recalc_counter_from_clients()

CHANGES.clear()
for b in BOOKINGS.values():
    push_change("upsert", b)

CLIENT_CHANGES.clear()
for c in CLIENTS.values():
    push_client_change("upsert", c)

# =========================
# EMAIL
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
    to_email = clean_str(booking.get("email") or "")
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
        print(f"[EMAIL] ERRO: {type(e).__name__}: {e}", flush=True)
        return False, f"{type(e).__name__}: {e}"

# =========================
# CLIENTES
# =========================
def _next_client_id_str():
    global COUNTER

    max_id = _get_max_client_id()

    if COUNTER is None:
        COUNTER = _load_counter()

    current_next = int(COUNTER.get("next", 1) or 1)
    new_id = max(max_id + 1, current_next, 1)

    COUNTER["next"] = new_id + 1
    _save_counter(COUNTER)
    return str(new_id)

def _find_client_by_phone(phone: str) -> str:
    p = norm_phone(phone or "")
    if not p:
        return ""
    for cid, c in CLIENTS.items():
        if norm_phone(c.get("phone") or "") == p:
            return str(cid)
    return ""

def _safe_set_contact_field(dst: dict, field: str, new_value: str):
    nv = clean_str(new_value or "")
    if field == "phone":
        nv = norm_phone(nv)
    elif field == "email":
        nv = norm_email(nv)

    if not nv:
        return

    cur = clean_str(dst.get(field) or "")
    if field == "phone":
        cur = norm_phone(cur)
    elif field == "email":
        cur = norm_email(cur)

    if not cur:
        dst[field] = nv
        return

    if cur == nv:
        dst[field] = nv
        return

def _safe_set_name(dst: dict, new_name: str):
    nn = clean_str(new_name or "")
    if not nn:
        return

    cur = clean_str(dst.get("name") or "")
    if not cur:
        dst["name"] = nn
        return

    if same_person_name(cur, nn):
        dst["name"] = nn

def _find_client_match_for_public_booking(name: str, phone: str, email: str) -> str:
    """
    Regras seguras:
    - telefone exato => match
    - nome sozinho => nunca
    - email sozinho => nunca
    """
    p = norm_phone(phone)
    if p and valid_pt_mobile_phone(p):
        hit = _find_client_by_phone(p)
        if hit:
            return hit
    return ""

def ensure_client_basic(name: str, phone: str, email: str, client_id: str = "", source: str = "") -> str:
    """
    Regras:
    - telefone é obrigatório e é a chave principal
    - se vier client_id válido -> usa esse cliente
    - sem client_id -> tenta match APENAS por telefone válido
    - nunca faz match por nome
    - nunca faz match por email
    - nunca substitui telefone existente por outro diferente
    """
    cid = norm_client_id(client_id)
    source = clean_str(source)

    incoming_name = clean_str(name)
    incoming_phone = norm_phone(phone or "")
    incoming_email = norm_email(email or "")

    if not valid_pt_mobile_phone(incoming_phone):
        raise ValueError("telefone inválido: deve ter 9 dígitos e começar por 9")

    if cid and cid in CLIENTS:
        c = CLIENTS.get(cid) or {"id": cid, "created_at": int(time.time())}
    else:
        cid = ""

        if source == "public_web":
            hit = _find_client_match_for_public_booking(incoming_name, incoming_phone, incoming_email)
        else:
            hit = _find_client_by_phone(incoming_phone)

        hit = norm_client_id(hit)
        if hit and hit in CLIENTS:
            cid = hit
            c = CLIENTS.get(cid) or {"id": cid, "created_at": int(time.time())}
        else:
            cid = _next_client_id_str()
            c = {"id": cid, "created_at": int(time.time())}

    c["id"] = cid
    _safe_set_name(c, incoming_name)
    _safe_set_contact_field(c, "phone", incoming_phone)
    _safe_set_contact_field(c, "email", incoming_email)
    c["updated_at"] = int(time.time())

    CLIENTS[cid] = c
    save_clients()
    _recalc_counter_from_clients()
    push_client_change("upsert", c)
    return cid

# =========================
# HOME / DEBUG
# =========================
@app.get("/")
def home():
    try:
        rules_count = len(list(app.url_map.iter_rules()))
    except Exception:
        rules_count = -1

    counter = _load_counter()

    return jsonify({
        "ok": True,
        "service": "barbearia-api",
        "app_file": __file__,
        "routes_count": rules_count,
        "bookings": len(BOOKINGS),
        "changes": len(CHANGES),
        "clients": len(CLIENTS),
        "client_changes": len(CLIENT_CHANGES),
        "client_id_next": counter.get("next", None),
        "persist": BOOKINGS_FILE or "NO_PERSIST",
        "data_dir": DATA_DIR or "NO_DATA_DIR",
        "uploads_dir": UPLOADS_DIR or "NO_UPLOADS",
        "bridge_pc_base": BRIDGE_PC_BASE or "",
        "render_external_url": clean_str(os.environ.get("RENDER_EXTERNAL_URL","") or ""),
        "allow_multi_reset_same_day": bool(ALLOW_MULTI_RESET_SAME_DAY),
        "smtp": {
            "from": FROM_EMAIL,
            "host": SMTP_HOST,
            "port": SMTP_PORT,
            "user_set": bool(SMTP_USER),
            "pass_set": bool(SMTP_PASS),
        }
    })

@app.get("/health")
def health():
    return jsonify({"ok": True})

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
    c = CLIENTS.get(norm_client_id(cid))
    return jsonify({"ok": True, "item": c or None})

@app.get("/debug/clients_raw")
def debug_clients_raw():
    out = list(CLIENTS.values())

    def _k(x):
        sid = clean_str(x.get("id",""))
        return (clean_str(x.get("name","")).lower(), int(sid) if sid.isdigit() else 10**18, sid)

    out.sort(key=_k)
    return jsonify({"ok": True, "count": len(out), "items": out})

# =========================
# FILES
# =========================
@app.get("/files/<path:filepath>")
def files(filepath):
    if not UPLOADS_DIR:
        return bad("uploads disabled", 500)
    safe = filepath.replace("..", "").lstrip("/\\")
    return send_from_directory(UPLOADS_DIR, safe, as_attachment=False)

# =========================
# BRIDGE / PUBLIC CLIENTS
# =========================
@app.get("/bridge/clients")
def bridge_clients():
    secret = clean_str(request.args.get("secret") or "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    items = []
    for cid, c in CLIENTS.items():
        before_u = clean_str(c.get("photo_before_url") or "")
        after_u  = clean_str(c.get("photo_after_url") or "")

        items.append({
            "id": str(c.get("id") or cid),
            "name": clean_str(c.get("name") or ""),
            "phone": norm_phone(c.get("phone") or ""),
            "email": norm_email(c.get("email") or ""),
            "profession": clean_str(c.get("profession") or ""),
            "age": clean_str(c.get("age") or ""),
            "notes": clean_str(c.get("notes") or ""),
            "photo_before_url": abs_url(before_u) if before_u else "",
            "photo_after_url": abs_url(after_u) if after_u else "",
            "updated_at": int(c.get("updated_at") or 0),
            "created_at": int(c.get("created_at") or 0),
        })

    def _sid(x):
        s = clean_str(x.get("id",""))
        return int(s) if s.isdigit() else 10**18

    items.sort(key=lambda x: (x.get("name",""), _sid(x), str(x.get("id",""))))
    return jsonify({"ok": True, "items": items})

@app.get("/bridge/clients/pull")
def bridge_clients_pull():
    secret = clean_str(request.args.get("secret", ""))
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    cursor = int(request.args.get("cursor", "0"))
    limit  = int(request.args.get("limit", "200"))

    changes_list = list(CLIENT_CHANGES)
    out = changes_list[cursor: cursor + limit]
    new_cursor = min(cursor + len(out), len(changes_list))
    return jsonify({"ok": True, "cursor": new_cursor, "items": out})

@app.post("/bridge/clients/sync")
def bridge_clients_sync():
    secret = clean_str(request.args.get("secret", ""))
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes", [])
    if not isinstance(changes, list):
        return bad("changes inválido")

    applied = 0

    for ch in changes:
        op = clean_str(ch.get("op") or "")
        payload = ch.get("payload") or {}
        cid = norm_client_id(payload.get("id") or "")
        if not cid:
            continue

        if op == "delete":
            existed = CLIENTS.pop(cid, None)

            if UPLOADS_DIR:
                p = os.path.join(UPLOADS_DIR, cid)
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                except Exception:
                    pass

            if existed is not None:
                save_clients()
                push_client_change("delete", {"id": cid})
            applied += 1

        elif op == "upsert":
            current = CLIENTS.get(cid, {})
            merged = {**current, **payload}
            merged["id"] = cid
            merged["phone"] = norm_phone(merged.get("phone") or "")
            merged["email"] = norm_email(merged.get("email") or "")
            merged["updated_at"] = int(merged.get("updated_at") or time.time())

            CLIENTS[cid] = merged
            save_clients()
            _recalc_counter_from_clients()
            push_client_change("upsert", merged)
            applied += 1

    return jsonify({"ok": True, "applied": applied})

@app.post("/bridge/clients/replace_all")
def bridge_clients_replace_all():
    secret = clean_str(request.args.get("secret", ""))
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return bad("items inválido")

    global CLIENTS
    new_clients = {}

    for c in items:
        cid = norm_client_id(c.get("id") or "")
        if not cid:
            continue
        c["id"] = cid
        c["phone"] = norm_phone(c.get("phone") or "")
        c["email"] = norm_email(c.get("email") or "")
        new_clients[cid] = c

    CLIENTS = new_clients
    save_clients()
    _recalc_counter_from_clients()

    CLIENT_CHANGES.clear()
    for c in CLIENTS.values():
        push_client_change("upsert", c)

    return jsonify({"ok": True, "count": len(CLIENTS)})

@app.get("/public/clients")
def public_clients():
    secret = clean_str(request.args.get("secret") or "")
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

# =========================
# EMAIL TEST
# =========================
@app.post("/admin/test_email")
def admin_test_email():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    to_email = clean_str(data.get("to") or "")
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
# BOOK
# =========================
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}
    required = ["name", "phone", "service", "barber", "date", "time", "dur"]
    for k in required:
        if not clean_str(data.get(k, "")):
            return bad(f"Campo obrigatório em falta: {k}")

    t = clean_str(data["time"])[:5]
    bid = clean_str(data.get("id") or "") or now_id()

    name = clean_str(data.get("name", ""))
    phone = norm_phone(data.get("phone", ""))
    email = norm_email(data.get("email", ""))
    incoming_client_id = norm_client_id(data.get("client_id", ""))
    created_via = clean_str(data.get("created_via", ""))

    if not valid_pt_mobile_phone(phone):
        return bad("Telefone inválido: deve ter 9 dígitos e começar por 9", 400)

    try:
        if incoming_client_id and incoming_client_id in CLIENTS:
            client_id = incoming_client_id
            c = CLIENTS.get(client_id) or {}
            if norm_phone(c.get("phone") or "") != phone:
                return bad("Telefone não corresponde ao cliente indicado", 409)
        else:
            client_id = ensure_client_basic(
                name=name,
                phone=phone,
                email=email,
                client_id="",
                source=created_via
            )
            c = CLIENTS.get(client_id) or {}
    except ValueError as e:
        return bad(str(e), 400)

    booking_name = clean_str(c.get("name") or name)
    booking_phone = norm_phone(c.get("phone") or phone)
    booking_email = norm_email(c.get("email") or email)

    item = {
        "id": bid,
        "name": booking_name,
        "phone": booking_phone,
        "email": booking_email,
        "service": clean_str(data.get("service", "")),
        "barber": clean_str(data.get("barber", "")),
        "date": clean_str(data.get("date", "")),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": clean_str(data.get("notes", "")),
        "status": "Marcado",
        "client_id": client_id,
        "client": booking_name,
        "created_at": int(time.time()),
        "created_by": clean_str(data.get("created_by", "")),
        "created_via": created_via,
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)

    return jsonify({"ok": True, "id": bid, "client_id": client_id, "item": item})

# =========================
# CLIENTE: VER / CANCELAR
# =========================
@app.get("/my-bookings")
def my_bookings():
    phone = norm_phone(request.args.get("phone", ""))
    if not phone:
        return bad("Telefone em falta")
    if not valid_pt_mobile_phone(phone):
        return bad("Telefone inválido")

    today_iso = date.today().isoformat()
    out = []

    for b in BOOKINGS.values():
        if norm_phone(b.get("phone", "")) != phone:
            continue

        bdate = clean_str(b.get("date", ""))
        if bdate and bdate < today_iso:
            continue

        out.append({
            "id": clean_str(b.get("id", "")),
            "date": clean_str(b.get("date", "")),
            "time": clean_str(b.get("time", "")),
            "dur": b.get("dur", 45),
            "name": clean_str(b.get("name", "")),
            "phone": norm_phone(b.get("phone", "")),
            "email": norm_email(b.get("email", "")),
            "barber": clean_str(b.get("barber", "")),
            "service": clean_str(b.get("service", "")),
            "status": clean_str(b.get("status", "Marcado")),
            "notes": clean_str(b.get("notes", "")),
            "client_id": norm_client_id(b.get("client_id", "")),
            "client": clean_str(b.get("client", "")),
            "created_by": clean_str(b.get("created_by", "")),
            "created_via": clean_str(b.get("created_via", "")),
        })

    out.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
    return jsonify({"ok": True, "items": out})

@app.post("/cancel-booking")
def cancel_booking():
    data = request.get_json(silent=True) or {}

    bid = clean_str(data.get("id", ""))
    phone = norm_phone(data.get("phone", ""))

    if not bid:
        return bad("ID em falta")
    if not phone:
        return bad("Telefone em falta")
    if not valid_pt_mobile_phone(phone):
        return bad("Telefone inválido")

    b = BOOKINGS.get(bid)
    if not b:
        return bad("Marcação não encontrada", 404)

    if norm_phone(b.get("phone", "")) != phone:
        return bad("Telefone não corresponde à marcação", 403)

    if clean_str(b.get("status", "")) == "Cancelado":
        return jsonify({"ok": True, "id": bid, "status": "Cancelado"})

    BOOKINGS[bid]["status"] = "Cancelado"
    BOOKINGS[bid]["cancelled_at"] = int(time.time())

    save_bookings()
    push_change("upsert", BOOKINGS[bid])

    return jsonify({"ok": True, "id": bid, "status": "Cancelado"})

# =========================
# BRIDGE SYNC
# =========================
@app.get("/pull")
def pull():
    secret = clean_str(request.args.get("secret", ""))
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
    secret = clean_str(request.args.get("secret", ""))
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
        bid = clean_str(payload.get("id") or "")
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
            current = BOOKINGS.get(bid, {})
            merged = {**current, **payload}

            cid = norm_client_id(merged.get("client_id") or "")
            merged["client_id"] = cid

            if cid and cid in CLIENTS:
                cc = CLIENTS.get(cid) or {}
                if clean_str(cc.get("name") or ""):
                    merged["name"] = clean_str(cc.get("name") or merged.get("name") or "")
                    merged["client"] = clean_str(cc.get("name") or "")
                if clean_str(cc.get("phone") or ""):
                    merged["phone"] = norm_phone(cc.get("phone") or "")
                if clean_str(cc.get("email") or ""):
                    merged["email"] = norm_email(cc.get("email") or "")

            BOOKINGS[bid] = merged
            save_bookings()
            push_change("upsert", BOOKINGS[bid])
            applied += 1

    return jsonify({"ok": True, "applied": applied})

@app.post("/replace_all")
def replace_all():
    secret = clean_str(request.args.get("secret", ""))
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return bad("items inválido")

    global BOOKINGS
    BOOKINGS = {}

    for b in items:
        bid = clean_str(b.get("id", ""))
        if not bid:
            continue

        cid = norm_client_id(b.get("client_id") or "")
        b["client_id"] = cid

        if cid and cid in CLIENTS:
            cc = CLIENTS.get(cid) or {}
            if clean_str(cc.get("name") or ""):
                b["name"] = clean_str(cc.get("name") or b.get("name") or "")
                b["client"] = clean_str(cc.get("name") or "")
            else:
                b["client"] = clean_str(b.get("client") or "")
            if clean_str(cc.get("phone") or ""):
                b["phone"] = norm_phone(cc.get("phone") or "")
            if clean_str(cc.get("email") or ""):
                b["email"] = norm_email(cc.get("email") or "")
        else:
            b["client"] = clean_str(b.get("client") or "")

        BOOKINGS[bid] = b

    save_bookings()
    save_clients()

    CHANGES.clear()
    for b in BOOKINGS.values():
        push_change("upsert", b)

    return jsonify({"ok": True, "count": len(BOOKINGS)})

# =========================
# DAY / BUSY
# =========================
def _day_items_for_clients(date_: str = "", barber: str = ""):
    out = []
    for b in BOOKINGS.values():
        if date_ and b.get("date") != date_:
            continue
        if barber and b.get("barber") != barber:
            continue

        st = b.get("status", "Marcado")
        if st in ["Cancelado", "Desbloqueado"]:
            continue

        is_block = (st == "Bloqueado") or (b.get("service") == "INDISPONIVEL")
        out.append({
            "id": clean_str(b.get("id", "")),
            "time": clean_str(b.get("time", "")),
            "dur": b.get("dur", 30),
            "status": clean_str(st),
            "label": "INDISPONÍVEL" if is_block else "OCUPADO",
            "service": clean_str(b.get("service", "")),
            "notes": clean_str(b.get("notes", "")),
            "name": clean_str(b.get("name", "")),
            "client": clean_str(b.get("client", "")),
            "phone": norm_phone(b.get("phone", "")),
            "email": norm_email(b.get("email", "")),
            "client_id": norm_client_id(b.get("client_id", "")),
            "created_by": clean_str(b.get("created_by", "")),
            "created_via": clean_str(b.get("created_via", "")),
        })
    return out

@app.get("/day")
def day():
    date_ = clean_str(request.args.get("date", ""))
    barber = clean_str(request.args.get("barber", ""))
    return jsonify({"ok": True, "items": _day_items_for_clients(date_, barber)})

@app.get("/busy")
def busy():
    date_ = clean_str(request.args.get("date", ""))
    barber = clean_str(request.args.get("barber", ""))
    return jsonify({"ok": True, "items": _day_items_for_clients(date_, barber)})

# =========================
# ADMIN BOOKINGS
# =========================
@app.get("/admin/bookings")
def admin_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date_ = clean_str(request.args.get("date") or "")
    barber = clean_str(request.args.get("barber") or "")

    out = []
    for b in BOOKINGS.values():
        if date_ and b.get("date") != date_:
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

@app.post("/admin/block")
def admin_block():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    date_ = clean_str(data.get("date", ""))
    time_ = clean_str(data.get("time", ""))[:5]
    barber = clean_str(data.get("barber", ""))

    if not date_ or not time_ or not barber:
        return bad("Campos obrigatórios: date, time, barber")

    bid = now_id()
    item = {
        "id": bid,
        "name": "INDISPONÍVEL",
        "phone": "",
        "email": "",
        "service": "INDISPONIVEL",
        "barber": barber,
        "date": date_,
        "time": time_,
        "dur": 45,
        "notes": "",
        "status": "Bloqueado",
        "client_id": "",
        "client": "INDISPONÍVEL",
        "created_at": int(time.time()),
        "created_by": "",
        "created_via": "",
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})

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

@app.post("/admin/validate_and_email/<bid>")
def admin_validate_and_email(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    new_status = clean_str(data.get("status") or "Chegou")

    incoming_email = clean_str(data.get("email") or "")
    if incoming_email:
        BOOKINGS[bid]["email"] = norm_email(incoming_email)

    BOOKINGS[bid]["status"] = new_status
    save_bookings()
    push_change("upsert", BOOKINGS[bid])

    ok, msg_email = send_validation_email(BOOKINGS[bid])

    return jsonify({
        "ok": True,
        "status": new_status,
        "email_sent": ok,
        "message": msg_email,
    })

@app.post("/admin/booking/<bid>/link-client")
def admin_link_client(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    cid = norm_client_id(data.get("client_id") or "")
    if not cid or cid not in CLIENTS:
        return bad("cliente não encontrado", 404)

    current_phone = norm_phone(BOOKINGS[bid].get("phone") or "")
    client_phone = norm_phone(CLIENTS[cid].get("phone") or "")

    if current_phone and client_phone and current_phone != client_phone:
        return bad("não é possível ligar booking a cliente com telefone diferente", 409)

    BOOKINGS[bid]["client_id"] = cid

    c = CLIENTS.get(cid) or {}
    if clean_str(c.get("name") or ""):
        BOOKINGS[bid]["client"] = clean_str(c.get("name") or "")
        BOOKINGS[bid]["name"] = clean_str(c.get("name") or "")
    if clean_str(c.get("phone") or ""):
        BOOKINGS[bid]["phone"] = norm_phone(c.get("phone") or "")
    if clean_str(c.get("email") or ""):
        BOOKINGS[bid]["email"] = norm_email(c.get("email") or "")

    save_bookings()
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True, "item": BOOKINGS[bid]})

# =========================
# ADMIN CLIENTS
# =========================
@app.get("/admin/clients")
def admin_clients_list():
    if not is_admin(request):
        return bad("unauthorized", 401)

    q = clean_str(request.args.get("q") or "").lower()
    items = list(CLIENTS.values())

    if q:
        def hit(c):
            return (
                q in str(c.get("id","")).lower()
                or q in clean_str(c.get("name","")).lower()
                or q in clean_str(c.get("phone","")).lower()
                or q in clean_str(c.get("email","")).lower()
            )
        items = [c for c in items if hit(c)]

    def _k(x):
        sid = clean_str(x.get("id",""))
        return (clean_str(x.get("name","")).lower(), int(sid) if sid.isdigit() else 10**18, sid)

    items.sort(key=_k)
    return jsonify({"ok": True, "count": len(items), "items": items})

@app.get("/admin/client/<cid>")
def admin_client_get(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    cid = norm_client_id(cid)
    c = CLIENTS.get(cid)
    if not c:
        return bad("not found", 404)
    return jsonify({"ok": True, "item": c})

@app.get("/admin/reset_clients_status")
def admin_reset_clients_status():
    if not is_admin(request):
        return bad("unauthorized", 401)

    allowed, last_reset_date, today = _can_reset_clients_today()

    return jsonify({
        "ok": True,
        "allowed_today": bool(allowed or ALLOW_MULTI_RESET_SAME_DAY),
        "last_reset_date": last_reset_date,
        "today": today,
        "override_enabled": bool(ALLOW_MULTI_RESET_SAME_DAY),
        "clients_count": len(CLIENTS),
    })

@app.post("/admin/reset_clients")
def admin_reset_clients():
    if not is_admin(request):
        return bad("unauthorized", 401)

    allowed, last_reset_date, today = _can_reset_clients_today()
    if not allowed and not ALLOW_MULTI_RESET_SAME_DAY:
        return jsonify({
            "ok": False,
            "error": "reset_clients já foi usado hoje",
            "last_reset_date": last_reset_date,
            "today": today,
        }), 429

    data = request.get_json(silent=True) or {}
    unlink_bookings = bool(data.get("unlink_bookings", False))
    delete_uploads = bool(data.get("delete_uploads", False))

    old_client_ids = list(CLIENTS.keys())
    deleted_clients = len(CLIENTS)
    unlinked = 0

    CLIENTS.clear()

    if unlink_bookings:
        for bid, b in BOOKINGS.items():
            if norm_client_id(b.get("client_id") or ""):
                b["client_id"] = ""
                b["client"] = ""
                unlinked += 1
                push_change("upsert", b)
        save_bookings()

    if delete_uploads and UPLOADS_DIR:
        try:
            if os.path.isdir(UPLOADS_DIR):
                shutil.rmtree(UPLOADS_DIR, ignore_errors=True)
            os.makedirs(UPLOADS_DIR, exist_ok=True)
        except Exception:
            pass

    save_clients()

    for cid in old_client_ids:
        push_client_change("delete", {"id": cid})

    global COUNTER
    COUNTER = {"next": 1}
    _save_counter(COUNTER)

    _save_reset_state({
        "last_reset_date": today
    })

    return jsonify({
        "ok": True,
        "deleted_clients": deleted_clients,
        "bookings_unlinked": unlinked,
        "uploads_reset": delete_uploads,
        "next_client_id": 1,
        "reset_date": today,
        "override_enabled": bool(ALLOW_MULTI_RESET_SAME_DAY),
    })

@app.post("/admin/clients")
def admin_clients_upsert():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    incoming_id = norm_client_id(data.get("id") or "")

    name  = clean_str(data.get("name") or "")
    phone = norm_phone(data.get("phone") or "")
    email = norm_email(data.get("email") or "")
    profession = clean_str(data.get("profession") or "")
    age = clean_str(data.get("age") or "")
    notes = clean_str(data.get("notes") or "")

    if not phone:
        return bad("telefone é obrigatório", 400)

    if not valid_pt_mobile_phone(phone):
        return bad("telefone inválido: deve ter 9 dígitos e começar por 9", 400)

    created = False

    if incoming_id:
        cid = incoming_id
        c = CLIENTS.get(cid)

        if not c:
            other_cid = _find_client_by_phone(phone)
            if other_cid and other_cid != cid:
                return bad("já existe outro cliente com esse telefone", 409)

            c = {"id": cid, "created_at": int(time.time())}
            created = True
        else:
            existing_phone = norm_phone(c.get("phone") or "")
            if existing_phone and existing_phone != phone:
                other_cid = _find_client_by_phone(phone)
                if other_cid and other_cid != cid:
                    return bad("já existe outro cliente com esse telefone", 409)
    else:
        dup_id = _find_client_by_phone(phone)
        if dup_id:
            existing = CLIENTS.get(dup_id) or {}
            return jsonify({
                "ok": False,
                "error": "cliente duplicado",
                "duplicate": True,
                "id": dup_id,
                "item": existing,
            }), 409

        cid = _next_client_id_str()
        c = {"id": cid, "created_at": int(time.time())}
        created = True

    c["id"] = cid
    _safe_set_name(c, name)
    _safe_set_contact_field(c, "phone", phone)
    _safe_set_contact_field(c, "email", email)

    if profession:
        c["profession"] = profession
    if age:
        c["age"] = age
    if notes:
        c["notes"] = notes

    if data.get("photo_before_url") is not None:
        c["photo_before_url"] = clean_str(data.get("photo_before_url") or "")

    if data.get("photo_after_url") is not None:
        c["photo_after_url"] = clean_str(data.get("photo_after_url") or "")

    c["updated_at"] = int(time.time())
    CLIENTS[cid] = c
    save_clients()

    _recalc_counter_from_clients()
    push_client_change("upsert", c)

    return jsonify({"ok": True, "id": cid, "created": created, "item": c})

def _delete_client_internal(cid: str):
    existed = CLIENTS.pop(cid, None)

    if UPLOADS_DIR:
        p = os.path.join(UPLOADS_DIR, cid)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass

    changed = 0
    for bid, b in BOOKINGS.items():
        if norm_client_id(b.get("client_id") or "") == cid:
            b["client_id"] = ""
            b["client"] = ""
            changed += 1
            push_change("upsert", b)

    if existed is not None:
        save_clients()
        push_client_change("delete", {"id": cid})
    if changed:
        save_bookings()

    return existed, changed

@app.delete("/admin/client/<cid>")
def admin_client_delete(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    cid = norm_client_id(cid)
    if not cid or cid not in CLIENTS:
        return bad("not found", 404)

    existed, changed = _delete_client_internal(cid)
    return jsonify({"ok": True, "deleted": cid, "bookings_unlinked": changed, "item": existed})

@app.post("/admin/client/<cid>/delete")
def admin_client_delete_post(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    cid = norm_client_id(cid)
    if not cid or cid not in CLIENTS:
        return bad("not found", 404)

    existed, changed = _delete_client_internal(cid)
    return jsonify({"ok": True, "deleted": cid, "bookings_unlinked": changed, "item": existed})

@app.post("/admin/client/<cid>/photo")
def admin_client_upload_photo(cid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if not UPLOADS_DIR:
        return bad("uploads disabled", 500)

    cid = norm_client_id(cid)
    if not cid:
        return bad("client id inválido", 400)

    kind = clean_str(request.form.get("kind") or "before").lower()
    if kind not in ["before", "after"]:
        return bad("kind inválido (before|after)")

    if "file" not in request.files:
        return bad("Falta ficheiro: file")

    f = request.files["file"]
    if not f or not f.filename:
        return bad("Ficheiro inválido")

    c = CLIENTS.get(cid)
    if not c:
        return bad("cliente não encontrado", 404)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"]:
        ext = ".jpg"

    client_dir = os.path.join(UPLOADS_DIR, cid)
    os.makedirs(client_dir, exist_ok=True)

    ts = str(int(time.time()))
    filename = f"{kind}-{ts}{ext}"
    save_path = os.path.join(client_dir, filename)
    f.save(save_path)

    url = f"/files/{cid}/{filename}"

    if kind == "before":
        c["photo_before_url"] = url
        c["photo_before_at"] = int(time.time())
    else:
        c["photo_after_url"] = url
        c["photo_after_at"] = int(time.time())

    note = clean_str(request.form.get("note") or "")
    if note:
        c.setdefault("photo_notes", [])
        c["photo_notes"].append({"kind": kind, "note": note, "ts": int(time.time())})

    c["updated_at"] = int(time.time())
    CLIENTS[cid] = c
    save_clients()
    push_client_change("upsert", c)

    return jsonify({"ok": True, "url": url, "item": c})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
