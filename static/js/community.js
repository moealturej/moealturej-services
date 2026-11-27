(() => {
  'use strict';
  const root = document.getElementById('community'); if (!root) return;
  const $ = id => document.getElementById(id);
  const list = $('messageList'), feed = $('roomFeed'), form = $('messageForm'), older = $('olderMessages');
  const signed = root.dataset.signedIn === 'yes', admin = root.dataset.admin === 'yes';
  const descriptions = {general:'A place for everyday conversation.',questions:'Ask the community for help.',feedback:'Share ideas and constructive feedback.','off-topic':'Everything else, within the community rules.'};
  let channel='general', generation=0, history=false, busy=false, rows=[], images=false, posting=true;
  const status = (msg) => { if ($('composeStatus')) $('composeStatus').textContent=msg; else $('feedStatus').textContent=msg; };
  async function api(url, options={}) {
    const response=await fetch(url,{cache:'no-store',credentials:'same-origin',...options});
    let data; try {data=await response.json();} catch {throw new Error('Request failed. Refresh or try again.');}
    if (!response.ok) throw new Error(data.error||'Request failed.'); return data;
  }
  function element(tag, cls, text) {const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
  async function action(kind,id,reason) {
    const body=new FormData(); if(reason)body.set('reason',reason);
    await api(`/api/forum/${kind}/${encodeURIComponent(id)}`,{method:'POST',headers:{'X-CSRF-Token':root.dataset.csrf},body});
  }
  function render() {
    const fragment=document.createDocumentFragment();
    for(const row of rows) {
      const card=element('article','community-message');const initial=element('div','message-avatar',(row.author||'?').slice(0,1).toUpperCase());card.append(initial);
      const body=element('div','message-body'), head=element('div','message-meta');head.append(element('strong','',row.author));if(row.staff)head.append(element('span','staff-label','Staff')); const time=new Date(row.created);head.append(element('time','',Number.isNaN(time.valueOf())?'':time.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})));body.append(head,element('p','message-text',row.text));
      if(images && row.image) {const img=element('img','message-image');img.src=row.image;img.alt='Community attachment';img.loading='lazy';body.append(img);}
      if(signed){const controls=element('div','message-controls'); if(!row.mine){const select=element('select','report-reason');select.setAttribute('aria-label','Report reason');for(const val of ['Harassment','Spam','Unsafe content','Other']){const opt=element('option','',val);opt.value=val;select.append(opt);}const report=element('button','','Report');report.type='button';report.onclick=async()=>{report.disabled=true;try{await action('report',row.id,select.value);status('Report submitted.');report.textContent='Reported';}catch(e){status(e.message);report.disabled=false;}};controls.append(select,report);}if(row.mine||admin){const del=element('button','','Remove');del.type='button';del.onclick=async()=>{if(!confirm('Remove this message?'))return;try{await action('delete',row.id);rows=rows.filter(r=>r.id!==row.id);render();status('Message removed.');}catch(e){status(e.message);}};controls.append(del);}body.append(controls);}
      card.append(body);fragment.append(card);
    }
    list.replaceChildren(fragment); $('roomEmpty').hidden=rows.length>0; $('roomEmpty').textContent='No messages yet. Start the conversation.';
  }
  function updateOptions(data){images=!!data.images;posting=!!data.posting;if(form){$('imageControl').hidden=!images;if(!images){$('imageInput').value='';$('imageName').textContent='';}$('sendMessage').disabled=!posting&&!admin;$('messageText').disabled=!posting&&!admin;}}
  async function refresh(loadOlder=false){
    if(busy||root.dataset.ready!=='yes')return;busy=true;const token=generation;const nearBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<100;const height=feed.scrollHeight;
    try{const url=new URL('/api/forum/messages',location.origin);url.searchParams.set('channel',channel);if(loadOlder&&rows.length)url.searchParams.set('before',rows[0].id);else if(history)url.searchParams.set('limit',Math.min(300,Math.max(50,rows.length)));const data=await api(url);if(token!==generation)return;updateOptions(data);if(loadOlder){rows=[...data.messages,...rows].slice(-300);history=true;older.hidden=!data.more||rows.length>=300;}else{rows=data.messages;older.hidden=!data.more||rows.length>=300;}
    render();if(loadOlder)feed.scrollTop=feed.scrollHeight-height;else if(nearBottom)feed.scrollTop=feed.scrollHeight;$('feedStatus').textContent=posting?'Live · updates every 10s':'Posting paused';}
    catch(e){if(token===generation)$('feedStatus').textContent=e.message;}finally{busy=false;if(token!==generation)refresh();}
  }
  root.querySelectorAll('[data-channel]').forEach(btn=>btn.addEventListener('click',()=>{if(channel===btn.dataset.channel)return;channel=btn.dataset.channel;generation++;history=false;rows=[];list.replaceChildren();older.hidden=true;$('roomEmpty').hidden=false;$('roomEmpty').textContent='Loading…';root.querySelectorAll('[data-channel]').forEach(b=>{b.classList.toggle('active',b===btn);b.setAttribute('aria-pressed',String(b===btn));});$('roomTitle').textContent='# '+btn.textContent.trim().replace(/^#\s*/,'');$('roomDescription').textContent=descriptions[channel];refresh();}));
  older.onclick=()=>refresh(true);
  if(form){const text=$('messageText');text.addEventListener('input',()=>{$('messageCount').textContent=`${text.value.length} / 1000`;});$('imageInput').addEventListener('change',()=>{$('imageName').textContent=$('imageInput').files[0]?.name||'';});form.addEventListener('submit',async e=>{e.preventDefault();const button=$('sendMessage');if(button.disabled)return;button.disabled=true;const token=generation;const body=new FormData(form);body.set('channel',channel);try{const data=await api('/api/forum/messages',{method:'POST',headers:{'X-CSRF-Token':root.dataset.csrf},body});form.reset();$('messageCount').textContent='0 / 1000';$('imageName').textContent='';status('');if(token===generation){if(!rows.some(r=>r.id===data.message.id))rows.push(data.message);render();feed.scrollTop=feed.scrollHeight;}refresh();}catch(e){status(e.message);}finally{button.disabled=!posting&&!admin;}});}
  if(root.dataset.ready!=='yes'){$('feedStatus').textContent='Unavailable';return;}refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
})();
