from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from collections import deque
import os, time, secrets, json, re

# ✅ email
import smtplib, ssl, socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# =========================
# CORS (robusto p/ preflight + headers custom)
# =========================
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=False,
    allow_headers=["Content-Type", "X-Admin-Token", "X-Client-Token"],
    expose_headers=["Content-Type"],
    methods=["GET", "POST", "OPTIONS"]
)

@app.after_request
def add_cors_headers(resp):
    # garante headers em todas as respostas (inclui erros)
    resp.headers.setdefault("Access-Control-Allow-Origin", "*")
    resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token, X-Client-Token")
    resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    return resp

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def options_any(path):
    return make_response(("", 204))

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

# ✅ OTP / CLIENT LOGIN
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))          # 10 min
OTP_RESEND_SECONDS = int(os.environ.get("OTP_RESEND_SECONDS", "45"))     # 45s
OTP_LEN = int(os.environ.get("OTP_LEN", "6"))
DEV_OTP_ECHO = (os.environ.get("DEV_OTP_ECHO", "1").strip() == "1")      # devolve code em JSON p/ testes
CLIENT_TOKEN_TTL = int(os.environ.get("CLIENT_TOKEN_TTL", "2592000"))    # 30 dias

# Stores
BOOKINGS = {}                    # id -> booking dict (persistente)
CHANGES  = deque(maxlen=20000)   # eventos para bridge (memória)

OTP_STORE = {}                   # phone -> {code, exp, last_sent, tries}
CLIENT_SESSIONS = {}             # token -> {phone, exp}

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

def norm_phone(p: str) -> str:
    p = norm_str(p)
    # mantém só dígitos
    digits = re.sub(r"\D+", "", p)
    # aceita PT 9 dígitos ou 351 + 9 dígitos
    if digits.startswith("351") and len(digits) == 12:
        return digits
    if len(digits) == 9:
        return digits
    return digits  # devolve o que tiver; vamos validar depois

def gen_otp(n=6):
    # 6 dígitos por defeito
    base = 10 ** (n - 1)
    return str(secrets.randbelow(9 * base) + base)

def client_token_from_header(req):
    return norm_str(req.headers.get("X-Client-Token"))

def require_client(req):
    tok = client_token_from_header(req)
    if not tok:
        return None, ("missing token", 401)
    sess = CLIENT_SESSIONS.get(tok)
    if not sess:
        return None, ("invalid token", 401)
    if int(time.time()) > int(sess.get("exp", 0)):
        CLIENT_SESSIONS.pop(tok, None)
        return None, ("token expired", 401)
    return sess, None

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

load_bookings()

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
        "otp": {
            "ttl": OTP_TTL_SECONDS,
            "resend": OTP_RESEND_SECONDS,
            "len": OTP_LEN,
            "dev_echo": DEV_OTP_ECHO
        },
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
# ✅ CLIENT LOGIN (OTP)
# =========================
@app.post("/client/request_code")
def client_request_code():
    data = request.get_json(silent=True) or {}
    phone_raw = data.get("phone") or data.get("telemovel") or data.get("mobile") or ""
    phone = norm_phone(phone_raw)

    if len(phone) not in (9, 12):
        return bad("Telefone inválido (usa 9 dígitos ou 351+9).")

    now = int(time.time())
    st = OTP_STORE.get(phone) or {}

    last_sent = int(st.get("last_sent", 0) or 0)
    if last_sent and (now - last_sent) < OTP_RESEND_SECONDS:
        wait = OTP_RESEND_SECONDS - (now - last_sent)
        return jsonify({"ok": True, "message": f"Já foi enviado. Tenta novamente em {wait}s."})

    code = gen_otp(OTP_LEN)
    OTP_STORE[phone] = {
        "code": code,
        "exp": now + OTP_TTL_SECONDS,
        "last_sent": now,
        "tries": 0
    }

    # ⚠️ Aqui era onde enviavas SMS (Twilio etc.)
    # Para já: DEV_MODE pode devolver o código no JSON p/ testes.
    print(f"[OTP] phone={phone} code={code} exp={OTP_TTL_SECONDS}s", flush=True)

    resp = {"ok": True, "message": "Código enviado."}
    if DEV_OTP_ECHO:
        resp["dev_code"] = code
    return jsonify(resp)

@app.post("/client/verify_code")
def client_verify_code():
    data = request.get_json(silent=True) or {}
    phone_raw = data.get("phone") or data.get("telemovel") or data.get("mobile") or ""
    code = norm_str(data.get("code") or data.get("codigo") or "")

    phone = norm_phone(phone_raw)
    if len(phone) not in (9, 12):
        return bad("Telefone inválido.")

    st = OTP_STORE.get(phone)
    if not st:
        return bad("Não existe código para este número. Carrega em 'Receber código'.")

    now = int(time.time())
    if now > int(st.get("exp", 0)):
        OTP_STORE.pop(phone, None)
        return bad("Código expirado. Pede um novo.")

    st["tries"] = int(st.get("tries", 0) or 0) + 1
    if st["tries"] > 6:
        OTP_STORE.pop(phone, None)
        return bad("Muitas tentativas. Pede novo código.", 429)

    if code != str(st.get("code")):
        return bad("Código errado.")

    # ok -> cria sessão
    OTP_STORE.pop(phone, None)
    tok = "c_" + secrets.token_hex(24)
    CLIENT_SESSIONS[tok] = {"phone": phone, "exp": now + CLIENT_TOKEN_TTL}

    return jsonify({"ok": True, "token": tok, "phone": phone})

@app.get("/client/me")
def client_me():
    sess, err = require_client(request)
    if err:
        msg, code = err
        return bad(msg, code)
    return jsonify({"ok": True, "phone": sess["phone"]})

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

    item = {
        "id": bid,
        "name": norm_str(data.get("name")),
        "phone": norm_str(data.get("phone")),
        "email": norm_str(data.get("email")),
        "service": norm_str(data.get("service")),
        "barber": norm_str(data.get("barber")),
        "date": norm_str(data.get("date")),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": norm_str(data.get("notes")),
        "status": "Marcado",
        "client_id": norm_str(data.get("client_id")),
        "client": norm_str(data.get("client")),
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
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

def merge_preserving_contact(old: dict, incoming: dict) -> dict:
    merged = {**(old or {}), **(incoming or {})}

    for key in ["phone", "email"]:
        inc = norm_str((incoming or {}).get(key))
        if inc:
            merged[key] = inc
        else:
            merged[key] = norm_str((old or {}).get(key))

    merged["name"] = norm_str(merged.get("name"))
    merged["service"] = norm_str(merged.get("service"))
    merged["barber"] = norm_str(merged.get("barber"))
    merged["date"] = norm_str(merged.get("date"))
    merged["time"] = norm_str(merged.get("time"))[:5]
    merged["status"] = norm_str(merged.get("status")) or "Marcado"
    return merged

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
            BOOKINGS[bid] = merge_preserving_contact(old, payload)
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
        BOOKINGS[bid] = merge_preserving_contact(old_all.get(bid, {}), b)

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

@app.get("/admin/booking/<bid>")
def admin_get_booking(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)

    return jsonify({"ok": True, "item": b})

@app.post("/admin/update_contact/<bid>")
def admin_update_contact(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    email = norm_str(data.get("email"))
    phone = norm_str(data.get("phone"))

    if email:
        BOOKINGS[bid]["email"] = email
    if phone:
        BOOKINGS[bid]["phone"] = phone

    save_bookings()
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True, "item": BOOKINGS[bid]})

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
    new_status = norm_str(data.get("status")) or "Chegou"

    incoming_email = norm_str(data.get("email"))
    incoming_phone = norm_str(data.get("phone"))
    if incoming_email:
        BOOKINGS[bid]["email"] = incoming_email
    if incoming_phone:
        BOOKINGS[bid]["phone"] = incoming_phone

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
