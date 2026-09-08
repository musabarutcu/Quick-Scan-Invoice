# ============================================================
# NOTES.md — Projeyi Çalıştırma Adımları (Hızlı Başlangıç)
# ============================================================
# Bu dosya kısa bir başvuru kaynağıdır.
# Ayrıntılı README son aşamada hazırlanacak.
# ============================================================

## 1. Sanal Ortam Oluşturun

```powershell
# Proje klasörüne gidin
cd "C:\Users\musab\OneDrive\Masaüstü\ocr"

# Python sanal ortamı oluştur
python -m venv venv

# Sanal ortamı etkinleştir (Windows)
venv\Scripts\activate

# Paketleri kur
pip install -r requirements.txt
```

---

## 2. .env Dosyasını Oluşturun

```powershell
# .env.example'ı kopyala
copy .env.example .env
```

`.env` dosyasını açın ve `SECRET_KEY` değerini doldurun:

```powershell
# Güvenli rastgele anahtar üret
python -c "import secrets; print(secrets.token_hex(32))"
```

Üretilen değeri `.env` dosyasındaki `SECRET_KEY=` satırına yapıştırın.

---

## 3. Veritabanı Migration'ını Çalıştırın

```powershell
# Alembic ile ilk migration'ı uygula
alembic upgrade head
```

> **Not:** İlk çalıştırmada `main.py`'deki `Base.metadata.create_all()` 
> tabloları otomatik oluşturur. Alembic migration sadece şema 
> değişikliklerini yönetmek için gereklidir.

---

## 4. Uygulamayı Başlatın

```powershell
uvicorn main:app --reload --port 8000
```

- `--reload`: Kod değişikliklerinde otomatik yeniden başlatır (sadece geliştirme)
- `--port 8000`: Varsayılan port (değiştirebilirsiniz)

---

## 5. Test Edin

| Adres | Açıklama |
|-------|----------|
| http://localhost:8000 | Ana sayfa |
| http://localhost:8000/auth/register | Kayıt sayfası |
| http://localhost:8000/auth/login | Giriş sayfası |
| http://localhost:8000/dashboard | Fatura listesi |
| http://localhost:8000/docs | Swagger UI (API testi) |
| http://localhost:8000/redoc | ReDoc (API dokümantasyonu) |

---

## Hızlı API Testi (Swagger UI ile)

1. `http://localhost:8000/docs` adresine gidin
2. `POST /auth/register` → Kullanıcı oluşturun
3. `POST /auth/login` → Token alın
4. Sayfanın üstündeki **"Authorize"** butonuna tıklayın
5. Token'ı `Bearer <token>` formatında girin
6. Artık tüm korumalı endpoint'leri test edebilirsiniz

---

## Alembic Notları

```powershell
# Yeni migration oluştur (model değişikliği sonrası)
alembic revision --autogenerate -m "add new field"

# Migration'ı uygula
alembic upgrade head

# Bir önceki versiyona dön
alembic downgrade -1

# Migration geçmişini gör
alembic history
```

---

## Proje Yapısı Hatırlatıcı

```
main.py         → Giriş noktası
database.py     → DB bağlantısı
models.py       → Tablolar
schemas.py      → API veri şemaları
dependencies.py → JWT doğrulama
routers/
  auth.py       → /auth/*
  invoices.py   → /invoices/*
services/
  ocr_service.py → Aşama 2'de dolacak
templates/      → HTML sayfaları
static/js/      → JavaScript
```
