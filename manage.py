import os
import uuid
from flask import Flask, render_template, request, jsonify, g, session
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash

from contextlib import contextmanager

# Database support
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

import sqlite3

app = Flask(__name__, static_folder='static')
os.makedirs('static/uploads', exist_ok=True)
app.secret_key = 'super_secret_saye_key_change_in_production'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# On Render, DATABASE_URL will be set. Locally, it will fall back to SQLite.
DATABASE_URL = os.environ.get('DATABASE_URL') 
SQLITE_DB = 'db.sqlite'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        if DATABASE_URL and HAS_POSTGRES:
            try:
                db = g._database = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
                g.db_type = 'postgres'
            except Exception as e:
                print(f"CRITICAL: Postgres connection failed. Falling back to EPHEMERAL SQLite: {e}")
                db = g._database = sqlite3.connect(SQLITE_DB)
                db.row_factory = sqlite3.Row
                g.db_type = 'sqlite'
        else:
            db = g._database = sqlite3.connect(SQLITE_DB)
            db.row_factory = sqlite3.Row
            g.db_type = 'sqlite'
    return db

class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
    def execute(self, query, params=None):
        query = query.replace('%s', '?')
        if 'RETURNING id' in query.upper():
            query = query.replace('RETURNING id', '').replace('returning id', '')
        if params:
            return self.cursor.execute(query, params)
        return self.cursor.execute(query)
    def fetchone(self):
        row = self.cursor.fetchone()
        if row and not isinstance(row, dict) and hasattr(row, 'keys'):
            return dict(row) # Ensure it behaves like a dict
        return row
    def fetchall(self):
        rows = self.cursor.fetchall()
        return [dict(r) if hasattr(r, 'keys') else r for r in rows]
    def __getattr__(self, name):
        return getattr(self.cursor, name)

@contextmanager
def db_cursor():
    db = get_db()
    cur = db.cursor()
    try:
        if g.get('db_type') == 'sqlite':
            yield SQLiteCursorWrapper(cur)
        else:
            yield cur
    finally:
        cur.close()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        try:
            db = get_db()
            # Use 'id SERIAL' for Postgres, 'id INTEGER PRIMARY KEY AUTOINCREMENT' for SQLite
            id_type = 'SERIAL PRIMARY KEY' if g.db_type == 'postgres' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
            bool_true = 'TRUE' if g.db_type == 'postgres' else '1'
            bool_false = 'FALSE' if g.db_type == 'postgres' else '0'
            timestamp_type = 'TIMESTAMP' if g.db_type == 'postgres' else 'DATETIME'

            with db_cursor() as cur:
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS users (
                        id {id_type},
                        full_name TEXT NOT NULL,
                        contact TEXT NOT NULL,
                        address TEXT,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS farmers (
                        id {id_type},
                        user_id INTEGER,
                        name TEXT NOT NULL,
                        rating REAL DEFAULT 0,
                        cat TEXT,
                        img TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS products (
                        id {id_type},
                        farmer_id INTEGER,
                        name TEXT NOT NULL,
                        price TEXT,
                        cat TEXT,
                        FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS payment_methods (
                        id {id_type},
                        user_id INTEGER,
                        type TEXT NOT NULL,
                        details TEXT NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS orders (
                        id {id_type},
                        product_id INTEGER,
                        farmer_id INTEGER,
                        consumer_id INTEGER,
                        price TEXT,
                        payment_method_id INTEGER
                    )
                ''')
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS banned_contacts (
                        contact TEXT PRIMARY KEY
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS messages (
                        id {id_type},
                        sender_id INTEGER NOT NULL,
                        receiver_id INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        is_read BOOLEAN DEFAULT {bool_false},
                        timestamp {timestamp_type} DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(receiver_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                cur.execute(f'''
                    CREATE TABLE IF NOT EXISTS notifications (
                        id {id_type},
                        user_id INTEGER NOT NULL,
                        type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        is_read BOOLEAN DEFAULT {bool_false},
                        timestamp {timestamp_type} DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                
                # Seed master admin
                # We need to handle the placeholder here manually or use db_execute
                query = 'SELECT COUNT(*) as count FROM users WHERE role = %s'
                if g.db_type == 'sqlite': query = query.replace('%s', '?')
                cur.execute(query, ('admin',))
                row = cur.fetchone()
                # Row might be a dict or a tuple depending on DB
                count = row['count'] if isinstance(row, dict) or g.db_type == 'postgres' else row[0]
                
                if count == 0:
                    hashed_pw = generate_password_hash('admin')
                    ins_query = 'INSERT INTO users (full_name, contact, address, password, role) VALUES (%s, %s, %s, %s, %s)'
                    if g.db_type == 'sqlite': ins_query = ins_query.replace('%s', '?')
                    cur.execute(ins_query, ('Master Admin', 'admin', 'System', hashed_pw, 'admin'))
                
                # Resilient ALTER TABLE
                if g.db_type == 'postgres':
                    cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_img TEXT')
                    cur.execute('ALTER TABLE farmers ADD COLUMN IF NOT EXISTS banner_img TEXT')
                    cur.execute('ALTER TABLE farmers ADD COLUMN IF NOT EXISTS description TEXT')
                    cur.execute('ALTER TABLE farmers ADD COLUMN IF NOT EXISTS is_open BOOLEAN DEFAULT TRUE')
                    cur.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS img TEXT')
                    cur.execute('ALTER TABLE products ADD COLUMN IF NOT EXISTS stock INTEGER DEFAULT 0')
                    cur.execute('ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1')
                    cur.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE')
                else:
                    # SQLite: ALTER TABLE ADD COLUMN ignore if exists via try-except
                    cols = [
                        ('users', 'profile_img', 'TEXT'),
                        ('farmers', 'banner_img', 'TEXT'),
                        ('farmers', 'description', 'TEXT'),
                        ('farmers', 'is_open', 'BOOLEAN DEFAULT 1'),
                        ('products', 'img', 'TEXT'),
                        ('products', 'stock', 'INTEGER DEFAULT 0'),
                        ('orders', 'quantity', 'INTEGER DEFAULT 1'),
                        ('messages', 'is_read', 'BOOLEAN DEFAULT 0')
                    ]
                    for table, col, ctype in cols:
                        try:
                            cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ctype}')
                        except:
                            pass

                db.commit()
        except Exception as e:
            print(f"Database initialization failed: {e}")
            if 'db' in locals():
                db.rollback()

with app.app_context():
    init_db()

@app.route('/')
def index():
    return render_template('Flask.html')

# --- AUTH ENDPOINTS ---
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    full_name = data.get('full_name')
    contact = data.get('contact')
    address = data.get('address', '')
    password = data.get('password')
    role = data.get('role')
    shop_name = data.get('shop_name', full_name)
    shop_cat = data.get('shop_cat', 'veggies')
    
    if not full_name or not contact or not password or not role:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400
        
    # SECURITY: Block registering as admin
    if role == 'admin':
        return jsonify({"status": "error", "message": "Unauthorized role"}), 403
        
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT contact FROM banned_contacts WHERE contact = %s', (contact,))
        if cur.fetchone() is not None:
            return jsonify({"status": "error", "message": "This contact has been banned from the platform"}), 403

        cur.execute('SELECT id FROM users WHERE contact = %s', (contact,))
        if cur.fetchone() is not None:
            return jsonify({"status": "error", "message": "Contact/Email already registered"}), 400
            
        hashed_pw = generate_password_hash(password)
        cur.execute('INSERT INTO users (full_name, contact, address, password, role) VALUES (%s, %s, %s, %s, %s) RETURNING id',
                         (full_name, contact, address, hashed_pw, role))
        if g.db_type == 'postgres':
            user_id = cur.fetchone()['id']
        else:
            user_id = cur.lastrowid
        
        if role == 'farmer':
            img = "https://images.unsplash.com/photo-1586201327693-86619addc216?w=200" # Default rice/farm img
            cur.execute('INSERT INTO farmers (user_id, name, rating, cat, img) VALUES (%s, %s, %s, %s, %s)',
                       (user_id, shop_name, 0.0, shop_cat, img))
        
        db.commit()
    
    session['user_id'] = user_id
    session['role'] = role
    return jsonify({"status": "success", "user_id": user_id, "role": role})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    contact = data.get('contact')
    password = data.get('password')
    
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT * FROM users WHERE contact = %s', (contact,))
        user = cur.fetchone()
    
    if user and check_password_hash(user['password'], password):
        session.permanent = data.get('remember', False)
        session['user_id'] = user['id']
        session['role'] = user['role']
        return jsonify({"status": "success", "user_id": user['id'], "role": user['role'], "full_name": user['full_name']})
    
    return jsonify({"status": "error", "message": "Invalid credentials"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "success"})

@app.route('/api/me', methods=['GET'])
def get_me():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT id, full_name, role, contact, address, profile_img FROM users WHERE id = %s', (session['user_id'],))
        user = cur.fetchone()
        
        if not user:
            session.clear()
            return jsonify({"status": "error"}), 401
            
        user_data = dict(user)
        
        if user['role'] == 'farmer':
            cur.execute('SELECT id as farmer_id, name, cat, img, banner_img, description, is_open FROM farmers WHERE user_id = %s', (user['id'],))
            farmer = cur.fetchone()
            if farmer:
                user_data['farmer_id'] = farmer['farmer_id']
                user_data['shop_name'] = farmer['name']
                user_data['shop_cat'] = farmer['cat']
                user_data['shop_banner'] = farmer['banner_img']
                user_data['shop_img'] = farmer['img']
                user_data['shop_description'] = farmer['description']
                user_data['shop_is_open'] = bool(farmer['is_open'])
                
    return jsonify({"status": "success", "user": user_data, "db_type": g.get('db_type', 'unknown')})


from werkzeug.utils import secure_filename

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
    
    # Handle File Upload for Shop Image (Circular Profile)
    shop_img_url = None
    if 'shop_img' in request.files:
        file = request.files['shop_img']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            filename = f"shop_img_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
            file.save(os.path.join('static', 'uploads', filename))
            shop_img_url = f"/static/uploads/{filename}"
            
    updates = []
    params = []
    
    if full_name:
        updates.append("full_name = %s")
        params.append(full_name)
    if contact:
        updates.append("contact = %s")
        params.append(contact)
    if address:
        updates.append("address = %s")
        params.append(address)
    if profile_img_url:
        updates.append("profile_img = %s")
        params.append(profile_img_url)
        
    if shop_img_url:
        updates.append("img = %s")
        params.append(shop_img_url)
        
    with db_cursor() as cur:
        if updates:
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = %s"
            params.append(user_id)
            cur.execute(query, params)
            
        # If farmer, update shop_name
        shop_name = request.form.get('shop_name')
        if shop_name and session.get('role') == 'farmer':
            cur.execute("UPDATE farmers SET name = %s WHERE user_id = %s", (shop_name, user_id))
            if profile_img_url:
                cur.execute("UPDATE farmers SET img = %s WHERE user_id = %s", (profile_img_url, user_id))
                
        db.commit()
    
    return jsonify({"status": "success", "profile_img": profile_img_url})


@app.route('/api/shop_settings', methods=['POST'])
def update_shop_settings():
    if 'user_id' not in session or session.get('role') != 'farmer':
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    db = get_db()
    user_id = session['user_id']
    
    description = request.form.get('description')
    cat = request.form.get('cat')
    is_open = request.form.get('is_open') == 'true'
    
    # Handle File Upload for Banner
    banner_img_url = None
    if 'banner_img' in request.files:
        file = request.files['banner_img']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            filename = f"shop_banner_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
            file.save(os.path.join('static', 'uploads', filename))
            banner_img_url = f"/static/uploads/{filename}"
            
    
    # Handle File Upload for Shop Image (Circular Profile)
    shop_img_url = None
    if 'shop_img' in request.files:
        file = request.files['shop_img']
        if file and file.filename:
            ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
            filename = f"shop_img_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
            file.save(os.path.join('static', 'uploads', filename))
            shop_img_url = f"/static/uploads/{filename}"
            
    updates = []
    params = []
    
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    if cat is not None:
        updates.append("cat = %s")
        params.append(cat)
    if is_open is not None:
        updates.append("is_open = %s")
        params.append(bool(is_open))
    if banner_img_url:
        updates.append("banner_img = %s")
        params.append(banner_img_url)
        
    if shop_img_url:
        updates.append("img = %s")
        params.append(shop_img_url)
        
    with db_cursor() as cur:
        if updates:
            query = f"UPDATE farmers SET {', '.join(updates)} WHERE user_id = %s"
            params.append(user_id)
            cur.execute(query, params)
            db.commit()
        
    return jsonify({"status": "success", "banner_img": banner_img_url})

@app.route('/api/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')
    
    if not current_password or not new_password:
        return jsonify({"status": "error", "message": "Missing fields"}), 400
        
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT password FROM users WHERE id = %s', (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password'], current_password):
            return jsonify({"status": "error", "message": "Incorrect current password"}), 401
            
        hashed_pw = generate_password_hash(new_password)
        cur.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_pw, session['user_id']))
        db.commit()
    
    return jsonify({"status": "success", "message": "Password updated successfully"})

# --- PRODUCTS & FARMERS ENDPOINTS ---
@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    db = get_db()
    if request.method == 'POST':
        if 'user_id' not in session or session['role'] != 'farmer':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        with db_cursor() as cur:
            cur.execute('SELECT id FROM farmers WHERE user_id = %s', (session['user_id'],))
            farmer = cur.fetchone()
            if not farmer:
                return jsonify({"status": "error"}), 400
                
            name = request.form.get('name')
            price = request.form.get('price')
            cat = request.form.get('cat', 'other')
            stock = int(request.form.get('stock', 0))
            
            img_url = None
            if 'product_img' in request.files:
                file = request.files['product_img']
                if file and file.filename:
                    import uuid
                    ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
                    filename = f"prod_{farmer['id']}_{uuid.uuid4().hex[:8]}.{ext}"
                    file.save(os.path.join('static', 'uploads', filename))
                    img_url = f"/static/uploads/{filename}"
            
            cur.execute('INSERT INTO products (farmer_id, name, price, cat, img, stock) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id', 
                             (farmer['id'], name, price, cat, img_url, stock))
            if g.db_type == 'postgres':
                new_id = cur.fetchone()['id']
            else:
                new_id = cur.lastrowid
            db.commit()
            
            return jsonify({"status": "success", "item": {"id": new_id, "farmer_id": farmer['id'], "name": name, "price": price, "cat": cat, "img": img_url, "stock": stock}})
    
    with db_cursor() as cur:
        cur.execute('SELECT * FROM products')
        products = [dict(row) for row in cur.fetchall()]
    return jsonify(products)

@app.route('/api/products/<int:id>', methods=['PUT'])
def edit_product(id):
    if 'user_id' not in session or session['role'] != 'farmer':
        return jsonify({"status": "error"}), 401
        
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT id FROM farmers WHERE user_id = %s', (session['user_id'],))
        farmer = cur.fetchone()
        if not farmer:
            return jsonify({"status": "error"}), 400
            
        name = request.form.get('name')
        price = request.form.get('price')
        cat = request.form.get('cat')
        stock = request.form.get('stock')
        
        img_url = None
        if 'product_img' in request.files:
            file = request.files['product_img']
            if file and file.filename:
                import uuid
                ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'
                filename = f"prod_{farmer['id']}_{uuid.uuid4().hex[:8]}.{ext}"
                file.save(os.path.join('static', 'uploads', filename))
                img_url = f"/static/uploads/{filename}"
                
        updates = []
        params = []
        if name is not None:
            updates.append("name = %s")
            params.append(name)
        if price is not None:
            updates.append("price = %s")
            params.append(price)
        if cat is not None:
            updates.append("cat = %s")
            params.append(cat)
        if stock is not None:
            updates.append("stock = %s")
            params.append(int(stock))
        if img_url is not None:
            updates.append("img = %s")
            params.append(img_url)
            
        if updates:
            query = f"UPDATE products SET {', '.join(updates)} WHERE id = %s AND farmer_id = %s"
            params.extend([id, farmer['id']])
            cur.execute(query, params)
            db.commit()
            
    return jsonify({"status": "success", "img": img_url})

@app.route('/api/products/<int:id>', methods=['DELETE'])
def delete_product(id):
    if 'user_id' not in session or session['role'] != 'farmer':
        return jsonify({"status": "error"}), 401
        
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT id FROM farmers WHERE user_id = %s', (session['user_id'],))
        farmer = cur.fetchone()
        if not farmer:
            return jsonify({"status": "error"}), 400
            
        cur.execute('DELETE FROM products WHERE id = %s AND farmer_id = %s', (id, farmer['id']))
        db.commit()
    return jsonify({"status": "success"})

@app.route('/api/farmers')
def get_farmers():
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT * FROM farmers')
        farmers = [dict(row) for row in cur.fetchall()]
    return jsonify(farmers)

# --- PAYMENTS ENDPOINTS ---
@app.route('/api/payments', methods=['GET', 'POST'])
def handle_payments():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    db = get_db()
    if request.method == 'POST':
        data = request.json
        p_type = data.get('type')
        details = data.get('details')
        with db_cursor() as cur:
            cur.execute('INSERT INTO payment_methods (user_id, type, details) VALUES (%s, %s, %s) RETURNING id',
                             (session['user_id'], p_type, details))
            if g.db_type == 'postgres':
                new_id = cur.fetchone()['id']
            else:
                new_id = cur.lastrowid
            db.commit()
        return jsonify({"status": "success", "method": {"id": new_id, "type": p_type, "details": details}})
        
    with db_cursor() as cur:
        cur.execute('SELECT id, type, details FROM payment_methods WHERE user_id = %s', (session['user_id'],))
        methods = [dict(row) for row in cur.fetchall()]
    return jsonify(methods)

@app.route('/api/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session or session['role'] != 'consumer':
        return jsonify({"status": "error", "message": "Must be logged in as consumer to buy"}), 401
        
    data = request.json
    product_id = data.get('product_id')
    payment_method_id = data.get('payment_method_id')
    quantity = int(data.get('quantity', 1))
    
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT farmer_id, name, price, stock FROM products WHERE id = %s', (product_id,))
        product = cur.fetchone()
        if not product:
            return jsonify({"status": "error", "message": "Product not found"}), 404
        if product['stock'] < quantity:
            return jsonify({"status": "error", "message": "Not enough stock"}), 400
            
        cur.execute('UPDATE products SET stock = stock - %s WHERE id = %s', (quantity, product_id,))
        cur.execute('INSERT INTO orders (product_id, farmer_id, consumer_id, price, payment_method_id, quantity) VALUES (%s, %s, %s, %s, %s, %s)',
                   (product_id, product['farmer_id'], session['user_id'], product['price'], payment_method_id, quantity))
                   
        # Send automated order chat
        cur.execute('SELECT user_id, name FROM farmers WHERE id = %s', (product['farmer_id'],))
        farmer_row = cur.fetchone()
        if farmer_row:
            farmer_user_id = farmer_row['user_id']
            farmer_name = farmer_row['name']
            
            message_content = f"🛒 New Order! I would like to buy {quantity}x of {product['name']}. Please prepare my order."
            cur.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (%s, %s, %s)',
                       (session['user_id'], farmer_user_id, message_content))
                       
            # Automated reply from Farmer to Customer
            cur.execute('SELECT type FROM payment_methods WHERE id = %s', (payment_method_id,))
            pm_row = cur.fetchone()
            payment_type = pm_row['type'].upper() if pm_row else "UNKNOWN"
            
            reply_content = (f"Hello! Thank you for your order. Please confirm your delivery details below:\n\n"
                             f" - Full Name:\n"
                             f" - Contact No.:\n"
                             f" - Delivery Address:\n\n"
                             f"Your selected payment method is: {payment_type}.")
            
            cur.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (%s, %s, %s)',
                       (farmer_user_id, session['user_id'], reply_content))
            
            # CREATE NOTIFICATIONS
            # 1. Notify Farmer about new order
            cur.execute('INSERT INTO notifications (user_id, type, content) VALUES (%s, %s, %s)',
                       (farmer_user_id, 'order', f"New order received for {quantity}x {product['name']}!"))
            
            # 2. Notify Consumer about successful order
            cur.execute('INSERT INTO notifications (user_id, type, content) VALUES (%s, %s, %s)',
                       (session['user_id'], 'order', f"Order placed successfully for {product['name']}!"))
                       
        db.commit()
    
    return jsonify({"status": "success", "message": "Order placed successfully!"})


# --- ADMIN ENDPOINTS ---
@app.route('/api/admin/users', methods=['GET'])
def get_admin_users():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    db = get_db()
    with db_cursor() as cur:
        cur.execute('SELECT id, full_name, contact, address, role FROM users WHERE role != %s', ('admin',))
        users = [dict(row) for row in cur.fetchall()]
    return jsonify(users)

@app.route('/api/admin/users/<int:id>', methods=['DELETE'])
def delete_admin_user(id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    ban = request.args.get('ban', 'false').lower() == 'true'
    db = get_db()
    with db_cursor() as cur:
        if ban:
            cur.execute('SELECT contact FROM users WHERE id = %s', (id,))
            user = cur.fetchone()
            if user:
                # Add to banned contacts, ignore if already there
                cur.execute('INSERT INTO banned_contacts (contact) VALUES (%s) ON CONFLICT (contact) DO NOTHING', (user['contact'],))
        
        # Due to CASCADE not always being enabled by default in sqlite depending on pragmas, we manually delete
        cur.execute('SELECT id FROM farmers WHERE user_id = %s', (id,))
        farmer = cur.fetchone()
        if farmer:
            cur.execute('DELETE FROM products WHERE farmer_id = %s', (farmer['id'],))
            cur.execute('DELETE FROM farmers WHERE user_id = %s', (id,))
            
        cur.execute('DELETE FROM payment_methods WHERE user_id = %s', (id,))
        cur.execute('DELETE FROM orders WHERE consumer_id = %s', (id,))
        
        cur.execute('DELETE FROM users WHERE id = %s', (id,))
        db.commit()
    return jsonify({"status": "success"})

@app.route('/api/admin/orders', methods=['GET'])
def get_admin_orders():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    db = get_db()
    query = '''
        SELECT 
            o.id, o.price, o.quantity,
            p.name as product_name, 
            u.full_name as consumer_name, 
            f.name as farmer_name
        FROM orders o
        JOIN products p ON o.product_id = p.id
        JOIN users u ON o.consumer_id = u.id
        JOIN farmers f ON o.farmer_id = f.id
        ORDER BY o.id DESC
    '''
    with db_cursor() as cur:
        cur.execute(query)
        orders = [dict(row) for row in cur.fetchall()]
    return jsonify(orders)


# --- CHAT ENDPOINTS ---
@app.route('/api/messages/contacts', methods=['GET'])
def get_chat_contacts():
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    db = get_db()
    user_id = session['user_id']
    
    # Get all users we have exchanged messages with, plus unread count
    query = '''
        SELECT DISTINCT u.id, u.full_name, u.role, u.profile_img, f.name as shop_name, f.img as shop_img,
               (SELECT COUNT(*) FROM messages m2 WHERE m2.sender_id = u.id AND m2.receiver_id = %s AND m2.is_read = FALSE) as unread_count
        FROM users u
        LEFT JOIN farmers f ON u.id = f.user_id
        JOIN messages m ON (u.id = m.sender_id OR u.id = m.receiver_id)
        WHERE (m.sender_id = %s OR m.receiver_id = %s) AND u.id != %s
    '''
    with db_cursor() as cur:
        cur.execute(query, (user_id, user_id, user_id, user_id))
        contacts = [dict(row) for row in cur.fetchall()]
    return jsonify(contacts)

@app.route('/api/messages/<int:other_user_id>', methods=['GET'])
def get_messages(other_user_id):
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    db = get_db()
    user_id = session['user_id']
    
    query = '''
        SELECT m.id, m.sender_id, m.receiver_id, m.content, m.timestamp
        FROM messages m
        WHERE (m.sender_id = %s AND m.receiver_id = %s) 
           OR (m.sender_id = %s AND m.receiver_id = %s)
        ORDER BY m.timestamp ASC
    '''
    with db_cursor() as cur:
        cur.execute(query, (user_id, other_user_id, other_user_id, user_id))
        messages = [dict(row) for row in cur.fetchall()]
        
        # Mark as read
        cur.execute('UPDATE messages SET is_read = TRUE WHERE sender_id = %s AND receiver_id = %s', (other_user_id, user_id))
        db.commit()
    
    return jsonify(messages)

@app.route('/api/messages/<int:receiver_id>', methods=['POST'])
def send_message(receiver_id):
    if 'user_id' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    content = request.json.get('content')
    if not content:
        return jsonify({"status": "error", "message": "Message cannot be empty"}), 400
        
    db = get_db()
    user_id = session['user_id']
    
    with db.cursor() as cur:
        cur.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (%s, %s, %s)',
                   (user_id, receiver_id, content))
                   
        # Notification for receiver
        cur.execute('SELECT full_name FROM users WHERE id = %s', (user_id,))
        sender = cur.fetchone()
        sender_name = sender['full_name'] if sender else "Someone"
        
        cur.execute('INSERT INTO notifications (user_id, type, content) VALUES (%s, %s, %s)',
                   (receiver_id, 'message', f"New message from {sender_name}: {content[:30]}..."))
                   
        db.commit()
    
    return jsonify({"status": "success"})


# --- NOTIFICATIONS ENDPOINTS ---
@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT * FROM notifications WHERE user_id = %s ORDER BY timestamp DESC LIMIT 20', (session['user_id'],))
        notifications = [dict(row) for row in cur.fetchall()]
    return jsonify(notifications)

@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    db = get_db()
    with db.cursor() as cur:
        cur.execute('UPDATE notifications SET is_read = TRUE WHERE user_id = %s', (session['user_id'],))
        db.commit()
    return jsonify({"status": "success"})


if __name__ == '__main__':
    app.run(debug=True)