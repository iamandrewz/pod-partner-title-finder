"""
Pod Partner Title Finder — Standalone Backend
==============================================
Flask API for Title Finder and V3 Title Lab.
Runs on port 5003.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import json
import uuid
import threading
import time
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# =============================================================================
# FLASK APP SETUP
# =============================================================================

app = Flask(__name__)

# Trust proxy headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Configure
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB

# CORS - allow frontend on port 3102
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:3102",
            "http://127.0.0.1:3102",
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
        "supports_credentials": True,
    }
})

# =============================================================================
# AUTH CONFIG & HELPERS
# =============================================================================

AUTH_COOKIE_NAME = 'podpartner_session'
SESSION_SECRET = os.getenv('SESSION_SECRET', 'pursue-podcasting-secret-2024')

# In-memory allowed users (loaded from env or defaults)
def get_allowed_users():
    emails_env = os.getenv('ALLOWED_EMAILS', '')
    if emails_env:
        return [e.strip().lower() for e in emails_env.split(',') if e.strip()]
    return [
        'pursuepodcasting@gmail.com',
        'calebsettlage@gmail.com',
        'settlagesac@gmail.com',
    ]

SHARED_PASSWORD = os.getenv('SHARED_PASSWORD', 'PursuePodcasting!Team1')

ALLOWED_EMAILS = get_allowed_users()

def create_session_token(email: str) -> str:
    payload = f"{email.lower().strip()}:{SESSION_SECRET}:{int(time.time())}"
    import base64
    return base64.b64encode(payload.encode()).decode()

def validate_session_token(token: str) -> bool:
    try:
        import base64
        decoded = base64.b64decode(token.encode()).decode()
        email, secret, timestamp = decoded.split(':')
        if secret != SESSION_SECRET:
            return False
        # 7-day expiry
        if int(time.time()) - int(timestamp) > 7 * 24 * 60 * 60:
            return False
        return email.lower().strip() in ALLOWED_EMAILS
    except:
        return False

# =============================================================================
# SESSION ROUTES
# =============================================================================

@app.route('/api/auth/login', methods=['POST', 'OPTIONS'])
def auth_login():
    if request.method == 'OPTIONS':
        return '', 204
    try:
        data = request.get_json() or {}
        email = data.get('email', '').lower().strip()
        password = data.get('password', '')

        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400

        if email not in ALLOWED_EMAILS:
            return jsonify({'error': 'Email not authorized'}), 401

        if password != SHARED_PASSWORD:
            return jsonify({'error': 'Invalid password'}), 401

        token = create_session_token(email)
        response = jsonify({'success': True})
        response.set_cookie(
            AUTH_COOKIE_NAME, token,
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            samesite='lax',
            path='/'
        )
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/logout', methods=['POST', 'OPTIONS'])
def auth_logout():
    if request.method == 'OPTIONS':
        return '', 204
    response = jsonify({'success': True})
    response.set_cookie(AUTH_COOKIE_NAME, '', max_age=0, path='/')
    return response

@app.route('/api/users', methods=['GET'])
def get_users():
    """Return allowed emails and shared password for frontend auth validation."""
    return jsonify({
        'emails': ALLOWED_EMAILS,
        'password': SHARED_PASSWORD
    }), 200

# =============================================================================
# YOUTUBE AUTH ROUTES (stub - requires Google OAuth credentials)
# =============================================================================

@app.route('/api/auth/youtube/status', methods=['GET', 'OPTIONS'])
def youtube_status():
    if request.method == 'OPTIONS':
        return '', 204
    # Check session
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token or not validate_session_token(token):
        return jsonify({'error': 'Unauthorized'}), 401
    # Stub: return disconnected
    return jsonify({'connected': False}), 200

@app.route('/api/auth/youtube/connect', methods=['GET', 'OPTIONS'])
def youtube_connect():
    if request.method == 'OPTIONS':
        return '', 204
    return jsonify({'error': 'YouTube OAuth not configured in standalone mode'}), 501

@app.route('/api/auth/youtube/callback', methods=['GET', 'OPTIONS'])
def youtube_callback():
    if request.method == 'OPTIONS':
        return '', 204
    return jsonify({'error': 'YouTube OAuth not configured in standalone mode'}), 501

@app.route('/api/auth/youtube/disconnect', methods=['POST', 'OPTIONS'])
def youtube_disconnect():
    if request.method == 'OPTIONS':
        return '', 204
    return jsonify({'success': True}), 200

# =============================================================================
# TITLE FINDER ROUTES
# =============================================================================

title_finder_jobs = {}
title_finder_lock = threading.Lock()
TITLE_FINDER_TIMEOUT = 110

def run_title_finder_async(job_id, youtube_url, podcast):
    """Background thread to run title finder with hard timeout."""
    import concurrent.futures
    start_time = time.time()
    try:
        from title_finder import find_winning_titles
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(find_winning_titles, youtube_url, podcast)
            try:
                result_data = fut.result(timeout=TITLE_FINDER_TIMEOUT)
            except Exception as e:
                elapsed = time.time() - start_time
                print(f"[TitleFinder Job {job_id}] TIMEOUT after {elapsed:.1f}s: {e}")
                from title_finder import model_ranked_titles
                result_data = model_ranked_titles(youtube_url, podcast)
                result_data['error'] = f"Job timed out after {TITLE_FINDER_TIMEOUT}s (model-ranked fallback)"

        elapsed = time.time() - start_time
        print(f"[TitleFinder Job {job_id}] Complete in {elapsed:.1f}s")
        with title_finder_lock:
            title_finder_jobs[job_id]['result'] = result_data
            title_finder_jobs[job_id]['status'] = 'complete'
    except Exception as e:
        with title_finder_lock:
            title_finder_jobs[job_id]['status'] = 'error'
            title_finder_jobs[job_id]['error'] = str(e)
            title_finder_jobs[job_id]['traceback'] = traceback.format_exc()
        print(f"[TitleFinder Job {job_id}] Error: {e}")

@app.route('/api/title-finder', methods=['POST', 'OPTIONS'])
def title_finder():
    if request.method == 'OPTIONS':
        return '', 204, {'Access-Control-Allow-Origin': '*'}
    try:
        data = request.get_json() or {}
        youtube_url = data.get('youtube_url')
        podcast = data.get('podcast', 'generic')

        if not youtube_url:
            return jsonify({'error': 'youtube_url is required'}), 400

        valid_podcasts = ['spp', 'jpi', 'sbs', 'wow', 'agp', 'generic']
        if podcast not in valid_podcasts:
            return jsonify({'error': f'Invalid podcast. Must be one of: {valid_podcasts}'}), 400

        try:
            from title_finder import find_winning_titles
        except ImportError as e:
            return jsonify({'error': f'Title finder not available: {str(e)}'}), 500

        job_id = str(uuid.uuid4())
        with title_finder_lock:
            title_finder_jobs[job_id] = {
                'status': 'processing',
                'youtube_url': youtube_url,
                'podcast': podcast,
                'created_at': datetime.now().isoformat()
            }

        thread = threading.Thread(target=run_title_finder_async, args=(job_id, youtube_url, podcast))
        thread.daemon = True
        thread.start()

        print(f"[TitleFinder API] Started job {job_id} for {youtube_url}")
        return jsonify({
            'job_id': job_id,
            'status': 'processing',
            'message': 'Title finder started. Poll /api/title-finder/status/{job_id} for results.'
        }), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/title-finder/status/<job_id>', methods=['GET', 'OPTIONS'])
def title_finder_status(job_id):
    if request.method == 'OPTIONS':
        return '', 204, {'Access-Control-Allow-Origin': '*'}
    with title_finder_lock:
        job = title_finder_jobs.get(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    response = {
        'job_id': job_id,
        'status': job.get('status', 'unknown'),
        'youtube_url': job.get('youtube_url'),
        'podcast': job.get('podcast'),
        'created_at': job.get('created_at')
    }
    if job.get('status') == 'complete':
        response['result'] = job.get('result')
    elif job.get('status') == 'error':
        response['error'] = job.get('error')
        response['traceback'] = job.get('traceback')

    return jsonify(response), 200

# =============================================================================
# V3 OPTIMIZER ROUTES
# =============================================================================

try:
    from v3_optimizer import optimize as v3_optimize, mimic_title as v3_mimic

    @app.route('/api/v3/optimize', methods=['POST', 'OPTIONS'])
    def v3_optimizer():
        if request.method == 'OPTIONS':
            return '', 204, {'Access-Control-Allow-Origin': '*'}
        try:
            data = request.get_json() or {}
            youtube_url = data.get('youtube_url', '')
            manual_transcript = data.get('manual_transcript')
            niche = data.get('niche', '')
            audience = data.get('audience', '')
            focus = data.get('focus', '')

            if not youtube_url and not manual_transcript:
                return jsonify({'error': 'youtube_url or manual_transcript is required'}), 400

            print(f"[V3 API] Starting optimization for: {youtube_url or 'manual transcript'}")
            result = v3_optimize(
                youtube_url or '', 
                niche=niche, 
                audience=audience, 
                focus=focus, 
                manual_transcript=manual_transcript
            )
            print(f"[V3 API] Optimization complete. Success: {result.get('success')}")
            return jsonify(result), 200
        except Exception as e:
            print(f"[V3 API] Error: {e}")
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': str(e),
                'video_title': None,
                'topics': [],
                'titles': []
            }), 500

    @app.route('/api/v3/mimic', methods=['POST', 'OPTIONS'])
    def v3_mimic_route():
        if request.method == 'OPTIONS':
            return '', 204, {'Access-Control-Allow-Origin': '*'}
        try:
            data = request.get_json() or {}
            title_to_mimic = data.get('title_to_mimic')
            topics = data.get('topics', [])
            transcript_summary = data.get('transcript_summary', '')

            if not title_to_mimic:
                return jsonify({'error': 'title_to_mimic is required'}), 400
            if not topics:
                return jsonify({'error': 'topics are required'}), 400

            niche = data.get('niche', '')
            audience = data.get('audience', '')
            focus = data.get('focus', '')

            print(f"[V3 Mimic API] Mimicking: {title_to_mimic[:40]}...")
            result = v3_mimic(
                transcript_summary, topics, title_to_mimic,
                niche=niche, audience=audience, focus=focus
            )
            if result.get('success'):
                mimicked_titles = [t['title'] for t in result.get('titles', [])]
                return jsonify({'success': True, 'mimicked_titles': mimicked_titles}), 200
            else:
                return jsonify({'success': False, 'error': result.get('error', 'Failed')}), 500
        except Exception as e:
            print(f"[V3 Mimic API] Error: {e}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    print("[STARTUP] V3 optimizer routes registered", flush=True)

except ImportError as e:
    print(f"[WARN] V3 optimizer not available: {e}", flush=True)

# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'pod-partner-title-finder'}), 200

# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5003))
    print(f"[STARTUP] Starting Pod Partner Title Finder on port {port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
