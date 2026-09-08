# ============================================================
# tests/conftest.py — Ortak pytest Fixture'ları
# ============================================================
# conftest.py, pytest'in özel bir dosyasıdır. İçindeki
# fixture'lar, aynı dizindeki ve alt dizinlerdeki tüm test
# dosyalarında otomatik olarak kullanılabilir hale gelir —
# ayrıca import etmek gerekmez.
#
# NEDEN FIXTURE KULLANIYORUZ?
#   Test setup/teardown kodunu her test fonksiyonuna tekrar
#   yazmak yerine, fixture'lar bu kodu bir kez tanımlamamızı
#   ve tüm testlerde yeniden kullanmamızı sağlar (DRY prensibi).
#
# TEST İZOLASYONU — NEDEN IN-MEMORY SQLite?
#   Gerçek veritabanı (ocr_app.db) üzerinde test çalıştırmak:
#   - Gerçek verileri bozabilir
#   - Test verileri temizlenmezse sonraki testler etkilenir
#   - Paralel testlerde yarış koşulları oluşabilir
#
#   In-memory SQLite (sqlite:///:memory:) çözümü:
#   - Her test oturumu için taze, boş bir veritabanı açılır
#   - Test bittikten sonra veritabanı otomatik olarak silinir
#   - Testler birbirinden tamamen izole kalır
#   - RAM'de çalıştığı için disk I/O yok → daha hızlı
#
# ÖNEMLİ: In-memory SQLite'da her yeni bağlantı FARKLI bir veritabanı
# açar. Bu yüzden tek bir bağlantıyı paylaşmak zorundayız.
# "check_same_thread": False + tek bağlantı stratejisi bunu sağlar.
# ============================================================

import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# TEST ORTAMI İÇİN SAHTE ORTAM DEĞİŞKENLERİ
# Bu satırlar, main.py ve dependencies.py import edilmeden önce
# çalışmalıdır. SECRET_KEY tanımlanmazsa dependencies.py RuntimeError
# fırlatır ve testler başlamadan çöker.
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("GOOGLE_API_KEY", "fake-api-key-for-tests")

from database import Base, get_db
from main import app


# ============================================================
# In-Memory Test Veritabanı Motoru
# ============================================================
# connect_args={"check_same_thread": False}: SQLite'ın varsayılan
# thread güvenlik kısıtını devre dışı bırakır.
#
# NEDEN TEK BAĞLANTI?
# SQLite in-memory veritabanı yalnızca oluşturulduğu bağlantıda yaşar.
# Farklı bağlantıdan erişince boş yeni bir veritabanı açılır.
# "check_same_thread=False" ile aynı bağlantıyı test ve app paylaşır.
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Tek bağlantıyı sakla — tüm session'lar bunu paylaşacak
_connection = test_engine.connect()

# Session factory: autocommit=False ile değişiklikler commit olmadan DB'ye gitmez
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_connection)


def override_get_db():
    """
    Test veritabanı oturumunu sağlayan dependency override.
    
    FastAPI'nin dependency injection sistemi, gerçek get_db()
    yerine bu fonksiyonu kullanacak şekilde yapılandırılır.
    Böylece testler gerçek veritabanına dokunmaz.
    """
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """
    Tüm test oturumu için veritabanı tablolarını bir kez oluşturur.
    
    scope="session": Bu fixture tüm test oturumu boyunca sadece
    bir kez çalışır. Tablo oluşturma pahalı bir işlem olduğundan
    paylaşmak performansı artırır.
    
    autouse=True: Tüm testlerde otomatik çalışır, ayrıca parametre gerekmez.
    
    NEDEN SHARED CONNECTION ÜZERİNDE?
    In-memory SQLite'da create_all() ve testler aynı _connection üzerinde
    çalışmalı — farklı bağlantı = farklı (boş) DB demektir.
    """
    Base.metadata.create_all(bind=_connection)
    yield
    Base.metadata.drop_all(bind=_connection)
    _connection.close()


@pytest.fixture(autouse=True)
def clean_tables():
    """
    Her testten ÖNCE tüm tabloları temizler.
    
    scope varsayılan "function" — her test fonksiyonu için çalışır.
    Bu sayede testler birbirini etkilemez:
    - Test A bir kullanıcı oluştursa bile Test B temiz başlar.
    - Email/username unique constraint ihlali olmaz.
    """
    db = TestSessionLocal()
    try:
        from models import LineItem, Invoice, Category, User
        db.query(LineItem).delete()
        db.query(Invoice).delete()
        db.query(Category).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client():
    """
    FastAPI TestClient — HTTP isteklerini simüle eder.
    
    TestClient, gerçek bir sunucu başlatmadan HTTP isteklerini
    doğrudan ASGI interface üzerinden iletir. Bu sayede:
    - Ağ I/O yok → çok daha hızlı
    - Port çakışması yok
    - Test ortamında sunucu yönetimi gerekmez
    
    app.dependency_overrides ile gerçek get_db yerine test DB'yi kullanırız.
    """
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def registered_user(client):
    """
    Test için önceden kayıtlı bir kullanıcı oluşturur.
    
    Birçok test "giriş yapmış kullanıcı" durumunu gerektirir.
    Bu fixture, kayıt + giriş adımlarını kapsüller ve
    {"user": ..., "token": ..., "headers": ...} dict'i döndürür.
    """
    user_data = {
        "email": "test@example.com",
        "username": "testuser",
        "password": "testpassword123"
    }
    # Kayıt ol
    reg_resp = client.post("/auth/register", json=user_data)
    assert reg_resp.status_code == 201, f"Kayıt başarısız: {reg_resp.text}"

    # Giriş yap — login endpoint email bekliyor
    response = client.post(
        "/auth/login",
        json={"email": "test@example.com", "password": "testpassword123"}
    )
    assert response.status_code == 200, f"Giriş başarısız: {response.text}"
    token = response.json()["access_token"]

    return {
        "user": user_data,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"}
    }


@pytest.fixture
def second_user(client):
    """
    İkinci bir kullanıcı — IDOR testi için kullanılır.
    
    IDOR (Insecure Direct Object Reference) testi:
    Kullanıcı A'nın faturasına Kullanıcı B erişememeli.
    Bu fixture, ikinci bir kullanıcı oluşturarak bu senaryoyu
    test etmemizi sağlar.
    """
    user_data = {
        "email": "other@example.com",
        "username": "otheruser",
        "password": "otherpassword123"
    }
    client.post("/auth/register", json=user_data)
    response = client.post(
        "/auth/login",
        json={"email": "other@example.com", "password": "otherpassword123"}
    )
    token = response.json()["access_token"]

    return {
        "user": user_data,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"}
    }
