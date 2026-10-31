// Erez-Stocks push receiver. No fetch handler: page requests are untouched.
// Payload may be Declarative Web Push (notification.navigate handled natively
// by iOS 18.4+) or legacy top-level fields parsed here for older browsers.
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
  // remember the tap target so the page can route even if the OS drops the URL
  var store = caches.open(TARGET_CACHE).then(function(c){
    return c.put('/last-target', new Response(JSON.stringify({url:url, at:Date.now()})));
  });
  event.waitUntil(store.then(function(){
    return self.registration.showNotification(title, {
      body: body, tag: data.tag || note.tag || 'bio-hot', dir: 'rtl', lang: 'he',
      icon: 'icon-192.png', badge: 'icon-192.png',
      data: { url: url }
    });
  }).then(function(){
    return clients.matchAll({type:'window', includeUncontrolled:true});
  }).then(function(cs){
    cs.forEach(function(c){ c.postMessage({type:'push', title:title, body:body, url:url}); });
  }));
});
self.addEventListener('notificationclick', function(event){
  var url = (event.notification.data && event.notification.data.url) || null;
  event.waitUntil(
    caches.open(TARGET_CACHE).then(function(c){ return c.match('/last-target'); }).then(function(r){
      if (url || !r) return url;
      return r.json().then(function(j){ return (j && j.url) || null; });
    }).then(function(u){
      event.notification.close();
      var abs = new URL(u || './#bio', self.registration.scope).href;
      return clients.matchAll({type:'window', includeUncontrolled:true}).then(function(cs){
        var i, c;
        for (i=0;i<cs.length;i++){ if (cs[i].url.indexOf('500scanners') !== -1) { c = cs[i]; break; } }
        if (c && c.focus) {
          return c.focus().then(function(f){
            if (f && f.navigate) return f.navigate(abs).catch(function(){});
          });
        }
        return clients.openWindow(abs).catch(function(){});
      });
    })
  );
});
