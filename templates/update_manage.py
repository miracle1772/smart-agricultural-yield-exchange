import os
import re

with open("c:\\MJ\\manage.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Ensure static/uploads exists
if "os.makedirs('static/uploads', exist_ok=True)" not in content:
    content = content.replace("app = Flask(__name__)", "app = Flask(__name__, static_folder='static')\nos.makedirs('static/uploads', exist_ok=True)")

# 2. Add profile_img to init_db
init_db_patch = """
        # Add profile_img column if it doesn't exist
        try:
            db.execute('ALTER TABLE users ADD COLUMN profile_img TEXT')
        except sqlite3.OperationalError:
            pass
"""
if "ALTER TABLE users ADD COLUMN profile_img TEXT" not in content:
    content = content.replace("db.commit()", f"{init_db_patch}\n        db.commit()", 1)

# 3. Update /api/me to return profile_img
if "profile_img" not in content.split("def get_me():")[1].split("def ")[0]:
    content = content.replace("SELECT id, full_name, role, contact, address FROM users", "SELECT id, full_name, role, contact, address, profile_img FROM users")

# 4. Add /api/profile endpoint for file upload and profile editing
from werkzeug.utils import secure_filename
profile_endpoint = """
from werkzeug.utils import secure_filename
import uuid

@app.route('/api/profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    db = get_db()
    user_id = session['user_id']
    
    full_name = request.form.get('full_name')
    contact = request.form.get('contact')
    address = request.form.get('address')
    
    # Handle File Upload
    profile_img_url = None
    if 'profile_img' in request.files:
        file = request.files['profile_img']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            filename = f"user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
            file.save(os.path.join('static', 'uploads', filename))
            profile_img_url = f"/static/uploads/{filename}"
            
    # Update Users Table
    updates = []
    params = []
    
    if full_name:
        updates.append("full_name = ?")
        params.append(full_name)
    if contact:
        updates.append("contact = ?")
        params.append(contact)
    if address:
        updates.append("address = ?")
        params.append(address)
    if profile_img_url:
        updates.append("profile_img = ?")
        params.append(profile_img_url)
        
    if updates:
        query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
        params.append(user_id)
        db.execute(query, params)
        
    # If farmer, update shop_name
    shop_name = request.form.get('shop_name')
    if shop_name and session.get('role') == 'farmer':
        db.execute("UPDATE farmers SET name = ? WHERE user_id = ?", (shop_name, user_id))
        if profile_img_url:
            db.execute("UPDATE farmers SET img = ? WHERE user_id = ?", (profile_img_url, user_id))
            
    db.commit()
    
    return jsonify({"status": "success", "profile_img": profile_img_url})
"""

if "@app.route('/api/profile'" not in content:
    content = content.replace("@app.route('/api/change_password'", profile_endpoint + "\n@app.route('/api/change_password'")

# Need to ensure werkzeug secure_filename and uuid are imported properly if they aren't already. 
# The string above handles the imports inline which is fine for flask.

with open("c:\\MJ\\manage.py", "w", encoding="utf-8") as f:
    f.write(content)
