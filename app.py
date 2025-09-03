import os
from flask import Flask, request, jsonify, Response
import html
import requests
from datetime import datetime
from dotenv import load_dotenv
from config import DISPLAY_NAME, INSTANCE_LABEL, TIMEZONE, FEATURES, PROFILE_PATH
from core.llm import generate_reply
from core.memory import Memory
from infra.monitoring import health_payload

load_dotenv()

app = Flask(__name__)
memory = Memory(profile_path=PROFILE_PATH)

@app.get("/health")
def health():
    return jsonify(health_payload(instance_label=INSTANCE_LABEL)), 200

@app.post("/internal/send")
def internal_send():
    expected = os.getenv("INTERNAL_TOKEN")
    provided = request.headers.get("X-Token")

    # Bloque si la variable n'existe pas OU si le header est absent/mauvais
    if not expected or provided != expected:
        return jsonify({"error": "forbidden"}), 403

    data = request.json or {}
    text = data.get("text", "Bonjour")
    profile = memory.get_profile()
    reply = generate_reply(text, profile)

    if (request.args.get("format") or "").lower() == "text":
        return Response(reply, mimetype="text/plain; charset=utf-8"), 200

    return jsonify({"ok": True, "request_text": text, "reply": reply}), 200

@app.post("/internal/checkin")
def internal_checkin():
    """Déclenche un check-in du matin.
       - Protégé par X-Token (même logique que /internal/send)
       - Envoie WhatsApp si TWILIO_* présents, sinon dry-run
    """
    expected = os.getenv("INTERNAL_TOKEN")
    provided = request.headers.get("X-Token")
    if not expected or provided != expected:
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    to = body.get("to") or os.getenv("USER_WHATSAPP_TO")
    weather_hint = body.get("weather") or os.getenv("WEATHER_SUMMARY")

    # Construit la demande de check-in (on utilise generate_reply pour rester 100% compatible)
    profile = memory.get_profile()
    now = datetime.now().strftime("%A %d %B, %H:%M")
    prompt = "Fais un check-in du matin (bref). Format: bonjour bref + météo (si fournie) + 1–2 priorités + 1 conseil."
    if weather_hint:
        prompt += f" Météo: {weather_hint}."
    prompt += f" Date/heure: {now}. Utilise mes intérêts si utile."

    text = generate_reply(prompt, profile)

    # Envoi via Twilio si configuré
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    tok = os.getenv("TWILIO_AUTH_TOKEN")
    from_wa = os.getenv("TWILIO_SANDBOX_FROM", "whatsapp:+14155238886")  # numéro sandbox par défaut

    if sid and tok and to:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        data = {"From": from_wa, "To": to, "Body": text}
        r = requests.post(url, data=data, auth=(sid, tok), timeout=15)
        try:
            js = r.json()
        except Exception:
            js = {"status_code": r.status_code, "text": r.text[:200]}
        return jsonify({"status": "sent", "twilio": js}), 200

    # Sinon: dry-run
    return jsonify({"status": "dry-run", "text": text}), 200

@app.post("/whatsapp/webhook")
def whatsapp_webhook():
    incoming = request.form or request.json or {}
    text = (incoming.get("Body") or incoming.get("text") or "").strip() or "Salut"
    profile = memory.get_profile()
    reply = generate_reply(text, profile)
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{html.escape(reply)}</Message></Response>'
    return Response(twiml, mimetype="application/xml")
