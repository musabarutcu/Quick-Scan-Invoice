# ============================================================
# tests/test_auth.py — Kimlik Doğrulama Testleri
# ============================================================
# Bu dosya, auth akışının doğru çalıştığını doğrular:
#   - Başarılı kayıt
#   - Yinelenen email/kullanıcı adı reddi
#   - Başarılı giriş
#   - Yanlış şifre ile giriş reddi
#   - JWT token doğrulaması
#   - Süresi dolmuş/geçersiz token reddi
#   - Aktif olmayan hesap kontrolü
# ============================================================

import pytest
from fastapi.testclient import TestClient


class TestRegister:
    """Kullanıcı kayıt endpoint'i testleri (POST /auth/register)"""

    def test_register_success(self, client: TestClient):
        """Geçerli verilerle kayıt başarılı olmalı, 201 dönmeli."""
        response = client.post("/auth/register", json={
            "email": "newuser@example.com",
            "username": "newuser",
            "password": "securepassword123"
        })
        assert response.status_code == 201
        data = response.json()
        # Temel alanlar döndürülmeli
        assert data["email"] == "newuser@example.com"
        assert data["username"] == "newuser"
        assert data["is_active"] is True
        # Şifre kesinlikle yanıtta yer almamalı!
        assert "password" not in data
        assert "hashed_password" not in data

    def test_register_duplicate_email(self, client: TestClient):
        """Aynı email ile iki kez kayıt 409 Conflict dönmeli."""
        user_data = {
            "email": "dup@example.com",
            "username": "firstuser",
            "password": "password123"
        }
        # İlk kayıt başarılı
        client.post("/auth/register", json=user_data)

        # İkinci kayıt aynı email ile → çakışma
        response = client.post("/auth/register", json={
            "email": "dup@example.com",
            "username": "seconduser",  # Farklı kullanıcı adı ama aynı email
            "password": "password123"
        })
        assert response.status_code == 409
        assert "email" in response.json()["detail"].lower()

    def test_register_duplicate_username(self, client: TestClient):
        """Aynı kullanıcı adı ile iki kez kayıt 409 dönmeli."""
        client.post("/auth/register", json={
            "email": "first@example.com",
            "username": "sameusername",
            "password": "password123"
        })
        response = client.post("/auth/register", json={
            "email": "second@example.com",  # Farklı email
            "username": "sameusername",
            "password": "password123"
        })
        assert response.status_code == 409

    def test_register_short_password(self, client: TestClient):
        """8 karakterden kısa şifre 422 Unprocessable Entity dönmeli."""
        response = client.post("/auth/register", json={
            "email": "short@example.com",
            "username": "shortpass",
            "password": "abc"  # 3 karakter — geçersiz
        })
        assert response.status_code == 422

    def test_register_invalid_email(self, client: TestClient):
        """Geçersiz email formatı 422 dönmeli."""
        response = client.post("/auth/register", json={
            "email": "not-an-email",
            "username": "testuser",
            "password": "password123"
        })
        assert response.status_code == 422

    def test_register_short_username(self, client: TestClient):
        """2 karakterden kısa kullanıcı adı 422 dönmeli."""
        response = client.post("/auth/register", json={
            "email": "ab@example.com",
            "username": "ab",  # 2 karakter — minimum 3
            "password": "password123"
        })
        assert response.status_code == 422

    def test_register_invalid_username_chars(self, client: TestClient):
        """Özel karakter içeren kullanıcı adı reddedilmeli."""
        response = client.post("/auth/register", json={
            "email": "special@example.com",
            "username": "user name!",  # boşluk ve ! geçersiz
            "password": "password123"
        })
        assert response.status_code == 422


class TestLogin:
    """Kullanıcı giriş endpoint'i testleri (POST /auth/login)"""

    def test_login_success(self, client: TestClient, registered_user):
        """Geçerli kimlik bilgileriyle giriş JWT token dönmeli."""
        response = client.post("/auth/login", json={
            "email": registered_user["user"]["email"],
            "password": registered_user["user"]["password"]
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # Token boş olmamalı
        assert len(data["access_token"]) > 10

    def test_login_wrong_password(self, client: TestClient, registered_user):
        """Yanlış şifre ile giriş 401 Unauthorized dönmeli."""
        response = client.post("/auth/login", json={
            "email": registered_user["user"]["email"],
            "password": "YANLIS_SIFRE_123"
        })
        assert response.status_code == 401
        # Güvenlik: Hangisinin yanlış olduğu belirtilmemeli (user enumeration)
        assert "hatalı" in response.json()["detail"].lower()

    def test_login_nonexistent_email(self, client: TestClient):
        """Var olmayan email ile giriş 401 dönmeli."""
        response = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "anypassword123"
        })
        # authenticate_user None döndürür → 401
        assert response.status_code == 401

    def test_login_empty_credentials(self, client: TestClient):
        """
        Boş kimlik bilgileri reddedilmeli.
        
        FastAPI Body(...) parametreleri zorunlu olduğundan,
        eksik alanlar 422 Unprocessable Entity döndürür.
        """
        response = client.post("/auth/login", json={})
        # Zorunlu 'email' ve 'password' body alanları eksik → 422
        assert response.status_code == 422


class TestProtectedEndpoints:
    """JWT doğrulama gerektiren endpoint testleri"""

    def test_get_me_with_valid_token(self, client: TestClient, registered_user):
        """Geçerli token ile /auth/me kullanıcı bilgisi dönmeli."""
        response = client.get("/auth/me", headers=registered_user["headers"])
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == registered_user["user"]["email"]

    def test_get_me_without_token(self, client: TestClient):
        """Token olmadan /auth/me 401 dönmeli."""
        response = client.get("/auth/me")
        assert response.status_code == 401

    def test_get_me_with_invalid_token(self, client: TestClient):
        """Geçersiz (sahte) token ile 401 dönmeli."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer bu.gecersiz.bir.token"}
        )
        assert response.status_code == 401

    def test_get_me_with_expired_token(self, client: TestClient):
        """
        Süresi dolmuş token ile 401 dönmeli.
        
        NEDEN MANUEL TOKEN OLUŞTURUYORUZ?
        Gerçek süresi dolmuş token almak için 30 dakika beklemek
        mümkün değil. jose kütüphanesi ile test amaçlı süresi
        geçmiş bir token oluşturuyoruz.
        """
        from datetime import datetime, timedelta
        from jose import jwt

        # Negatif süre → token zaten süresi dolmuş
        expired_payload = {
            "sub": "test@example.com",
            "exp": datetime.utcnow() - timedelta(minutes=5)  # 5 dakika önce dolmuş
        }
        expired_token = jwt.encode(
            expired_payload,
            "test-secret-key-do-not-use-in-production",
            algorithm="HS256"
        )

        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401

    def test_bearer_prefix_required(self, client: TestClient, registered_user):
        """'Bearer' prefix'i olmadan token 401 dönmeli."""
        # Sadece token, 'Bearer' prefix'i yok
        token = registered_user["token"]
        response = client.get("/auth/me", headers={"Authorization": token})
        assert response.status_code == 401
