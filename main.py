# ============================================================
# main.py — FastAPI Uygulama Giriş Noktası
# ============================================================
# Bu dosya FastAPI uygulamasını oluşturur ve yapılandırır.
# `uvicorn main:app` komutu bu dosyayı hedef alır.
#
# Sorumlulukları:
# 1. FastAPI uygulamasını başlat
# 2. Router'ları kaydet (auth, invoices)
# 3. Static dosyaları ve template motoru bağla
# 4. Uygulama başlarken gerekli klasörleri oluştur
# 5. Ana sayfa yönlendirmesini tanımla
# ============================================================

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import Base, engine
from routers import auth, invoices

# .env dosyasını yükle — UYGULAMA BAŞINDA ilk iş bu olmalı
# Diğer modüller import edilmeden önce ortam değişkenleri hazır olsun
load_dotenv()

# Yükleme klasörü yolunu al
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))


# ============================================================
# Lifespan — Uygulama Yaşam Döngüsü Yönetimi
# ============================================================
# FastAPI'nin modern yaklaşımı: @app.on_event("startup") yerine
# asynccontextmanager ile lifespan kullanılır.
#
# yield'den ÖNCEKİ kod: Uygulama başlarken çalışır (startup)
# yield'den SONRAKİ kod: Uygulama kapanırken çalışır (shutdown)
#
# Neden lifespan?
#   - Startup ve shutdown mantığını tek bir yerde toplar
#   - Test ortamında kolayca override edilebilir
#   - Async context manager, kaynak yönetimini temiz tutar
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ──────────────────────────────────────────────
    print("[START] Uygulama baslatiliyor...")

    # Gerekli klasörleri oluştur (yoksa)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[OK] Yukleme klasoru hazir: {UPLOAD_DIR}")

    # Veritabanı tablolarını oluştur
    # NOT: Üretim ortamında bu satır yerine Alembic migration kullanılır.
    # Bu satır geliştirme için kullanışlıdır: Alembic'i çalıştırmadan
    # tüm tabloları otomatik oluşturur.
    # Alembic varsa bu satırı kaldır, çakışabilir.
    Base.metadata.create_all(bind=engine)
    print("[OK] Veritabani tablolari hazir")

    print("[OK] Uygulama basladi! http://localhost:8000")
    print("[DOCS] API dokumantasyonu: http://localhost:8000/docs")

    yield  # ← Uygulama burada çalışır

    # ── SHUTDOWN ─────────────────────────────────────────────
    print("[STOP] Uygulama kapatiliyor...")


# ============================================================
# FastAPI Uygulaması
# ============================================================
app = FastAPI(
    title="QuickScan — Fatura/Makbuz OCR Muhasebeleştirme",
    description="""
    ## QuickScan — OCR & AI Destekli Muhasebeleştirme
    Fatura ve makbuz görsellerinden Tesseract OCR ve Google Gemini AI
    ile otomatik veri çıkarılır ve muhasebe kategorilerine göre sınıflandırılır.
    
    ## Kimlik Doğrulama
    Korumalı endpoint'lere erişmek için önce `/auth/login` ile
    JWT token alın, ardından "Authorize" butonuna tıklayın.
    """,
    version="0.1.0",
    lifespan=lifespan,
)

# ============================================================
# Static Dosyalar
# ============================================================
# "/static" URL'sinden "static/" klasörüne erişim sağlar
# Örnek: /static/js/base.js → static/js/base.js dosyasını sunar
app.mount("/static", StaticFiles(directory="static"), name="static")

# Template motoru
templates = Jinja2Templates(directory="templates")

# ============================================================
# Router Kaydı
# ============================================================
# Router'ları ana uygulamaya bağla.
# Her router kendi prefix ve tag'ini zaten tanımladı (auth.py, invoices.py)
# Burada sadece include ediyoruz.
app.include_router(auth.router)
app.include_router(invoices.router)


# ============================================================
# Ana Sayfa
# ============================================================

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request):
    """
    Ana sayfa — Giriş yapmış kullanıcıyı fatura listesine yönlendir,
    girmeyen kullanıcıyı giriş sayfasına yönlendir.
    
    JavaScript tarafı (base.js) token kontrolünü yapacak ve
    yönlendirmeyi gerçekleştirecek. Sunucu tarafında şimdilik
    login sayfasına yönlendiriyoruz.
    """
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    """Fatura listesi dashboard sayfası."""
    return templates.TemplateResponse("invoices.html", {"request": request})
