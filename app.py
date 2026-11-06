from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
import os
import uuid
import sqlite3

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'  # Нужно для работы сессий (входа)

UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15 МБ

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
DATABASE = 'board.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # 1. Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    # 2. Таблица картинок (добавили user_id)
    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT NOT NULL,
            title TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    # 3. Таблица комментариев
    c.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            author TEXT DEFAULT 'Гость',
            text TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (image_id) REFERENCES images (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Главная страница (Доска) ---
@app.route('/')
def index():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, filename, title FROM images ORDER BY id DESC')
    rows = c.fetchall()
    conn.close()
    images = [{'id': row['id'], 'filename': row['filename'], 'title': row['title']} for row in rows]
    return render_template('index.html', images=images, active_page='board')

# --- Регистрация ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            return render_template('auth.html', error='Заполните все поля', mode='register')

        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id FROM users WHERE username = ?', (username,))
        if c.fetchone():
            conn.close()
            return render_template('auth.html', error='Это имя уже занято', mode='register')

        # Шифруем пароль перед сохранением
        hashed_pw = generate_password_hash(password)
        c.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed_pw))
        conn.commit()
        conn.close()

        return redirect(url_for('login'))
    return render_template('auth.html', mode='register')

# --- Вход ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id, password_hash FROM users WHERE username = ?', (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('auth.html', error='Неверное имя или пароль', mode='login')
    return render_template('auth.html', mode='login')

# --- Выход ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- Загрузка картинки (теперь только для вошедших) ---
@app.route('/upload', methods=['POST'])
def upload():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    file = request.files.get('image')
    title = request.form.get('title', '').strip()

    if not file or file.filename == '':
        return redirect(url_for('index'))

    if not allowed_file(file.filename):
        return 'Неподдерживаемый формат файла', 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO images (user_id, filename, title) VALUES (?, ?, ?)', (session['user_id'], filename, title))
    conn.commit()
    conn.close()

    return redirect(url_for('index'))

# --- Мои картинки (только свои) ---
@app.route('/my-images')
def my_images():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, filename, title FROM images WHERE user_id = ? ORDER BY id DESC', (session['user_id'],))
    rows = c.fetchall()
    conn.close()
    images = [{'id': row['id'], 'filename': row['filename'], 'title': row['title']} for row in rows]
    return render_template('index.html', images=images, active_page='my')

# --- Комментарии ---
@app.route('/comments/<int:image_id>')
def get_comments(image_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT author, text FROM comments WHERE image_id = ? ORDER BY id ASC', (image_id,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{'author': row['author'], 'text': row['text']} for row in rows])

@app.route('/add_comment', methods=['POST'])
def add_comment():
    if 'user_id' not in session:
        return jsonify({'error': 'Вы должны войти, чтобы писать комментарии'}), 401
        
    data = request.get_json()
    image_id = data.get('image_id')
    # Если вошёл, берём имя из сессии, иначе "Гость" (но мы уже запретили гостям)
    author = session.get('username', 'Гость')
    text = data.get('text', '').strip()

    if not image_id or not text:
        return jsonify({'error': 'Пустой комментарий'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO comments (image_id, author, text) VALUES (?, ?, ?)', (image_id, author, text))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True)