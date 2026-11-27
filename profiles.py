"""Independent account profile endpoints."""
from __future__ import annotations
import re, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from flask import flash, g, jsonify, redirect, request, session, url_for
from werkzeug.utils import secure_filename
PROFILE_IMAGE_MAX_BYTES = 4 * 1024 * 1024
ALLOWED_PROFILE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
def _utcnow(): return datetime.now(timezone.utc)
def _naive_now(): return _utcnow().replace(tzinfo=None)
def register_profiles(
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
    optimize_uploaded_image_file=None,
):
    def _fresh_user(*, force: bool = False) -> dict | None:
        if not force and hasattr(g, "profile_user"):
            return g.profile_user
        user = current_user() or {}
        email = str(user.get("email") or "").strip().lower()
        if not email or not using_mongo() or users_col is None:
            return None
        fresh = users_col.find_one({"email": email}, {"_id": 0})
        g.profile_user = fresh
        return fresh

    def _avatar_for(user: dict | None) -> str:
        user = user or {}
        return str(
            user.get("avatar_url")
            or user.get("google_avatar")
            or user.get("discord_avatar")
            or "/static/default.jpg"
        )[:500]

    def _detect_profile_image(upload) -> tuple[str, str]:
        """Validate the real file signature instead of trusting the browser MIME type."""
        stream = upload.stream
        position = stream.tell()
        header = stream.read(32)
        stream.seek(position)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png", "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "jpg", "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "gif", "image/gif"
        if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "webp", "image/webp"
        raise ValueError("Choose a real PNG, JPG, GIF, or WEBP image.")

    def _save_profile_avatar(user: dict, upload) -> str:
        if not upload or not upload.filename:
            raise ValueError("Choose a profile image first.")
        if request.content_length and request.content_length > PROFILE_IMAGE_MAX_BYTES + (768 * 1024):
            raise ValueError("Profile images must be 4 MB or smaller.")

        detected_ext, mime_type = _detect_profile_image(upload)
        original_name = secure_filename(str(upload.filename or "")) or f"profile.{detected_ext}"
        supplied_ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        if supplied_ext and supplied_ext not in ALLOWED_PROFILE_EXTENSIONS:
            raise ValueError("Profile images must be PNG, JPG, GIF, or WEBP.")

        uploads_dir.mkdir(parents=True, exist_ok=True)
        filename = f"profile-{uuid.uuid4().hex}.{detected_ext}"
        local_path = uploads_dir / filename
        upload.stream.seek(0)
        upload.save(local_path)
        size_bytes = local_path.stat().st_size if local_path.exists() else 0
        if size_bytes <= 0:
            local_path.unlink(missing_ok=True)
            raise ValueError("The selected image was empty. Choose another file.")
        if size_bytes > PROFILE_IMAGE_MAX_BYTES:
            local_path.unlink(missing_ok=True)
            raise ValueError("Profile images must be 4 MB or smaller.")

        # Profile avatars are shown frequently. Convert static PNG/JPEG uploads to
        # compact WebP before storing them so repeated avatar renders stay cheap.
        if callable(optimize_uploaded_image_file):
            local_path = optimize_uploaded_image_file(local_path, max_side=512, quality=82)
            filename = local_path.name
            if local_path.suffix.lower() == ".webp":
                mime_type = "image/webp"
            size_bytes = local_path.stat().st_size if local_path.exists() else size_bytes

        gridfs_id = save_upload_to_mongo_storage(
            local_path, filename, original_name, "profile_image", mime_type
        )
        avatar_url = url_for("media_file", filename=filename)
        save_media_record({
            "filename": filename,
            "original_name": original_name,
            "kind": "profile_image",
            "url": avatar_url,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "storage": "mongodb_gridfs" if gridfs_id else "local_disk",
            "gridfs_id": gridfs_id,
            "created_at": _utcnow().isoformat(),
            "created_by": user["email"],
        })
        return avatar_url

    def _valid_profile_name(username: str) -> bool:
        if not 3 <= len(username) <= 24:
            return False
        if any(ord(char) < 32 for char in username):
            return False
        return not any(char in username for char in "<>/\\@")

    @app.route("/account/profile/avatar", methods=["POST"])
    @login_required
    @limiter.limit("20 per hour")
    def account_profile_avatar_update():
        user = _fresh_user()
        wants_json = request.headers.get("Accept", "").lower().find("application/json") >= 0
        if not user:
            if wants_json:
                return jsonify({"ok": False, "error": "Profile updates require MongoDB."}), 503
            flash("Profile updates require MongoDB.", "danger")
            return redirect(url_for("account") + "#profile")
        try:
            avatar_url = _save_profile_avatar(user, request.files.get("avatar"))
            users_col.update_one(
                {"email": user["email"]},
                {"$set": {
                    "avatar_url": avatar_url,
                    "profile_customized": True,
                    "updated_at": _naive_now(),
                }},
            )
            fresh = _fresh_user(force=True)
            session["user"] = sanitize_user_for_session(fresh)
            record_audit("account_avatar_update", str(user.get("email") or ""), {"avatar_url": avatar_url})
            if wants_json:
                return jsonify({"ok": True, "avatar_url": avatar_url, "message": "Profile image updated."})
            flash("Profile image updated.", "success")
            return redirect(url_for("account") + "#profile")
        except ValueError as exc:
            if wants_json:
                return jsonify({"ok": False, "error": str(exc)}), 400
            flash(str(exc), "danger")
            return redirect(url_for("account") + "#profile")
        except Exception:
            app.logger.exception("Profile image upload failed for %s", user.get("email"))
            if wants_json:
                return jsonify({"ok": False, "error": "The image could not be saved. Try another file."}), 500
            flash("The image could not be saved. Try another file.", "danger")
            return redirect(url_for("account") + "#profile")

    @app.route("/account/profile", methods=["POST"])
    @login_required
    @limiter.limit("20 per hour")
    def account_profile_update():
        user = _fresh_user()
        if not user:
            flash("Profile updates require MongoDB.", "danger")
            return redirect(url_for("account") + "#profile")
        username = re.sub(r"\s+", " ", str(request.form.get("username") or "").strip())
        bio = str(request.form.get("bio") or "").strip()
        if not _valid_profile_name(username):
            flash("Display names must be 3–24 characters and cannot contain <, >, /, \\, or @.", "danger")
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

        upload = request.files.get("avatar")
        if upload and upload.filename:
            try:
                update["avatar_url"] = _save_profile_avatar(user, upload)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("account") + "#profile")
            except Exception:
                app.logger.exception("Profile image upload failed for %s", user.get("email"))
                flash("The image could not be saved. Try another file.", "danger")
                return redirect(url_for("account") + "#profile")

        users_col.update_one({"email": user["email"]}, {"$set": update})
        fresh = _fresh_user(force=True)
        session["user"] = sanitize_user_for_session(fresh)
        record_audit("account_profile_update", str(user.get("email") or ""), {"username": username})
        flash("Profile updated.", "success")
        return redirect(url_for("account") + "#profile")

