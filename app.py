import os
import json
import uuid
import logging
import time
import threading
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

# Secret & API Keys
app.secret_key = os.getenv('SECRET_KEY')
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SIGNING_SECRET')  # <-- make sure this is your whsec_… value
STRIPE_PUBLIC_KEY = os.getenv('STRIPE_PUBLIC_KEY')
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL')

# Fail-fast if keys missing
if not stripe.api_key:
    raise RuntimeError("Missing STRIPE_SECRET_KEY environment variable")
if not WEBHOOK_SECRET:
    raise RuntimeError("Missing STRIPE_WEBHOOK_SIGNING_SECRET environment variable")

# Logging
logging.basicConfig(level=logging.INFO)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_PATH = os.path.join(BASE_DIR, 'products.json')
ROUTES_PATH = os.path.join(BASE_DIR, 'static', 'data', 'routes.json')

# -----------------------------------------------------------------------------
# Data Loading
# -----------------------------------------------------------------------------
def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

app.products = load_json(PRODUCTS_PATH)

def find_product(slug):
    return app.products.get(slug)

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

def send_email(to_email, subject, body_html):
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = os.getenv('MAIL_USERNAME')
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.set_charset('utf-8')

        # Plain-text fallback
        msg.attach(MIMEText('This email requires an HTML-capable client.', 'plain', _charset='utf-8'))
        msg.attach(MIMEText(body_html, 'html', _charset='utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(os.getenv('MAIL_USERNAME'), os.getenv('MAIL_PASSWORD'))
            server.sendmail(msg['From'], msg['To'], msg.as_string().encode('utf-8'))

        logging.info(f'📧 Email sent to {to_email}')
    except Exception as e:
        logging.error(f'❌ Email error: {e}')


def send_notification_to_webhook(session_data):
    # 1) Convert StripeObject → plain dict without deprecation warnings
    if hasattr(session_data, 'to_dict_recursive'):
        session_info = session_data.to_dict_recursive()
    else:
        # StripeObject subclasses dict, so this will also work
        session_info = dict(session_data)

    meta = session_info.get('metadata', {})
    amount_paid = session_info.get('amount_total', 0) / 100
    customer_email = session_info.get('customer_email', 'Unknown')

    discord_payload = {
        'content': 'New Sale Notification',
        'username': 'Store Bot',
        'embeds': [{
            'fields': [
                {'name': 'Invoice ID', 'value': meta.get('invoice_id', 'N/A'), 'inline': False},
                {'name': 'Total',      'value': f"${amount_paid:.2f}",    'inline': False},
                {'name': 'Customer',   'value': customer_email,           'inline': False},
                {'name': 'Item',       'value': f"{meta.get('product_id')} – {meta.get('plan').title()}", 'inline': False},
            ],
            'footer': {
                'text': f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}"
            }
        }]
    }

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=discord_payload)

        # 2) Handle rate‑limits (429) by waiting and retrying once
        if res.status_code == 429:
            retry_after = int(res.headers.get('Retry-After', 1))
            logging.warning(f"⌛ Discord rate limit, retrying in {retry_after}s…")
            time.sleep(retry_after)
            res = requests.post(DISCORD_WEBHOOK_URL, json=discord_payload)

        res.raise_for_status()
        logging.info('✅ Discord webhook sent.')
    except requests.HTTPError as http_err:
        logging.error(f'❌ Discord webhook HTTP error: {http_err} (status {res.status_code})')
    except Exception as e:
        logging.error(f'❌ Discord webhook error: {e}')


def handle_successful_payment(session_data):
    threading.Thread(target=send_notification_to_webhook, args=(session_data,)).start()

# -----------------------------------------------------------------------------
# Dynamic Routes
# -----------------------------------------------------------------------------
def load_routes():
    if not os.path.exists(ROUTES_PATH):
        return []
    return load_json(ROUTES_PATH)

for route in load_routes():
    tpl, url = route.get('template'), route.get('url')
    if tpl and url:
        app.add_url_rule(url, endpoint=url, view_func=lambda tpl=tpl: render_template(tpl))

# -----------------------------------------------------------------------------
# Routes: Store, Cart & Checkout
# -----------------------------------------------------------------------------

@app.route('/store/<slug>')
def product_page(slug):
    p = app.products.get(slug)
    if not p:
        abort(404)
    return render_template('product.html', product=p)

@app.context_processor
def inject_cart_count():
    cart = session.get('cart', [])
    count = sum(item.get('quantity', 1) for item in cart)
    return dict(cart_count=count)


@app.route('/add-to-cart', methods=['POST'])
def add_to_cart():
    try:
        title = request.form['title']
        slug = request.form['slug']
        price = float(request.form['price'])
        plan = request.form['plan']
        quantity = int(request.form.get('quantity', 1))

        product = find_product(slug)
        if not product:
            logging.error(f"Product not found for slug: {slug}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "error": "Product not found"}), 404
            return "Product not found", 404

        item = {
            'id': f"{slug}-{plan}",
            'title': title,
            'slug': slug,
            'price': price,
            'plan': plan,
            'image_url': product['image_url'],
            'subtitle': product['subtitle'],
            'quantity': quantity
        }

        cart = session.get('cart', [])

        for existing in cart:
            if existing.get('id') == item['id']:
                existing['quantity'] += quantity
                break
        else:
            cart.append(item)

        session['cart'] = cart
        session.modified = True

        # Compute total quantity
        total_quantity = sum(i.get('quantity', 1) for i in cart)

        # AJAX request?
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "cart_count": total_quantity})

        flash('Item added to cart!', 'success')
        return redirect(request.referrer or '/store')

    except Exception as e:
        logging.error(f"Error in add_to_cart: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": str(e)}), 400
        return "Bad Request", 400

@app.route("/cart")
def cart():
    cart_items = session.get("cart", [])

    # 1) Calculate subtotal
    cart_subtotal = sum(item['price'] * item['quantity'] for item in cart_items)

    # 2) (Optional) Calculate tax
    TAX_RATE = 0.0
    cart_tax = round(cart_subtotal * TAX_RATE, 2)

    # 3) Calculate total (subtotal + tax)
    cart_total = round(cart_subtotal + cart_tax, 2)

    # 4) Pass all three into the template
    return render_template(
        "cart.html",
        cart_items=cart_items,
        cart_subtotal=cart_subtotal,
        cart_tax=cart_tax,
        cart_total=cart_total
    )


@app.route('/update-quantity', methods=['POST'])
def update_quantity():
    try:
        item_id = request.form.get('item_id')
        quantity = int(request.form.get('quantity', 1))

        cart = session.get('cart', [])

        for item in cart:
            if str(item.get('id')) == str(item_id):
                item['quantity'] = max(quantity, 1)
                break

        session['cart'] = cart
        session.modified = True
        return redirect(url_for('cart'))

    except Exception as e:
        logging.error(f"Error in update_quantity: {e}")
        return "Bad Request", 400


@app.route('/remove-from-cart', methods=['POST'])
def remove_from_cart():
    try:
        item_id = request.form.get('item_id')
        cart = session.get('cart', [])
        cart = [item for item in cart if str(item.get('id')) != str(item_id)]

        session['cart'] = cart
        session.modified = True
        return redirect(url_for('cart'))

    except Exception as e:
        logging.error(f"Error in remove_from_cart: {e}")
        return "Bad Request", 400

@app.route('/clear-cart')
def clear_cart():
    session.pop('cart', None)
    session.pop('_flashes', None)
    flash('Cart cleared.', 'info')
    return redirect(request.referrer or url_for('cart'))


@app.route('/checkout')
def checkout():
    cart = session.get('cart', [])
    if not cart:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart'))

    total = sum(item.get('price', 0) * item.get('quantity', 1) for item in cart)

    return render_template(
        'checkout.html',
        cart_items=session.get("cart", []),
        total=total,
        stripe_public_key=STRIPE_PUBLIC_KEY
    )

from flask import Flask, request, session, jsonify, render_template
import stripe
import uuid
import json
import logging
from datetime import datetime, timezone

# -----------------------------------------------------------------------------
# Stripe Checkout Session (Cart-based)
# -----------------------------------------------------------------------------
@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = request.get_json(force=True) or {}
    email = data.get('email')

    cart = session.get('cart', [])
    if not cart or not email:
        return jsonify({'error': 'Missing cart items or email'}), 400

    line_items = []
    metadata_items = []
    total_amount = 0

    for item in cart:
        quantity = int(item.get('quantity', 1))
        raw_name = f"{item['title']} – {item['plan'].title()}"
        name = raw_name[:127]
        unit_amount = int(item['price'] * 100)
        line_items.append({
            'price_data': {
                'currency': 'usd',
                'product_data': {'name': name},
                'unit_amount': unit_amount,
            },
            'quantity': quantity,
        })
        total_amount += unit_amount * quantity
        metadata_items.append(f"{item['slug']}:{item['plan']}:{quantity}")

    try:
        invoice_id = str(uuid.uuid4())
        session_obj = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=line_items,
            mode='payment',
            customer_email=email,
            success_url=f"{request.host_url}success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{request.host_url}cancel",
            metadata={
                'invoice_id': invoice_id,
                'cart_items': json.dumps(metadata_items),
                'ip_address': request.remote_addr,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }
        )

        # Send "Order Created" email
        total_str = f"${total_amount/100:.2f}"
        items_html = ''.join([f"<li>{item['title']} – {item['plan'].title()} × {item.get('quantity',1)} – ${int(item['price']*item.get('quantity',1)):.2f}</li>" for item in cart])
        pay_link = session_obj.url

        html = f"""
<!DOCTYPE html>
<html lang=\"en\"><head><meta charset=\"UTF-8\"><title>Order Created</title></head>
<body style=\"margin:0;padding:0;background:#121212;color:#ECECEC;font-family:Arial,sans-serif;\">
  <table width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:600px;margin:40px auto;\">
    <tr>
      <td style=\"background:#1A1A1A;padding:30px;border-bottom:1px solid #333;\">
        <h2 style=\"color:#FFF;\">Order Created</h2>
        <p>Thank you for your order. Your transaction is pending payment.</p>
        <h3>Order Details</h3>
        <p><strong>Invoice ID:</strong> {invoice_id}</p>
        <ul>{items_html}</ul>
        <p><strong>Total:</strong> {total_str}</p>
        <p style=\"text-align:center;\"><a href=\"{pay_link}\" style=\"padding:12px 24px;background:#2979FF;color:#FFF;text-decoration:none;border-radius:4px;\">Pay Now</a></p>
        <p style=\"font-size:12px;color:#777;\">We will send a confirmation once your payment clears.</p>
      </td>
    </tr>
  </table>
</body></html>
"""
        send_email(email, 'Order Created', html)

        # Clear cart
        session.pop('cart', None)
        return jsonify({'id': session_obj.id})

    except stripe.error.StripeError as e:
        logging.exception("Stripe error creating checkout session")
        return jsonify(error=e.user_message or str(e)), 500
    except Exception as e:
        logging.exception("Unexpected error in create_checkout_session")
        return jsonify(error=str(e)), 500


# -----------------------------------------------------------------------------
# Success Route (Post-Payment)
# -----------------------------------------------------------------------------
@app.route('/success')
def success():
    # 1) Normalize & sanitize session_id
    raw_id     = request.args.get('session_id', '')
    session_id = raw_id.strip('{}')
    if not session_id:
        return render_template('404.html', message='No session ID provided.'), 400

    try:
        # 2) Fetch the session (we expand only line_items so we get amount_total)
        sess = stripe.checkout.Session.retrieve(
            session_id,
            expand=['line_items']
        )

        # 3) Core data
        customer      = sess.customer_email or sess.customer_details.email
        invoice_id    = sess.metadata.get('invoice_id', 'N/A')
        total_dollars = f"${(sess.amount_total or 0) / 100:.2f}"

       # 4) Build HTML list & summary from the cart_items metadata
        raw_cart = sess.metadata.get('cart_items', '[]')
        entries  = json.loads(raw_cart)   # e.g. ["slug1:basic:2", "slug2:pro:1"]

        html_items = ""
        product_summary = []
        for entry in entries:
            slug, plan, qty = entry.split(':')
            qty = int(qty)

            # lookup the product title from your loaded JSON
            prod = find_product(slug)
            title = prod['title'] if prod else slug

            # build both representations
            html_items += f"<li>{title} – {plan.title()} × {qty}</li>"
            product_summary.append(f"{title} – {plan.title()} × {qty}")

        product_str = ', '.join(product_summary)
        # 5) Send the confirmation email
        email_html = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Payment Confirmed</title></head>
<body style="margin:0;padding:0;background:#121212;color:#ECECEC;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:40px auto;">
    <tr>
      <td style="background:#1A1A1A;padding:30px;border-bottom:1px solid #333;">
        <h2 style="margin:0 0 20px;font-weight:400;color:#FFF;">Payment Confirmed</h2>
        <p>Thank you for your purchase, {customer}!</p>
        <h3>Invoice ID: {invoice_id}</h3>
        <ul style="margin:12px 0;padding-left:20px;">
          {html_items or '<li>No details available</li>'}
        </ul>
        <p><strong>Total Paid:</strong> {total_dollars}</p>
        <p style="text-align:center;margin:32px 0;">
          <a href="{request.url_root}"
             style="padding:12px 24px;background:#2979FF;color:#FFF;
                    text-decoration:none;border-radius:4px;display:inline-block;">
            Return to Site
          </a>
        </p>
        <p style="font-size:12px;color:#777;margin:0;">
          A copy of this receipt has been sent to your email.
        </p>
      </td>
    </tr>
    <tr>
      <td style="background:#1F1F1F;padding:16px;text-align:center;font-size:12px;color:#777;">
        © 2025 moealturej | All rights reserved.
      </td>
    </tr>
  </table>
</body>
</html>
"""
        send_email(customer, 'Payment Confirmed', email_html)

        # 6) Render your pretty success page
        return render_template(
            'success.html',
            email=customer,
            invoice_id=invoice_id,
            product=product_str,
            total=total_dollars
        )

    except Exception:
        logging.exception('Error in success route')
        return render_template('404.html', message='Error verifying payment.'), 500
                               
@app.route('/cancel')
def cancel():
    return render_template('cancel.html')

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if sig_header is None:
        logging.warning("⚠️  Missing Stripe-Signature header")
        return 'Missing signature', 400

    # Verify Stripe signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, WEBHOOK_SECRET
        )
        logging.info("✅ Stripe event verified successfully.")
    except ValueError as e:
        logging.warning(f"⚠️  Invalid payload: {e}")
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError as e:
        logging.warning(f"⚠️  Invalid signature: {e}")
        return 'Invalid signature', 400

    # Handle the event
    try:
        if event['type'] == 'checkout.session.completed':
            session_obj = event['data']['object']
            logging.info("💳 Checkout session completed. Processing payment...")

            # Run webhook handler in a background thread to avoid delays
            threading.Thread(
                target=handle_successful_payment,
                args=(session_obj,),
                daemon=True  # Will not block server shutdown
            ).start()

    except Exception as e:
        logging.exception(f"🚨 Error handling {event['type']}: {e}")
        return 'Webhook handler error', 200  # Respond OK to prevent retries

    return 'Webhook handled', 200  # Acknowledge receipt

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), debug=False)
