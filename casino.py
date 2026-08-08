"""Virtual-credit casino for the moealturej Flask site.

All outcomes and balances are server-authoritative. Credits have no cash value,
are not purchasable, transferable, or withdrawable, and exist only for play.
"""
from __future__ import annotations

import math
import mimetypes
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

INITIAL_CREDITS = 5_000
DAILY_CREDITS = 500
MIN_WAGER = 10
MAX_WAGER = 25_000
PROFILE_IMAGE_MAX_BYTES = 4 * 1024 * 1024
ALLOWED_PROFILE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

SLOT_SYMBOLS = [
    {"key": "cherry", "symbol": "🍒", "weight": 30, "triple": 6.0},
    {"key": "lemon", "symbol": "🍋", "weight": 25, "triple": 8.0},
    {"key": "grape", "symbol": "🍇", "weight": 18, "triple": 12.0},
    {"key": "bell", "symbol": "🔔", "weight": 12, "triple": 20.0},
    {"key": "star", "symbol": "⭐", "weight": 9, "triple": 35.0},
    {"key": "seven", "symbol": "7", "weight": 6, "triple": 80.0},
]
PLINKO_MULTIPLIERS = [14.0, 6.0, 2.4, 1.55, 1.08, 0.72, 0.55, 0.72, 1.08, 1.55, 2.4, 6.0, 14.0]
ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_WHEEL = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
CARD_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
CARD_SUITS = ["♠", "♥", "♦", "♣"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive_now() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value or "")


def _weighted_choice(items: list[dict]) -> dict:
    total = sum(int(item["weight"]) for item in items)
    point = secrets.randbelow(total)
    for item in items:
        point -= int(item["weight"])
        if point < 0:
            return item
    return items[-1]


def _secure_shuffle(values: list[Any]) -> list[Any]:
    values = list(values)
    for index in range(len(values) - 1, 0, -1):
        swap = secrets.randbelow(index + 1)
        values[index], values[swap] = values[swap], values[index]
    return values


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def register_casino(
    app,
    *,
    db,
    users_col,
    using_mongo: Callable[[], bool],
    current_user: Callable[[], dict | None],
    login_required,
    limiter,
    csrf_token: Callable[[], str],
    verify_csrf: Callable[[], bool],
    sanitize_user_for_session: Callable[[dict | None], dict | None],
    uploads_dir: Path,
    save_upload_to_mongo_storage,
    save_media_record,
    record_audit,
):
    chat_col = db["casino_chat"] if db is not None else None
    ledger_col = db["casino_ledger"] if db is not None else None
    rounds_col = db["casino_rounds"] if db is not None else None

    if db is not None:
        try:
            from pymongo import ASCENDING, DESCENDING
            chat_col.create_index([("created_at", DESCENDING)])
            chat_col.create_index([("message_id", ASCENDING)], unique=True)
            ledger_col.create_index([("created_at", DESCENDING)])
            ledger_col.create_index([("email", ASCENDING), ("created_at", DESCENDING)])
            rounds_col.create_index([("game_id", ASCENDING)], unique=True)
            rounds_col.create_index([("email", ASCENDING), ("status", ASCENDING)])
            rounds_col.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
        except Exception:
            app.logger.exception("Casino index setup failed")

    def _fresh_user() -> dict | None:
        user = current_user() or {}
        email = str(user.get("email") or "").strip().lower()
        if not email or not using_mongo() or users_col is None:
            return None
        return users_col.find_one({"email": email}, {"_id": 0})

    def _avatar_for(user: dict | None) -> str:
        user = user or {}
        return str(
            user.get("avatar_url")
            or user.get("google_avatar")
            or user.get("discord_avatar")
            or "/static/logo.png"
        )[:500]

    def _ensure_wallet(email: str) -> dict:
        users_col.update_one(
            {"email": email, "casino_credits": {"$exists": False}},
            {"$set": {
                "casino_credits": INITIAL_CREDITS,
                "casino_games_played": 0,
                "casino_total_wagered": 0,
                "casino_total_paid": 0,
                "casino_created_at": _naive_now(),
            }},
        )
        return users_col.find_one({"email": email}, {"_id": 0}) or {}

    def _wallet_user() -> dict:
        user = _fresh_user()
        if not user:
            raise RuntimeError("Casino requires MongoDB and a signed-in account.")
        return _ensure_wallet(str(user["email"]).lower())

    def _parse_wager(value: Any) -> int:
        wager = _safe_int(value)
        if wager < MIN_WAGER:
            raise ValueError(f"Minimum wager is {MIN_WAGER:,} credits.")
        if wager > MAX_WAGER:
            raise ValueError(f"Maximum wager is {MAX_WAGER:,} credits.")
        return wager

    def _log_game(user: dict, game: str, wager: int, payout: int, details: dict | None = None) -> None:
        if ledger_col is None:
            return
        ledger_col.insert_one({
            "entry_id": uuid.uuid4().hex,
            "email": str(user.get("email") or "").lower(),
            "username": str(user.get("username") or "Player")[:32],
            "game": game,
            "wager": int(wager),
            "payout": int(payout),
            "profit": int(payout) - int(wager),
            "details": details or {},
            "created_at": _naive_now(),
        })

    def _settle_single(user: dict, game: str, wager: int, payout: int, details: dict | None = None) -> int:
        from pymongo import ReturnDocument
        email = str(user["email"]).lower()
        updated = users_col.find_one_and_update(
            {"email": email, "casino_credits": {"$gte": wager}},
            {"$inc": {
                "casino_credits": int(payout) - int(wager),
                "casino_games_played": 1,
                "casino_total_wagered": int(wager),
                "casino_total_paid": int(payout),
            }, "$set": {"casino_last_played_at": _naive_now()}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "casino_credits": 1},
        )
        if not updated:
            raise ValueError("You do not have enough credits for that wager.")
        _log_game(user, game, wager, payout, details)
        return int(updated.get("casino_credits", 0))

    def _debit(user: dict, game: str, wager: int, *, count_game: bool = True) -> int:
        from pymongo import ReturnDocument
        email = str(user["email"]).lower()
        increments = {
            "casino_credits": -int(wager),
            "casino_total_wagered": int(wager),
        }
        if count_game:
            increments["casino_games_played"] = 1
        updated = users_col.find_one_and_update(
            {"email": email, "casino_credits": {"$gte": wager}},
            {"$inc": increments, "$set": {"casino_last_played_at": _naive_now()}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "casino_credits": 1},
        )
        if not updated:
            raise ValueError("You do not have enough credits for that wager.")
        return int(updated.get("casino_credits", 0))

    def _credit(user: dict, amount: int) -> int:
        from pymongo import ReturnDocument
        updated = users_col.find_one_and_update(
            {"email": str(user["email"]).lower()},
            {"$inc": {"casino_credits": int(amount), "casino_total_paid": int(amount)}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "casino_credits": 1},
        )
        return int((updated or {}).get("casino_credits", 0))

    def _refund_debit(user: dict, amount: int, *, count_game: bool = True) -> None:
        increments = {
            "casino_credits": int(amount),
            "casino_total_wagered": -int(amount),
        }
        if count_game:
            increments["casino_games_played"] = -1
        users_col.update_one({"email": str(user["email"]).lower()}, {"$inc": increments})

    def api_guard(*, csrf: bool = False, age: bool = True):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if not current_user():
                    return jsonify({"ok": False, "error": "Sign in to use the casino."}), 401
                if not using_mongo() or users_col is None:
                    return jsonify({"ok": False, "error": "Casino storage is temporarily unavailable."}), 503
                user = _fresh_user()
                if not user:
                    return jsonify({"ok": False, "error": "Account could not be loaded."}), 401
                if age and not bool(user.get("age_18_confirmed")):
                    return jsonify({"ok": False, "error": "Confirm that you are 18 or older first.", "age_gate_required": True}), 403
                if csrf and not verify_csrf():
                    return jsonify({"ok": False, "error": "Security token expired. Refresh the page."}), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    def _json_error(exc: Exception, status: int = 400):
        app.logger.info("Casino request rejected: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), status

    @app.route("/casino/age-check", methods=["GET", "POST"])
    @login_required
    @limiter.limit("20 per minute")
    def casino_age_check():
        user = _fresh_user()
        if not user:
            flash("The casino requires MongoDB-backed accounts.", "warning")
            return redirect(url_for("account"))
        if request.method == "POST":
            if request.form.get("answer") == "yes":
                users_col.update_one(
                    {"email": user["email"]},
                    {"$set": {"age_18_confirmed": True, "age_18_confirmed_at": _naive_now(), "updated_at": _naive_now()}},
                )
                fresh = _fresh_user()
                session["user"] = sanitize_user_for_session(fresh)
                record_audit("casino_age_confirmed", str(user.get("email") or ""))
                return redirect(url_for("casino_home"))
            flash("Casino access is limited to users who confirm they are 18 or older.", "warning")
            return redirect(url_for("home"))
        if user.get("age_18_confirmed"):
            return redirect(url_for("casino_home"))
        return render_template("casino_age.html", active_page="casino")

    @app.route("/casino")
    @login_required
    @limiter.limit("120 per minute")
    def casino_home():
        user = _fresh_user()
        if not user:
            flash("The casino requires MongoDB-backed accounts.", "warning")
            return redirect(url_for("account"))
        if not user.get("age_18_confirmed"):
            return redirect(url_for("casino_age_check"))
        user = _ensure_wallet(str(user["email"]).lower())
        return render_template(
            "casino.html",
            active_page="casino",
            casino_user=user,
            casino_avatar=_avatar_for(user),
            casino_min_wager=MIN_WAGER,
            casino_max_wager=MAX_WAGER,
            plinko_multipliers=PLINKO_MULTIPLIERS,
        )

    @app.route("/account/profile", methods=["POST"])
    @login_required
    @limiter.limit("12 per hour")
    def account_profile_update():
        user = _fresh_user()
        if not user:
            flash("Profile updates require MongoDB.", "danger")
            return redirect(url_for("account") + "#profile")
        username = re.sub(r"\s+", " ", str(request.form.get("username") or "").strip())
        bio = str(request.form.get("bio") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{3,24}", username):
            flash("Display names must be 3–24 characters using letters, numbers, spaces, dots, dashes, or underscores.", "danger")
            return redirect(url_for("account") + "#profile")
        existing = users_col.find_one({
            "email": {"$ne": user["email"]},
            "$or": [
                {"username_lower": username.lower()},
                {"username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}},
            ],
        }, {"_id": 1})
        if existing:
            flash("That display name is already in use.", "danger")
            return redirect(url_for("account") + "#profile")
        if len(bio) > 240:
            flash("Profile bio must be 240 characters or fewer.", "danger")
            return redirect(url_for("account") + "#profile")

        update = {
            "username": username,
            "username_lower": username.lower(),
            "bio": bio,
            "profile_customized": True,
            "updated_at": _naive_now(),
        }
        if request.form.get("age_18_confirmed") == "on":
            update["age_18_confirmed"] = True
            update["age_18_confirmed_at"] = user.get("age_18_confirmed_at") or _naive_now()

        upload = request.files.get("avatar")
        if upload and upload.filename:
            if request.content_length and request.content_length > PROFILE_IMAGE_MAX_BYTES + (512 * 1024):
                flash("Profile images must be 4 MB or smaller.", "danger")
                return redirect(url_for("account") + "#profile")
            original = secure_filename(upload.filename)
            ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
            mime_type = str(upload.mimetype or mimetypes.guess_type(original)[0] or "")
            if ext not in ALLOWED_PROFILE_EXTENSIONS or not mime_type.startswith("image/"):
                flash("Profile images must be PNG, JPG, GIF, or WEBP.", "danger")
                return redirect(url_for("account") + "#profile")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            filename = f"profile-{uuid.uuid4().hex[:18]}.{ext}"
            local_path = uploads_dir / filename
            upload.save(local_path)
            if local_path.stat().st_size > PROFILE_IMAGE_MAX_BYTES:
                local_path.unlink(missing_ok=True)
                flash("Profile images must be 4 MB or smaller.", "danger")
                return redirect(url_for("account") + "#profile")
            gridfs_id = save_upload_to_mongo_storage(local_path, filename, original, "image", mime_type)
            save_media_record({
                "filename": filename,
                "original_name": original,
                "kind": "profile_image",
                "url": url_for("media_file", filename=filename),
                "mime_type": mime_type,
                "size_bytes": local_path.stat().st_size,
                "storage": "mongodb_gridfs" if gridfs_id else "local_disk",
                "gridfs_id": gridfs_id,
                "created_at": _utcnow().isoformat(),
                "created_by": user["email"],
            })
            update["avatar_url"] = url_for("media_file", filename=filename)

        users_col.update_one({"email": user["email"]}, {"$set": update})
        fresh = _fresh_user()
        session["user"] = sanitize_user_for_session(fresh)
        record_audit("account_profile_update", str(user.get("email") or ""), {"username": username})
        flash("Profile updated.", "success")
        return redirect(url_for("account") + "#profile")

    @app.route("/api/casino/state")
    @api_guard()
    @limiter.limit("120 per minute")
    def casino_state():
        user = _wallet_user()
        users_col.update_one({"email": user["email"]}, {"$set": {"casino_last_seen": _naive_now()}})
        history = []
        if ledger_col is not None:
            for entry in ledger_col.find({"email": user["email"]}, {"_id": 0}).sort("created_at", -1).limit(12):
                entry["created_at"] = _iso(entry.get("created_at"))
                history.append(entry)
        online = 1
        try:
            online = users_col.count_documents({"casino_last_seen": {"$gte": _naive_now() - timedelta(seconds=35)}})
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "balance": int(user.get("casino_credits", INITIAL_CREDITS)),
            "daily_available": str(user.get("casino_daily_claim") or "") != _utcnow().strftime("%Y-%m-%d"),
            "history": history,
            "online": online,
            "profile": {
                "username": user.get("username") or "Player",
                "avatar_url": _avatar_for(user),
                "bio": user.get("bio") or "",
            },
        })

    @app.route("/api/casino/daily", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("8 per hour")
    def casino_daily():
        from pymongo import ReturnDocument
        user = _wallet_user()
        today = _utcnow().strftime("%Y-%m-%d")
        updated = users_col.find_one_and_update(
            {"email": user["email"], "casino_daily_claim": {"$ne": today}},
            {"$inc": {"casino_credits": DAILY_CREDITS}, "$set": {"casino_daily_claim": today, "casino_daily_claimed_at": _naive_now()}},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "casino_credits": 1},
        )
        if not updated:
            return jsonify({"ok": False, "error": "You already claimed today’s free credits."}), 409
        return jsonify({"ok": True, "balance": int(updated["casino_credits"]), "bonus": DAILY_CREDITS})

    @app.route("/api/casino/play/slots", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("45 per minute")
    def casino_slots():
        try:
            user = _wallet_user()
            wager = _parse_wager((request.get_json(silent=True) or {}).get("wager"))
            reels = [_weighted_choice(SLOT_SYMBOLS) for _ in range(3)]
            if reels[0]["key"] == reels[1]["key"] == reels[2]["key"]:
                multiplier = float(reels[0]["triple"])
                label = f"Triple {reels[0]['key']}"
            elif len({reel["key"] for reel in reels}) == 2:
                multiplier = 1.05
                label = "Two of a kind"
            else:
                multiplier = 0.0
                label = "No match"
            payout = int(math.floor(wager * multiplier))
            balance = _settle_single(user, "slots", wager, payout, {"symbols": [r["key"] for r in reels], "multiplier": multiplier})
            return jsonify({"ok": True, "reels": [r["symbol"] for r in reels], "multiplier": multiplier, "payout": payout, "balance": balance, "label": label})
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/play/plinko", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("45 per minute")
    def casino_plinko():
        try:
            user = _wallet_user()
            wager = _parse_wager((request.get_json(silent=True) or {}).get("wager"))
            path = [secrets.randbelow(2) for _ in range(12)]
            slot = sum(path)
            multiplier = PLINKO_MULTIPLIERS[slot]
            payout = int(math.floor(wager * multiplier))
            balance = _settle_single(user, "plinko", wager, payout, {"slot": slot, "path": path, "multiplier": multiplier})
            return jsonify({"ok": True, "path": path, "slot": slot, "multiplier": multiplier, "payout": payout, "balance": balance})
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/play/roulette", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("40 per minute")
    def casino_roulette():
        try:
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            wager = _parse_wager(payload.get("wager"))
            bet_type = str(payload.get("bet_type") or "").lower()
            bet_value = str(payload.get("bet_value") or "").lower()
            number = secrets.randbelow(37)
            color = "green" if number == 0 else ("red" if number in ROULETTE_RED else "black")
            won = False
            multiplier = 0.0
            if bet_type == "color" and bet_value in {"red", "black"}:
                won, multiplier = color == bet_value, 2.0
            elif bet_type == "parity" and bet_value in {"even", "odd"}:
                won, multiplier = number != 0 and ((number % 2 == 0) == (bet_value == "even")), 2.0
            elif bet_type == "range" and bet_value in {"low", "high"}:
                won, multiplier = number != 0 and ((1 <= number <= 18) if bet_value == "low" else (19 <= number <= 36)), 2.0
            elif bet_type == "dozen" and bet_value in {"1", "2", "3"}:
                start = (int(bet_value) - 1) * 12 + 1
                won, multiplier = start <= number <= start + 11, 3.0
            elif bet_type == "straight":
                chosen = _safe_int(bet_value, -1)
                if not 0 <= chosen <= 36:
                    raise ValueError("Choose a roulette number from 0 to 36.")
                won, multiplier = number == chosen, 36.0
            else:
                raise ValueError("Choose a valid roulette bet.")
            payout = int(wager * multiplier) if won else 0
            balance = _settle_single(user, "roulette", wager, payout, {"number": number, "color": color, "bet_type": bet_type, "bet_value": bet_value})
            return jsonify({"ok": True, "number": number, "color": color, "won": won, "multiplier": multiplier if won else 0, "payout": payout, "balance": balance, "wheel_index": ROULETTE_WHEEL.index(number)})
        except Exception as exc:
            return _json_error(exc)

    def _hl_card_payload(value: int) -> dict:
        rank = CARD_RANKS[value - 2]
        suit = CARD_SUITS[secrets.randbelow(len(CARD_SUITS))]
        return {"value": value, "rank": rank, "suit": suit, "red": suit in {"♥", "♦"}}

    def _hl_multipliers(value: int) -> dict:
        higher_wins = max(0, 14 - value)
        lower_wins = max(0, value - 2)
        return {
            "higher": round(0.96 / (higher_wins / 13), 2) if higher_wins else 0,
            "lower": round(0.96 / (lower_wins / 13), 2) if lower_wins else 0,
        }

    @app.route("/api/casino/higher-lower/start", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("60 per minute")
    def casino_hl_start():
        value = secrets.randbelow(13) + 2
        token = secrets.token_urlsafe(18)
        session["casino_hl"] = {"token": token, "value": value, "expires": int(_utcnow().timestamp()) + 300}
        return jsonify({"ok": True, "token": token, "card": _hl_card_payload(value), "multipliers": _hl_multipliers(value)})

    @app.route("/api/casino/higher-lower/guess", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("45 per minute")
    def casino_hl_guess():
        try:
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            wager = _parse_wager(payload.get("wager"))
            direction = str(payload.get("direction") or "").lower()
            round_data = session.get("casino_hl") or {}
            if payload.get("token") != round_data.get("token") or _safe_int(round_data.get("expires")) < int(_utcnow().timestamp()):
                raise ValueError("That Higher/Lower round expired. Deal a new card.")
            current = _safe_int(round_data.get("value"))
            multipliers = _hl_multipliers(current)
            if direction not in {"higher", "lower"} or not multipliers.get(direction):
                raise ValueError("That guess is not available for this card.")
            next_value = secrets.randbelow(13) + 2
            won = next_value > current if direction == "higher" else next_value < current
            multiplier = float(multipliers[direction]) if won else 0.0
            payout = int(math.floor(wager * multiplier)) if won else 0
            session.pop("casino_hl", None)
            balance = _settle_single(user, "higher_lower", wager, payout, {"current": current, "next": next_value, "direction": direction, "multiplier": multiplier})
            return jsonify({"ok": True, "won": won, "current": _hl_card_payload(current), "next": _hl_card_payload(next_value), "multiplier": multiplier, "payout": payout, "balance": balance})
        except Exception as exc:
            return _json_error(exc)

    def _mines_multiplier(mine_count: int, revealed_count: int) -> float:
        if revealed_count <= 0:
            return 1.0
        probability = math.comb(25 - mine_count, revealed_count) / math.comb(25, revealed_count)
        return round(0.97 / probability, 2)

    @app.route("/api/casino/mines/start", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("25 per minute")
    def casino_mines_start():
        try:
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            wager = _parse_wager(payload.get("wager"))
            mine_count = _safe_int(payload.get("mines"), 5)
            if not 3 <= mine_count <= 10:
                raise ValueError("Choose between 3 and 10 mines.")
            balance = _debit(user, "mines", wager)
            positions = _secure_shuffle(list(range(25)))[:mine_count]
            game_id = uuid.uuid4().hex
            try:
                rounds_col.update_many({"email": user["email"], "game": "mines", "status": "active"}, {"$set": {"status": "abandoned"}})
                rounds_col.insert_one({
                    "game_id": game_id,
                    "game": "mines",
                    "email": user["email"],
                    "wager": wager,
                    "mine_count": mine_count,
                    "mines": positions,
                    "revealed": [],
                    "status": "active",
                    "created_at": _naive_now(),
                    "expires_at": _naive_now() + timedelta(hours=2),
                })
            except Exception:
                _refund_debit(user, wager)
                raise
            return jsonify({"ok": True, "game_id": game_id, "mine_count": mine_count, "balance": balance, "multiplier": 1.0})
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/mines/reveal", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("120 per minute")
    def casino_mines_reveal():
        try:
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            game_id = str(payload.get("game_id") or "")
            tile = _safe_int(payload.get("tile"), -1)
            if not 0 <= tile < 25:
                raise ValueError("Invalid mine tile.")
            game = rounds_col.find_one({"game_id": game_id, "email": user["email"], "game": "mines", "status": "active"})
            if not game:
                raise ValueError("That Mines round is no longer active.")
            if tile in game.get("revealed", []):
                raise ValueError("That tile is already open.")
            if tile in game.get("mines", []):
                loss_result = rounds_col.update_one({"_id": game["_id"], "status": "active"}, {"$set": {"status": "lost", "lost_tile": tile, "finished_at": _naive_now()}})
                if loss_result.modified_count != 1:
                    raise ValueError("That Mines round changed before the tile was settled.")
                _log_game(user, "mines", int(game["wager"]), 0, {"mines": game.get("mines", []), "revealed": game.get("revealed", []), "lost_tile": tile})
                wallet = _wallet_user()
                return jsonify({"ok": True, "hit_mine": True, "tile": tile, "mines": game.get("mines", []), "balance": int(wallet.get("casino_credits", 0)), "payout": 0})
            update_result = rounds_col.update_one(
                {"_id": game["_id"], "status": "active", "revealed": {"$ne": tile}},
                {"$addToSet": {"revealed": tile}, "$set": {"updated_at": _naive_now()}},
            )
            fresh_game = rounds_col.find_one({"_id": game["_id"]}) or game
            if update_result.modified_count != 1 or fresh_game.get("status") != "active":
                raise ValueError("That Mines round changed. Refresh the round state.")
            revealed = list(fresh_game.get("revealed", []))
            multiplier = _mines_multiplier(int(fresh_game["mine_count"]), len(revealed))
            potential = int(math.floor(int(fresh_game["wager"]) * multiplier))
            return jsonify({"ok": True, "hit_mine": False, "tile": tile, "revealed_count": len(revealed), "multiplier": multiplier, "potential_payout": potential})
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/mines/cashout", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("30 per minute")
    def casino_mines_cashout():
        try:
            from pymongo import ReturnDocument
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            game = rounds_col.find_one_and_update(
                {"game_id": str(payload.get("game_id") or ""), "email": user["email"], "game": "mines", "status": "active", "revealed.0": {"$exists": True}},
                {"$set": {"status": "cashed_out", "finished_at": _naive_now()}},
                return_document=ReturnDocument.BEFORE,
            )
            if not game:
                raise ValueError("Reveal at least one safe tile before cashing out.")
            multiplier = _mines_multiplier(int(game["mine_count"]), len(game.get("revealed", [])))
            payout = int(math.floor(int(game["wager"]) * multiplier))
            balance = _credit(user, payout)
            _log_game(user, "mines", int(game["wager"]), payout, {"multiplier": multiplier, "revealed": game.get("revealed", [])})
            return jsonify({"ok": True, "payout": payout, "multiplier": multiplier, "balance": balance, "mines": game.get("mines", [])})
        except Exception as exc:
            return _json_error(exc)

    def _new_shoe() -> list[dict]:
        shoe = []
        for _ in range(6):
            for rank in CARD_RANKS:
                for suit in CARD_SUITS:
                    shoe.append({"rank": rank, "suit": suit})
        return _secure_shuffle(shoe)

    def _card_points(rank: str) -> int:
        if rank == "A":
            return 11
        if rank in {"K", "Q", "J"}:
            return 10
        return int(rank)

    def _hand_value(cards: list[dict]) -> tuple[int, bool]:
        total = sum(_card_points(card["rank"]) for card in cards)
        aces = sum(1 for card in cards if card["rank"] == "A")
        while total > 21 and aces:
            total -= 10
            aces -= 1
        soft = any(card["rank"] == "A" for card in cards) and total <= 21 and sum(_card_points(card["rank"]) for card in cards) == total
        return total, soft

    def _card_public(card: dict) -> dict:
        return {"rank": card["rank"], "suit": card["suit"], "red": card["suit"] in {"♥", "♦"}}

    def _blackjack_payload(game: dict, *, reveal_dealer: bool = False) -> dict:
        player = list(game.get("player", []))
        dealer = list(game.get("dealer", []))
        player_value, _ = _hand_value(player)
        dealer_value, _ = _hand_value(dealer)
        active = game.get("status") == "active"
        dealer_cards = [_card_public(card) for card in dealer]
        if active and not reveal_dealer and len(dealer_cards) > 1:
            dealer_cards[1] = {"hidden": True}
            visible_value, _ = _hand_value(dealer[:1])
        else:
            visible_value = dealer_value
        return {
            "game_id": game.get("game_id"),
            "status": game.get("status"),
            "result": game.get("result") or "",
            "wager": int(game.get("wager", 0)),
            "player": [_card_public(card) for card in player],
            "dealer": dealer_cards,
            "player_value": player_value,
            "dealer_value": visible_value,
            "payout": int(game.get("payout", 0)),
            "can_double": active and len(player) == 2 and not game.get("doubled"),
        }

    def _finish_blackjack(user: dict, game: dict, result: str, payout: int) -> tuple[dict, int]:
        rounds_col.update_one(
            {"_id": game["_id"]},
            {"$set": {"status": "done", "result": result, "payout": int(payout), "finished_at": _naive_now()}, "$unset": {"action_lock": ""}},
        )
        balance = _credit(user, payout) if payout else int(_wallet_user().get("casino_credits", 0))
        game.update({"status": "done", "result": result, "payout": int(payout)})
        _log_game(user, "blackjack", int(game["wager"]), int(payout), {"result": result, "player": game.get("player", []), "dealer": game.get("dealer", [])})
        return game, balance

    @app.route("/api/casino/blackjack/start", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("25 per minute")
    def casino_blackjack_start():
        try:
            user = _wallet_user()
            wager = _parse_wager((request.get_json(silent=True) or {}).get("wager"))
            balance = _debit(user, "blackjack", wager)
            shoe = _new_shoe()
            player = [shoe.pop(), shoe.pop()]
            dealer = [shoe.pop(), shoe.pop()]
            game = {
                "game_id": uuid.uuid4().hex,
                "game": "blackjack",
                "email": user["email"],
                "wager": wager,
                "player": player,
                "dealer": dealer,
                "shoe": shoe,
                "status": "active",
                "created_at": _naive_now(),
                "expires_at": _naive_now() + timedelta(hours=2),
            }
            try:
                rounds_col.update_many({"email": user["email"], "game": "blackjack", "status": "active"}, {"$set": {"status": "abandoned"}})
                insert = rounds_col.insert_one(game)
                game["_id"] = insert.inserted_id
            except Exception:
                _refund_debit(user, wager)
                raise
            player_value, _ = _hand_value(player)
            dealer_value, _ = _hand_value(dealer)
            if player_value == 21 or dealer_value == 21:
                if player_value == 21 and dealer_value == 21:
                    game, balance = _finish_blackjack(user, game, "Push — both have blackjack", wager)
                elif player_value == 21:
                    game, balance = _finish_blackjack(user, game, "Blackjack pays 3:2", int(math.floor(wager * 2.5)))
                else:
                    game, balance = _finish_blackjack(user, game, "Dealer blackjack", 0)
                return jsonify({"ok": True, "game": _blackjack_payload(game, reveal_dealer=True), "balance": balance})
            return jsonify({"ok": True, "game": _blackjack_payload(game), "balance": balance})
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/blackjack/action", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("80 per minute")
    def casino_blackjack_action():
        try:
            from pymongo import ReturnDocument
            payload = request.get_json(silent=True) or {}
            user = _wallet_user()
            action = str(payload.get("action") or "").lower()
            game = rounds_col.find_one_and_update(
                {"game_id": str(payload.get("game_id") or ""), "email": user["email"], "game": "blackjack", "status": "active", "action_lock": {"$ne": True}},
                {"$set": {"action_lock": True, "updated_at": _naive_now()}},
                return_document=ReturnDocument.AFTER,
            )
            if not game:
                raise ValueError("That blackjack hand is no longer active.")
            try:
                if action not in {"hit", "stand", "double"}:
                    raise ValueError("Choose hit, stand, or double.")
                shoe = list(game["shoe"])
                player = list(game["player"])
                dealer = list(game["dealer"])
                if action == "double":
                    if len(player) != 2 or game.get("doubled"):
                        raise ValueError("Double down is only available on your first two cards.")
                    _debit(user, "blackjack_double", int(game["wager"]), count_game=False)
                    game["wager"] = int(game["wager"]) * 2
                    game["doubled"] = True
                    player.append(shoe.pop())
                    action = "stand" if _hand_value(player)[0] <= 21 else "bust"
                elif action == "hit":
                    player.append(shoe.pop())

                game.update({"shoe": shoe, "player": player, "dealer": dealer})
                player_value, _ = _hand_value(player)
                if player_value > 21 or action == "bust":
                    game, balance = _finish_blackjack(user, game, "Bust — dealer wins", 0)
                    return jsonify({"ok": True, "game": _blackjack_payload(game, reveal_dealer=True), "balance": balance})
                if action == "hit":
                    rounds_col.update_one({"_id": game["_id"]}, {"$set": {"shoe": shoe, "player": player, "dealer": dealer, "wager": game["wager"], "doubled": bool(game.get("doubled"))}, "$unset": {"action_lock": ""}})
                    wallet = _wallet_user()
                    return jsonify({"ok": True, "game": _blackjack_payload(game), "balance": int(wallet.get("casino_credits", 0))})

                while True:
                    dealer_value, _ = _hand_value(dealer)
                    if dealer_value >= 17:
                        break
                    dealer.append(shoe.pop())
                game.update({"shoe": shoe, "dealer": dealer})
                dealer_value, _ = _hand_value(dealer)
                if dealer_value > 21:
                    result, payout = "Dealer bust — you win", int(game["wager"]) * 2
                elif player_value > dealer_value:
                    result, payout = "You win", int(game["wager"]) * 2
                elif player_value == dealer_value:
                    result, payout = "Push", int(game["wager"])
                else:
                    result, payout = "Dealer wins", 0
                game, balance = _finish_blackjack(user, game, result, payout)
                return jsonify({"ok": True, "game": _blackjack_payload(game, reveal_dealer=True), "balance": balance})
            except Exception:
                rounds_col.update_one({"_id": game["_id"], "status": "active"}, {"$unset": {"action_lock": ""}})
                raise
        except Exception as exc:
            return _json_error(exc)

    @app.route("/api/casino/chat")
    @api_guard()
    @limiter.limit("120 per minute")
    def casino_chat_list():
        user = _wallet_user()
        users_col.update_one({"email": user["email"]}, {"$set": {"casino_last_seen": _naive_now()}})
        messages = []
        if chat_col is not None:
            query = {"hidden": {"$ne": True}}
            rows = list(chat_col.find(query, {"_id": 0, "reporters": 0}).sort("created_at", -1).limit(80))
            rows.reverse()
            for row in rows:
                row["created_at"] = _iso(row.get("created_at"))
                row["own"] = row.get("email") == user["email"]
                row["can_moderate"] = bool(user.get("is_owner") or user.get("role") in {"owner", "admin", "support"})
                row.pop("email", None)
                messages.append(row)
        return jsonify({"ok": True, "messages": messages})

    @app.route("/api/casino/chat", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("12 per minute")
    def casino_chat_post():
        user = _wallet_user()
        body = re.sub(r"\s+", " ", str((request.get_json(silent=True) or {}).get("message") or "").strip())
        if len(body) < 2 or len(body) > 300:
            return jsonify({"ok": False, "error": "Chat messages must be 2–300 characters."}), 400
        if re.search(r"(?:https?://|www\.)", body, flags=re.I) and not (user.get("is_owner") or user.get("role") in {"owner", "admin", "support"}):
            return jsonify({"ok": False, "error": "Links are disabled in casino chat."}), 400
        recent = chat_col.find_one({"email": user["email"]}, sort=[("created_at", -1)]) if chat_col is not None else None
        if recent and str(recent.get("body") or "").casefold() == body.casefold():
            return jsonify({"ok": False, "error": "Please do not repeat the same message."}), 429
        row = {
            "message_id": uuid.uuid4().hex,
            "email": user["email"],
            "username": str(user.get("username") or "Player")[:32],
            "avatar_url": _avatar_for(user),
            "body": body,
            "created_at": _naive_now(),
            "hidden": False,
            "reporters": [],
        }
        chat_col.insert_one(row)
        row.pop("_id", None)
        row.pop("email", None)
        row.pop("reporters", None)
        row["created_at"] = _iso(row["created_at"])
        row["own"] = True
        row["can_moderate"] = bool(user.get("is_owner") or user.get("role") in {"owner", "admin", "support"})
        return jsonify({"ok": True, "message": row})

    @app.route("/api/casino/chat/<message_id>/report", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("10 per hour")
    def casino_chat_report(message_id: str):
        user = _wallet_user()
        row = chat_col.find_one({"message_id": message_id})
        if not row:
            return jsonify({"ok": False, "error": "Message not found."}), 404
        if row.get("email") == user["email"]:
            return jsonify({"ok": False, "error": "You cannot report your own message."}), 400
        chat_col.update_one({"message_id": message_id}, {"$addToSet": {"reporters": user["email"]}})
        fresh = chat_col.find_one({"message_id": message_id}, {"reporters": 1}) or {}
        if len(fresh.get("reporters") or []) >= 3:
            chat_col.update_one({"message_id": message_id}, {"$set": {"hidden": True, "hidden_reason": "community_reports"}})
        record_audit("casino_chat_report", message_id, {"reporter": user["email"]})
        return jsonify({"ok": True})

    @app.route("/api/casino/chat/<message_id>/delete", methods=["POST"])
    @api_guard(csrf=True)
    @limiter.limit("30 per minute")
    def casino_chat_delete(message_id: str):
        user = _wallet_user()
        row = chat_col.find_one({"message_id": message_id})
        if not row:
            return jsonify({"ok": False, "error": "Message not found."}), 404
        can_moderate = bool(user.get("is_owner") or user.get("role") in {"owner", "admin", "support"})
        if row.get("email") != user["email"] and not can_moderate:
            abort(403)
        chat_col.update_one({"message_id": message_id}, {"$set": {"hidden": True, "hidden_by": user["email"], "hidden_at": _naive_now()}})
        record_audit("casino_chat_delete", message_id, {"by": user["email"]})
        return jsonify({"ok": True})
