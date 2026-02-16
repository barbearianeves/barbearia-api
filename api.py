import os
import time
from flask import Flask, request, jsonify, send_from_directory
from storage import read_all, upsert, delete

app = Flask(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "muda-isto")

def require_admin():
    token = request.headers.get("X-Admin-Token", "")
    return token == ADMIN_TOKEN

@app.get("/api/appointments")
def list_appointments():
    # opcional: filtrar por barber/data no futuro
    return jsonify(read_all())

@app.post("/api/appointments")
def create_or_update():
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True)
    # se não vier id, cria um
    if not data.get("id"):
        data["id"] = str(int(time.time() * 1000))
    return jsonify(upsert(data))

@app.patch("/api/appointments/<item_id>")
def patch(item_id):
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True)
    data["id"] = item_id
    return jsonify(upsert(data))

@app.delete("/api/appointments/<item_id>")
def remove(item_id):
    if not require_admin():
        return jsonify({"error": "unauthorized"}), 401
    delete(item_id)
    return jsonify({"ok": True})

# (opcional) servir os HTML do /public no mesmo serviço:
@app.get("/")
def home():
    return send_from_directory("../public", "index.html")

@app.get("/admin")
def admin():
    return send_from_directory("../public", "admin.html")

@app.get("/<path:path>")
def static_files(path):
    return send_from_directory("../public", path)
