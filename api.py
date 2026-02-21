from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets, json, re

app = Flask(__name__)

# ✅ IMPORTANTE: permitir header X-Admin-Token
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    allow_headers=["Content-Type", "X-Admin-Token"],
    expose_headers=["Content-Type"],
)

# =========================
# CONFIG / SECRETS
# =========================
BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345").strip()
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "neves-12345").strip()

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

def is_bridge(req):
    secret = (req.args.get("secret", "") or "").strip()
    return secret == BRIDGE_SECRET

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
    s = (raw or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if digits.startswith("351") and len(digits) >= 12:
        digits = digits[3:]
    if len(digits) > 9:
        digits = digits[-9:]
    return digits if len(digits) == 9 else ""

# -------------------------
# STORAGE: escolher diretório escrevível
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

CLIENTS_DIR = os.path.join(DATA_DIR, "clientes") if DATA_DIR else None
CLIENT_INDEX_FILE = os.path.join(DATA_DIR, "clients_index.json") if DATA_DIR else None  # phone9 -> client_id

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
                tel9 = normalize_phone_pt9(data.get("telefone", ""))
                return {
                    "id": data.get("id",""),
                    "nome": data.get("nome",""),
                    "telefone": tel9,
                    "email": data.get("email",""),
                }
    return {}

def write_cliente_txt(client: dict):
    cid = client.get("id") or ""
    nome = (client.get("nome") or "Cliente").strip()
    tel9 = normalize_phone_pt9(client.get("telefone") or client.get("phone") or "")
    email = norm_email(client.get("email"))

    path = client_txt_path(cid, nome)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    lines = [
        f"id={cid}",
        f"nome={nome}",
        f"telefone={tel9}",
        f"email={email}",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def get_or_create_client_id_by_phone9(phone9: str) -> tuple[str, bool]:
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
    cur_id = idx.get(phone9)
    if cur_id and cur_id != client_id:
        return {}, False, "Este telemóvel já existe noutro cliente"

    idx[phone9] = client_id
    save_client_index(idx)

    cur = find_client_by_id(client_id)
    created = not bool(cur)

    client = cur or {"id": client_id, "nome": "", "telefone": "", "email": ""}
    client["id"] = client_id
    client["telefone"] = phone9
    client["nome"] = name
    if email:
        client["email"] = email

    write_cliente_txt(client)
    return dict(client), created, ""

# =========================
# BOOT STORAGE
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
    return jsonify({"ok": True, "service": "barbearia-api", "bookings": len(BOOKINGS), "data_dir": DATA_DIR})

@app.get("/health")
def health():
    return jsonify({"ok": True})

# =========================
# CLIENT (SEM OTP): /client/login
# =========================
@app.post("/client/login")
def client_login():
    data = request.get_json(silent=True) or {}
    phone9 = normalize_phone_pt9(norm_str(data.get("phone")))
    if not phone9:
        return bad("Telefone inválido (usa 9 dígitos PT)")

    client_id, existing = get_or_create_client_id_by_phone9(phone9)

    if existing:
        c = find_client_by_id(client_id) or {"id": client_id, "nome": "", "telefone": phone9, "email": ""}
        return jsonify({"ok": True, "existing": True, "client": c})

    # novo
    return jsonify({"ok": True, "existing": False, "client": {"id": client_id, "nome": "", "telefone": phone9, "email": ""}})

@app.get("/client/<client_id>")
def client_get(client_id):
    cid = norm_str(client_id)
    if not cid:
        return bad("id inválido")
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
# BOOKINGS (CLIENT)
# =========================
@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}

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
        "phone": phone9,
        "email": norm_email(data.get("email")) or norm_email(c.get("email")),
        "service": norm_str(data.get("service")),
        "barber": norm_str(data.get("barber")),
        "date": norm_str(data.get("date")),
        "time": t,
        "dur": int(float(data.get("dur", 45))),
        "notes": norm_str(data.get("notes")),
        "status": "Marcado",
        "client_id": cid,
        "client": norm_str(c.get("nome")),
        "created_at": int(time.time()),
    }

    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid, "client_id": item["client_id"]})

# =========================
# DAY (CLIENT VIEW, sem dados pessoais)
# =========================
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
            "dur": b.get("dur", 45),
            "status": st,
            "label": "INDISPONÍVEL" if is_block else "OCUPADO",
            "service": b.get("service", ""),
            "notes": b.get("notes", ""),
        })
    return out

@app.get("/day")
def day():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _day_items_for_clients(date, barber)})

# =========================
# ✅ ADMIN ENDPOINTS (para o admin.html)
# =========================
@app.get("/admin/bookings")
def admin_bookings():
    if not is_admin(request):
        return bad("unauthorized", 401)

    date = norm_str(request.args.get("date", ""))
    barber = norm_str(request.args.get("barber", ""))

    items = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        items.append(b)

    items.sort(key=lambda x: (x.get("date",""), x.get("time","")))
    return jsonify({"ok": True, "items": items})

@app.get("/admin/booking/<bid>")
def admin_booking_get(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)
    bid = norm_str(bid)
    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)
    return jsonify({"ok": True, "item": b})

@app.post("/admin/block")
def admin_block():
    if not is_admin(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    date = norm_str(data.get("date"))
    time_s = norm_str(data.get("time"))[:5]
    barber = norm_str(data.get("barber"))

    if not date or not time_s or not barber:
        return bad("date/time/barber obrigatórios")

    bid = now_id()
    item = {
        "id": bid,
        "name": "—",
        "phone": "",
        "email": "",
        "service": "INDISPONIVEL",
        "barber": barber,
        "date": date,
        "time": time_s,
        "dur": 45,
        "notes": "Bloqueado pelo barbeiro",
        "status": "Bloqueado",
        "client_id": "",
        "client": "",
        "created_at": int(time.time()),
    }
    BOOKINGS[bid] = item
    save_bookings()
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})

@app.post("/admin/unblock/<bid>")
def admin_unblock(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    bid = norm_str(bid)
    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)

    b["status"] = "Desbloqueado"
    BOOKINGS[bid] = b
    save_bookings()
    push_change("upsert", b)
    return jsonify({"ok": True})

@app.post("/admin/cancel/<bid>")
def admin_cancel(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    bid = norm_str(bid)
    b = BOOKINGS.get(bid)
    if not b:
        return bad("not found", 404)

    b["status"] = "Cancelado"
    BOOKINGS[bid] = b
    save_bookings()
    push_change("upsert", b)
    return jsonify({"ok": True})

# =========================
# ✅ BRIDGE ENDPOINTS
# =========================
@app.get("/pull")
def bridge_pull():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    try:
        cursor = int(request.args.get("cursor", "0") or "0")
    except Exception:
        cursor = 0
    try:
        limit = int(request.args.get("limit", "300") or "300")
    except Exception:
        limit = 300

    if cursor < 0:
        cursor = 0
    if limit < 1:
        limit = 1
    if limit > 2000:
        limit = 2000

    changes = list(CHANGES)
    batch = changes[cursor: cursor + limit]
    next_cursor = cursor + len(batch)

    return jsonify({"ok": True, "items": batch, "cursor": next_cursor})

@app.post("/sync")
def bridge_sync():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return bad("changes deve ser lista")

    applied = 0

    for ev in changes:
        if not isinstance(ev, dict):
            continue
        op = (ev.get("op") or "").strip()
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        bid = norm_str(payload.get("id"))
        if not bid:
            continue

        if op == "delete":
            if bid in BOOKINGS:
                BOOKINGS.pop(bid, None)
                applied += 1
            push_change("delete", {"id": bid})
            continue

        if op == "upsert":
            cur = BOOKINGS.get(bid, {})
            if not isinstance(cur, dict):
                cur = {}
            cur.update(payload)

            if cur.get("phone"):
                cur["phone"] = normalize_phone_pt9(cur.get("phone"))
            if cur.get("email"):
                cur["email"] = norm_email(cur.get("email"))
            if cur.get("time"):
                cur["time"] = norm_str(cur.get("time"))[:5]
            if cur.get("dur") is not None:
                try:
                    cur["dur"] = int(float(cur.get("dur")))
                except Exception:
                    cur["dur"] = 45
            if not cur.get("created_at"):
                cur["created_at"] = int(time.time())

            BOOKINGS[bid] = cur
            push_change("upsert", cur)
            applied += 1
            continue

    save_bookings()
    return jsonify({"ok": True, "applied": applied})

@app.post("/replace_all")
def bridge_replace_all():
    if not is_bridge(request):
        return bad("unauthorized", 401)

    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    if not isinstance(items, list):
        return bad("items deve ser lista")

    BOOKINGS.clear()

    for b in items:
        if not isinstance(b, dict):
            continue
        bid = norm_str(b.get("id"))
        if not bid:
            continue

        if b.get("phone"):
            b["phone"] = normalize_phone_pt9(b.get("phone"))
        if b.get("email"):
            b["email"] = norm_email(b.get("email"))
        if b.get("time"):
            b["time"] = norm_str(b.get("time"))[:5]
        if b.get("dur") is not None:
            try:
                b["dur"] = int(float(b.get("dur")))
            except Exception:
                b["dur"] = 45
        if not b.get("created_at"):
            b["created_at"] = int(time.time())

        BOOKINGS[bid] = b

    save_bookings()

    CHANGES.clear()
    for b in BOOKINGS.values():
        push_change("upsert", b)

    return jsonify({"ok": True, "count": len(BOOKINGS)})

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
