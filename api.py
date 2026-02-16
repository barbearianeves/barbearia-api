
from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import deque
import os, time, secrets

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

BRIDGE_SECRET = os.environ.get("BRIDGE_SECRET", "neves-12345")

# Estado central (memória) — no free tier reinicia se dormir (ok p/ já)
BOOKINGS = {}  # id -> booking dict
CHANGES = deque(maxlen=20000)  # fila de eventos p/ bridge


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

    bid = now_id()
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
        "status": "Marcado",
        "created_at": int(time.time())
    }

    BOOKINGS[bid] = item
    push_change("upsert", item)
    return jsonify({"ok": True, "id": bid})


# Bridge puxa alterações (novos/updates/deletes)
@app.get("/pull")
def pull():
    secret = request.args.get("secret", "")
    if secret != BRIDGE_SECRET:
        return bad("unauthorized", 401)

    cursor = int(request.args.get("cursor", "0"))
    limit = int(request.args.get("limit", "200"))

    # cursor é um índice simples na lista de changes
    changes_list = list(CHANGES)
    out = changes_list[cursor: cursor + limit]
    new_cursor = min(cursor + len(out), len(changes_list))

    return jsonify({"ok": True, "cursor": new_cursor, "items": out})


# Bridge envia alterações vindas do PC (apagar/cancelar/editar)
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
            if bid and bid in BOOKINGS:
                BOOKINGS.pop(bid, None)
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
            "id": b["id"],
            "time": b["time"],
            "dur": b.get("dur", 30),
            "status": b.get("status", "Marcado")
        })
    return out


# (para a página saber o que está ocupado)
@app.get("/busy")
def busy():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _busy_items(date, barber)})


# Alias para compatibilidade com o frontend que chama /day (evita 404)
@app.get("/day")
def day():
    date = request.args.get("date", "")
    barber = request.args.get("barber", "")
    return jsonify({"ok": True, "items": _busy_items(date, barber)})


if __name__ == "__main__":
    # Ajusta host/port se precisares
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
