from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse, unquote

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for, send_from_directory
from werkzeug.utils import safe_join


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _sha256_text(value: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}:{value}".encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _user_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def register_loader_api(
    app,
    *,
    current_user: Callable[[], dict | None],
    load_products: Callable[[], list],
    load_orders_for_user: Callable[[str | None], list],
    get_user_by_email: Callable[[str], dict | None],
    authenticate_password: Callable[[str, str], dict | None],
    files_dir: Path,
    using_mongo: Callable[[], bool],
    db,
    load_support_tickets_for_user: Callable[[str], list] | None = None,
    find_support_ticket: Callable[[str], dict | None] | None = None,
    save_support_ticket: Callable[[dict], None] | None = None,
    append_ticket_message: Callable[[dict, dict, str, list | None], dict] | None = None,
    ticket_public_id: Callable[[], str] | None = None,
    login_endpoint: str = "login",
):
    """Register a browser-approved device login and authenticated loader API."""

    bp = Blueprint("loader_api", __name__)
    data_dir = Path(app.root_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    devices_file = data_dir / "loader_devices.json"
    sessions_file = data_dir / "loader_sessions.json"
    pepper = str(app.config.get("SECRET_KEY") or "")

    def _json_load(path: Path) -> list[dict]:
        try:
            return json.loads(path.read_text("utf-8")) if path.exists() else []
        except Exception:
            return []

    def _json_save(path: Path, items: list[dict]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(items, indent=2, default=str), "utf-8")
        temp.replace(path)

    def _collection(name: str):
        return db[name] if using_mongo() and db is not None else None

    def _device_find(query: dict) -> dict | None:
        col = _collection("loader_devices")
        if col is not None:
            return col.find_one(query, {"_id": 0})
        for item in _json_load(devices_file):
            if all(item.get(k) == v for k, v in query.items()):
                return item
        return None

    def _device_upsert(device_code_hash: str, payload: dict) -> None:
        payload = {**payload, "device_code_hash": device_code_hash}
        col = _collection("loader_devices")
        if col is not None:
            col.update_one({"device_code_hash": device_code_hash}, {"$set": payload}, upsert=True)
            return
        items = [x for x in _json_load(devices_file) if x.get("device_code_hash") != device_code_hash]
        items.append(payload)
        _json_save(devices_file, items[-500:])

    def _device_delete(device_code_hash: str) -> None:
        col = _collection("loader_devices")
        if col is not None:
            col.delete_one({"device_code_hash": device_code_hash})
            return
        _json_save(devices_file, [x for x in _json_load(devices_file) if x.get("device_code_hash") != device_code_hash])

    def _session_find(access_hash: str | None = None, refresh_hash: str | None = None) -> dict | None:
        query = {"access_hash": access_hash} if access_hash else {"refresh_hash": refresh_hash}
        col = _collection("loader_sessions")
        if col is not None:
            return col.find_one(query, {"_id": 0})
        for item in _json_load(sessions_file):
            if all(item.get(k) == v for k, v in query.items()):
                return item
        return None

    def _session_upsert(refresh_hash: str, payload: dict) -> None:
        payload = {**payload, "refresh_hash": refresh_hash}
        col = _collection("loader_sessions")
        if col is not None:
            col.update_one({"refresh_hash": refresh_hash}, {"$set": payload}, upsert=True)
            return
        items = [x for x in _json_load(sessions_file) if x.get("refresh_hash") != refresh_hash]
        items.append(payload)
        _json_save(sessions_file, items[-1000:])

    def _session_delete(refresh_hash: str) -> None:
        col = _collection("loader_sessions")
        if col is not None:
            col.delete_one({"refresh_hash": refresh_hash})
            return
        _json_save(sessions_file, [x for x in _json_load(sessions_file) if x.get("refresh_hash") != refresh_hash])

    def _new_tokens(email: str, device_name: str, device_id: str) -> dict:
        access = secrets.token_urlsafe(40)
        refresh = secrets.token_urlsafe(56)
        now = _utcnow()
        record = {
            "email": email.lower(),
            "device_name": device_name[:120],
            "device_id": device_id[:160],
            "access_hash": _sha256_text(access, pepper),
            "access_expires_at": _iso(now + timedelta(minutes=15)),
            "refresh_expires_at": _iso(now + timedelta(days=30)),
            "created_at": _iso(now),
            "last_seen_at": _iso(now),
            "revoked": False,
        }
        _session_upsert(_sha256_text(refresh, pepper), record)
        return {"access_token": access, "refresh_token": refresh, "expires_in": 900, "token_type": "Bearer"}

    def _bearer_session() -> dict | None:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:].strip()
        if not token:
            return None
        record = _session_find(access_hash=_sha256_text(token, pepper))
        if not record or record.get("revoked"):
            return None
        try:
            if _parse(record.get("access_expires_at")) <= _utcnow():
                return None
        except Exception:
            return None
        return record

    def _api_auth(view):
        from functools import wraps

        @wraps(view)
        def wrapped(*args, **kwargs):
            auth = _bearer_session()
            if not auth:
                return jsonify({"ok": False, "error": "unauthorized"}), 401
            request.loader_session = auth  # type: ignore[attr-defined]
            return view(*args, **kwargs)

        return wrapped

    def _owned_slugs(email: str) -> set[str]:
        user = get_user_by_email(email) or {}
        if user.get("is_owner") or user.get("role") in {"owner", "admin"}:
            return {str(p.get("slug")) for p in load_products() if p.get("slug")}
        owned: set[str] = set()
        for order in load_orders_for_user(email):
            if str(order.get("status", "")).lower() != "paid":
                continue
            for item in ((order.get("cart") or {}).get("items") or []):
                slug = str(item.get("slug") or "").strip()
                if slug:
                    owned.add(slug)
        return owned

    def _product_for_slug(slug: str) -> dict | None:
        return next((p for p in load_products() if str(p.get("slug")) == slug), None)

    def _product_file(product: dict) -> tuple[Path, str] | None:
        downloads = product.get("downloads") or {}
        raw = str(
            downloads.get("downloadUrl")
            or downloads.get("download_url")
            or downloads.get("fileUrl")
            or downloads.get("file_url")
            or ""
        ).strip()
        if not raw:
            return None

        parsed = urlparse(raw)
        path_part = unquote(parsed.path if parsed.scheme or parsed.netloc else raw)
        filename = ""
        if path_part.startswith("/download/"):
            filename = path_part.removeprefix("/download/").strip("/")
        elif path_part.startswith("download/"):
            filename = path_part.removeprefix("download/").strip("/")
        else:
            candidate = str(downloads.get("fileName") or downloads.get("file_name") or "").strip()
            if candidate:
                filename = candidate.strip("/")

        if not filename:
            return None
        safe = safe_join(str(files_dir), filename)
        if not safe:
            return None
        path = Path(safe)
        return (path, filename) if path.exists() and path.is_file() else None

    def _public_product(product: dict, *, owned: bool = False) -> dict:
        downloads = product.get("downloads") or {}
        status = product.get("status") or {}
        file_info = _product_file(product)
        enabled = bool(downloads.get("enabled"))
        sha = _file_sha256(file_info[0]) if file_info else ""
        size = file_info[0].stat().st_size if file_info else 0
        image = str(product.get("image") or "/static/logo.png")
        slug = str(product.get("slug") or "")
        return {
            "id": str(product.get("id") or slug),
            "slug": slug,
            "name": product.get("name") or "Product",
            "image_url": image if image.startswith("http") else request.host_url.rstrip("/") + image,
            "version": str(downloads.get("version") or "Latest"),
            "status": str(status.get("label") or status.get("state") or "Unknown"),
            "available": bool(enabled and file_info),
            "owned": bool(owned),
            "file_name": file_info[1] if file_info else "",
            "file_size": size,
            "sha256": sha,
            "release_notes": str(downloads.get("releaseNotes") or downloads.get("release_notes") or ""),
            "download_url": url_for("loader_api.loader_download", slug=slug, _external=True) if owned and enabled and file_info else "",
            "product_url": url_for("product_detail", slug=slug, _external=True) if slug else url_for("products", _external=True),
        }


    def _current_loader_user() -> dict:
        auth = request.loader_session  # type: ignore[attr-defined]
        return get_user_by_email(auth.get("email")) or {"email": auth.get("email"), "username": str(auth.get("email", "")).split("@")[0]}

    def _public_ticket(ticket: dict, include_messages: bool = True) -> dict:
        payload = {
            "ticket_id": str(ticket.get("ticket_id") or ""),
            "subject": str(ticket.get("subject") or "Support ticket"),
            "status": str(ticket.get("status") or "open"),
            "category": str(ticket.get("category") or "general"),
            "product_name": str(ticket.get("product_name") or ""),
            "order_id": str(ticket.get("order_id") or ""),
            "created_at": str(ticket.get("created_at") or ""),
            "updated_at": str(ticket.get("updated_at") or ""),
            "messages": [],
        }
        if include_messages:
            payload["messages"] = [{
                "id": str(m.get("id") or ""),
                "body": str(m.get("body") or ""),
                "actor_kind": str(m.get("actor_kind") or "customer"),
                "author": str((m.get("actor") or {}).get("username") or (m.get("actor") or {}).get("email") or "Support"),
                "created_at": str(m.get("created_at") or ""),
            } for m in (ticket.get("messages") or [])]
        return payload

    def _owned_order_items(email: str) -> list[dict]:
        result = []
        for order in load_orders_for_user(email):
            if str(order.get("status", "")).lower() != "paid":
                continue
            deliveries = order.get("deliveries") or {}
            for index, item in enumerate(((order.get("cart") or {}).get("items") or [])):
                delivery = deliveries.get(str(index)) or {}
                result.append({
                    "order_id": str(order.get("order_id") or ""),
                    "item_index": index,
                    "product_name": str(item.get("product_name") or item.get("name") or "Product"),
                    "option_name": str(item.get("option_name") or item.get("option") or ""),
                    "slug": str(item.get("slug") or ""),
                    "purchased_at": str(order.get("paid_at") or order.get("created_at") or ""),
                    "delivery_status": "delivered" if delivery else "pending",
                    "product_key": str(delivery.get("product_key") or ""),
                    "note": str(delivery.get("note") or ""),
                })
        return result

    @bp.post("/api/loader/login")
    def native_login():
        payload = request.get_json(silent=True) or {}
        email = str(payload.get("email") or "").strip().lower()[:254]
        password = str(payload.get("password") or "")
        device_name = str(payload.get("device_name") or "Windows PC")[:120]
        device_id = str(payload.get("device_id") or "")[:160]

        # Keep the response generic so account existence is not disclosed.
        user = authenticate_password(email, password) if email and password else None
        if not user or str(user.get("status") or "active").lower() == "suspended":
            return jsonify({"ok": False, "error": "Invalid email or password."}), 401

        tokens = _new_tokens(str(user.get("email") or email), device_name, device_id)
        return jsonify({"ok": True, "status": "approved", **tokens})

    @bp.post("/api/loader/device/start")
    def device_start():
        payload = request.get_json(silent=True) or {}
        device_code = secrets.token_urlsafe(36)
        user_code = _user_code()
        now = _utcnow()
        record = {
            "user_code": user_code,
            "device_name": str(payload.get("device_name") or "Windows PC")[:120],
            "device_id": str(payload.get("device_id") or "")[:160],
            "status": "pending",
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(minutes=10)),
            "last_poll_at": "",
        }
        _device_upsert(_sha256_text(device_code, pepper), record)
        return jsonify({
            "ok": True,
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": url_for("loader_api.activate", _external=True),
            "verification_uri_complete": url_for("loader_api.activate", code=user_code, _external=True),
            "expires_in": 600,
            "interval": 3,
        })

    @bp.route("/loader/activate", methods=["GET", "POST"])
    def activate():
        user = current_user()
        code = str(request.values.get("code") or "").strip().upper()
        provider = str(request.values.get("provider") or "").strip().lower()
        auto = str(request.values.get("auto") or "") == "1"
        if not user:
            next_url = url_for("loader_api.activate", code=code, provider=provider, auto="1" if auto else "0") if code else url_for("loader_api.activate")
            if provider == "discord":
                return redirect(url_for("discord_login", next=next_url))
            if provider == "google":
                return redirect(url_for("google_login", next=next_url))
            return redirect(url_for(login_endpoint, next=next_url))
        message = ""
        success = False
        if request.method == "POST" or auto:
            record = _device_find({"user_code": code}) if code else None
            if not record:
                message = "That code was not found. Check the loader and try again."
            else:
                try:
                    expired = _parse(record.get("expires_at")) <= _utcnow()
                except Exception:
                    expired = True
                if expired:
                    message = "That code expired. Start sign-in again from the loader."
                elif record.get("status") != "pending":
                    message = "That code has already been used."
                else:
                    device_hash = record.get("device_code_hash")
                    record.update({
                        "status": "approved",
                        "email": str(user.get("email") or "").lower(),
                        "username": user.get("username") or user.get("email"),
                        "approved_at": _iso(_utcnow()),
                    })
                    _device_upsert(device_hash, record)
                    success = True
                    message = "Your loader is connected. You can return to the app."
        return render_template("loader_activate.html", code=code, message=message, success=success, user=user)

    @bp.post("/api/loader/device/status")
    def device_status():
        payload = request.get_json(silent=True) or {}
        device_code = str(payload.get("device_code") or "")
        record = _device_find({"device_code_hash": _sha256_text(device_code, pepper)}) if device_code else None
        if not record:
            return jsonify({"ok": False, "error": "invalid_device_code"}), 400
        try:
            if _parse(record.get("expires_at")) <= _utcnow():
                _device_delete(record.get("device_code_hash"))
                return jsonify({"ok": False, "error": "expired_token"}), 400
        except Exception:
            return jsonify({"ok": False, "error": "expired_token"}), 400
        if record.get("status") != "approved":
            return jsonify({"ok": True, "status": "authorization_pending"}), 202
        tokens = _new_tokens(record.get("email", ""), record.get("device_name", "Windows PC"), record.get("device_id", ""))
        _device_delete(record.get("device_code_hash"))
        return jsonify({"ok": True, "status": "approved", **tokens})

    @bp.post("/api/loader/session/refresh")
    def refresh():
        payload = request.get_json(silent=True) or {}
        refresh_token = str(payload.get("refresh_token") or "")
        refresh_hash = _sha256_text(refresh_token, pepper)
        record = _session_find(refresh_hash=refresh_hash) if refresh_token else None
        if not record or record.get("revoked"):
            return jsonify({"ok": False, "error": "invalid_refresh_token"}), 401
        try:
            if _parse(record.get("refresh_expires_at")) <= _utcnow():
                _session_delete(refresh_hash)
                return jsonify({"ok": False, "error": "refresh_token_expired"}), 401
        except Exception:
            return jsonify({"ok": False, "error": "refresh_token_expired"}), 401
        access = secrets.token_urlsafe(40)
        record["access_hash"] = _sha256_text(access, pepper)
        record["access_expires_at"] = _iso(_utcnow() + timedelta(minutes=15))
        record["last_seen_at"] = _iso(_utcnow())
        _session_upsert(refresh_hash, record)
        return jsonify({"ok": True, "access_token": access, "expires_in": 900, "token_type": "Bearer"})

    @bp.post("/api/loader/logout")
    def logout():
        payload = request.get_json(silent=True) or {}
        refresh_token = str(payload.get("refresh_token") or "")
        if refresh_token:
            _session_delete(_sha256_text(refresh_token, pepper))
        return jsonify({"ok": True})

    @bp.get("/api/loader/account")
    @_api_auth
    def account():
        auth = request.loader_session  # type: ignore[attr-defined]
        user = get_user_by_email(auth.get("email")) or {"email": auth.get("email")}
        return jsonify({
            "ok": True,
            "account": {
                "email": user.get("email"),
                "username": user.get("username") or str(user.get("email", "")).split("@")[0],
                "role": user.get("role") or "user",
                "discord_connected": bool(user.get("discord_id")),
                "discord_username": user.get("discord_username") or user.get("discord_global_name") or "",
                "discord_avatar": user.get("discord_avatar") or "",
            },
        })

    @bp.get("/api/loader/library")
    @_api_auth
    def library():
        auth = request.loader_session  # type: ignore[attr-defined]
        owned_slugs = _owned_slugs(auth.get("email", ""))
        products = []
        for product in load_products():
            slug = str(product.get("slug") or "")
            products.append(_public_product(product, owned=slug in owned_slugs))
        products.sort(key=lambda item: (not item.get("owned", False), not item.get("available", False), str(item.get("name", "")).lower()))
        return jsonify({
            "ok": True,
            "products": products,
            "count": len(products),
            "owned_count": sum(1 for item in products if item.get("owned")),
        })

    @bp.get("/api/loader/products/<slug>/manifest")
    @_api_auth
    def manifest(slug: str):
        auth = request.loader_session  # type: ignore[attr-defined]
        if slug not in _owned_slugs(auth.get("email", "")):
            return jsonify({"ok": False, "error": "not_owned"}), 403
        product = _product_for_slug(slug)
        if not product:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "product": _public_product(product, owned=True)})

    @bp.get("/api/loader/products/<slug>/download")
    @_api_auth
    def loader_download(slug: str):
        auth = request.loader_session  # type: ignore[attr-defined]
        if slug not in _owned_slugs(auth.get("email", "")):
            abort(403)
        product = _product_for_slug(slug)
        file_info = _product_file(product or {})
        if not product or not (product.get("downloads") or {}).get("enabled") or not file_info:
            abort(404)
        return send_from_directory(str(files_dir), file_info[1], as_attachment=True, conditional=True)


    @bp.get("/api/loader/keys")
    @_api_auth
    def keys():
        user = _current_loader_user()
        items = _owned_order_items(str(user.get("email") or ""))
        return jsonify({"ok": True, "keys": items, "count": len(items)})

    @bp.get("/api/loader/support/tickets")
    @_api_auth
    def support_tickets():
        if load_support_tickets_for_user is None:
            return jsonify({"ok": False, "error": "support_unavailable"}), 503
        user = _current_loader_user()
        tickets = load_support_tickets_for_user(str(user.get("email") or ""))
        return jsonify({"ok": True, "tickets": [_public_ticket(t, False) for t in tickets]})

    @bp.get("/api/loader/support/tickets/<ticket_id>")
    @_api_auth
    def support_ticket(ticket_id: str):
        if find_support_ticket is None:
            return jsonify({"ok": False, "error": "support_unavailable"}), 503
        user = _current_loader_user()
        ticket = find_support_ticket(ticket_id)
        if not ticket or str(ticket.get("user_email", "")).lower() != str(user.get("email", "")).lower():
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "ticket": _public_ticket(ticket, True)})

    @bp.post("/api/loader/support/tickets")
    @_api_auth
    def create_support_ticket():
        if save_support_ticket is None or ticket_public_id is None:
            return jsonify({"ok": False, "error": "support_unavailable"}), 503
        user = _current_loader_user()
        payload = request.get_json(silent=True) or {}
        subject = str(payload.get("subject") or "").strip()[:140]
        body = str(payload.get("message") or "").strip()[:3000]
        category = str(payload.get("category") or "general").strip().lower()[:40]
        order_id = str(payload.get("order_id") or "").strip()[:120]
        try: item_index = int(payload.get("item_index", -1))
        except Exception: item_index = -1
        owned_items = _owned_order_items(str(user.get("email") or ""))
        match = next((x for x in owned_items if x["order_id"] == order_id and x["item_index"] == item_index), None)
        if not subject or len(body) < 3:
            return jsonify({"ok": False, "error": "Add a subject and message."}), 400
        if not match:
            return jsonify({"ok": False, "error": "Choose a valid purchased product."}), 400
        now = _iso(_utcnow())
        ticket = {
            "ticket_id": ticket_public_id(), "user_email": str(user.get("email") or "").lower(),
            "username": user.get("username") or user.get("email"), "subject": subject, "category": category,
            "status": "open", "order_id": order_id, "item_index": item_index, "product_name": match["product_name"],
            "created_at": now, "updated_at": now, "messages": []
        }
        if append_ticket_message is not None:
            append_ticket_message(ticket, user, body, [])
        else:
            ticket["messages"].append({"id": secrets.token_urlsafe(10), "body": body, "actor_kind": "customer", "actor": {"username": user.get("username"), "email": user.get("email")}, "created_at": now})
            save_support_ticket(ticket)
        return jsonify({"ok": True, "ticket": _public_ticket(ticket, True)}), 201

    @bp.post("/api/loader/support/tickets/<ticket_id>/reply")
    @_api_auth
    def reply_support_ticket(ticket_id: str):
        if find_support_ticket is None or append_ticket_message is None:
            return jsonify({"ok": False, "error": "support_unavailable"}), 503
        user = _current_loader_user()
        ticket = find_support_ticket(ticket_id)
        if not ticket or str(ticket.get("user_email", "")).lower() != str(user.get("email", "")).lower():
            return jsonify({"ok": False, "error": "not_found"}), 404
        if str(ticket.get("status") or "").lower() == "closed":
            return jsonify({"ok": False, "error": "This ticket is closed."}), 409
        body = str((request.get_json(silent=True) or {}).get("message") or "").strip()[:3000]
        if len(body) < 1:
            return jsonify({"ok": False, "error": "Write a message first."}), 400
        append_ticket_message(ticket, user, body, [])
        return jsonify({"ok": True, "ticket": _public_ticket(ticket, True)})

    app.register_blueprint(bp)
