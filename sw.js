// Erez-Stocks push receiver. No fetch handler: page requests are untouched.
// Payload may be Declarative Web Push (notification.navigate handled natively
// by iOS 18.4+, which does not fire the push event) or legacy top-level fields
// parsed here for older browsers - never both, so no duplicate notifications.
var TARGET_CACHE = 'bio-push-v1';
function targetUrl(data){
  return (data && (data.url || (data.notification && data.notification.navigate))) || './#bio';
}
self.addEventListener('push', function(event){
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch(e) {}
  var note = data.notification || {};
  var title = data.title || note.title || 'התראת ביוטק חמה';
  var body = data.body || note.body || '';
  var url = targetUrl(data);
  // NOTE: nothing is persisted here. A push that is never tapped must not
  // affect the next plain app open.
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body, tag: data.tag || note.tag || 'bio-hot', dir: 'rtl', lang: 'he',
      icon: 'icon-192.png', badge: 'icon-192.png',
      data: { url: url }
    }).then(function(){
      return clients.matchAll({type:'window', includeUncontrolled:true});
    }).then(function(cs){
      cs.forEach(function(c){ c.postMessage({type:'push', title:title, body:body, url:url}); });
    })
  );
});
self.addEventListener('notificationclick', function(event){
  var url = (event.notification.data && event.notification.data.url) || './#bio';
  var abs = new URL(url, self.registration.scope).href;
  event.waitUntil(
    // record the tap FIRST: if openWindow drops the URL (WebKit 263687) the
    // page reads this entry once, routes, and clears it; it expires in 2 min.
    caches.open(TARGET_CACHE).then(function(c){
      return c.put('/last-click', new Response(JSON.stringify({url:abs, at:Date.now()})));
    }).then(function(){
      event.notification.close();
      return clients.matchAll({type:'window', includeUncontrolled:true});
    }).then(function(cs){
      var i, c;
      for (i=0;i<cs.length;i++){ if (cs[i].url.indexOf('500scanners') !== -1) { c = cs[i]; break; } }
      if (c && c.focus) {
        return c.focus().then(function(f){
          if (f && f.navigate) return f.navigate(abs).catch(function(){});
        });
      }
      return clients.openWindow(abs).catch(function(){});
    })
  );
});
