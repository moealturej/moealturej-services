"""Public community with authenticated posting and owner-managed moderation."""
from datetime import datetime, timezone, timedelta
from io import BytesIO
import uuid
import warnings
from flask import abort, jsonify, render_template, request, redirect, send_file, flash, g
from community_rules import DEFAULT_WORDS, CHANNELS, valid_text, blocked

DEFAULTS = {'images': False, 'posting': True, 'words': DEFAULT_WORDS}

def register_community(app, *, db, using_mongo, current_user, owner_required, limiter, verify_csrf, has_admin_access, record_audit):
    messages = db['community_messages'] if db is not None else None
    config = db['community_settings'] if db is not None else None
    reports = db['community_reports'] if db is not None else None
    mutes = db['community_mutes'] if db is not None else None
    if db is not None:
        try:
            messages.create_index([('channel',1),('created',-1),('_id',-1)])
            reports.create_index([('resolved',1),('created',-1)])
            reports.create_index([('message_id',1),('email',1)], unique=True)
        except Exception: app.logger.exception('Community index initialization failed')

    def ready(): return using_mongo() and messages is not None
    def settings():
        if not ready(): return dict(DEFAULTS)
        return {**DEFAULTS, **(config.find_one({'_id':'settings'}) or {})}
    def now(): return datetime.now(timezone.utc).replace(tzinfo=None)
    def json_error(message, status=400): return jsonify(error=message), status
    def actor(): return current_user() or {}
    def public(row, opts):
        return {'id':row['_id'], 'author':row['author'], 'text':row['text'], 'created':row['created'].isoformat()+'Z', 'image': '/forum/image/'+row['_id'] if opts['images'] and row.get('image') else None, 'mine':actor().get('email') == row.get('email'), 'staff':bool(row.get('staff'))}
    def can_write(opts):
        if not ready(): return json_error('Community storage is unavailable.',503)
        if not actor().get('email'): return json_error('Sign in to participate.',401)
        if not verify_csrf(): return json_error('Refresh the page and try again.',403)
        if not opts['posting'] and not has_admin_access(actor()): return json_error('Posting is temporarily paused.',403)
        if mutes.find_one({'_id':actor()['email'],'until':{'$gt':now()}}): return json_error('Your community posting access is temporarily restricted.',403)
        return None

    @app.get('/forum')
    def community_home():
        return render_template('forum.html', active_page='forum', channels=CHANNELS, community_ready=ready(), community_options=settings())

    @app.get('/api/forum/messages')
    @limiter.limit('90 per minute')
    def community_feed():
        if not ready(): return json_error('Community storage is unavailable.',503)
        channel=request.args.get('channel','general')
        if channel not in CHANNELS: return json_error('Unknown channel.',404)
        opts=settings()
        query={'channel':channel,'hidden':{'$ne':True}}
        before=request.args.get('before')
        if before:
            anchor=messages.find_one({'_id':before,'channel':channel},{'created':1})
            if not anchor: return json_error('Invalid page cursor.')
            query['$or']=[{'created':{'$lt':anchor['created']}},{'created':anchor['created'],'_id':{'$lt':before}}]
        page_size = 50 if before else min(300, max(50, request.args.get('limit', 50, type=int)))
        rows=list(messages.find(query,{'image':0}).sort([('created',-1),('_id',-1)]).limit(page_size+1))
        for row in rows: row['image']=row.get('has_image',False)
        return jsonify(messages=[public(row,opts) for row in reversed(rows[:page_size])], more=len(rows)>page_size, images=opts['images'], posting=opts['posting'])

    @app.post('/api/forum/messages')
    @limiter.limit('8 per minute; 80 per hour', key_func=lambda: (current_user() or {}).get('email') or request.remote_addr or 'unknown')
    def community_post():
        if request.content_length and request.content_length>4*1024*1024+65536: return json_error('Upload too large.',413)
        opts=settings(); error=can_write(opts)
        if error: return error
        channel=request.form.get('channel','general')
        if channel not in CHANNELS: return json_error('Unknown channel.',404)
        upload=request.files.get('image'); image=None
        try:
            text=valid_text(request.form.get('text',''),opts['words'])
            author=actor().get('username') or 'Member'
            if blocked(author,opts['words']): return json_error('Update your display name before posting.')
            if upload and upload.filename:
                if not opts['images']: return json_error('Image uploads are disabled.',403)
                from PIL import Image, ImageOps
                raw=upload.stream.read(4*1024*1024+1)
                if len(raw)>4*1024*1024: raise ValueError('Images must be 4 MB or smaller.')
                with warnings.catch_warnings():
                    warnings.simplefilter('error',Image.DecompressionBombWarning)
                    with Image.open(BytesIO(raw)) as source:
                        if source.format not in {'JPEG','PNG','WEBP'} or source.width*source.height>16000000: raise ValueError('Use a JPG, PNG or WebP under 16 megapixels.')
                        clean=ImageOps.exif_transpose(source).convert('RGB'); clean.thumbnail((1600,1600)); out=BytesIO(); clean.save(out,'WEBP',quality=82); image=out.getvalue()
        except ValueError as exc: return json_error(str(exc))
        except Exception: return json_error('Invalid or unsupported image.')
        row={'_id':uuid.uuid4().hex,'channel':channel,'author':str(author)[:80],'email':actor()['email'],'staff':has_admin_access(actor()),'text':text,'created':now(),'hidden':False,'has_image':bool(image)}
        if image: row['image']=image
        messages.insert_one(row)
        return jsonify(message=public(row,opts)),201

    @app.get('/forum/image/<message_id>')
    @limiter.limit('120 per minute')
    def community_image(message_id):
        if not ready() or not settings()['images']: abort(404)
        row=messages.find_one({'_id':message_id,'hidden':{'$ne':True},'has_image':True},{'image':1})
        if not row or not row.get('image'): abort(404)
        response=send_file(BytesIO(row['image']),mimetype='image/webp',max_age=0)
        response.headers['Cache-Control']='no-store'; return response

    @app.post('/api/forum/report/<message_id>')
    @limiter.limit('10 per hour')
    def community_report(message_id):
        if not ready(): return json_error('Storage unavailable.',503)
        if not actor().get('email') or not verify_csrf(): return json_error('Sign in and refresh.',403)
        row=messages.find_one({'_id':message_id,'hidden':{'$ne':True}},{'_id':1})
        if not row: return json_error('Message not found.',404)
        reason=request.form.get('reason','Other')
        if reason not in {'Harassment','Spam','Unsafe content','Other'}: return json_error('Invalid report reason.')
        reports.update_one({'message_id':message_id,'email':actor()['email']},{'$setOnInsert':{'created':now(),'reason':reason,'resolved':False}},upsert=True)
        return jsonify(ok=True)

    @app.post('/api/forum/delete/<message_id>')
    @limiter.limit('30 per minute')
    def community_delete(message_id):
        if not ready(): return json_error('Storage unavailable.',503)
        if not actor().get('email') or not verify_csrf(): return json_error('Sign in and refresh.',403)
        query={'_id':message_id,'hidden':{'$ne':True}}
        if not has_admin_access(actor()): query['email']=actor()['email']
        result=messages.update_one(query,{'$set':{'hidden':True},'$unset':{'image':''}})
        if not result.matched_count: return json_error('Message not found or access denied.',404)
        reports.update_many({'message_id':message_id},{'$set':{'resolved':True}})
        return jsonify(ok=True)

    @app.route('/admin/forum',methods=['GET','POST'])
    @owner_required
    @limiter.limit('60 per minute')
    def community_admin():
        if request.method=='POST':
            if not ready(): abort(503)
            if not verify_csrf(): abort(403)
            action=request.form.get('action')
            if action=='settings':
                words=list(dict.fromkeys(w.strip() for w in request.form.get('words','').splitlines() if w.strip()))
                if len(words)>250 or any(len(w)>80 for w in words): abort(400)
                config.update_one({'_id':'settings'},{'$set':{'images':request.form.get('images')=='on','posting':request.form.get('posting')=='on','words':words}},upsert=True)
            elif action in {'hide','mute','unmute'}:
                row=messages.find_one({'_id':request.form.get('id')},{'email':1})
                if not row: abort(404)
                if action=='hide':
                    messages.update_one({'_id':row['_id']},{'$set':{'hidden':True},'$unset':{'image':''}})
                    reports.update_many({'message_id':row['_id']},{'$set':{'resolved':True}})
                elif action=='mute':
                    if row['email']==actor().get('email'): abort(400)
                    mutes.update_one({'_id':row['email']},{'$set':{'until':now()+timedelta(hours=24)}},upsert=True)
                else: mutes.delete_one({'_id':row['email']})
            elif action=='dismiss':
                reports.update_many({'message_id':request.form.get('id')},{'$set':{'resolved':True}})
            else: abort(400)
            record_audit('community_'+action, actor().get('email',''), {'message_id':request.form.get('id','')})
            flash('Community settings updated.','success'); return redirect('/admin/forum')
        recent=[]; queue=[]
        if ready():
            recent=list(messages.find({'hidden':{'$ne':True}},{'image':0}).sort('created',-1).limit(100))
            for report in reports.find({'resolved':False}).sort('created',-1).limit(100):
                row=messages.find_one({'_id':report['message_id']},{'image':0})
                if row: queue.append({'reason':report['reason'],'message':row})
        return render_template('forum_admin.html',active_page='admin', options=settings(), community_ready=ready(), recent=recent, queue=queue)
