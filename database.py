# ============================================================
# database.py — Veritabanı Bağlantı Katmanı
# ============================================================
# Bu dosya, SQLAlchemy ORM'i yapılandırır ve üç temel bileşeni
# sağlar:
#   1. engine       — Veritabanına fiziksel bağlantı
#   2. SessionLocal — Her HTTP isteği için bir DB oturumu fabrikası
#   3. Base         — Tüm modellerimizin miras alacağı temel sınıf
#
# Neden ayrı bir dosya?
#   Bağlantı mantığını models.py'dan ayırmak "Separation of
#   Concerns" prensibini uygular. Database.py'yi değiştirdiğimizde
#   modellerimize dokunmamıza gerek kalmaz.
# ============================================================

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# .env dosyasını yükle — bu satır olmadan os.getenv() boş döner
load_dotenv()

# Veritabanı URL'sini ortam değişkeninden al
# SQLite için: "sqlite:///./dosyaadi.db"
# PostgreSQL için: "postgresql://user:pass@host/dbname"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ocr_app.db")

# ============================================================
# Engine — Veritabanı Motoru
# ============================================================
# Engine, SQLAlchemy'nin veritabanıyla konuştuğu ana nesnedir.
# "connect_args" yalnızca SQLite için gereklidir:
#   check_same_thread=False — FastAPI birden fazla thread kullandığı
#   için SQLite'ın varsayılan thread güvenlik kısıtını devre dışı
#   bırakırız. PostgreSQL'de bu parametreye gerek yoktur.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

# ============================================================
# SessionLocal — Oturum Fabrikası
# ============================================================
# Her HTTP isteği için ayrı bir veritabanı oturumu oluştururuz.
# Bu sayede:
#   - Oturumlar birbirini karıştırmaz (thread-safe)
#   - İstek bittiğinde oturumu kapatmak kolaydır
#
# autocommit=False → Değişiklikler otomatik kaydedilmez;
#                    db.commit() çağrısı gerekir (daha güvenli)
# autoflush=False  → Sorgu öncesi otomatik flush yapılmaz;
#                    performans için manuel kontrol sağlar
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ============================================================
# Base — Model Temel Sınıfı
# ============================================================
# Tüm SQLAlchemy modellerimiz (User, Invoice vb.) bu sınıftan
# miras alır. Base, modelleri tablolarla eşleştirir ve
# Alembic migration'ları için metadata sağlar.
Base = declarative_base()


# ============================================================
# get_db — FastAPI Dependency (Bağımlılık Enjeksiyonu)
# ============================================================
# Bu fonksiyon bir "generator" — yield ile değer üretir, ardından
# temizleme kodu çalışır. FastAPI, bunu "dependency injection"
# sistemiyle her endpoint'e otomatik olarak enjekte eder.
#
# Kullanım: def my_endpoint(db: Session = Depends(get_db))
#
# yield'den sonraki "finally" bloğu şunu garantiler:
#   - İstek başarılı olsa da, hata verse de oturum kapatılır
#   - Açık kalan bağlantılar veritabanı kaynaklarını tüketmez
def get_db():
    """
    Her HTTP isteği için yeni bir veritabanı oturumu açar
    ve istek tamamlandıktan sonra oturumu güvenli biçimde kapatır.
    """
    db = SessionLocal()
    try:
        yield db  # Bu noktada endpoint'e veritabanı oturumunu teslim et
    finally:
        db.close()  # İstek bittikten sonra (hata olsa bile) oturumu kapat
