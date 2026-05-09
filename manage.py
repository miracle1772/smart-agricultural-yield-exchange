import sqlite3
import os
from flask import Flask, render_template, request, jsonify, g, session
from datetime import timedelta
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='static')
os.makedirs('static/uploads', exist_ok=True)
app.secret_key = 'super_secret_saye_key_change_in_production'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
DATABASE = 'db.sqlite'

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                contact TEXT NOT NULL,
                address TEXT,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS farmers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT NOT NULL,
                rating REAL DEFAULT 0,
                cat TEXT,
                img TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                farmer_id INTEGER,
                name TEXT NOT NULL,
                price TEXT,
                cat TEXT,
                FOREIGN KEY(farmer_id) REFERENCES farmers(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT NOT NULL,
                details TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                farmer_id INTEGER,
                consumer_id INTEGER,
                price TEXT,
                payment_method_id INTEGER
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS banned_contacts (
                contact TEXT PRIMARY KEY
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_read BOOLEAN DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(receiver_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                is_read BOOLEAN DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # Seed master admin
        cur = db.execute('SELECT COUNT(*) FROM users WHERE role = "admin"')
        if cur.fetchone()[0] == 0:
            hashed_pw = generate_password_hash('admin')
            db.execute('INSERT INTO users (full_name, contact, address, password, role) VALUES (?, ?, ?, ?, ?)',
                       ('Master Admin', 'admin', 'System', hashed_pw, 'admin'))
            
        
        # Add profile_img column if it doesn't exist
        try:
            db.execute('ALTER TABLE users ADD COLUMN profile_img TEXT')
        except sqlite3.OperationalError:
            pass

        
        # Add shop settings columns if they don't exist
        try:
            db.execute('ALTER TABLE farmers ADD COLUMN banner_img TEXT')
            db.execute('ALTER TABLE farmers ADD COLUMN description TEXT')
            db.execute('ALTER TABLE farmers ADD COLUMN is_open BOOLEAN DEFAULT 1')
        except sqlite3.OperationalError:
            pass

        # Add products columns if they don't exist
        try:
            db.execute('ALTER TABLE products ADD COLUMN img TEXT')
            db.execute('ALTER TABLE products ADD COLUMN stock INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        # Add orders quantity column if it doesn't exist
        try:
            db.execute('ALTER TABLE orders ADD COLUMN quantity INTEGER DEFAULT 1')
        except sqlite3.OperationalError:
            pass

        # Add messages is_read column if it doesn't exist
        try:
            db.execute('ALTER TABLE messages ADD COLUMN is_read BOOLEAN DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        db.commit()

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
    
    cur = db.execute('SELECT contact FROM banned_contacts WHERE contact = ?', (contact,))
    if cur.fetchone() is not None:
        return jsonify({"status": "error", "message": "This contact has been banned from the platform"}), 403

    cur = db.execute('SELECT id FROM users WHERE contact = ?', (contact,))
    if cur.fetchone() is not None:
        return jsonify({"status": "error", "message": "Contact/Email already registered"}), 400
        
    hashed_pw = generate_password_hash(password)
    cur = db.execute('INSERT INTO users (full_name, contact, address, password, role) VALUES (?, ?, ?, ?, ?)',
                     (full_name, contact, address, hashed_pw, role))
    user_id = cur.lastrowid
    
    if role == 'farmer':
        img = "https://images.unsplash.com/photo-1595152772835-219674b2a8a6?w=200" # Default img
        db.execute('INSERT INTO farmers (user_id, name, rating, cat, img) VALUES (?, ?, ?, ?, ?)',
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
    cur = db.execute('SELECT * FROM users WHERE contact = ?', (contact,))
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
    cur = db.execute('SELECT id, full_name, role, contact, address, profile_img FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    
    if not user:
        session.clear()
        return jsonify({"status": "error"}), 401
        
    user_data = dict(user)
    
    if user['role'] == 'farmer':
        cur = db.execute('SELECT id as farmer_id, name, cat, img, banner_img, description, is_open FROM farmers WHERE user_id = ?', (user['id'],))
        farmer = cur.fetchone()
        if farmer:
            user_data['farmer_id'] = farmer['farmer_id']
            user_data['shop_name'] = farmer['name']
            user_data['shop_cat'] = farmer['cat']
            user_data['shop_banner'] = farmer['banner_img']
            user_data['shop_img'] = farmer['img']
            user_data['shop_description'] = farmer['description']
            user_data['shop_is_open'] = bool(farmer['is_open'])
            
    return jsonify({"status": "success", "user": user_data})


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
        
    
    if shop_img_url:
        updates.append("img = ?")
        params.append(shop_img_url)
        
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
        updates.append("description = ?")
        params.append(description)
    if cat is not None:
        updates.append("cat = ?")
        params.append(cat)
    if is_open is not None:
        updates.append("is_open = ?")
        params.append(int(is_open))
    if banner_img_url:
        updates.append("banner_img = ?")
        params.append(banner_img_url)
        
    
    if shop_img_url:
        updates.append("img = ?")
        params.append(shop_img_url)
        
    if updates:
        query = f"UPDATE farmers SET {', '.join(updates)} WHERE user_id = ?"
        params.append(user_id)
        db.execute(query, params)
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
    cur = db.execute('SELECT password FROM users WHERE id = ?', (session['user_id'],))
    user = cur.fetchone()
    
    if not user or not check_password_hash(user['password'], current_password):
        return jsonify({"status": "error", "message": "Incorrect current password"}), 401
        
    hashed_pw = generate_password_hash(new_password)
    db.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_pw, session['user_id']))
    db.commit()
    
    return jsonify({"status": "success", "message": "Password updated successfully"})

# --- PRODUCTS & FARMERS ENDPOINTS ---
@app.route('/api/products', methods=['GET', 'POST'])
def handle_products():
    db = get_db()
    if request.method == 'POST':
        if 'user_id' not in session or session['role'] != 'farmer':
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
            
        cur = db.execute('SELECT id FROM farmers WHERE user_id = ?', (session['user_id'],))
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
        
        cur = db.execute('INSERT INTO products (farmer_id, name, price, cat, img, stock) VALUES (?, ?, ?, ?, ?, ?)', 
                         (farmer['id'], name, price, cat, img_url, stock))
        db.commit()
        
        return jsonify({"status": "success", "item": {"id": cur.lastrowid, "farmer_id": farmer['id'], "name": name, "price": price, "cat": cat, "img": img_url, "stock": stock}})
    
    cur = db.execute('SELECT * FROM products')
    products = [dict(row) for row in cur.fetchall()]
    return jsonify(products)

@app.route('/api/products/<int:id>', methods=['PUT'])
def edit_product(id):
    if 'user_id' not in session or session['role'] != 'farmer':
        return jsonify({"status": "error"}), 401
        
    db = get_db()
    cur = db.execute('SELECT id FROM farmers WHERE user_id = ?', (session['user_id'],))
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
        updates.append("name = ?")
        params.append(name)
    if price is not None:
        updates.append("price = ?")
        params.append(price)
    if cat is not None:
        updates.append("cat = ?")
        params.append(cat)
    if stock is not None:
        updates.append("stock = ?")
        params.append(int(stock))
    if img_url is not None:
        updates.append("img = ?")
        params.append(img_url)
        
    if updates:
        query = f"UPDATE products SET {', '.join(updates)} WHERE id = ? AND farmer_id = ?"
        params.extend([id, farmer['id']])
        db.execute(query, params)
        db.commit()
        
    return jsonify({"status": "success", "img": img_url})

@app.route('/api/products/<int:id>', methods=['DELETE'])
def delete_product(id):
    if 'user_id' not in session or session['role'] != 'farmer':
        return jsonify({"status": "error"}), 401
        
    db = get_db()
    cur = db.execute('SELECT id FROM farmers WHERE user_id = ?', (session['user_id'],))
    farmer = cur.fetchone()
    if not farmer:
        return jsonify({"status": "error"}), 400
        
    db.execute('DELETE FROM products WHERE id = ? AND farmer_id = ?', (id, farmer['id']))
    db.commit()
    return jsonify({"status": "success"})

@app.route('/api/farmers')
def get_farmers():
    db = get_db()
    cur = db.execute('SELECT * FROM farmers')
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
        cur = db.execute('INSERT INTO payment_methods (user_id, type, details) VALUES (?, ?, ?)',
                         (session['user_id'], p_type, details))
        db.commit()
        return jsonify({"status": "success", "method": {"id": cur.lastrowid, "type": p_type, "details": details}})
        
    cur = db.execute('SELECT id, type, details FROM payment_methods WHERE user_id = ?', (session['user_id'],))
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
    cur = db.execute('SELECT farmer_id, name, price, stock FROM products WHERE id = ?', (product_id,))
    product = cur.fetchone()
    if not product:
        return jsonify({"status": "error", "message": "Product not found"}), 404
    if product['stock'] < quantity:
        return jsonify({"status": "error", "message": "Not enough stock"}), 400
        
    db.execute('UPDATE products SET stock = stock - ? WHERE id = ?', (quantity, product_id,))
    db.execute('INSERT INTO orders (product_id, farmer_id, consumer_id, price, payment_method_id, quantity) VALUES (?, ?, ?, ?, ?, ?)',
               (product_id, product['farmer_id'], session['user_id'], product['price'], payment_method_id, quantity))
               
    # Send automated order chat
    cur = db.execute('SELECT user_id, name FROM farmers WHERE id = ?', (product['farmer_id'],))
    farmer_row = cur.fetchone()
    if farmer_row:
        farmer_user_id = farmer_row['user_id']
        farmer_name = farmer_row['name']
        
        message_content = f"🛒 New Order! I would like to buy {quantity}x of {product['name']}. Please prepare my order."
        db.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)',
                   (session['user_id'], farmer_user_id, message_content))
                   
        # Automated reply from Farmer to Customer
        cur = db.execute('SELECT type FROM payment_methods WHERE id = ?', (payment_method_id,))
        pm_row = cur.fetchone()
        payment_type = pm_row['type'].upper() if pm_row else "UNKNOWN"
        
        reply_content = (f"Hello! Thank you for your order. Please confirm your delivery details below:\n\n"
                         f" - Full Name:\n"
                         f" - Contact No.:\n"
                         f" - Delivery Address:\n\n"
                         f"Your selected payment method is: {payment_type}.")
        
        db.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)',
                   (farmer_user_id, session['user_id'], reply_content))
        
        # CREATE NOTIFICATIONS
        # 1. Notify Farmer about new order
        db.execute('INSERT INTO notifications (user_id, type, content) VALUES (?, ?, ?)',
                   (farmer_user_id, 'order', f"New order received for {quantity}x {product['name']}!"))
        
        # 2. Notify Consumer about successful order
        db.execute('INSERT INTO notifications (user_id, type, content) VALUES (?, ?, ?)',
                   (session['user_id'], 'order', f"Order placed successfully for {product['name']}!"))
                   
    db.commit()
    
    return jsonify({"status": "success", "message": "Order placed successfully!"})


# --- ADMIN ENDPOINTS ---
@app.route('/api/admin/users', methods=['GET'])
def get_admin_users():
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    db = get_db()
    cur = db.execute('SELECT id, full_name, contact, address, role FROM users WHERE role != "admin"')
    users = [dict(row) for row in cur.fetchall()]
    return jsonify(users)

@app.route('/api/admin/users/<int:id>', methods=['DELETE'])
def delete_admin_user(id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({"status": "error", "message": "Unauthorized"}), 403
        
    ban = request.args.get('ban', 'false').lower() == 'true'
    db = get_db()
    
    if ban:
        cur = db.execute('SELECT contact FROM users WHERE id = ?', (id,))
        user = cur.fetchone()
        if user:
            # Add to banned contacts, ignore if already there
            db.execute('INSERT OR IGNORE INTO banned_contacts (contact) VALUES (?)', (user['contact'],))
    
    # Due to CASCADE not always being enabled by default in sqlite depending on pragmas, we manually delete
    cur = db.execute('SELECT id FROM farmers WHERE user_id = ?', (id,))
    farmer = cur.fetchone()
    if farmer:
        db.execute('DELETE FROM products WHERE farmer_id = ?', (farmer['id'],))
        db.execute('DELETE FROM farmers WHERE user_id = ?', (id,))
        
    db.execute('DELETE FROM payment_methods WHERE user_id = ?', (id,))
    db.execute('DELETE FROM orders WHERE consumer_id = ?', (id,))
    
    db.execute('DELETE FROM users WHERE id = ?', (id,))
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
    cur = db.execute(query)
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
               (SELECT COUNT(*) FROM messages m2 WHERE m2.sender_id = u.id AND m2.receiver_id = ? AND m2.is_read = 0) as unread_count
        FROM users u
        LEFT JOIN farmers f ON u.id = f.user_id
        JOIN messages m ON (u.id = m.sender_id OR u.id = m.receiver_id)
        WHERE (m.sender_id = ? OR m.receiver_id = ?) AND u.id != ?
    '''
    cur = db.execute(query, (user_id, user_id, user_id, user_id))
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
        WHERE (m.sender_id = ? AND m.receiver_id = ?) 
           OR (m.sender_id = ? AND m.receiver_id = ?)
        ORDER BY m.timestamp ASC
    '''
    cur = db.execute(query, (user_id, other_user_id, other_user_id, user_id))
    messages = [dict(row) for row in cur.fetchall()]
    
    # Mark as read
    db.execute('UPDATE messages SET is_read = 1 WHERE sender_id = ? AND receiver_id = ?', (other_user_id, user_id))
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
    
    db.execute('INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)',
               (user_id, receiver_id, content))
               
    # Notification for receiver
    cur = db.execute('SELECT full_name FROM users WHERE id = ?', (user_id,))
    sender = cur.fetchone()
    sender_name = sender['full_name'] if sender else "Someone"
    
    db.execute('INSERT INTO notifications (user_id, type, content) VALUES (?, ?, ?)',
               (receiver_id, 'message', f"New message from {sender_name}: {content[:30]}..."))
               
    db.commit()
    
    return jsonify({"status": "success"})


# --- NOTIFICATIONS ENDPOINTS ---
@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    db = get_db()
    cur = db.execute('SELECT * FROM notifications WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20', (session['user_id'],))
    notifications = [dict(row) for row in cur.fetchall()]
    return jsonify(notifications)

@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' not in session:
        return jsonify({"status": "error"}), 401
    db = get_db()
    db.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (session['user_id'],))
    db.commit()
    return jsonify({"status": "success"})


if __name__ == '__main__':
    app.run(debug=True)