from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets, json

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

def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({"op": op, "payload": payload, "ts": int(time.time())})

def is_admin(req):
    return (req.headers.get("X-Admin-Token", "") or "").strip() == ADMIN_TOKEN

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
    """
    Resolve host para IPv4 e liga por IPv4.
    Ajuda em ambientes sem rota IPv6.
    """
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

        # ✅ Logs úteis no Render
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
# ✅ ADMIN: TESTE EMAIL (NÃO depende de booking)
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

    item = {
        "id": bid,
        "name": str(data.get("name", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "service": str(data.get("service", "")).strip(),
        "barber": str(data.get("barber", "")).strip(),
        "date": str(data.get("date", "")).strip(),
        "time": t,
        "dur": int(float(data.get("dur", 30))),
        "notes": str(data.get("notes", "")).strip(),
        "status": "Marcado",
        "client_id": str(data.get("client_id", "")).strip(),
        "client": str(data.get("client", "")).strip(),
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

    save_bookings()

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
# ADMIN: LISTAR
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

    # (Opcional) permitir definir email aqui:
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
