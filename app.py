import os
import json
import uuid
import logging
import threading
import time
from urllib.parse import urlencode
from datetime import datetime, timezone

import stripe
import requests
from flask import (
    Flask, request, jsonify, render_template,
    abort, session, redirect, flash, url_for
)
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# -----------------------------------------------------------------------------
# Configuration & Initialization
# -----------------------------------------------------------------------------
load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv('SECRET_KEY', '')

stripe.api_key = os.getenv('STRIPE_SECRET_KEY', '')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SIGNING_SECRET', '')
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY', '')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')

logging.basicConfig(level=logging.INFO)

# Paths to JSON data
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_PATH = os.path.join(BASE_DIR, 'products.json')
ROUTES_PATH = os.path.join(BASE_DIR, 'static', 'data', 'routes.json')

# -----------------------------------------------------------------------------
# In-Memory Orders Store (for demo purposes)
# -----------------------------------------------------------------------------
# Structure: 
# orders = {
#    "invoice_id_abc": {
#         "email": "...",
#         "cart": [...],
#         "total": 123.45,
#         "method": "Stripe" or "PayPal",
#         "status": "pending" or "paid",
#         "created_at": datetime,
#         "paid_at": datetime or None
#     },
# }
orders = {}

# -----------------------------------------------------------------------------
# Data Loading
# -----------------------------------------------------------------------------
def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

app.products = load_json(PRODUCTS_PATH)

def find_product(slug):
    return app.products.get(slug)

def get_price_for(slug: str, plan: str) -> float:
    """
    Return the “authoritative” unit price (in USD) for a given product slug + plan,
    by scanning the product['plans'] list. Raises ValueError if not found.
    """
    product = find_product(slug)
    if not product:
        raise ValueError(f"No such product: {slug}")

    plans_list = product.get('plans', [])
    for entry in plans_list:
        if entry.get('plan') == plan:
            # Found matching plan
            return float(entry.get('price', 0.0))

    raise ValueError(f"No such plan '{plan}' for product '{slug}'")


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

def send_email(to_email: str, subject: str, body_html: str):
    """
    Sends an HTML email via Gmail SMTP.
    Expects MAIL_USERNAME and MAIL_PASSWORD in environment.
    """
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = os.getenv('MAIL_USERNAME', '')
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.set_charset('utf-8')

        # Plain-text fallback
        msg.attach(MIMEText('This email requires an HTML-capable client.', 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(os.getenv('MAIL_USERNAME', ''), os.getenv('MAIL_PASSWORD', ''))
            server.sendmail(msg['From'], msg['To'], msg.as_string().encode('utf-8'))

        logging.info(f'📧 Email sent to {to_email} ─ {subject}')
    except Exception as e:
        logging.error(f'❌ Email error: {e}')

def send_notification_to_webhook(session_data):
    """
    Sends a Discord webhook notification for new Stripe payments.
    """
    # Turn the Stripe session_data into a plain dict
    if hasattr(session_data, 'to_dict_recursive'):
        session_info = session_data.to_dict_recursive()
    else:
        session_info = dict(session_data)

    meta = session_info.get('metadata', {})
    amount_paid = session_info.get('amount_total', 0) / 100
    customer_email = session_info.get('customer_email', 'Unknown')

    # Load the JSON‐encoded product_summary (if present)
    raw_summary = meta.get('product_summary', '[]')
    try:
        product_list = json.loads(raw_summary)
        if not isinstance(product_list, list):
            product_list = []
    except Exception:
        product_list = []

    discord_payload = {
        'content': 'New Sale Notification',
        'username': 'Store Bot',
        'embeds': [{
            'fields': [
                {'name': 'Invoice ID', 'value': meta.get('invoice_id', 'N/A'), 'inline': False},
                {'name': 'Total',      'value': f"${amount_paid:.2f}", 'inline': False},
                {'name': 'Customer',   'value': customer_email, 'inline': False},
                {'name': 'Items',      'value': ', '.join(product_list) or 'N/A', 'inline': False},
            ],
            'footer': {
                'text': f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"
            }
        }]
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=discord_payload)
        if res.status_code == 429:
            retry_after = int(res.headers.get('Retry-After', 1))
            logging.warning(f"⌛ Discord rate limit, retrying in {retry_after}s…")
            time.sleep(retry_after)
            res = requests.post(DISCORD_WEBHOOK_URL, json=discord_payload)
        res.raise_for_status()
        logging.info('✅ Discord webhook sent.')
    except Exception as e:
        logging.error(f'❌ Discord webhook error: {e}')

def handle_successful_payment(session_data):
    """
    Fires off Discord webhook in a background thread to avoid blocking.
    """
    threading.Thread(
        target=send_notification_to_webhook,
        args=(session_data,),
        daemon=True
    ).start()

# -----------------------------------------------------------------------------
# Dynamic Routes Loader (optional)
# -----------------------------------------------------------------------------
def load_routes():
    if not os.path.exists(ROUTES_PATH):
        return []
    return load_json(ROUTES_PATH)

for route in load_routes():
    tpl = route.get('template')
    url = route.get('url')
    if tpl and url:
        app.add_url_rule(url, endpoint=url, view_func=lambda tpl=tpl: render_template(tpl))

# -----------------------------------------------------------------------------
# Store, Cart & Checkout
# -----------------------------------------------------------------------------
@app.route('/store/<slug>')
def product_page(slug):
    product = find_product(slug)
    if not product:
        abort(404)
    return render_template('product.html', product=product)

@app.context_processor
def inject_cart_count():
    cart = session.get('cart', [])
    count = sum(item.get('quantity', 1) for item in cart)
    return dict(cart_count=count)

@app.route('/add-to-cart', methods=['POST'])
def add_to_cart():
    """
    Adds a product to session['cart'], but pulls the price from server-side JSON
    (so users cannot tamper with the price in the browser).
    """
    try:
        title = request.form['title']
        slug = request.form['slug']
        plan = request.form['plan']
        quantity = int(request.form.get('quantity', 1))

        # Look up the “official” price on the server:
        try:
            unit_price_usd = get_price_for(slug, plan)
        except ValueError as e:
            return jsonify(success=False, error=str(e)), 400

        product = find_product(slug)
        if not product:
            return jsonify(success=False, error="Product not found"), 404

        item = {
            'id': f"{slug}-{plan}",
            'title': title,
            'slug': slug,
            'price': unit_price_usd,
            'plan': plan,
            'image_url': product.get('image_url', ''),
            'subtitle': product.get('subtitle', ''),
            'quantity': quantity
        }

        cart = session.get('cart', [])
        for existing in cart:
            if existing['id'] == item['id']:
                existing['quantity'] += quantity
                break
        else:
            cart.append(item)

        session['cart'] = cart
        session.modified = True

        total_qty = sum(i['quantity'] for i in cart)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=True, cart_count=total_qty)

        flash('Item added to cart!', 'success')
        return redirect(request.referrer or '/store')

    except Exception as e:
        logging.error(f"Error in add_to_cart: {e}")
        return jsonify(success=False, error=str(e)), 400

@app.route('/cart')
def cart():
    items = session.get('cart', [])
    subtotal = sum(i['price'] * i['quantity'] for i in items)
    TAX_RATE = 0.0
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + tax, 2)

    return render_template(
        'cart.html',
        cart_items=items,
        cart_subtotal=subtotal,
        cart_tax=tax,
        cart_total=total
    )

@app.route('/update-quantity', methods=['POST'])
def update_quantity():
    try:
        item_id = request.form['item_id']
        quantity = max(int(request.form.get('quantity', 1)), 1)
        cart = session.get('cart', [])
        for it in cart:
            if it['id'] == item_id:
                it['quantity'] = quantity
                break
        session['cart'] = cart
        session.modified = True
        return redirect(url_for('cart'))
    except Exception as e:
        logging.error(e)
        return "Bad Request", 400

@app.route('/remove-from-cart', methods=['POST'])
def remove_from_cart():
    try:
        item_id = request.form['item_id']
        cart = [i for i in session.get('cart', []) if i['id'] != item_id]
        session['cart'] = cart
        session.modified = True
        return redirect(url_for('cart'))
    except Exception as e:
        logging.error(e)
        return "Bad Request", 400

@app.route('/clear-cart')
def clear_cart():
    session.pop('cart', None)
    session.pop('_flashes', None)
    session.pop('order_id', None)
    flash('Cart cleared.', 'info')
    return redirect(request.referrer or url_for('cart'))

@app.route('/checkout')
def checkout():
    cart = session.get('cart', [])
    if not cart:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart'))

    # Generate a one-off order_id if missing
    if 'order_id' not in session:
        session['order_id'] = str(uuid.uuid4())

    total = sum(i['price'] * i['quantity'] for i in cart)
    return render_template(
        'checkout.html',
        cart_items=cart,
        total=total,
        stripe_public_key=STRIPE_PUBLIC_KEY,
        order_id=session['order_id']
    )

# -----------------------------------------------------------------------------
# PayPal Checkout Session – Cart Upload (no shipping, reuse order_id)
# -----------------------------------------------------------------------------
@app.route("/create-paypal-payment", methods=["POST"])
def create_paypal_payment():
    try:
        customer_email = request.form.get("email", "").strip()
        if not customer_email:
            flash("You must provide an email address.", "warning")
            return redirect(url_for("checkout"))

        cart = session.get("cart", [])
        if not cart:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("cart"))

        total = sum(i["price"] * i["quantity"] for i in cart)
        paypal_business = os.getenv("PAYPAL_EMAIL")
        if not paypal_business:
            logging.error("PAYPAL_EMAIL environment variable not set.")
            flash("Payment configuration error.", "danger")
            return redirect(url_for("checkout"))

        # Reuse existing order_id or generate new
        invoice_id = session.get("order_id") or str(uuid.uuid4())
        session["order_id"] = invoice_id

        # 1) Create order entry in memory (pending)
        orders[invoice_id] = {
            "email": customer_email,
            "cart": cart.copy(),
            "total": total,
            "method": "PayPal",
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "paid_at": None
        }

        # Send “Order Created” email (pending payment via PayPal)
        html_items = "".join(
            f"<li>{item['title']} – {item['plan'].title()} × {item['quantity']} – "
            f"${item['price'] * item['quantity']:.2f}</li>"
            for item in cart
        )
        send_email(
            customer_email,
            "Order Created – Pending PayPal Payment",
            f"""
<p>Thank you for your order. Your order is pending PayPal payment.</p>
<p><strong>Payment Method:</strong> PayPal</p>
<p><strong>Invoice ID:</strong> {invoice_id}</p>
<ul>{html_items}</ul>
<p><strong>Total:</strong> ${total:.2f}</p>
"""
        )

        # Build PayPal “cart upload” parameters
        paypal_params = {
            "cmd":           "_cart",
            "upload":        "1",
            "business":      paypal_business,
            "invoice":       invoice_id,
            "currency_code": "USD",
            "no_shipping":   "1",
            "return":        url_for("paypal_success", _external=True),
            "cancel_return": url_for("paypal_cancel", _external=True),
            "notify_url":    url_for("paypal_ipn", _external=True),
            "custom":        customer_email,
        }
        for idx, item in enumerate(cart, start=1):
            title = item.get("title", "Unknown Product")
            price = float(item.get("price", 0.0))
            qty = int(item.get("quantity", 1))
            paypal_params[f"item_name_{idx}"] = f"{title} – {item['plan'].title()}"
            paypal_params[f"amount_{idx}"] = f"{price:.2f}"
            paypal_params[f"quantity_{idx}"] = str(qty)

        # Save pending payment data for confirmation
        session["pending_payment"] = {
            "invoice_id":     invoice_id,
            "total":          total,
            "customer_email": customer_email,
            "cart":           cart.copy()
        }
        session.modified = True

        return redirect("https://www.paypal.com/cgi-bin/webscr?" + urlencode(paypal_params))

    except Exception as e:
        logging.error(f"PayPal redirect error: {e}", exc_info=True)
        flash("Payment processing error.", "danger")
        return redirect(url_for("checkout"))

@app.route("/paypal-success")
def paypal_success():
    """
    PayPal redirects here on successful payment.
    We clear session data and send a confirmation email.
    """
    pending = session.pop("pending_payment", None)
    session.pop("cart", None)
    session.pop("order_id", None)

    if not pending:
        flash("Invalid session or no pending payment found.", "error")
        return redirect(url_for("cart"))

    invoice_id = pending["invoice_id"]
    # Mark as paid if still pending
    order = orders.get(invoice_id)
    if order and order["status"] == "pending":
        order["status"] = "paid"
        order["paid_at"] = datetime.now(timezone.utc)

    send_email(
        pending["customer_email"],
        "PayPal Payment Confirmed",
        f"""
<p>Thank you for your PayPal payment of ${pending['total']:.2f}.</p>
<p><strong>Payment Method:</strong> PayPal</p>
<p><strong>Invoice ID:</strong> {invoice_id}</p>
"""
    )

    return render_template(
        "success.html",
        email=pending["customer_email"],
        invoice_id=invoice_id,
        total=f"${pending['total']:.2f}"
    )

@app.route("/paypal-cancel")
def paypal_cancel():
    """
    If the user cancels on PayPal’s side, they land here.
    We drop pending_payment and order_id so they can start fresh.
    """
    session.pop("pending_payment", None)
    session.pop("order_id", None)
    flash("PayPal payment was canceled.", "info")
    return render_template("cancel.html")

@app.route("/ipn", methods=["POST"])
def paypal_ipn():
    """
    PayPal IPN listener: validates incoming notification and marks orders as paid.
    """
    try:
        ipn_data = request.form.to_dict()
        verify_payload = {"cmd": "_notify-validate", **ipn_data}

        response = requests.post(
            "https://www.paypal.com/cgi-bin/webscr",
            data=verify_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        response.raise_for_status()

        if response.text == "VERIFIED":
            payment_status = ipn_data.get("payment_status")
            invoice = ipn_data.get("invoice")
            payer_email = ipn_data.get("payer_email")
            mc_gross = ipn_data.get("mc_gross")
            mc_currency = ipn_data.get("mc_currency")

            if payment_status == "Completed" and invoice in orders:
                order = orders[invoice]
                if order["status"] != "paid":
                    order["status"] = "paid"
                    order["paid_at"] = datetime.now(timezone.utc)
                    logging.info(f"IPN VERIFIED: invoice={invoice}, txn={ipn_data.get('txn_id')}, amount={mc_gross} {mc_currency}, payer={payer_email}")

                    # Send “Payment Confirmed” email (in case user returns via IPN rather than redirect)
                    send_email(
                        order["email"],
                        "PayPal Payment Confirmed",
                        f"""
<p>Your PayPal payment of ${order['total']:.2f} has been confirmed via IPN.</p>
<p><strong>Payment Method:</strong> PayPal</p>
<p><strong>Invoice ID:</strong> {invoice}</p>
"""
                    )
            else:
                logging.warning(f"IPN not Completed or invoice not found: status={payment_status}, invoice={invoice}")
        else:
            logging.error("IPN Verification FAILED.")

    except Exception as e:
        logging.error(f"Exception in IPN handler: {e}", exc_info=True)

    return ("", 200)

# -----------------------------------------------------------------------------
# Stripe Checkout Session (uses same order_id)
# -----------------------------------------------------------------------------
@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = request.get_json(force=True)
    customer_email = data.get('email', '').strip()
    cart = session.get('cart', [])
    order_id = session.get('order_id')

    if not cart or not customer_email or not order_id:
        return jsonify({'error': 'Missing cart, email, or order_id'}), 400

    line_items = []
    metadata_items = []
    html_items = ""
    total_amount_cents = 0
    product_summary = []

    # --- REPLACE trusting item['price'] with get_price_for(...) ---
    for item in cart:
        slug = item['slug']
        plan = item['plan']
        qty = item.get('quantity', 1)

        # 1) Look up authoritative unit price on the server
        try:
            unit_price_usd = get_price_for(slug, plan)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        unit_price_cents = int(unit_price_usd * 100)
        total_item_cents = unit_price_cents * qty
        total_amount_cents += total_item_cents

        raw_name = f"{item['title']} – {plan.title()}"
        name = raw_name[:127]

        line_items.append({
            'price_data': {
                'currency': 'usd',
                'unit_amount': unit_price_cents,
                'product_data': {'name': name},
            },
            'quantity': qty,
        })

        metadata_items.append(f"{slug}:{plan}:{qty}")
        html_items += f"<li>{name} × {qty} – ${total_item_cents / 100:.2f}</li>"
        product_summary.append(f"{name} × {qty}")

    try:
        # 1) Create Stripe Checkout Session with server‐defined prices
        session_obj = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            customer_email=customer_email,
            success_url=f"{request.host_url}stripe-success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{request.host_url}stripe-cancel",
            metadata={
                'invoice_id': order_id,
                'cart_items': json.dumps(metadata_items),
                'product_summary': json.dumps(product_summary),
                'ip_address': request.remote_addr,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        )

        # 2) Save “pending” order in memory, using canonical total
        total_dollars = total_amount_cents / 100
        orders[order_id] = {
            "email": customer_email,
            "cart": cart.copy(),
            "total": total_dollars,
            "method": "Stripe",
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "paid_at": None
        }

        # 3) Send “Order Created” email (pending payment via Stripe)
        send_email(
            customer_email,
            "Order Created – Pending Stripe Payment",
            f"""
<p>Thank you for your order. Your transaction is pending payment via Stripe.</p>
<p><strong>Payment Method:</strong> Stripe</p>
<p><strong>Invoice ID:</strong> {order_id}</p>
<ul>{html_items}</ul>
<p><strong>Total:</strong> ${total_dollars:.2f}</p>
<p><a href="{session_obj.url}">Click here to pay now</a></p>
"""
        )

        # 4) Clear cart so they cannot manually resubmit lower prices
        session.pop('cart', None)
        return jsonify({'id': session_obj.id})

    except Exception as e:
        logging.exception("Stripe checkout error")
        return jsonify(error=str(e)), 500

@app.route('/stripe-success')
def stripe_success():
    raw_session_id = request.args.get('session_id', '').strip('{}')
    if not raw_session_id:
        return render_template('404.html', message='No session ID provided.'), 400

    try:
        sess = stripe.checkout.Session.retrieve(raw_session_id, expand=['line_items'])
        meta = sess.get('metadata', {})
        customer_email = sess.get('customer_email') or sess.get('customer_details', {}).get('email')
        invoice_id = meta.get('invoice_id', '')
        raw_items = meta.get('cart_items', '[]')
        raw_product_summary = meta.get('product_summary', '[]')

        order_record = Order.query.get(invoice_id)
        if not order_record:
            logging.error(f"Order {invoice_id} not found.")
            return render_template('404.html', message='Order not found.'), 404

        actual_paid_cents = sess.get('amount_total', 0)
        expected_paid_cents = int(order_record.total * 100)

        if actual_paid_cents != expected_paid_cents:
            logging.error(
                f"⚠️ Payment amount mismatch for invoice {invoice_id}: "
                f"expected {expected_paid_cents}¢, got {actual_paid_cents}¢"
            )
            return render_template(
                '404.html',
                message='Payment validation error. Please contact support.'
            ), 400

        # Decode cart items (for building the “paid” summary table on the success page)
        try:
            cart_items = json.loads(raw_items)
        except Exception:
            cart_items = []

        html_rows = ""
        total_cents = 0
        for entry in cart_items:
            try:
                slug, plan, quantity = entry.split(":")
                quantity = int(quantity)
                name = f"{slug} – {plan.title()}"
                unit_price_usd = get_price_for(slug, plan)
                unit_price_cents = int(unit_price_usd * 100)
                subtotal_cents = unit_price_cents * quantity
                total_cents += subtotal_cents
                html_rows += (
                    f"<tr>"
                    f"<td style='padding:8px;border:1px solid #ddd;'>{name}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;text-align:center;'>{quantity}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;text-align:right;'>"
                    f"${subtotal_cents/100:.2f}</td>"
                    f"</tr>"
                )
            except Exception:
                continue

        total_paid_dollars = total_cents / 100

        # Mark order as paid if still pending
        if order_record.status == "pending":
            order_record.status = "paid"
            order_record.paid_at = datetime.now(timezone.utc)
            db.session.commit()

        # Remove email + Discord calls from here—those now live in the webhook.
        session.pop('order_id', None)

        # Decode product_summary so you can display it if needed
        try:
            product_summary_list = json.loads(raw_product_summary)
        except Exception:
            product_summary_list = []

        return render_template(
            'success.html',
            email=customer_email,
            invoice_id=invoice_id,
            product=", ".join(product_summary_list),
            total=f"${total_paid_dollars:.2f}"
        )

    except Exception as e:
        logging.exception('Error in stripe_success')
        return render_template('404.html', message='Error verifying payment.'), 500

@app.route('/stripe-cancel')
def stripe_cancel():
    """
    If the user cancels on Stripe’s side, they land here.
    We clear order_id so a new one is generated next time.
    """
    session.pop('order_id', None)
    flash('Stripe payment was canceled.', 'info')
    return render_template('cancel.html')

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        logging.warning("⚠️ Missing Stripe-Signature header")
        return 'Missing signature', 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
        logging.info("✅ Stripe event verified.")
    except ValueError as e:
        logging.warning(f"⚠️ Invalid payload: {e}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        logging.warning(f"⚠️ Invalid signature: {e}")
        return 'Invalid signature', 400

    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        invoice_id = session_obj.get('metadata', {}).get('invoice_id')
        customer_email = session_obj.get('customer_email') or session_obj.get('customer_details', {}).get('email')

        # 1) Update the Order record (if still pending)
        order_record = Order.query.get(invoice_id)
        if order_record and order_record.status == "pending":
            order_record.status = "paid"
            order_record.paid_at = datetime.now(timezone.utc)
            db.session.commit()
        else:
            logging.info(f"Order {invoice_id} already paid or not found.")

        # 2) Send the “payment confirmed” email
        raw_items = session_obj.get('metadata', {}).get('cart_items', '[]')
        try:
            cart_items = json.loads(raw_items)
        except Exception:
            cart_items = []

        html_rows = ""
        total_cents = 0
        for entry in cart_items:
            try:
                slug, plan, quantity = entry.split(":")
                quantity = int(quantity)
                name = f"{slug} – {plan.title()}"
                unit_price_usd = get_price_for(slug, plan)
                unit_price_cents = int(unit_price_usd * 100)
                subtotal_cents = unit_price_cents * quantity
                total_cents += subtotal_cents
                html_rows += (
                    f"<tr>"
                    f"<td style='padding:8px;border:1px solid #ddd;'>{name}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;text-align:center;'>{quantity}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;text-align:right;'>"
                    f"${subtotal_cents/100:.2f}</td>"
                    f"</tr>"
                )
            except Exception:
                continue

        total_paid_dollars = total_cents / 100

        email_html = f"""
<!DOCTYPE html>
<html>
  <body style="font-family: Arial, Helvetica, sans-serif; background:#f4f4f4; margin:0; padding:0;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center" style="padding:20px 0;">
          <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden;">
            <tr style="background:#4caf50; color:#ffffff;">
              <td style="padding:20px; text-align:center; font-size:24px; font-weight:bold;">
                moealturej Payment Confirmed
              </td>
            </tr>
            <tr>
              <td style="padding:20px; color:#333333;">
                <p>Hello,</p>
                <p>Your Stripe payment of <strong>${total_paid_dollars:.2f}</strong> has been confirmed.</p>
                <p><strong>Invoice ID:</strong> {invoice_id}</p>
                <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse; margin-top: 15px;">
                  <thead>
                    <tr style="background:#f0f0f0;">
                      <th style="padding:8px;border:1px solid #ddd;text-align:left;">Item</th>
                      <th style="padding:8px;border:1px solid #ddd;text-align:center;">Qty</th>
                      <th style="padding:8px;border:1px solid #ddd;text-align:right;">Subtotal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {html_rows}
                  </tbody>
                </table>
                <p style="text-align:right; margin-top:15px; font-size:18px;">
                  <strong>Total Paid: ${total_paid_dollars:.2f}</strong>
                </p>
                <p>Thank you for choosing moealturej!</p>
                <p>Cheers,<br>moealturej Team</p>
              </td>
            </tr>
            <tr style="background:#f0f0f0;">
              <td style="padding:15px; text-align:center; font-size:12px; color:#777777;">
                © {datetime.now().year} moealturej | All rights reserved.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

        # Only send email if order was pending (now paid)
        if order_record:
            send_email(
                customer_email,
                "moealturej – Stripe Payment Confirmed",
                email_html,
                body_text=(
                    f"Your payment of ${total_paid_dollars:.2f} has been confirmed. "
                    f"Invoice ID: {invoice_id}."
                )
            )

        # 3) Send Discord webhook (inside handle_successful_payment, which is idempotent)
        handle_successful_payment(session_obj)

    return 'OK', 200

# -----------------------------------------------------------------------------
# Run the Flask App
# -----------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 10000)),
        debug=False
    )
