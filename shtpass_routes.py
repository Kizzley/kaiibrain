# shtpass_routes.py
# Drop this file into /Users/kai/kaiibrain/
# Then add one line to kaiibrain_app.py (see bottom of this file)
#
# INSTALL (if needed): pip3 install flask flask-cors psycopg2-binary requests boto3

import os, json, uuid, hashlib, requests, re
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
import psycopg2
from psycopg2.extras import RealDictCursor
import boto3
from botocore.config import Config

# ── CONFIG ──
# Database — reads from Railway env var or falls back to localhost
_railway_url = os.environ.get("RAILWAY_DATABASE_URL", "")
if _railway_url:
    m = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', _railway_url)
    if m:
        DB_CONFIG = {"dbname": m.group(5), "user": m.group(1),
                     "password": m.group(2), "host": m.group(3), "port": int(m.group(4))}
    else:
        DB_CONFIG = {"dbname": "artistbrain", "user": "postgres",
                     "password": "PostyData62*", "host": "localhost", "port": 5432}
else:
    DB_CONFIG = {"dbname": "artistbrain", "user": "postgres",
                 "password": "PostyData62*", "host": "localhost", "port": 5432}

ASSETS_DIR = "/Users/kai/shtpass/assets"

# ── R2 / S3 CONFIG ──
R2_BUCKET    = os.environ.get("R2_BUCKET_NAME", "kaiii-songs")
R2_ENDPOINT  = os.environ.get("R2_ENDPOINT", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")

def _get_r2_client():
    if not R2_ENDPOINT or not R2_ACCESS_KEY or not R2_SECRET_KEY:
        return None
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4")
    )

def _upload_to_r2(local_path: str, r2_key: str) -> str:
    """Upload local file to R2, return the R2 public URL."""
    client = _get_r2_client()
    if not client:
        return local_path  # fallback to local
    try:
        client.upload_file(local_path, R2_BUCKET, r2_key,
                           ExtraArgs={"ContentType": "image/png"})
        return f"{R2_ENDPOINT}/{R2_BUCKET}/{r2_key}"
    except Exception as e:
        print(f"[shtpass] R2 upload failed: {e}")
        return local_path
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "7773290702")
BOT_TOKEN = "7425534899:AAFqBTHXxxxxxxxxxxxxxxx" # replace with real token

shtpass = Blueprint("shtpass", __name__, url_prefix="/shtpass")

# ── DB HELPER ──
def db():
    return psycopg2.connect(**DB_CONFIG)

def tg(msg):
    """Send Telegram notification to Mr. King"""
    try:
        tok = os.environ.get("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
        if tok:
            requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML"},
                timeout=5
            )
    except Exception as e:
        print(f"[ShtPass TG] {e}")

# Ensure asset dirs exist
for d in ["backgrounds","logos","frames","signatures"]:
    os.makedirs(f"{ASSETS_DIR}/{d}", exist_ok=True)

# ─────────────────────────────────────────────
# PASS ENDPOINTS
# ─────────────────────────────────────────────

@shtpass.route("/issue", methods=["POST"])
def issue_pass():
    """Issue a new pass — called from generator"""
    d = request.json or {}
    required = ["full_name","email","pass_code","song_title","serial"]
    if not all(d.get(k) for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Upsert recipient
        cur.execute("""
            INSERT INTO shtpass.recipients (full_name, alias, email, phone, whatsapp, mode, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (email) DO UPDATE SET
            full_name=EXCLUDED.full_name, alias=EXCLUDED.alias,
            phone=COALESCE(EXCLUDED.phone, shtpass.recipients.phone),
            whatsapp=COALESCE(EXCLUDED.whatsapp, shtpass.recipients.whatsapp),
            last_activity=NOW()
            RETURNING id
        """, (d["full_name"], d.get("alias"), d["email"],
              d.get("phone"), d.get("whatsapp"),
              d.get("mode","DJ"), d.get("kai_note")))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM shtpass.recipients WHERE email=%s", (d["email"],))
            row = cur.fetchone()
        recipient_id = row["id"]

        # Insert pass
        cur.execute("""
            INSERT INTO shtpass.passes
            (serial, recipient_id, series_id, pass_code, song_title, song_type,
             format, edition_num, edition_total, max_attempts, kai_note, file_ref, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            RETURNING id, serial, pass_code, issued_at
        """, (
            d["serial"], recipient_id, d.get("series_id"),
            d["pass_code"], d["song_title"], d.get("song_type","New Music"),
            d.get("format","WAV"),
            int(d["edition_num"]) if d.get("edition_num") and str(d.get("edition_num","")).isdigit() else None,
            int(d["edition_total"]) if d.get("edition_total") and str(d.get("edition_total","")).isdigit() else None,
            int(d.get("max_attempts",3)), d.get("kai_note"), d.get("file_ref")
        ))
        pass_row = cur.fetchone()

        cur.execute("UPDATE shtpass.recipients SET total_passes=total_passes+1 WHERE id=%s", (recipient_id,))
        if d.get("series_id"):
            cur.execute("UPDATE shtpass.series SET issued_count=issued_count+1 WHERE id=%s", (d["series_id"],))

        conn.commit()

        tg(f"🎵 <b>ShtPass Issued</b>\n"
           f"To: {d['full_name']}{' ('+d.get('alias')+')' if d.get('alias') else ''}\n"
           f"Song: {d['song_title']} · {d.get('song_type','')}\n"
           f"Code: <code>{d['pass_code']}</code>\n"
           f"Serial: {d['serial']}")

        return jsonify({"success": True, "pass": dict(pass_row), "recipient_id": recipient_id})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/verify", methods=["POST"])
def verify_pass():
    """Vault calls this to verify a pass code"""
    d = request.json or {}
    code = (d.get("code") or "").strip().lower()
    ip = request.remote_addr
    if not code:
        return jsonify({"valid": False, "reason": "No code provided"}), 400

    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT p.*, r.full_name, r.alias, r.email, r.phone, r.whatsapp
            FROM shtpass.passes p
            JOIN shtpass.recipients r ON r.id = p.recipient_id
            WHERE p.pass_code = %s
        """, (code,))
        p = cur.fetchone()

        if not p:
            cur.execute("""
                INSERT INTO shtpass.redemptions (attempted_code, success, ip_address)
                VALUES (%s, false, %s)
            """, (code, ip))
            conn.commit()
            return jsonify({"valid": False, "reason": "Code not found"})

        if p["status"] == "expired":
            return jsonify({"valid": False, "reason": "Pass expired"})
        if p["status"] == "redeemed":
            return jsonify({"valid": False, "reason": "Already redeemed"})
        if p["status"] == "revoked":
            return jsonify({"valid": False, "reason": "Pass revoked"})
        if p["attempts_used"] >= p["max_attempts"]:
            cur.execute("UPDATE shtpass.passes SET status='expired' WHERE id=%s", (p["id"],))
            conn.commit()
            return jsonify({"valid": False, "reason": "Max attempts reached"})

        cur.execute("UPDATE shtpass.passes SET attempts_used=attempts_used+1 WHERE id=%s", (p["id"],))
        conn.commit()

        return jsonify({
            "valid": True,
            "pass": {
                "id": p["id"], "serial": p["serial"],
                "song_title": p["song_title"], "song_type": p["song_type"],
                "format": p["format"], "edition_num": p["edition_num"],
                "edition_total": p["edition_total"], "kai_note": p["kai_note"],
                "file_ref": p["file_ref"], "status": p["status"]
            },
            "recipient": {
                "full_name": p["full_name"], "alias": p["alias"],
                "email": p["email"], "phone": p["phone"], "whatsapp": p["whatsapp"]
            }
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/redeem", methods=["POST"])
def redeem_pass():
    """Called after successful verify — marks pass redeemed, logs contact, triggers SMS"""
    d = request.json or {}
    pass_id = d.get("pass_id")
    recipient_id = d.get("recipient_id")
    ip = request.remote_addr
    if not pass_id:
        return jsonify({"error": "pass_id required"}), 400

    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM shtpass.passes WHERE id=%s", (pass_id,))
        p = cur.fetchone()
        if not p:
            return jsonify({"error": "Pass not found"}), 404

        cur.execute("UPDATE shtpass.passes SET status='redeemed', redeemed_at=NOW() WHERE id=%s", (pass_id,))

        if d.get("contact_phone") or d.get("contact_email"):
            cur.execute("""
                UPDATE shtpass.recipients SET
                phone=COALESCE(%s, phone),
                whatsapp=COALESCE(%s, whatsapp),
                email=COALESCE(%s, email),
                last_activity=NOW()
                WHERE id=%s
            """, (d.get("contact_phone"), d.get("contact_whatsapp"),
                  d.get("contact_email"), p["recipient_id"]))

        greetings = [
            "I've been waiting for you.",
            "The vault is open. This one's yours.",
            "You made it. The frequency is yours.",
            "Welcome. Kaiii has been expecting you."
        ]
        import random
        greeting = random.choice(greetings)

        cur.execute("""
            INSERT INTO shtpass.redemptions
            (pass_id, recipient_id, attempted_code, success, ip_address,
             contact_name, contact_phone, contact_email, kai_greeting, streamed)
            VALUES (%s,%s,%s,true,%s,%s,%s,%s,%s,%s)
        """, (pass_id, p["recipient_id"], p["pass_code"], ip,
              d.get("contact_name"), d.get("contact_phone"),
              d.get("contact_email"), greeting, d.get("streamed", False)))

        conn.commit()

        tg(f"✦ <b>ShtPass Redeemed!</b>\n"
           f"Song: {p['song_title']} · {p.get('song_type','')}\n"
           f"Code: <code>{p['pass_code']}</code>\n"
           f"Contact: {d.get('contact_name','')} · {d.get('contact_phone','')}\n"
           f"Email: {d.get('contact_email','')}\n"
           f"Serial: {p['serial']}")

        return jsonify({"success": True, "greeting": greeting, "sms_queued": False})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/passes", methods=["GET"])
def list_passes():
    """Dashboard — all passes with recipient info"""
    status = request.args.get("status")
    search = request.args.get("q","")
    limit = int(request.args.get("limit", 100))

    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        where = []; params = []
        if status:
            where.append("p.status = %s"); params.append(status)
        if search:
            where.append("(r.full_name ILIKE %s OR p.song_title ILIKE %s OR p.pass_code ILIKE %s)")
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]
        clause = ("WHERE " + " AND ".join(where)) if where else ""

        cur.execute(f"""
            SELECT p.*, r.full_name, r.alias, r.email, r.phone, r.whatsapp, r.mode,
                   s.name as series_name
            FROM shtpass.passes p
            JOIN shtpass.recipients r ON r.id = p.recipient_id
            LEFT JOIN shtpass.series s ON s.id = p.series_id
            {clause}
            ORDER BY p.created_at DESC LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()

        cur.execute("SELECT status, COUNT(*) FROM shtpass.passes GROUP BY status")
        stats = {r["status"]: r["count"] for r in cur.fetchall()}

        return jsonify({"passes": [dict(r) for r in rows], "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/passes/<int:pass_id>", methods=["PATCH"])
def update_pass(pass_id):
    """Edit a pass record"""
    d = request.json or {}
    allowed = ["song_title","song_type","format","status","kai_note","max_attempts","file_ref"]
    updates = {k: v for k,v in d.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    conn = db(); cur = conn.cursor()
    try:
        cols = ", ".join(f"{k}=%s" for k in updates)
        cur.execute(f"UPDATE shtpass.passes SET {cols} WHERE id=%s",
                    list(updates.values()) + [pass_id])
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/passes/<int:pass_id>/revoke", methods=["POST"])
def revoke_pass(pass_id):
    conn = db(); cur = conn.cursor()
    try:
        cur.execute("UPDATE shtpass.passes SET status='revoked' WHERE id=%s", (pass_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/upload-asset", methods=["POST"])
def upload_asset():
    """Upload background, logo, frame, or signature"""
    asset_type = request.form.get("type","background")
    label = request.form.get("label","")
    series_id = request.form.get("series_id")
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file provided"}), 400

    type_dir = {
        "background": "backgrounds", "logo": "logos",
        "frame": "frames", "signature": "signatures"
    }.get(asset_type, "backgrounds")

    filename = secure_filename(f"{uuid.uuid4().hex[:8]}_{file.filename}")
    local_path = f"{ASSETS_DIR}/{type_dir}/{filename}"
    r2_key = f"shtpass/{type_dir}/{filename}"
    file.save(local_path)

    # Try upload to R2, fallback to local if R2 not configured
    public_url = _upload_to_r2(local_path, r2_key)
    if public_url != local_path:
        os.remove(local_path)  # clean up local after R2 upload
        filepath = public_url

    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO shtpass.assets (asset_type, filename, filepath, label, series_id)
            VALUES (%s,%s,%s,%s,%s) RETURNING id, filename, filepath
        """, (asset_type, filename, filepath, label,
              int(series_id) if series_id else None))
        row = cur.fetchone(); conn.commit()
        return jsonify({"success": True, "asset": dict(row)})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/assets", methods=["GET"])
def list_assets():
    asset_type = request.args.get("type")
    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if asset_type:
            cur.execute("SELECT * FROM shtpass.assets WHERE asset_type=%s ORDER BY uploaded_at DESC", (asset_type,))
        else:
            cur.execute("SELECT * FROM shtpass.assets ORDER BY uploaded_at DESC")
        rows = cur.fetchall()
        return jsonify({"assets": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/series", methods=["GET"])
def list_series():
    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM shtpass.series ORDER BY created_at DESC")
        rows = cur.fetchall()
        return jsonify({"series": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/series", methods=["POST"])
def create_series():
    d = request.json or {}
    if not d.get("name"):
        return jsonify({"error": "name required"}), 400
    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            INSERT INTO shtpass.series
            (name, description, max_editions, bg_asset, logo_asset, frame_asset, sig_asset, font_name, warp_style)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING *
        """, (d["name"], d.get("description"), d.get("max_editions",50),
              d.get("bg_asset"), d.get("logo_asset"), d.get("frame_asset"),
              d.get("sig_asset"), d.get("font_name","Bebas Neue"), d.get("warp_style","Straight")))
        row = cur.fetchone(); conn.commit()
        return jsonify({"success": True, "series": dict(row)})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


@shtpass.route("/kai-context", methods=["GET"])
def kai_context():
    """
    Kaiii calls this to understand the full pass landscape.
    Returns intelligence summary for decision making.
    """
    conn = db(); cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='pending') as pending,
                COUNT(*) FILTER (WHERE status='redeemed') as redeemed,
                COUNT(*) FILTER (WHERE status='expired') as expired,
                COUNT(*) FILTER (WHERE status='revoked') as revoked,
                COUNT(*) as total
            FROM shtpass.passes
        """)
        stats = dict(cur.fetchone())

        cur.execute("""
            SELECT p.serial, p.pass_code, p.song_title, p.issued_at,
                   r.full_name, r.email, r.phone,
                   EXTRACT(DAY FROM NOW()-p.issued_at)::int as days_waiting
            FROM shtpass.passes p
            JOIN shtpass.recipients r ON r.id=p.recipient_id
            WHERE p.status='pending'
            AND p.issued_at < NOW() - INTERVAL '7 days'
            ORDER BY p.issued_at ASC LIMIT 10
        """)
        follow_ups = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT name, issued_count, max_editions,
                   max_editions - issued_count as remaining
            FROM shtpass.series
            WHERE status='active'
            AND issued_count >= max_editions * 0.8
        """)
        closing_series = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT rd.redeemed_at, rd.contact_name, rd.contact_phone,
                   p.song_title, p.pass_code
            FROM shtpass.redemptions rd
            JOIN shtpass.passes p ON p.id=rd.pass_id
            WHERE rd.success=true
            AND rd.redeemed_at > NOW() - INTERVAL '7 days'
            ORDER BY rd.redeemed_at DESC LIMIT 20
        """)
        recent = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT full_name, alias, total_passes, last_activity
            FROM shtpass.recipients
            ORDER BY total_passes DESC LIMIT 5
        """)
        top_recipients = [dict(r) for r in cur.fetchall()]

        return jsonify({
            "stats": stats,
            "follow_up_candidates": follow_ups,
            "series_closing_soon": closing_series,
            "recent_redemptions": recent,
            "top_recipients": top_recipients,
            "generated_at": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); conn.close()


# ─────────────────────────────────────────────
# HOW TO REGISTER IN kaiibrain_app.py
# ─────────────────────────────────────────────
#
# Add these TWO lines to kaiibrain_app.py:
#
# from shtpass_routes import shtpass
# app.register_blueprint(shtpass)
#
# That's it. All endpoints are live at:
# http://localhost:8010/shtpass/...
# ─────────────────────────────────────────────
