"""
shtpass_railway_app.py
Flask app that serves ONLY the shtpass blueprint on Railway.
Reads RAILWAY_DATABASE_URL from env — no local .env needed.
"""

import os
from flask import Flask
from flask_cors import CORS
from shtpass_routes import shtpass

app = Flask(__name__)
CORS(app)
app.register_blueprint(shtpass, url_prefix="/shtpass")

@app.route("/health")
def health():
    return {"status": "ok", "service": "shtpass-backend"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
