"""Лёгкая имитация firebase_admin/firestore для тестирования
send_digest.py без реального сервисного аккаунта и сети — тот же
принцип, что и у fake_firebase.js для браузерных тестов (test_dashboard8),
просто на стороне Python. НЕ используется в продакшене, только в
test_send_digest.py.

Поддерживает ровно то подмножество API, которым пользуется
send_digest.py: credentials.Certificate(...), initialize_app(...),
firestore.client(), .collection(x).document(y).get()/.set()/.update(),
.collection(x).stream(), firestore.SERVER_TIMESTAMP.
"""
import sys
import types
from datetime import datetime, timezone


class FakeDocSnapshot:
    def __init__(self, doc_id, data, reference=None):
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = reference

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, store, collection_name, doc_id):
        self._store = store
        self._collection_name = collection_name
        self._doc_id = doc_id

    @property
    def id(self):
        return self._doc_id

    def get(self):
        coll = self._store.setdefault(self._collection_name, {})
        return FakeDocSnapshot(self._doc_id, coll.get(self._doc_id), reference=self)

    def set(self, data):
        coll = self._store.setdefault(self._collection_name, {})
        coll[self._doc_id] = dict(data)

    def update(self, data):
        coll = self._store.setdefault(self._collection_name, {})
        existing = coll.setdefault(self._doc_id, {})
        for k, v in data.items():
            existing[k] = datetime.now(timezone.utc) if v is SERVER_TIMESTAMP else v


class FakeCollectionRef:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def document(self, doc_id):
        return FakeDocRef(self._store, self._name, doc_id)

    def stream(self):
        coll = self._store.get(self._name, {})
        return [
            FakeDocSnapshot(doc_id, data, reference=FakeDocRef(self._store, self._name, doc_id))
            for doc_id, data in list(coll.items())
        ]


class FakeFirestoreClient:
    def __init__(self):
        self._store = {}

    def collection(self, name):
        return FakeCollectionRef(self._store, name)

    def seed(self, collection_name, doc_id, data):
        self._store.setdefault(collection_name, {})[doc_id] = dict(data)


SERVER_TIMESTAMP = object()

_fake_client_singleton = FakeFirestoreClient()


def install():
    """Регистрирует поддельные firebase_admin/firebase_admin.credentials/
    firebase_admin.firestore в sys.modules — вызывать ДО import send_digest."""
    firebase_admin_mod = types.ModuleType("firebase_admin")
    credentials_mod = types.ModuleType("firebase_admin.credentials")
    firestore_mod = types.ModuleType("firebase_admin.firestore")

    credentials_mod.Certificate = lambda data: data
    firebase_admin_mod.initialize_app = lambda cred: None
    firebase_admin_mod.credentials = credentials_mod

    firestore_mod.client = lambda: _fake_client_singleton
    firestore_mod.SERVER_TIMESTAMP = SERVER_TIMESTAMP

    sys.modules["firebase_admin"] = firebase_admin_mod
    sys.modules["firebase_admin.credentials"] = credentials_mod
    sys.modules["firebase_admin.firestore"] = firestore_mod
    return _fake_client_singleton
