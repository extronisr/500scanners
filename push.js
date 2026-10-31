// Erez-Stocks biotech alerts: onsite toast + Web Push enable/test/off.
// Loads on every page view; all state is per-device (localStorage/browser).
(function(){
var WORKER='https://scanner-refresh.extronisr.workers.dev';
var SEEN='bio_hot_seen';
var $=function(id){return document.getElementById(id);};
var card=$('pushCard'), enableBtn=$('pushEnable'), testBtn=$('pushTest'),
    testCloseBtn=$('pushTestClose'), offBtn=$('pushOff'), statusEl=$('pushStatus');
function say(t){ if(statusEl) statusEl.textContent=t; }
function b64ToBuf(b64){
  var s=b64.replace(/-/g,'+').replace(/_/g,'/');
  while(s.length%4)s+='=';
  var bin=atob(s), out=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++)out[i]=bin.charCodeAt(i);
  return out;
}
var isIOS=/iP(hone|ad|od)/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
var standalone=(window.navigator.standalone===true)||(window.matchMedia&&window.matchMedia('(display-mode: standalone)').matches);
var supported=('serviceWorker' in navigator)&&('PushManager' in window)&&('Notification' in window);

// ---------- onsite toast (works even without push permission) ----------
var toast=document.createElement('div');
toast.style.cssText='position:fixed;top:10px;left:10px;right:10px;z-index:99;background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:10px 14px;box-shadow:0 6px 20px rgba(0,0,0,.15);font-size:.95rem;display:none;cursor:pointer';
document.body.appendChild(toast);
toast.addEventListener('click',function(){
  toast.style.display='none';
  var b=document.querySelector('.tabbar button[data-t="bio"]'); if(b)b.click();
});
function showToast(items){
  var tickers=[]; items.forEach(function(i){ if(i.ticker&&tickers.indexOf(i.ticker)<0) tickers.push(i.ticker); });
  toast.innerHTML='<b>🔥 התראה חמה חדשה: '+tickers.join(', ')+'</b><div style="font-size:.85rem;color:#7c2d12">'+
    (items[0].head||'').replace(/[<>&]/g,'').slice(0,110)+'</div>';
  toast.style.display='block';
  clearTimeout(showToast._t); showToast._t=setTimeout(function(){toast.style.display='none';},12000);
}
function seenKeys(){ try{return JSON.parse(localStorage.getItem(SEEN)||'null');}catch(e){return null;} }
function pollHot(init){
  fetch('bio_hot.json?cb='+Date.now(),{cache:'no-store'}).then(function(r){return r.json();}).then(function(feed){
    var hot=feed.hot||[]; var keys=hot.map(function(i){return i.key;});
    var seen=seenKeys();
    if(!seen){ localStorage.setItem(SEEN,JSON.stringify(keys)); return; } // first visit: no retroactive toast
    var fresh=hot.filter(function(i){return seen.indexOf(i.key)<0;});
    var merged=keys.concat(seen.filter(function(k){return keys.indexOf(k)<0;})).slice(-200);
    localStorage.setItem(SEEN,JSON.stringify(merged));
    if(fresh.length) showToast(fresh);
  }).catch(function(){});
}
pollHot(true); setInterval(pollHot,5*60*1000);
// route to the pane of the last received push (covers OSes that drop the URL)
if(window.caches){caches.open('bio-push-v1').then(function(c){return c.match('/last-target');}).then(function(r){
  if(!r) return;
  r.json().then(function(j){
    if(j&&j.url&&Date.now()-j.at<30*60*1000){
      var m=/#([a-z]+)/.exec(j.url);
      var b=m&&document.querySelector('.tabbar button[data-t="'+m[1]+'"]');
      if(b) b.click();
    }
    caches.open('bio-push-v1').then(function(c){c.delete('/last-target');});
  });
}).catch(function(){});}
navigator.serviceWorker&&navigator.serviceWorker.addEventListener('message',function(e){
  if(e.data&&e.data.type==='push'){ showToast([{ticker:'',head:(e.data.title+' - '+e.data.body)}]); }
});

// ---------- push subscription UI ----------
if(!card) return;
if(!supported){
  say('הדפדפן הזה לא תומך בהתראות'); enableBtn.disabled=true; return;
}
if(isIOS&&!standalone){
  say('באייפון: שתף ← "הוסף למסך הבית", ואז פתח מהאייקון והפעל כאן');
  enableBtn.disabled=true; return;
}
var regP=navigator.serviceWorker.register('sw.js');
function showActive(sub){
  say('ההתראות פעילות ✓');
  enableBtn.hidden=true; testBtn.hidden=false; testCloseBtn.hidden=false; offBtn.hidden=false;
  card.dataset.endpoint=sub.endpoint;
}
regP.then(function(reg){return reg.pushManager.getSubscription();}).then(function(sub){
  if(sub) showActive(sub);
});
enableBtn.addEventListener('click',function(){
  enableBtn.disabled=true; say('מפעיל…');
  Notification.requestPermission().then(function(perm){
    if(perm!=='granted'){ say('ההתראות לא אושרו בדפדפן'); enableBtn.disabled=false; return; }
    return regP.then(function(reg){
      return fetch(WORKER+'/push/vapid-key').then(function(r){return r.json();}).then(function(k){
        return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64ToBuf(k.key)});
      });
    }).then(function(sub){
      return fetch(WORKER+'/push/subscribe',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(sub)}).then(function(r){
        if(!r.ok) throw new Error('subscribe failed');
        showActive(sub);
      });
    }).catch(function(){ say('ההפעלה נכשלה - נסה שוב'); enableBtn.disabled=false; });
  });
});
testBtn.addEventListener('click',function(){
  testBtn.disabled=true; say('שולח התראת בדיקה…');
  fetch(WORKER+'/push/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({endpoint:card.dataset.endpoint})}).then(function(r){return r.json();}).then(function(t){
    say(t.ok?'נשלחה התראת בדיקה ✓':'השליחה נכשלה - נסה להפעיל מחדש'); testBtn.disabled=false;
  }).catch(function(){ say('השליחה נכשלה - בדוק חיבור'); testBtn.disabled=false; });
});
testCloseBtn.addEventListener('click',function(){
  testCloseBtn.disabled=true;
  fetch(WORKER+'/push/test',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({endpoint:card.dataset.endpoint,delay_sec:15})}).then(function(r){return r.json();}).then(function(t){
    if(t.ok){ say('סגור את האפליקציה עכשיו - ההתראה תגיע בעוד כ-15 שניות'); }
    else { say('השליחה נכשלה - נסה שוב'); }
    testCloseBtn.disabled=false;
  }).catch(function(){ say('השליחה נכשלה - בדוק חיבור'); testCloseBtn.disabled=false; });
});
offBtn.addEventListener('click',function(){
  regP.then(function(reg){return reg.pushManager.getSubscription();}).then(function(sub){
    var p=sub?sub.unsubscribe():Promise.resolve();
    return p.then(function(){
      return fetch(WORKER+'/push/unsubscribe',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({endpoint:card.dataset.endpoint})}).catch(function(){});
    });
  }).then(function(){
    say('ההתראות כבויות'); enableBtn.hidden=false; enableBtn.disabled=false;
    testBtn.hidden=true; testCloseBtn.hidden=true; offBtn.hidden=true;
  });
});
})();
