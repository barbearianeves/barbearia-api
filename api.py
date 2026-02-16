from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets, json

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345")
ADMIN_TOKEN   = os.environ.get("ADMIN_TOKEN", "barbeiro-2026")

# ✅ Persistência (Render Disk) — NÃO é base de dados
DATA_DIR = os.environ.get("DATA_DIR", "/var/data")
os.makedirs(DATA_DIR, exist_ok=True)
BOOKINGS_FILE = os.path.join(DATA_DIR, "bookings.json")

BOOKINGS = {}                    # id -> booking dict (persistente)
CHANGES  = deque(maxlen=20000)   # eventos para bridge (memória, ok)

def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({
        "op": op,
        "payload": payload,
        "ts": int(time.time())
    })

def is_admin(req):
    return req.headers.get("X-Admin-Token", "") == ADMIN_TOKEN

def load_bookings():
    global BOOKINGS
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
    tmp = BOOKINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(BOOKINGS, f, ensure_ascii=False)
    os.replace(tmp, BOOKINGS_FILE)

load_bookings()

@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "barbearia-api",
        "bookings": len(BOOKINGS),
        "changes": len(CHANGES),
        "persist": BOOKINGS_FILE
    })

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

        # compat bridge/C (se vier)
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
            # opcional: delete real (mantém compat)
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

# -------------------------
# CLIENTE: VER OCUPADOS/DIA
# (privacidade: não manda nome/telefone)
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
# ADMIN: LISTAR (com histórico)
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

    out.sort(key=lambda x: (x.get("date",""), x.get("time","")))
    return jsonify({"ok": True, "items": out})

# ==========================
# ADMIN: CANCELAR marcação (histórico)
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
# ADMIN: DESBLOQUEAR (histórico)
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

if __name__ == "__main__":
    # ✅ CORRIGIDO: sem vírgula, sem texto extra
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
