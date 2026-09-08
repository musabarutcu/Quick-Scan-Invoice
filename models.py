# ============================================================
# models.py — SQLAlchemy Veritabanı Modelleri
# ============================================================
# Bu dosya, veritabanı tablolarımızı Python sınıfları olarak
# tanımlar. SQLAlchemy ORM (Object-Relational Mapping), bu
# sınıfları SQL tablolarına çevirir — böylece raw SQL yazmak
# yerine Python nesneleriyle çalışırız.
#
# Her sınıf = Bir veritabanı tablosu
# Her sınıf değişkeni (Column) = Bir tablo sütunu
# Sınıflar arası relationship() = Foreign key ilişkileri
#
# Neden ayrı dosya?
#   - Büyüyen projelerde modeller çok yer kaplar
#   - main.py veya database.py'ye gömmek bakımı zorlaştırır
#   - Alembic bu dosyayı import ederek migration üretir
# ============================================================

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float,
    ForeignKey, Integer, String, Text
)
from sqlalchemy.orm import relationship

from database import Base


# ============================================================
# InvoiceStatus — Fatura Durum Enum'u
# ============================================================
# Python enum'u kullanmanın avantajı: geçersiz değerlerin
# veritabanına yazılmasını önleriz. "pending_typo" gibi bir
# değer kabul edilmez.
class InvoiceStatus(str, enum.Enum):
    """
    Fatura durumunu temsil eden sabit değerler.
    str'dan miras almak, Pydantic ile JSON serialize edilmesini sağlar.

    Durum geçiş akışı:
      pending → processing → processed → reviewed
                          ↘ failed  (OCR veya AI hatası)

    - pending    → Dosya yüklendi, arka plan görevi henüz başlamadı
    - processing → BackgroundTask çalışıyor: OCR + AI işleme sürecinde
    - processed  → OCR + AI başarıyla tamamlandı, veriler çıkarıldı
    - reviewed   → Kullanıcı inceledi ve onayladı
    - failed     → OCR veya AI adımında kritik hata oluştu
    """
    pending    = "pending"
    processing = "processing"
    processed  = "processed"
    reviewed   = "reviewed"
    failed     = "failed"


# ============================================================
# Model 1: User — Kullanıcı Tablosu
# ============================================================
class User(Base):
    """
    Sisteme kayıtlı kullanıcıları temsil eder.
    
    Güvenlik notu: Şifreyi ASLA düz metin olarak saklama!
    hashed_password alanı, bcrypt ile hash'lenmiş şifreyi tutar.
    Gerçek şifre veritabanına hiçbir zaman ulaşmaz.
    """
    __tablename__ = "users"

    # Birincil anahtar — her kullanıcı için benzersiz sayısal ID
    # index=True: Bu sütuna göre sorgular hızlanır
    id = Column(Integer, primary_key=True, index=True)

    # Email — benzersiz ve zorunlu
    # unique=True: Aynı email ile iki hesap açılamaz
    email = Column(String(255), unique=True, index=True, nullable=False)

    # Kullanıcı adı — benzersiz
    username = Column(String(100), unique=True, index=True, nullable=False)

    # Hash'lenmiş şifre — bcrypt çıktısı genellikle ~60 karakter
    hashed_password = Column(String(255), nullable=False)

    # Hesap aktif mi? False ise giriş engellenebilir
    # default=True: Kayıt olurken hesap otomatik aktif olsun
    is_active = Column(Boolean, default=True)

    # Hesap oluşturulma zamanı
    # default=datetime.utcnow: Kayıt anında otomatik doldurulur
    # NOT: datetime.utcnow (callable) geçiyoruz, datetime.utcnow()
    # (çağrılmış) değil — çağrılmış olsaydı tüm kayıtlar aynı
    # zamanı alırdı (modül yükleme zamanı).
    created_at = Column(DateTime, default=datetime.utcnow)

    # İlişki tanımı: Bir kullanıcının birden fazla faturası olabilir
    # back_populates: Invoice.owner → User.invoices çift yönlü bağ
    # cascade: Kullanıcı silinirse faturalar da silinsin
    invoices = relationship("Invoice", back_populates="owner", cascade="all, delete-orphan")


# ============================================================
# Model 2: Category — Harcama Kategori Tablosu
# ============================================================
class Category(Base):
    """
    Fatura kalemlerini sınıflandırmak için kullanılan kategoriler.
    Örnekler: "Ofis Malzemesi", "Ulaşım", "Yemek", "Yazılım"
    
    Bu tabloyu ayrı tutmanın sebebi: Kategoriler birden fazla
    fatura kalemi tarafından paylaşılır. Her kaleme kategori adını
    tekrar yazmak yerine (denormalizasyon), ID ile referans veririz.
    """
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)

    # Kategori adı — benzersiz olmalı (aynı isimde iki kategori anlamsız)
    name = Column(String(100), unique=True, nullable=False)

    # Bu kategoriye ait fatura kalemleri
    line_items = relationship("LineItem", back_populates="category")


# ============================================================
# Model 3: Invoice — Fatura Tablosu
# ============================================================
class Invoice(Base):
    """
    Yüklenen veya manuel girilen fatura/makbuzları temsil eder.
    
    owner_id, güvenliğin kilit noktasıdır:
    Her sorguda "WHERE owner_id = current_user.id" filtresi
    uygulayarak kullanıcıların birbirinin faturalarını görmesini
    engelliyoruz (IDOR — Insecure Direct Object Reference koruması).
    """
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)

    # Yabancı anahtar (Foreign Key): Bu faturanın sahibi hangi kullanıcı?
    # ondelete="CASCADE": Kullanıcı silinirse faturaları da silinsin
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Tedarikçi/satıcı adı — manuel giriş veya OCR ile doldurulacak
    vendor_name = Column(String(255), nullable=True)

    # Fatura tarihi (string olarak saklıyoruz — "YYYY-MM-DD" formatında)
    # Alternatif: Date sütun tipi, ama string parsing esneklik sağlar
    invoice_date = Column(String(20), nullable=True)

    # Toplam tutar — Float kullanıyoruz; üretim için Numeric(10, 2) daha doğru
    # Ancak bu aşamada Float yeterli
    total_amount = Column(Float, nullable=True)

    # Ham OCR metni — sonraki aşamada doldurulacak
    # nullable=True: Bu aşamada boş kalabilir
    # Text: String'den farklı olarak çok uzun metin saklayabilir
    raw_ocr_text = Column(Text, nullable=True)

    # Fatura durumu: pending → processed → reviewed
    # Enum tipi veritabanında kontrol edilir
    status = Column(
        Enum(InvoiceStatus),
        default=InvoiceStatus.pending,
        nullable=False
    )

    # Kaydın oluşturulma zamanı — timezone bilgisi olmadan UTC
    created_at = Column(DateTime, default=datetime.utcnow)

    # Güncelleme zamanı — UPDATE işlemlerinde otomatik değişir
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Yüklenen dosyanın sunucudaki yolu (opsiyonel)
    file_path = Column(String(500), nullable=True)

    # OCR veya AI adımında oluşan hata mesajı.
    # status="failed" olduğunda buraya hata detayı yazılır.
    # Kullanıcı arayüzde "neden başarısız oldu" sorusuna yanıt verir.
    # Text tipi: String'den farklı olarak uzun stack trace / hata metinleri sığar.
    error_message = Column(Text, nullable=True)

    # İlişkiler
    # owner: Bu faturanın sahibi (User nesnesi)
    owner = relationship("User", back_populates="invoices")

    # line_items: Bu faturanın kalemleri (liste)
    line_items = relationship("LineItem", back_populates="invoice", cascade="all, delete-orphan")


# ============================================================
# Model 4: LineItem — Fatura Kalemi Tablosu
# ============================================================
class LineItem(Base):
    """
    Bir faturanın içindeki tekil kalemlerini temsil eder.
    Örnek: "Kalem x10 → 50 TL" veya "Kargo → 15 TL"
    
    Bir Invoice'un birden fazla LineItem'ı olabilir (one-to-many).
    category_id nullable: Kalem henüz kategorilendirilmemiş olabilir.
    """
    __tablename__ = "line_items"

    id = Column(Integer, primary_key=True, index=True)

    # Hangi faturaya ait?
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)

    # Kalemin açıklaması: "Ofis koltuğu", "Toplu taşıma", vb.
    description = Column(String(500), nullable=False)

    # Kalemin tutarı
    amount = Column(Float, nullable=False)

    # Hangi kategoriye ait? (nullable — henüz kategorilenmemiş olabilir)
    # Aşama 2'de AI bu alanı otomatik dolduracak
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)

    # İlişkiler
    invoice = relationship("Invoice", back_populates="line_items")
    category = relationship("Category", back_populates="line_items")
