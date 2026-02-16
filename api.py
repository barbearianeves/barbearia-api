from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "neves-12345")

BOOKINGS = {}               # id -> booking dict (memória)
CHANGES = deque(maxlen=20000)  # eventos p/ bridge (memória)

def now_id():
    return str(int(time.time() * 1_000_000)) + "-" + secrets.token_hex(3)

def bad(msg, code=400):
    return jsonify({"error": msg}), code

def push_change(op, payload):
    CHANGES.append({
        "op": op,           # "upsert" | "delete"
        "payload": payload, # booking ou {"id":...}
        "ts": int(time.time())
    })

def is_admin(req):
    return req.headers.get("X-Admin-Token", "") == ADMIN_TOKEN

@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "barbearia-api",
        "bookings": len(BOOKINGS),
        "changes": len(CHANGES)
    })

@app.post("/book")
def book():
    data = request.get_json(silent=True) or {}
    required = ["name", "phone", "service", "barber", "date", "time", "dur"]
    for k in required:
        if not str(data.get(k, "")).strip():
            return bad(f"Campo obrigatório em falta: {k}")

    t = str(data["time"]).strip()
    if len(t) >= 5:
        t = t[:5]

    bid = data.get("id") or now_id()

    item = {
        "id": bid,
        "name": str(data.get("name", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "service": str(data.get("service", "")).strip(),
        "barber": str(data.get("barber", "")).strip(),
        "date": str(data.get("date", "")).strip(),   # AAAA-MM-DD
        "time": t,                                   # HH:MM
        "dur": int(float(data.get("dur", 30))),
        "notes": str(data.get("notes", "")).strip(),
        "status": str(data.get("status", "Marcado")).strip() or "Marcado",
        # compat com bridge/C
        "client_id": str(data.get("client_id", "")).strip(),
        "client": str(data.get("client", "")).strip(),
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})

# Bridge puxa alterações
@app.get("/pull")
def pull():
    secret = request.args.get("secret", "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    cursor = int(request.args.get("cursor", "0"))
    limit = int(request.args.get("limit", "200"))

    changes_list = list(CHANGES)
    out = changes_list[cursor: cursor + limit]
    new_cursor = min(cursor + len(out), len(changes_list))

    return jsonify({"ok": True, "cursor": new_cursor, "items": out})

# Bridge envia alterações do PC
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

        if op == "delete":
            bid = payload.get("id")
            if bid:
                existed = bid in BOOKINGS
                BOOKINGS.pop(bid, None)
                if existed:
                    push_change("delete", {"id": bid})
                applied += 1

        elif op == "upsert":
            bid = payload.get("id")
            if not bid:
                continue
            BOOKINGS[bid] = {**BOOKINGS.get(bid, {}), **payload}
            push_change("upsert", BOOKINGS[bid])
            applied += 1

    return jsonify({"ok": True, "applied": applied})

def _busy_items(date: str = "", barber: str = ""):
    out = []
    for b in BOOKINGS.values():
        if date and b.get("date") != date:
            continue
        if barber and b.get("barber") != barber:
            continue
        if b.get("status") == "Cancelado":
            continue
        out.append({
            "id": b.get("id", ""),
            "time": b.get("time", ""),
            "dur": b.get("dur", 30),
            "status": b.get("status", "Marcado"),
        })
    out.sort(key=lambda x: x.get("time", ""))
    return out

@app.get("/busy")
def busy():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _busy_items(date, barber)})

@app.get("/day")
def day():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _busy_items(date, barber)})

# -------- ADMIN (Barbeiro) --------

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

@app.patch("/admin/bookings/<bid>")
def admin_patch(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    if bid not in BOOKINGS:
        return bad("not found", 404)

    data = request.get_json(silent=True) or {}
    BOOKINGS[bid] = {**BOOKINGS[bid], **data}
    push_change("upsert", BOOKINGS[bid])
    return jsonify({"ok": True})

@app.delete("/admin/bookings/<bid>")
def admin_delete(bid):
    if not is_admin(request):
        return bad("unauthorized", 401)

    existed = bid in BOOKINGS
    BOOKINGS.pop(bid, None)
    if existed:
        push_change("delete", {"id": bid})
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
