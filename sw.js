// Erez-Stocks push receiver. No fetch handler: page requests are untouched.
self.addEventListener('push', function(event){
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch(e) {}
  var title = data.title || 'התראת ביוטק חמה';
  var body = data.body || '';
  var url = data.url || './#bio';
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body, tag: data.tag || 'bio-hot', dir: 'rtl', lang: 'he',
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
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || './#bio';
  event.waitUntil(
    clients.matchAll({type:'window', includeUncontrolled:true}).then(function(cs){
      for (var i=0;i<cs.length;i++){
        if (cs[i].url.indexOf('500scanners') !== -1 && 'focus' in cs[i]) return cs[i].focus();
      }
      return clients.openWindow(url);
    })
  );
});
