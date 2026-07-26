from __future__ import annotations

import json, re, uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for


def register_enhancements(app, *, db, using_mongo, data_dir: Path, current_user, login_required, owner_required,
                          verify_csrf, load_products, find_store_product, load_orders_for_user,
                          save_site_settings, load_site_settings, record_audit, create_notification,
                          send_html_email, money_to_cents, parse_order_datetime, load_support_tickets):
    REVIEWS = data_dir / 'reviews.json'
    INCIDENTS = data_dir / 'incidents.json'
    EVENTS = data_dir / 'analytics_events.json'

    def now(): return datetime.now(timezone.utc)
    def read_file(path):
        try:
            return json.loads(path.read_text('utf-8')) if path.exists() else []
        except Exception:
            return []
    def write_file(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2, default=str), 'utf-8')
    def collection(name):
        return db[name] if using_mongo() and db is not None else None
    def rows(name, path, query=None, sort='created_at', limit=1000):
        col=collection(name)
        if col is not None:
            return list(col.find(query or {}, {'_id':0}).sort(sort,-1).limit(limit))
        data=read_file(path)
        if query:
            data=[x for x in data if all(x.get(k)==v for k,v in query.items())]
        return sorted(data,key=lambda x:str(x.get(sort,'')),reverse=True)[:limit]
    def insert(name,path,item):
        col=collection(name)
        if col is not None: col.insert_one(dict(item))
        else:
            data=read_file(path); data.append(item); write_file(path,data)
    def replace(name,path,key,value,item):
        col=collection(name)
        if col is not None: col.replace_one({key:value},dict(item),upsert=True)
        else:
            data=read_file(path); found=False
            for i,x in enumerate(data):
                if x.get(key)==value: data[i]=item; found=True; break
            if not found:data.append(item)
            write_file(path,data)

    def user_owns(slug,email):
        if not email:return False
        for order in load_orders_for_user(email):
            if str(order.get('status','')).lower() not in {'paid','completed','delivered'}:continue
            for item in (order.get('cart') or {}).get('items') or []:
                if str(item.get('slug') or '')==slug:return True
        return False

    def product_reviews(slug, approved_only=True):
        q={'product_slug':slug}
        data=rows('reviews',REVIEWS,q,'created_at',500)
        return [r for r in data if (r.get('approved',False) or not approved_only)]

    def active_incidents():
        return [i for i in rows('incidents',INCIDENTS,None,'created_at',200) if i.get('status')!='resolved']

    def related_products(product, limit=4):
        catalog=[p for p in load_products() if (p.get('store') or {}).get('enabled',True) and p.get('slug')!=product.get('slug')]
        same=[p for p in catalog if p.get('category')==product.get('category')]
        return (same+[p for p in catalog if p not in same])[:limit]

    def track(event, slug='', value=0, metadata=None):
        item={'id':uuid.uuid4().hex,'event':event[:40],'product_slug':slug[:120], 'value':int(value or 0),
              'user_email':str((current_user() or {}).get('email') or '').lower(), 'session_id':session.setdefault('analytics_sid',uuid.uuid4().hex),
              'metadata':metadata or {},'created_at':now().isoformat()}
        insert('analytics_events',EVENTS,item)

    app.jinja_env.globals.update(product_reviews=product_reviews, active_incidents=active_incidents)
    app.extensions['moe_enhancements']={'reviews':product_reviews,'incidents':active_incidents,'related':related_products,'owns':user_owns,'track':track}

    @app.post('/api/analytics/event')
    def analytics_event():
        data=request.get_json(silent=True) or {}
        allowed={'product_view','add_to_cart','checkout_start','search','wishlist','download','support_open'}
        event=str(data.get('event') or '')
        if event not in allowed:return jsonify({'ok':False}),400
        track(event,str(data.get('product_slug') or ''),data.get('value') or 0,{'query':str(data.get('query') or '')[:120]})
        return jsonify({'ok':True})

    @app.get('/api/store/search-suggestions')
    def search_suggestions():
        q=str(request.args.get('q') or '').strip().lower()
        if len(q)<2:return jsonify({'items':[]})
        words=set(q.split())
        scored=[]
        for p in load_products():
            hay=' '.join([str(p.get('name','')),str(p.get('slug','')),str(p.get('category','')),' '.join(map(str,p.get('features') or []))]).lower()
            score=sum(3 for w in words if w in hay)+(4 if q in hay else 0)
            if score:scored.append((score,p))
        scored.sort(key=lambda x:(-x[0],str(x[1].get('name',''))))
        return jsonify({'items':[{'name':p.get('name'),'slug':p.get('slug'),'image':p.get('image'),'category':p.get('category')} for _,p in scored[:6]]})

    @app.post('/product/<slug>/review')
    @login_required
    def create_review(slug):
        if not verify_csrf():abort(400)
        user=current_user() or {}; email=str(user.get('email') or '').lower()
        product=find_store_product(slug)
        if not product:abort(404)
        if not user_owns(slug,email):
            flash('Only verified purchasers can review this product.','danger'); return redirect(url_for('product_detail',slug=slug)+'#reviews')
        try:rating=max(1,min(5,int(request.form.get('rating') or 0)))
        except Exception:rating=0
        body=str(request.form.get('body') or '').strip()
        if rating<1 or len(body)<10:
            flash('Choose a rating and write at least 10 characters.','danger'); return redirect(url_for('product_detail',slug=slug)+'#reviews')
        existing=next((r for r in product_reviews(slug,False) if r.get('user_email')==email),None)
        item={'id':existing.get('id') if existing else uuid.uuid4().hex,'product_slug':slug,'product_name':product.get('name'),
              'user_email':email,'display_name':str(user.get('username') or email.split('@')[0])[:40], 'rating':rating,'body':body[:1200],
              'verified':True,'approved': bool(existing and existing.get('approved')),'helpful':int((existing or {}).get('helpful') or 0),
              'created_at':(existing or {}).get('created_at') or now().isoformat(),'updated_at':now().isoformat()}
        replace('reviews',REVIEWS,'id',item['id'],item)
        record_audit('review_submit',slug,{'rating':rating,'email':email})
        flash('Review submitted for moderation.','success'); return redirect(url_for('product_detail',slug=slug)+'#reviews')

    @app.post('/reviews/<review_id>/helpful')
    def review_helpful(review_id):
        allrows=rows('reviews',REVIEWS,None,'created_at',2000)
        item=next((x for x in allrows if x.get('id')==review_id and x.get('approved')),None)
        if not item:abort(404)
        key='helpful_reviews'; used=set(session.get(key) or [])
        if review_id not in used:
            item['helpful']=int(item.get('helpful') or 0)+1; replace('reviews',REVIEWS,'id',review_id,item); used.add(review_id); session[key]=list(used)[-100:]
        return redirect(request.referrer or url_for('product_detail',slug=item.get('product_slug')))

    @app.post('/admin/reviews/<review_id>/<action>')
    @owner_required
    def admin_review_action(review_id,action):
        if not verify_csrf():abort(400)
        allrows=rows('reviews',REVIEWS,None,'created_at',2000); item=next((x for x in allrows if x.get('id')==review_id),None)
        if not item:abort(404)
        if action=='approve':item['approved']=True
        elif action=='hide':item['approved']=False
        elif action=='delete':
            col=collection('reviews')
            if col is not None:col.delete_one({'id':review_id})
            else:write_file(REVIEWS,[x for x in allrows if x.get('id')!=review_id])
            flash('Review deleted.','success'); return redirect(url_for('admin_dashboard')+'#reviews')
        else:abort(400)
        replace('reviews',REVIEWS,'id',review_id,item); flash('Review updated.','success'); return redirect(url_for('admin_dashboard')+'#reviews')

    @app.post('/admin/incidents')
    @owner_required
    def create_incident():
        if not verify_csrf():abort(400)
        title=str(request.form.get('title') or '').strip(); message=str(request.form.get('message') or '').strip()
        if not title or not message:flash('Incident title and message are required.','danger');return redirect(url_for('admin_dashboard')+'#incidents')
        products=[x for x in request.form.getlist('products') if x]
        item={'id':uuid.uuid4().hex,'title':title[:120],'message':message[:1600],'severity':str(request.form.get('severity') or 'minor')[:20],
              'status':'investigating','products':products,'updates':[],'created_at':now().isoformat(),'updated_at':now().isoformat()}
        insert('incidents',INCIDENTS,item)
        for order in load_orders_for_user(None):
            email=str(order.get('user_email') or '').lower()
            owned={str(x.get('slug') or '') for x in (order.get('cart') or {}).get('items') or []}
            if email and (not products or owned.intersection(products)):create_notification(email,'Service incident',title,'warning','/status')
        record_audit('incident_create',item['id'],{'title':title,'products':products}); flash('Incident published.','success')
        return redirect(url_for('admin_dashboard')+'#incidents')

    @app.post('/admin/incidents/<incident_id>/update')
    @owner_required
    def update_incident(incident_id):
        if not verify_csrf():abort(400)
        allrows=rows('incidents',INCIDENTS,None,'created_at',1000); item=next((x for x in allrows if x.get('id')==incident_id),None)
        if not item:abort(404)
        message=str(request.form.get('message') or '').strip(); status=str(request.form.get('status') or 'monitoring')
        if message:item.setdefault('updates',[]).append({'message':message[:1200],'status':status,'created_at':now().isoformat()})
        item['status']=status; item['updated_at']=now().isoformat()
        if status=='resolved':item['resolved_at']=now().isoformat()
        replace('incidents',INCIDENTS,'id',incident_id,item); record_audit('incident_update',incident_id,{'status':status})
        flash('Incident updated.','success');return redirect(url_for('admin_dashboard')+'#incidents')

    @app.get('/admin/analytics-data')
    @owner_required
    def analytics_data():
        return jsonify(build_analytics())

    def build_analytics():
        orders=load_orders_for_user(None); events=rows('analytics_events',EVENTS,None,'created_at',10000); tickets=load_support_tickets()
        cutoff=now()-timedelta(days=30); paid=[]; revenue=0
        for o in orders:
            dt=parse_order_datetime(o.get('created_at'))
            recent = True
            if dt is not None:
                aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                recent = aware >= cutoff
            if str(o.get('status','')).lower() in {'paid','completed','delivered'} and recent:
                paid.append(o); revenue+=int((o.get('cart') or {}).get('total_cents') or money_to_cents((o.get('cart') or {}).get('total') or 0))
        counts=Counter(e.get('event') for e in events); product_sales=Counter()
        for o in paid:
            for i in (o.get('cart') or {}).get('items') or []:product_sales[i.get('productName') or i.get('slug') or 'Unknown']+=int(i.get('quantity') or 1)
        checkout=counts.get('checkout_start',0); purchases=len(paid)
        return {'revenue_cents':revenue,'paid_orders':purchases,'conversion':round((purchases/checkout*100),1) if checkout else 0,
                'support_open':len([t for t in tickets if t.get('status')!='closed']),'events':dict(counts),'top_products':product_sales.most_common(8),
                'abandoned':max(0,checkout-purchases),'generated_at':now().isoformat()}

    @app.get('/api/account/recommendations')
    @login_required
    def recommendations():
        email=(current_user() or {}).get('email'); owned=set()
        for o in load_orders_for_user(email):
            for i in (o.get('cart') or {}).get('items') or []:owned.add(i.get('slug'))
        candidates=[p for p in load_products() if p.get('slug') not in owned and (p.get('store') or {}).get('enabled',True)]
        return jsonify({'items':[{'name':p.get('name'),'slug':p.get('slug'),'image':p.get('image'),'category':p.get('category')} for p in candidates[:6]]})

    # exposed helper for existing routes/templates
    app.extensions['moe_enhancements']['analytics']=build_analytics
    app.extensions['moe_enhancements']['all_reviews']=lambda: rows('reviews',REVIEWS,None,'created_at',1000)
    app.extensions['moe_enhancements']['all_incidents']=lambda: rows('incidents',INCIDENTS,None,'created_at',500)
