// Минимальная фейковая реализация Firebase compat SDK (app/firestore/auth)
// для тестов в песочнице без сети — подставляется вместо реальных
// gstatic-скриптов через Playwright route interception. Держит данные в
// памяти вкладки и достаточно точно повторяет поведение set(merge)/
// onSnapshot/runTransaction/signInAnonymously, которое реально использует
// build_dashboard.py.
(function () {
  if (window.firebase && window.firebase.__fake) return;

  const store = {};       // collectionPath -> { id: data }
  const subs = {};        // collectionPath -> [cb]
  const docSubs = {};     // "collectionPath/id" -> [cb]
  let currentUser = null;
  const authListeners = [];

  function coll(path) {
    if (!store[path]) store[path] = {};
    return store[path];
  }

  function notifyColl(path) {
    (subs[path] || []).forEach(fn => fn());
  }
  function notifyDoc(path, id) {
    (docSubs[path + '/' + id] || []).forEach(fn => fn());
  }

  function shallowMergeOneLevel(existing, incoming) {
    const out = Object.assign({}, existing || {});
    Object.keys(incoming).forEach(k => {
      const v = incoming[k];
      if (
        v && typeof v === 'object' && !v.__isServerTimestamp && !(v instanceof Date) &&
        out[k] && typeof out[k] === 'object'
      ) {
        out[k] = Object.assign({}, out[k], v);
      } else {
        out[k] = v;
      }
    });
    return out;
  }

  class DocRef {
    constructor(path, id) {
      this.path = path;
      this.id = id;
    }
    get() {
      const c = coll(this.path);
      const data = c[this.id];
      const self = this;
      return Promise.resolve({
        exists: data !== undefined,
        data: () => data,
        id: this.id,
        ref: self,
      });
    }
    set(data, opts) {
      const c = coll(this.path);
      c[this.id] = (opts && opts.merge) ? shallowMergeOneLevel(c[this.id], data) : Object.assign({}, data);
      notifyColl(this.path);
      notifyDoc(this.path, this.id);
      return Promise.resolve();
    }
    delete() {
      const c = coll(this.path);
      delete c[this.id];
      notifyColl(this.path);
      notifyDoc(this.path, this.id);
      return Promise.resolve();
    }
    update(data) {
      const c = coll(this.path);
      if (c[this.id] === undefined) return Promise.reject(new Error('No document to update: ' + this.path + '/' + this.id));
      c[this.id] = Object.assign({}, c[this.id], data);
      notifyColl(this.path);
      notifyDoc(this.path, this.id);
      return Promise.resolve();
    }
    collection(name) {
      return new CollectionRef(this.path + '/' + this.id + '/' + name);
    }
    onSnapshot(cb) {
      const fire = () => {
        const c = coll(this.path);
        const data = c[this.id];
        cb({ exists: data !== undefined, data: () => data, id: this.id, ref: this });
      };
      const key = this.path + '/' + this.id;
      docSubs[key] = docSubs[key] || [];
      docSubs[key].push(fire);
      fire();
      return () => { docSubs[key] = (docSubs[key] || []).filter(f => f !== fire); };
    }
  }

  class CollectionRef {
    constructor(path) {
      this.path = path;
    }
    doc(id) {
      return new DocRef(this.path, id || ('auto_' + Math.random().toString(36).slice(2)));
    }
    add(data) {
      const id = 'auto_' + Math.random().toString(36).slice(2);
      const ref = new DocRef(this.path, id);
      return ref.set(data).then(() => ref);
    }
    orderBy() { return this; }
    where() { return this; }
    onSnapshot(cb) {
      const fire = () => {
        const c = coll(this.path);
        const docs = Object.keys(c).map(id => ({
          id,
          data: () => c[id],
          ref: new DocRef(this.path, id),
        }));
        cb({
          empty: docs.length === 0,
          forEach: fn => docs.forEach(fn),
          docs,
        });
      };
      subs[this.path] = subs[this.path] || [];
      subs[this.path].push(fire);
      fire();
      return () => { subs[this.path] = (subs[this.path] || []).filter(f => f !== fire); };
    }
  }

  function firestore() {
    return {
      collection: path => new CollectionRef(path),
      runTransaction: updateFn => Promise.resolve().then(() => updateFn({
        get: ref => ref.get(),
        set: (ref, data, opts) => ref.set(data, opts),
      })),
    };
  }
  firestore.FieldValue = {
    serverTimestamp: () => ({ __isServerTimestamp: true, toDate: () => new Date() }),
  };

  function auth() {
    return {
      onAuthStateChanged: cb => {
        authListeners.push(cb);
        if (currentUser) cb(currentUser);
      },
      signInAnonymously: () => new Promise(resolve => {
        setTimeout(() => {
          currentUser = { uid: 'test-uid-fixed' };
          authListeners.forEach(cb => cb(currentUser));
          resolve({ user: currentUser });
        }, 10);
      }),
    };
  }

  window.firebase = {
    __fake: true,
    initializeApp: () => ({}),
    firestore,
    auth,
  };
})();
