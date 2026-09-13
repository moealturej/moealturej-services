(() => {
  const ready = fn => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', () => setTimeout(fn, 0)) : setTimeout(fn, 0);
  ready(() => {
    const page = document.querySelector('.admin-page');
    const layout = document.querySelector('.admin-layout');
    if (!page || !layout) return;
    document.body.classList.add('admin-modern-body');

    // Bring feature modules rendered by the growth include into the same workspace.
    ['analytics','incidents','reviews'].forEach(id => {
      const section = document.getElementById(id);
      if (section && section.parentElement !== layout) {
        section.classList.add('admin-card');
        layout.appendChild(section);
      }
    });

    const workspace = document.querySelector('.admin-workspace');
    const rail = document.querySelector('.admin-workspace-rail');
    const main = document.querySelector('.admin-workspace-main');
    const tabs = document.querySelector('.admin-tabs');
    if (!workspace || !rail || !main || !tabs) return;
    workspace.classList.add('modern-workspace');

    // Replace the inherited rail content with the cleaner navigation from the newer admin.
    const labels = {
      overview:['Overview','fa-gauge-high','General'],
      products:['Products','fa-box','Commerce'],
      orders:['Orders & keys','fa-receipt','Commerce'],
      discounts:['Discounts','fa-tags','Commerce'],
      'manual-grant':['Manual grant','fa-gift','Commerce'],
      'loader-release':['Windows Loader','fa-download','Delivery'],
      uploads:['Files & media','fa-cloud-arrow-up','Delivery'],
      'order-webhook':['Order webhook','fa-paper-plane','Delivery'],
      accounts:['Customers','fa-users','Customers'],
      support:['Support','fa-headset','Customers'],
      emails:['Email sender','fa-envelope','Customers'],
      guides:['Guides','fa-book-open','Content'],
      applications:['Applications','fa-clipboard-list','Content'],
      reviews:['Reviews','fa-star','Content'],
      incidents:['Incidents','fa-triangle-exclamation','Content'],
      owner:['Site settings','fa-sliders','Settings'],
      analytics:['Analytics','fa-chart-line','Settings'],
      health:['System health','fa-heart-pulse','Settings'],
      audit:['Audit log','fa-clock-rotate-left','Settings'],
      security:['Security','fa-shield-halved','Settings']
    };
    const order = Object.keys(labels);
    const existing = new Map([...tabs.querySelectorAll('.admin-tab')].map(a => [(a.getAttribute('href')||'').slice(1), a]));
    tabs.replaceChildren();
    let lastGroup = '';
    order.forEach(id => {
      const section = document.getElementById(id);
      if (!section) return;
      const [label,icon,group] = labels[id];
      if (group !== lastGroup) {
        const g = document.createElement('div'); g.className='admin-tab-group'; g.textContent=group; tabs.appendChild(g); lastGroup=group;
      }
      const a = existing.get(id) || document.createElement('a');
      a.className='admin-tab'; a.href='#'+id; a.innerHTML=`<i class="fas ${icon}"></i><span>${label}</span>`; tabs.appendChild(a);
    });
    const railHead = rail.querySelector('.admin-rail-head');
    if (railHead) railHead.innerHTML = `<div class="admin-rail-title"><span><i class="fas fa-crown"></i></span><span><strong>moealturej</strong><small>OWNER ADMIN</small></span></div><button class="admin-rail-close" type="button" aria-label="Close navigation"><i class="fas fa-xmark"></i></button>`;
    rail.querySelector('.admin-tools')?.remove();
    const bottom = document.createElement('div'); bottom.className='admin-sidebar-bottom';
    bottom.innerHTML = `<a href="/"><span>Storefront</span><i class="fas fa-arrow-up-right-from-square"></i></a><a href="/account"><span>My account</span><i class="fas fa-user"></i></a><a href="/logout"><span>Sign out</span><i class="fas fa-arrow-right-from-bracket"></i></a>`;
    rail.appendChild(bottom);

    // Clean top bar.
    main.querySelector('.admin-mobile-bar')?.remove();
    main.querySelector('.admin-quick-actions')?.remove();
    const top = document.createElement('header'); top.className='modern-admin-topbar';
    top.innerHTML = `<div class="modern-admin-title"><span>ADMIN</span><h1 id="modernAdminHeading">Overview</h1></div><div class="modern-admin-actions"><button class="modern-mobile-menu" type="button"><i class="fas fa-bars"></i><span>Menu</span></button><button class="modern-command-btn" type="button" id="modernCommandOpen"><i class="fas fa-magnifying-glass"></i><span>Jump to…</span><kbd>Ctrl K</kbd></button><a class="modern-top-link" href="/" target="_blank" rel="noopener">Storefront <i class="fas fa-arrow-up-right-from-square"></i></a></div>`;
    main.prepend(top);
    const heading = top.querySelector('#modernAdminHeading');

    const sections = [...layout.querySelectorAll(':scope > .admin-section[id]')];
    sections.forEach(s => { s.classList.remove('is-collapsed'); s.removeAttribute('data-search-hidden'); });

    const overlay = document.createElement('div'); overlay.className='admin-modern-overlay'; document.body.appendChild(overlay);
    const closeRail=()=>{rail.classList.remove('is-open');overlay.classList.remove('show')};
    top.querySelector('.modern-mobile-menu')?.addEventListener('click',()=>{rail.classList.add('is-open');overlay.classList.add('show')});
    rail.querySelector('.admin-rail-close')?.addEventListener('click',closeRail); overlay.addEventListener('click',closeRail);

    function activate(id, push=true) {
      if (!document.getElementById(id) || !labels[id]) id='overview';
      sections.forEach(s => { const on=s.id===id; s.classList.toggle('modern-active',on); s.classList.remove('is-collapsed'); });
      tabs.querySelectorAll('.admin-tab').forEach(a=>a.classList.toggle('is-active',(a.getAttribute('href')||'')==='#'+id));
      if (heading) heading.textContent=labels[id]?.[0] || 'Admin';
      if (push) history.replaceState(null,'','#'+id);
      closeRail();
      window.scrollTo({top:0,behavior:'instant'});
    }
    tabs.addEventListener('click',e=>{const a=e.target.closest('.admin-tab');if(!a)return;e.preventDefault();activate((a.getAttribute('href')||'#overview').slice(1));});
    document.addEventListener('click', e => {
      const a=e.target.closest('a[href^="#"]'); if(!a || !page.contains(a)) return;
      const id=(a.getAttribute('href')||'').slice(1); if(labels[id]){e.preventDefault();activate(id);}
    }, true);

    // Recent order shortcuts switch to Orders and then highlight the matching card when possible.
    document.querySelectorAll('[data-order-jump]').forEach(a=>a.addEventListener('click',e=>{
      e.preventDefault(); const id=a.dataset.orderJump; activate('orders');
      setTimeout(()=>{const hit=[...document.querySelectorAll('#orders .user-row')].find(x=>x.textContent.includes(id));if(hit){hit.style.outline='1px solid rgba(147,104,255,.55)';hit.scrollIntoView({behavior:'smooth',block:'center'});setTimeout(()=>hit.style.outline='',1800)}},60);
    }));

    // Command palette: page navigation + current admin data search.
    const command = document.createElement('div'); command.className='modern-command'; command.setAttribute('aria-hidden','true');
    command.innerHTML = `<div class="modern-command-panel" role="dialog" aria-modal="true" aria-label="Admin quick navigation"><div class="modern-command-search"><i class="fas fa-magnifying-glass"></i><input autocomplete="off" placeholder="Search admin…" id="modernCommandSearch"><kbd>ESC</kbd></div><div class="modern-command-results" id="modernCommandResults"></div></div>`;
    document.body.appendChild(command);
    const input=command.querySelector('#modernCommandSearch'),results=command.querySelector('#modernCommandResults');
    const commands=order.filter(id=>document.getElementById(id)).map(id=>({id,label:labels[id][0],icon:labels[id][1],group:labels[id][2]}));
    function renderCommands(q=''){
      q=q.trim().toLowerCase(); const filtered=commands.filter(c=>!q||`${c.label} ${c.group} ${c.id}`.toLowerCase().includes(q));
      results.innerHTML=filtered.length?filtered.map((c,i)=>`<button type="button" class="modern-command-item ${i===0?'active':''}" data-command-id="${c.id}"><i class="fas ${c.icon}"></i><span>${c.label}<small>${c.group}</small></span></button>`).join(''):'<div class="modern-command-empty">No admin section matches that search.</div>';
    }
    const showCommand=()=>{renderCommands();command.classList.add('open');command.setAttribute('aria-hidden','false');setTimeout(()=>input.focus(),20)};
    const hideCommand=()=>{command.classList.remove('open');command.setAttribute('aria-hidden','true');input.value=''};
    top.querySelector('#modernCommandOpen')?.addEventListener('click',showCommand); command.addEventListener('click',e=>{if(e.target===command)hideCommand();const item=e.target.closest('[data-command-id]');if(item){activate(item.dataset.commandId);hideCommand();}});
    input.addEventListener('input',()=>renderCommands(input.value));
    document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();command.classList.contains('open')?hideCommand():showCommand();}if(e.key==='Escape'){if(command.classList.contains('open'))hideCommand();else closeRail();}});

    // Preserve unsaved-change warning without blocking normal successful submissions.
    const dirtyForms=new Set();
    layout.querySelectorAll('form').forEach(form=>{const mark=()=>{dirtyForms.add(form);form.closest('.admin-section')?.classList.add('is-dirty')};form.addEventListener('input',mark);form.addEventListener('change',mark);form.addEventListener('submit',()=>{dirtyForms.delete(form);form.closest('.admin-section')?.classList.remove('is-dirty')});});
    window.addEventListener('beforeunload',e=>{if(!dirtyForms.size)return;e.preventDefault();e.returnValue='';});

    // Better submit feedback: disable the clicked submit button and say what is happening.
    page.querySelectorAll('form[method="post"],form[method="POST"]').forEach(form=>form.addEventListener('submit',()=>{const btn=form.querySelector('button[type="submit"]:focus')||form.querySelector('button[type="submit"]');if(!btn||btn.dataset.busy==='1')return;btn.dataset.busy='1';btn.dataset.originalHtml=btn.innerHTML;btn.disabled=true;btn.innerHTML='<i class="fas fa-circle-notch fa-spin"></i> Saving…';setTimeout(()=>{if(document.body.contains(btn)){btn.disabled=false;btn.dataset.busy='0';if(btn.dataset.originalHtml)btn.innerHTML=btn.dataset.originalHtml}},12000)}));

    // Keep active section after a redirect/refresh.
    const initial=(location.hash||'#overview').slice(1); activate(labels[initial]?initial:'overview',false);
  });
})();
