from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
import secrets
import hashlib
import json
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import os

from db_helper import get_db, execute_query

app = Flask(__name__)

# IMPORTANT: Use a fixed secret key - don't change on each restart
app.secret_key = '011235'  # Keep this consistent
app.config['SECRET_KEY'] = '011235'
app.config['SESSION_COOKIE_NAME'] = 'pos_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True if using HTTPS
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_PERMANENT'] = True

# Configure CORS properly for credentials
CORS(app, supports_credentials=True, origins=['http://localhost:5000', 'https://connectlinkhardware.onrender.com'])

user_sessions = {}

# ==================== HELPER FUNCTIONS ====================

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_transaction_number():
    """Generate unique transaction number"""
    date_str = datetime.now().strftime('%Y%m%d')
    random_part = secrets.token_hex(4).upper()
    return f"REC-{date_str}-{random_part}-CONLINK"

def login_required(f):
    """Decorator to check if user is logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to check if user is admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        if session.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function

def get_user_by_id(user_id):
    """Get user by ID"""
    query = "SELECT id, username, email, full_name, role, created_at FROM users WHERE id = %s"
    result = execute_query(query, (user_id,), fetch_one=True)
    return result

# ==================== DATABASE INITIALIZATION ====================

def init_database():
    """Initialize database tables"""
    # Users table
    execute_query("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            email VARCHAR(120) UNIQUE NOT NULL,
            password_hash VARCHAR(200) NOT NULL,
            full_name VARCHAR(100),
            role VARCHAR(20) DEFAULT 'cashier',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP
        )
    """, commit=True)
    
    # Products table
    execute_query("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            category VARCHAR(50) NOT NULL,
            unit_type VARCHAR(20) DEFAULT 'piece',
            unit_details VARCHAR(100),
            price DECIMAL(10,2) NOT NULL,
            stock INTEGER DEFAULT 0,
            min_stock_level INTEGER DEFAULT 10,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """, commit=True)

    # Remove old columns if they exist
    execute_query("""
        ALTER TABLE products 
            DROP COLUMN IF EXISTS barcode,
            DROP COLUMN IF EXISTS icon
    """, commit=True)

    execute_query("""
        ALTER TABLE products 
        ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE
    """, commit=True)

    # Rename price to sell_price if it exists
    try:
        check_query = """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'products' AND column_name = 'price'
        """
        result = execute_query(check_query, fetch_one=True)
        
        if result:
            execute_query("""
                ALTER TABLE products 
                RENAME COLUMN price TO sell_price
            """, commit=True)
            print("Renamed price column to sell_price")
    except Exception as e:
        print(f"Note: Could not rename column: {e}")

    # Add buy_price column if it doesn't exist
    execute_query("""
        ALTER TABLE transaction_items 
        ADD COLUMN IF NOT EXISTS unit_type VARCHAR(20) DEFAULT 'piece';
    """, commit=True)
    
    # Transactions table - CREATE OR UPDATE
    execute_query("""
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            transaction_number VARCHAR(50) UNIQUE NOT NULL,
            user_id INTEGER REFERENCES users(id),
            payment_method VARCHAR(20) NOT NULL,
            status VARCHAR(20) DEFAULT 'completed',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """, commit=True)
    
    # ADD ALL MISSING COLUMNS TO TRANSACTIONS TABLE
    execute_query("""
        ALTER TABLE transaction_items 
        ADD COLUMN IF NOT EXISTS unit_details VARCHAR(100) DEFAULT '';
    """, commit=True)
    
    execute_query("""
        ALTER TABLE transactions 
        ADD COLUMN IF NOT EXISTS tax DECIMAL(10,2) DEFAULT 0.00
    """, commit=True)
    
    execute_query("""
        ALTER TABLE transactions 
        ADD COLUMN IF NOT EXISTS tax_rate DECIMAL(5,2) DEFAULT 10.0
    """, commit=True)
    
    execute_query("""
        ALTER TABLE transactions 
        ADD COLUMN IF NOT EXISTS total DECIMAL(10,2) DEFAULT 0.00
    """, commit=True)
    
    execute_query("""
        ALTER TABLE transactions 
        ADD COLUMN IF NOT EXISTS amount_paid DECIMAL(10,2) DEFAULT 0.00
    """, commit=True)
    
    execute_query("""
        ALTER TABLE transactions 
        ADD COLUMN IF NOT EXISTS change_amount DECIMAL(10,2) DEFAULT 0.00
    """, commit=True)
    
    # Transaction Items table
    execute_query("""
        CREATE TABLE IF NOT EXISTS transaction_items (
            id SERIAL PRIMARY KEY,
            transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id),
            quantity INTEGER NOT NULL,
            price_at_time DECIMAL(10,2) NOT NULL,
            subtotal DECIMAL(10,2) NOT NULL
        )
    """, commit=True)
    
    # Categories table
    execute_query("""
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,
            display_order INTEGER DEFAULT 0
        )
    """, commit=True)
    
    # Create default admin user if not exists
    admin_check = execute_query("SELECT id FROM users WHERE username = 'admin'", fetch_one=True)
    if not admin_check:
        admin_password = hash_password('admin123')
        execute_query("""
            INSERT INTO users (username, email, password_hash, full_name, role)
            VALUES (%s, %s, %s, %s, %s)
        """, ('admin', 'admin@connectlink.com', admin_password, 'System Administrator', 'admin'), commit=True)
        print("Default admin user created - username: admin, password: admin123")
    
    # Create default categories
    default_categories = [
        ('Hand Tools', 1),
        ('Power Tools', 2),
        ('Fasteners', 3),
        ('Paint', 4),
        ('Lumber', 5),
        ('Electrical', 6),
        ('Plumbing', 7),
        ('Safety', 8)
    ]
    
    for cat_name, order in default_categories:
        cat_check = execute_query("SELECT id FROM categories WHERE name = %s", (cat_name,), fetch_one=True)
        if not cat_check:
            execute_query("""
                INSERT INTO categories (name, display_order)
                VALUES (%s, %s)
            """, (cat_name, order), commit=True)

# ==================== PRODUCT FETCH FUNCTION ====================

def run1():
    """Fetch all active products from the Products table"""
    query = """
        SELECT id, name, category, unit_type, unit_details, buy_price, sell_price, stock, 
               min_stock_level, description, created_at, updated_at
        FROM products
        WHERE is_active = TRUE
        ORDER BY name
    """
    result = execute_query(query, fetch_all=True)
    
    products = []
    if result:
        for row in result:
            products.append({
                'id': row[0],
                'name': row[1],
                'category': row[2],
                'unit_type': row[3],
                'unit_details': row[4],
                'buy_price': float(row[5]) if row[5] else 0.00,
                'sell_price': float(row[6]) if row[6] else 0.00,  # Make sure this exists
                'stock': row[7] if row[7] else 0,
                'min_stock_level': row[8] if row[8] else 10,
                'description': row[9] if row[9] else '',
                'created_at': row[10].isoformat() if row[10] else None,
                'updated_at': row[11].isoformat() if row[11] else None,
                'low_stock': row[7] < row[8] if row[7] and row[8] else False
            })
    
    return products

# ==================== USER AUTHENTICATION ====================

@app.route('/api/login', methods=['POST'])
def api_login():
    """API endpoint for login"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    password_hash = hash_password(password)
    
    query = "SELECT id, username, email, full_name, role FROM users WHERE username = %s AND password_hash = %s AND is_active = TRUE"
    user = execute_query(query, (username, password_hash), fetch_one=True)
    
    if user:
        # Set session as permanent
        session.permanent = True
        session['user_id'] = user[0]
        session['username'] = user[1]
        session['full_name'] = user[3]
        session['role'] = user[4]
        
        # Update last login
        execute_query("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s", (user[0],), commit=True)
        
        return jsonify({
            'success': True,
            'user': {
                'id': user[0],
                'username': user[1],
                'email': user[2],
                'full_name': user[3],
                'role': user[4]
            },
            'message': 'Login successful'
        })
    
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    """Check if user is authenticated via session"""
    if 'user_id' in session:
        return jsonify({
            'authenticated': True,
            'user': {
                'id': session['user_id'],
                'username': session.get('username'),
                'full_name': session.get('full_name'),
                'role': session.get('role')
            }
        })
    return jsonify({'authenticated': False}), 401

# ==================== PRODUCT MANAGEMENT ====================

@app.route('/api/products', methods=['GET'])
@login_required
def get_products_api():
    """API endpoint to fetch products using run1()"""
    category = request.args.get('category')
    search = request.args.get('search')
    
    # Get all products using run1
    all_products = run1()
    
    # Apply filters
    filtered_products = all_products
    if category and category != 'all':
        filtered_products = [p for p in filtered_products if p['category'] == category]
    if search:
        search_lower = search.lower()
        filtered_products = [p for p in filtered_products 
                            if search_lower in p['name'].lower() 
                            or (p.get('unit_details') and search_lower in p['unit_details'].lower())]
    
    return jsonify({
        'success': True,
        'products': filtered_products,
        'total': len(filtered_products)
    })

@app.route('/api/products/<int:product_id>', methods=['GET'])
@login_required
def get_product(product_id):
    query = "SELECT id, name, category, unit_type, unit_details, buy_price, sell_price, stock, min_stock_level, description FROM products WHERE id = %s"
    product = execute_query(query, (product_id,), fetch_one=True)
    
    if not product:
        return jsonify({'error': 'Product not found'}), 404
    
    return jsonify({
        'success': True,
        'product': {
            'id': product[0],
            'name': product[1],
            'category': product[2],
            'unit_type': product[3],
            'unit_details': product[4],
            'buy_price': float(product[5]) if product[5] else 0.00,
            'sell_price': float(product[6]),
            'stock': product[7],
            'min_stock_level': product[8],
            'description': product[9]
        }
    })

@app.route('/api/products', methods=['POST'])
@login_required
def create_product():
    """Create new product"""
    data = request.json
    
    # Validate required fields
    if 'sell_price' not in data:
        return jsonify({'error': 'sell_price is required'}), 400
    
    query = """
        INSERT INTO products (name, category, unit_type, unit_details, buy_price, sell_price, stock, min_stock_level, description)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    
    params = (
        data['name'],
        data['category'],
        data.get('unit_type', 'piece'),
        data.get('unit_details', ''),
        data.get('buy_price', 0.00),
        data['sell_price'],
        data.get('stock', 0),
        data.get('min_stock_level', 10),
        data.get('description', '')
    )
    
    result = execute_query(query, params, fetch_one=True, commit=True)
    
    return jsonify({
        'success': True,
        'product_id': result[0],
        'message': 'Product created successfully'
    }), 201

@app.route('/api/products/<int:product_id>', methods=['PUT'])
@login_required
def update_product(product_id):
    data = request.json
    
    update_fields = []
    params = []
    
    updatable_fields = ['name', 'category', 'unit_type', 'unit_details', 'buy_price', 'sell_price', 'stock', 'min_stock_level', 'description']
    
    for field in updatable_fields:
        if field in data:
            update_fields.append(f"{field} = %s")
            params.append(data[field])
    
    if not update_fields:
        return jsonify({'error': 'No fields to update'}), 400
    
    params.append(product_id)
    query = f"UPDATE products SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = %s"
    
    execute_query(query, tuple(params), commit=True)
    
    return jsonify({'success': True, 'message': 'Product updated successfully'})

@app.route('/api/products/<int:product_id>', methods=['DELETE'])
@login_required
def delete_product(product_id):
    # Soft delete - just mark as inactive
    execute_query("""
        UPDATE products 
        SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP 
        WHERE id = %s
    """, (product_id,), commit=True)
    
    return jsonify({'success': True, 'message': 'Product deactivated successfully'})

# ==================== TRANSACTION MANAGEMENT ====================

@app.route('/api/transactions', methods=['POST'])
@login_required
def create_transaction():
    """Create new transaction"""
    try:
        data = request.json
        items = data.get('items', [])
        
        print("Received transaction data:", data)  # Debug print
        
        if not items:
            return jsonify({'error': 'No items in transaction'}), 400
        
        # Calculate subtotal from items
        subtotal = sum(item['price'] * item['quantity'] for item in items)
        total = subtotal  # No tax
        
        transaction_number = generate_transaction_number()
        
        # Insert transaction
        trans_query = """
            INSERT INTO transactions (transaction_number, user_id, subtotal, tax, total, payment_method, amount_paid, change_amount, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        
        trans_params = (
            transaction_number,
            session['user_id'],
            subtotal,
            0,  # tax
            total,
            data['payment_method'],
            data.get('amount_paid', total),
            data.get('change_amount', 0),
            data.get('notes', '')
        )
        
        print("Transaction params:", trans_params)
        
        trans_result = execute_query(trans_query, trans_params, fetch_one=True, commit=True)
        transaction_id = trans_result[0]
        
        # Insert transaction items - NOW INCLUDING UNIT DETAILS
        for item in items:
            # First get the product details from database to ensure we have unit info
            product_query = "SELECT unit_type, unit_details FROM products WHERE id = %s"
            product_info = execute_query(product_query, (item['id'],), fetch_one=True)
            
            unit_type = product_info[0] if product_info else ''
            unit_details = product_info[1] if product_info else ''
            
            item_query = """
                INSERT INTO transaction_items (transaction_id, product_id, quantity, price_at_time, subtotal, unit_type, unit_details)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            item_params = (
                transaction_id,
                item['id'],
                item['quantity'],
                item['price'],
                item['price'] * item['quantity'],
                unit_type,
                unit_details
            )
            execute_query(item_query, item_params, commit=True)
            
            # Update stock
            stock_query = "UPDATE products SET stock = stock - %s WHERE id = %s"
            execute_query(stock_query, (item['quantity'], item['id']), commit=True)
        
        return jsonify({
            'success': True,
            'transaction_id': transaction_id,
            'transaction_number': transaction_number,
            'message': 'Transaction completed successfully'
        }), 201
        
    except Exception as e:
        print(f"Error creating transaction: {e}")
        return jsonify({'error': str(e)}), 500
        
@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    """Get all transactions"""
    limit = request.args.get('limit', 50, type=int)
    
    query = """
        SELECT t.id, t.transaction_number, t.user_id, u.full_name as cashier, 
               t.subtotal, t.tax, t.total, t.payment_method, t.amount_paid, 
               t.change_amount, t.status, t.created_at,
               COALESCE((
                   SELECT json_agg(json_build_object(
                       'product_id', ti.product_id,
                       'product_name', p.name,
                       'quantity', ti.quantity,
                       'price', ti.price_at_time,
                       'subtotal', ti.subtotal,
                       'unit_type', ti.unit_type,
                       'unit_details', ti.unit_details
                   ))
                   FROM transaction_items ti
                   LEFT JOIN products p ON ti.product_id = p.id
                   WHERE ti.transaction_id = t.id
               ), '[]'::json) as items
        FROM transactions t
        LEFT JOIN users u ON t.user_id = u.id
        ORDER BY t.created_at DESC
        LIMIT %s
    """
    
    transactions = execute_query(query, (limit,), fetch_all=True)
    
    transaction_list = []
    for t in transactions:
        transaction_list.append({
            'id': t[0],
            'transaction_number': t[1],
            'user_id': t[2],
            'cashier': t[3],
            'subtotal': float(t[4]),
            'tax': float(t[5]),
            'total': float(t[6]),
            'payment_method': t[7],
            'amount_paid': float(t[8]),
            'change_amount': float(t[9]),
            'status': t[10],
            'created_at': t[11].isoformat() if t[11] else None,
            'items': t[12] if t[12] else []
        })
    
    return jsonify({
        'success': True,
        'transactions': transaction_list
    })

@app.route('/api/transactions/daily-summary', methods=['GET'])
@login_required
def get_daily_summary():
    """Get today's sales summary"""
    query = """
        SELECT 
            COALESCE(SUM(total), 0) as total_sales,
            COALESCE(SUM(subtotal), 0) as total_subtotal,
            COALESCE(SUM(tax), 0) as total_tax,
            COUNT(*) as transaction_count,
            COALESCE(SUM(
                (SELECT COALESCE(SUM(quantity), 0) 
                 FROM transaction_items 
                 WHERE transaction_id = transactions.id)
            ), 0) as items_sold
        FROM transactions
        WHERE DATE(created_at) = CURRENT_DATE
        AND status = 'completed'
    """
    
    result = execute_query(query, fetch_one=True)
    
    return jsonify({
        'success': True,
        'today_sales': float(result[0]) if result else 0,
        'total_subtotal': float(result[1]) if result else 0,
        'total_tax': float(result[2]) if result else 0,
        'transaction_count': result[3] if result else 0,
        'items_sold': result[4] if result else 0
    })

# ==================== CATEGORY MANAGEMENT ====================

@app.route('/api/categories', methods=['GET'])
@login_required
def get_categories():
    query = "SELECT name FROM categories ORDER BY display_order"
    categories = execute_query(query, fetch_all=True)
    
    category_list = [{'name': 'all'}]  # Add 'All' category
    for cat in categories:
        category_list.append({
            'name': cat[0]
        })
    
    return jsonify({
        'success': True,
        'categories': category_list
    })

# ==================== DASHBOARD STATISTICS ====================

@app.route('/api/dashboard/stats', methods=['GET'])
@login_required
def get_dashboard_stats():
    """Get dashboard statistics"""
    today_query = """
        SELECT 
            COALESCE(SUM(total), 0) as today_sales,
            COALESCE(SUM(
                (SELECT COALESCE(SUM(quantity), 0) 
                 FROM transaction_items 
                 WHERE transaction_id = transactions.id)
            ), 0) as items_sold
        FROM transactions
        WHERE DATE(created_at) = CURRENT_DATE
        AND status = 'completed'
    """
    today_result = execute_query(today_query, fetch_one=True)
    
    low_stock_query = "SELECT COUNT(*) FROM products WHERE stock < COALESCE(min_stock_level, 10)"
    low_stock_result = execute_query(low_stock_query, fetch_one=True)
    
    total_products_query = "SELECT COUNT(*) FROM products"
    total_products_result = execute_query(total_products_query, fetch_one=True)
    
    return jsonify({
        'success': True,
        'stats': {
            'today_sales': float(today_result[0]) if today_result else 0,
            'items_sold': today_result[1] if today_result else 0,
            'low_stock_count': low_stock_result[0] if low_stock_result else 0,
            'total_products': total_products_result[0] if total_products_result else 0
        }
    })

# ==================== TEMPLATE ROUTES ====================

@app.route('/')
def index():
    return send_from_directory('templates', 'login.html')

@app.route('/login')
def login_page():
    return send_from_directory('templates', 'login.html')

@app.route('/pos-system.html')
def pos_static():
    """Serve the POS HTML"""
    return send_from_directory('templates', 'pos-system.html')

# ==================== INITIALIZE DATABASE ====================

with app.app_context():
    init_database()

# ==================== RUN APP ====================

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)