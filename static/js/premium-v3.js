(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const selectors = ['.home-hero','.confidence-strip','.home-row','.section-card','.retention-card','.store-hero','.store-panel-clean','.store-toolbar','.product-card','.account-hero','.overview-card','.orders-panel','.admin-hero','.admin-side-panel','.admin-stats','.admin-panel','.commerce-head','.commerce-card','.pay-card','.detail-image-card','.detail-info-card','.detail-section-card','.status-card','.download-card','.legal-card','.support-mini-card'];
  const nodes=[...document.querySelectorAll(selectors.join(','))];
  if(!reduced && 'IntersectionObserver' in window){
    const observer=new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){e.target.classList.add('is-visible');observer.unobserve(e.target)}}),{threshold:.06,rootMargin:'0px 0px -20px'});
    nodes.forEach((el,i)=>{el.classList.add('v3-reveal');el.style.transitionDelay=`${Math.min((i%4)*35,105)}ms`;observer.observe(el)});
  }else nodes.forEach(el=>el.classList.add('is-visible'));
  document.querySelectorAll('img:not([loading])').forEach((img,i)=>{if(i>1)img.loading='lazy';img.decoding='async'});
})();
