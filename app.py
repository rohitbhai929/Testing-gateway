import os, sqlite3, secrets, io, json, hmac, hashlib, re
import requests
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
import qrcode
from jinja2 import DictLoader
from werkzeug.utils import secure_filename

BASE=os.path.dirname(os.path.abspath(__file__))

def load_templates():
    content=open(os.path.join(BASE, 'index.html'), encoding='utf-8').read()
    blocks=__import__('re').findall(r'<!-- TEMPLATE:([^ ]+) -->\n(.*?)\n<!-- END_TEMPLATE -->', content, __import__('re').S)
    return DictLoader(dict(blocks))
app=Flask(__name__)
app.jinja_loader=load_templates()
app.secret_key=os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config['MAX_CONTENT_LENGTH']=5*1024*1024
DB=os.path.join(BASE,'maker.db')
os.makedirs(os.path.join(BASE,'uploads'),exist_ok=True)
UPI=os.getenv('UPI_ID','samiranbhuyan@fam')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','')
FAM_KEY=os.getenv('FAMGATEWAY_API_KEY','')
FAM_BASE='https://famgateway.in'
PUBLIC_URL=os.getenv('PUBLIC_URL','').rstrip('/')
if not ADMIN_PASSWORD: print('WARNING: ADMIN_PASSWORD not configured; admin login disabled')

def conn():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c

def init():
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY,name TEXT,days INTEGER,price TEXT,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,customer TEXT,contact TEXT,plan_id INTEGER,amount TEXT,status TEXT DEFAULT 'awaiting_payment',screenshot TEXT,bot_username TEXT,bot_token TEXT,bot_admin TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
  INSERT OR IGNORE INTO settings VALUES('description','⚡ UPI QR Payments\n🔑 Bot Setup After Approval\n🤖 Demo Bot - @AllStoreShopBot');''')
  columns={x[1] for x in c.execute('PRAGMA table_info(orders)')}
  for col,kind in [('gateway','TEXT'),('gateway_order_id','TEXT'),('utr','TEXT'),('checkout_url','TEXT'),('qr_url','TEXT')]:
   if col not in columns:c.execute(f'ALTER TABLE orders ADD COLUMN {col} {kind}')
  c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_order ON orders(gateway_order_id)')
  if not c.execute('SELECT 1 FROM plans').fetchone():c.executemany('INSERT INTO plans(name,days,price) VALUES(?,?,?)',[('Starter',1,'15.00'),('Weekly',7,'99.00'),('Monthly',30,'299.00')])

def setting(k):
 with conn() as c:r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone();return r['v'] if r else ''

def admin_required(f):
 @wraps(f)
 def wrap(*a,**kw):
  if not session.get('admin'):return redirect(url_for('home'))
  return f(*a,**kw)
 return wrap

@app.get('/')
def home():
 with conn() as c:plans=c.execute('SELECT * FROM plans WHERE active=1 ORDER BY id').fetchall()
 return render_template('index.html',plans=plans,description=setting('description'),upi=UPI,admin=session.get('admin',False))

@app.post('/order')
def order():
 name=request.form.get('name','').strip()[:90];contact=request.form.get('contact','').strip()[:120]
 try:pid=int(request.form.get('plan','0'))
 except ValueError:abort(400)
 if not name or not contact:flash('Enter your name and contact.');return redirect(url_for('home')+'#plans')
 with conn() as c:
  p=c.execute('SELECT * FROM plans WHERE id=? AND active=1',(pid,)).fetchone()
  if not p:abort(404)
  oid='PBM'+secrets.token_hex(5).upper()
  c.execute('INSERT INTO orders(id,customer,contact,plan_id,amount,created) VALUES(?,?,?,?,?,?)',(oid,name,contact,pid,p['price'],datetime.now().isoformat(timespec='seconds')))
 return redirect(url_for('order_view',oid=oid))

@app.get('/order/<oid>')
def order_view(oid):
 with conn() as c:o=c.execute('SELECT o.*,p.name AS plan_name,p.days FROM orders o JOIN plans p ON p.id=o.plan_id WHERE o.id=?',(oid,)).fetchone()
 if not o:abort(404)
 return render_template('order.html',o=o,upi=UPI)

@app.get('/qr/<oid>')
def qr(oid):
 with conn() as c:o=c.execute('SELECT amount FROM orders WHERE id=?',(oid,)).fetchone()
 if not o:abort(404)
 from urllib.parse import urlencode
 uri='upi://pay?'+urlencode({'pa':UPI,'pn':'Premium Bot Maker','am':o['amount'],'cu':'INR','tn':oid})
 pic=qrcode.make(uri);buf=io.BytesIO();pic.save(buf,format='PNG');buf.seek(0)
 return send_file(buf,mimetype='image/png',max_age=0)

@app.post('/order/<oid>/screenshot')
def screenshot(oid):
 with conn() as c:o=c.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()
 if not o:abort(404)
 if o['status']!='awaiting_payment':flash('Screenshot already submitted or order processed.');return redirect(url_for('order_view',oid=oid))
 f=request.files.get('screenshot')
 if not f or not f.filename:flash('Choose a payment screenshot.');return redirect(url_for('order_view',oid=oid))
 from PIL import Image,UnidentifiedImageError
 try:
  im=Image.open(f.stream);im.verify();f.stream.seek(0)
  if im.format not in ('PNG','JPEG','WEBP'):raise ValueError()
 except (UnidentifiedImageError,ValueError):flash('Upload a PNG, JPG or WEBP image.');return redirect(url_for('order_view',oid=oid))
 ext={'PNG':'png','JPEG':'jpg','WEBP':'webp'}[im.format]
 filename=oid+'_'+secrets.token_hex(5)+'.'+ext
 f.save(os.path.join(BASE,'uploads',filename))
 with conn() as c:c.execute("UPDATE orders SET screenshot=?,status='payment_review' WHERE id=? AND status='awaiting_payment'",(filename,oid))
 flash('Screenshot submitted. Admin will check the payment in UPI records.')
 return redirect(url_for('order_view',oid=oid))

@app.post('/order/<oid>/bot')
def bot_details(oid):
 token=request.form.get('token','').strip();username=request.form.get('username','').strip().lstrip('@');aid=request.form.get('admin_id','').strip()
 if not token or not username or not aid.isdigit():flash('Enter bot username, token and numeric admin ID.');return redirect(url_for('order_view',oid=oid))
 with conn() as c:
  o=c.execute('SELECT status FROM orders WHERE id=?',(oid,)).fetchone()
  if not o:abort(404)
  if o['status']!='payment_approved':flash('Payment approval is required first.');return redirect(url_for('order_view',oid=oid))
  c.execute("UPDATE orders SET bot_username=?,bot_token=?,bot_admin=?,status='bot_review' WHERE id=?",(username,token,aid,oid))
 flash('Bot details submitted for admin review.')
 return redirect(url_for('order_view',oid=oid))



def money(value):
 try:
  return Decimal(str(value)).quantize(Decimal('0.01'))
 except Exception:
  return None


def apply_verified_payment(local_id, provider_id, amount, utr):
 """Idempotent credit: provider order and exact amount must match."""
 with conn() as db:
  db.execute('BEGIN IMMEDIATE')
  row=db.execute('SELECT * FROM orders WHERE id=? AND gateway_order_id=? AND gateway=?', (local_id,provider_id,'famgateway')).fetchone()
  if not row or money(amount)!=money(row['amount']):return False
  if row['status']=='awaiting_payment':
   db.execute("UPDATE orders SET status='payment_approved',utr=? WHERE id=? AND status='awaiting_payment'",(str(utr or '')[:100],local_id))
  return True


def verify_fam(provider_id):
 if not FAM_KEY:raise ValueError('FamGateway is not configured')
 response=requests.get(FAM_BASE+'/api/verify-order.php',params={'api_key':FAM_KEY,'order_id':provider_id},timeout=15)
 response.raise_for_status()
 payload=response.json()
 # Accept only explicitly successful, provider-verified status.
 info=payload.get('data',payload)
 if not isinstance(info,dict):return None
 if str(info.get('status','')).lower() not in ('success','paid','completed'):
  return None
 if info.get('order_id') and str(info['order_id'])!=provider_id:return None
 return info


@app.post('/order/<oid>/famgateway')
def create_fam_payment(oid):
 if not FAM_KEY:
  flash('FamGateway API key is not configured on server.')
  return redirect(url_for('order_view',oid=oid))
 with conn() as db:row=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
 if not row:abort(404)
 if row['status']!='awaiting_payment':return redirect(url_for('order_view',oid=oid))
 if row['gateway_order_id']:return redirect(url_for('order_view',oid=oid))
 payload={'amount':float(row['amount']),'customer_name':row['customer']}
 if PUBLIC_URL:payload['webhook_url']=PUBLIC_URL+'/api/fam-webhook'
 try:
  response=requests.post(FAM_BASE+'/api/create-order',headers={'X-Api-Key':FAM_KEY,'Content-Type':'application/json'},json=payload,timeout=20)
  response.raise_for_status();body=response.json();info=body.get('data',{})
  if body.get('status')!='success' or not info.get('order_id') or not info.get('qr_url'):raise ValueError('Invalid gateway response')
  if money(info.get('amount'))!=money(row['amount']):raise ValueError('Gateway amount mismatch')
  with conn() as db:
   db.execute("UPDATE orders SET gateway='famgateway',gateway_order_id=?,qr_url=?,checkout_url=? WHERE id=? AND status='awaiting_payment' AND gateway_order_id IS NULL",(str(info['order_id']),str(info['qr_url']),str(info.get('checkout_url','')),oid))
 except Exception:
  app.logger.exception('FamGateway order creation failed')
  flash('Could not create payment. Please try again later.')
 return redirect(url_for('order_view',oid=oid))


@app.get('/order/<oid>/check-payment')
def check_fam_payment(oid):
 with conn() as db:row=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
 if not row:abort(404)
 if row['status']!='awaiting_payment':return redirect(url_for('order_view',oid=oid))
 if row['gateway']!='famgateway' or not row['gateway_order_id']:abort(400)
 try:
  result=verify_fam(row['gateway_order_id'])
  if result:
   apply_verified_payment(oid,row['gateway_order_id'],result.get('amount'),result.get('utr') or result.get('transaction_id'))
  else:flash('Payment not confirmed yet. Please check again shortly.')
 except Exception:
  app.logger.exception('FamGateway verification error');flash('Verification temporarily unavailable.')
 return redirect(url_for('order_view',oid=oid))


@app.post('/api/fam-webhook')
def fam_webhook():
 if not FAM_KEY:abort(503)
 signature=request.headers.get('X-FamGateway-Signature','')
 expected=hmac.new(FAM_KEY.encode(),request.get_data(),hashlib.sha256).hexdigest()
 if not re.fullmatch(r'[a-fA-F0-9]{64}',signature) or not hmac.compare_digest(expected,signature.lower()):abort(401)
 payload=request.get_json(silent=True) or {}
 if payload.get('event')!='payment.success' or payload.get('status')!='success':return {'status':'ignored'}
 provider_id=str(payload.get('order_id',''))
 with conn() as db:row=db.execute('SELECT id FROM orders WHERE gateway_order_id=? AND gateway=?',(provider_id,'famgateway')).fetchone()
 if not row:return {'status':'unknown_order'},404
 # Confirm against server-to-server API as a second check.
 try:verified=verify_fam(provider_id)
 except Exception:
  app.logger.exception('Webhook authoritative verification failed');return {'status':'retry'},503
 if not verified or money(verified.get('amount'))!=money(payload.get('amount')):return {'status':'unverified'},409
 apply_verified_payment(row['id'],provider_id,verified.get('amount'),verified.get('utr') or payload.get('utr'))
 return {'status':'ok'}

@app.post('/admin/login')
def admin_login():
 if ADMIN_PASSWORD and secrets.compare_digest(request.form.get('password',''),ADMIN_PASSWORD):session['admin']=True;return redirect(url_for('dashboard'))
 flash('Incorrect admin password.');return redirect(url_for('home'))

@app.post('/admin/logout')
def logout():session.pop('admin',None);return redirect(url_for('home'))

@app.get('/admin')
@admin_required
def dashboard():
 with conn() as c:orders=c.execute('SELECT o.*,p.name AS plan_name FROM orders o JOIN plans p ON p.id=o.plan_id ORDER BY o.created DESC').fetchall();plans=c.execute('SELECT * FROM plans ORDER BY id').fetchall()
 return render_template('admin.html',orders=orders,plans=plans,description=setting('description'))

@app.post('/admin/description')
@admin_required
def description():
 with conn() as c:c.execute("INSERT INTO settings(k,v) VALUES('description',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",(request.form.get('description','')[:1000],))
 flash('Description saved.');return redirect(url_for('dashboard'))

@app.post('/admin/plan')
@admin_required
def plan():
 try:
  name=request.form['name'].strip()[:60];days=int(request.form['days']);price=Decimal(request.form['price'])
  if not name or not 1<=days<=3650 or not Decimal('0')<price<=Decimal('100000'):raise ValueError()
 except (ValueError,InvalidOperation,KeyError):flash('Invalid plan.');return redirect(url_for('dashboard'))
 with conn() as c:c.execute('INSERT INTO plans(name,days,price) VALUES(?,?,?)',(name,days,f'{price:.2f}'))
 flash('Plan added.');return redirect(url_for('dashboard'))

@app.post('/admin/plan/<int:pid>/toggle')
@admin_required
def toggle(pid):
 with conn() as c:c.execute('UPDATE plans SET active=1-active WHERE id=?',(pid,))
 return redirect(url_for('dashboard'))

@app.post('/admin/order/<oid>/<action>')
@admin_required
def decide(oid,action):
 states={'approve_payment':('payment_review','payment_approved'),'decline_payment':('payment_review','payment_declined'),'approve_bot':('bot_review','approved'),'decline_bot':('bot_review','declined')}
 if action not in states:abort(400)
 old,new=states[action]
 with conn() as c:
  n=c.execute('UPDATE orders SET status=? WHERE id=? AND status=?',(new,oid,old)).rowcount
 flash('Status updated.' if n else 'Invalid status transition.')
 return redirect(url_for('dashboard'))

@app.get('/admin/screenshot/<oid>')
@admin_required
def view_screenshot(oid):
 with conn() as c:r=c.execute('SELECT screenshot FROM orders WHERE id=?',(oid,)).fetchone()
 if not r or not r['screenshot']:abort(404)
 return send_file(os.path.join(BASE,'uploads',os.path.basename(r['screenshot'])))

init()

if __name__=='__main__':
 app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=False)
