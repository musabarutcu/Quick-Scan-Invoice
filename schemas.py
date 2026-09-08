# ============================================================
# schemas.py — Pydantic Veri Şemaları (Request/Response Modelleri)
# ============================================================
# Bu dosya, API'nin dış dünyayla (HTTP request/response) nasıl
# iletişim kurduğunu tanımlar. SQLAlchemy modelleri (models.py)
# veritabanı katmanını temsil ederken, Pydantic şemaları API
# katmanını temsil eder.
#
# NEDEN İKİ AYRI MODEL? (SQLAlchemy vs Pydantic)
#   - SQLAlchemy modeli: Veritabanı satırını temsil eder
#     (hashed_password, ilişkili nesneler dahil tüm sütunlar)
#   - Pydantic şema: API'ye gelen/giden veriyi temsil eder
#     (sadece gerekli alanlar — örneğin hashed_password asla
#      response olarak gönderilmemeli!)
#
# Bu ayrım hem güvenlik hem de esneklik sağlar.
# ============================================================

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, field_validator

from models import InvoiceStatus


# ============================================================
# BÖLÜM 1: Kullanıcı (User) Şemaları
# ============================================================

class UserCreate(BaseModel):
    """
    Kayıt isteği (POST /auth/register) için gelen veri şeması.
    Kullanıcı bu alanları doldurup gönderir.
    """
    email: EmailStr          # Pydantic otomatik email formatı doğrular
    username: str
    password: str            # Düz metin şifre — sadece kayıt anında gelir

    @field_validator("username")
    @classmethod
    def username_must_be_valid(cls, v: str) -> str:
        """
        Kullanıcı adı doğrulama:
        - Minimum 3 karakter
        - Maksimum 50 karakter
        - Yalnızca harf, rakam ve alt çizgi
        """
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Kullanıcı adı en az 3 karakter olmalıdır")
        if len(v) > 50:
            raise ValueError("Kullanıcı adı en fazla 50 karakter olabilir")
        if not v.replace("_", "").isalnum():
            raise ValueError("Kullanıcı adı yalnızca harf, rakam ve _ içerebilir")
        return v

    @field_validator("password")
    @classmethod
    def password_must_be_strong(cls, v: str) -> str:
        """Şifre en az 8 karakter olmalı."""
        if len(v) < 8:
            raise ValueError("Şifre en az 8 karakter olmalıdır")
        return v


class UserResponse(BaseModel):
    """
    Kullanıcı bilgisi döndürürken kullanılan şema.
    
    GÜVENL İK: hashed_password bu şemada YOK.
    API hiçbir zaman hash'lenmiş şifreyi istemciye döndürmez.
    """
    id: int
    email: str
    username: str
    is_active: bool
    created_at: datetime

    # Pydantic v2'de ORM nesnelerini (SQLAlchemy) okuyabilmek için
    # model_config gerekir. from_attributes=True ile SQLAlchemy
    # nesnelerini doğrudan Pydantic modeline çevirebiliriz.
    model_config = {"from_attributes": True}


# ============================================================
# BÖLÜM 2: Authentication (JWT) Şemaları
# ============================================================

class Token(BaseModel):
    """
    Başarılı giriş sonucunda döndürülen JWT token şeması.
    access_token: İmzalanmış JWT string'i
    token_type: OAuth2 standardı gereği "bearer"
    """
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """
    JWT token içindeki "payload" (claim) verisi.
    Token decode edildiğinde bu yapıya çevrilir.
    sub (subject): Token'ın hangi kullanıcıya ait olduğu (email)
    """
    sub: Optional[str] = None  # JWT standardında "sub" = subject


class LoginRequest(BaseModel):
    """JSON body ile login için şema (OAuth2 form yerine)."""
    username: str  # email veya kullanıcı adı
    password: str


# ============================================================
# BÖLÜM 3: Kategori (Category) Şemaları
# ============================================================

class CategoryCreate(BaseModel):
    """Yeni kategori oluşturma isteği."""
    name: str


class CategoryResponse(BaseModel):
    """Kategori bilgisi döndürme şeması."""
    id: int
    name: str

    model_config = {"from_attributes": True}


# ============================================================
# BÖLÜM 4: Fatura Kalemi (LineItem) Şemaları
# ============================================================

class LineItemCreate(BaseModel):
    """
    Faturaya kalem ekleme isteği.
    category_id nullable: Henüz kategorilenmemiş olabilir.
    """
    description: str
    amount: float
    category_id: Optional[int] = None


class LineItemResponse(BaseModel):
    """Fatura kalemi döndürme şeması."""
    id: int
    description: str
    amount: float
    category_id: Optional[int] = None
    category: Optional[CategoryResponse] = None

    model_config = {"from_attributes": True}


# ============================================================
# BÖLÜM 5: Fatura (Invoice) Şemaları
# ============================================================

class InvoiceCreate(BaseModel):
    """
    Manuel fatura oluşturma isteği (POST /invoices).
    Bu aşamada kullanıcı verileri elle girer.
    Sonraki aşamada OCR bu verileri otomatik dolduracak.
    """
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None   # "YYYY-MM-DD" formatı beklenir
    total_amount: Optional[float] = None

    @field_validator("invoice_date")
    @classmethod
    def validate_date_format(cls, v: Optional[str]) -> Optional[str]:
        """Tarih formatını doğrula: YYYY-MM-DD"""
        if v is None:
            return v
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Tarih formatı YYYY-MM-DD olmalıdır (örn: 2024-01-15)")
        return v


class InvoiceUpdate(BaseModel):
    """
    Fatura güncelleme isteği (PUT /invoices/{id}).
    Tüm alanlar opsiyonel — yalnızca gönderilen alanlar güncellenir.
    Bu pattern "partial update" veya "PATCH semantics" olarak bilinir.
    """
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None
    status: Optional[InvoiceStatus] = None


class InvoiceResponse(BaseModel):
    """
    Fatura listesi gibi özet görünümlerde döndürülen şema.
    Fatura kalemleri (line_items) dahil değil — performans için.
    """
    id: int
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None
    total_amount: Optional[float] = None
    status: InvoiceStatus
    created_at: datetime
    owner_id: int

    model_config = {"from_attributes": True}


class InvoiceDetailResponse(InvoiceResponse):
    """
    Tek fatura görüntülemede (GET /invoices/{id}) döndürülen detaylı şema.
    InvoiceResponse'dan miras alır ve fatura kalemlerini de içerir.
    
    Pydantic miras kullanmanın avantajı: Tekrar kod yazmayız,
    sadece ek alanları ekleriz.
    """
    line_items: List[LineItemResponse] = []
    raw_ocr_text: Optional[str] = None
    file_path: Optional[str] = None
    updated_at: Optional[datetime] = None
    error_message: Optional[str] = None


class UploadResponse(BaseModel):
    """
    Dosya yükleme endpoint'i (POST /invoices/upload) yanıt şeması.
    HTTP 202 Accepted: İstek alındı, arka planda işleniyor.
    Kullanıcı durumu /invoices/{id}/status ile polling yaparak takip eder.
    """
    message: str
    invoice_id: int
    file_path: str
    status: InvoiceStatus


class InvoiceStatusResponse(BaseModel):
    """
    Fatura işleme durumu polling endpoint'i (GET /invoices/{id}/status) şeması.

    Bu şema kasıtlı olarak küçük tutulmuştur — sadece durum bilgisi taşır.
    Frontend bu endpoint'i setInterval ile düzenli aralıklarla çağırır.
    Küçük payload = daha az band genişliği tüketimi = polling maliyeti azalır.

    error_message: status="failed" olduğunda OCR/AI hata detayını içerir.
    Kullanıcı arayüzde "neden başarısız oldu" sorusuna yanıt verir.
    """
    id: int
    status: InvoiceStatus
    vendor_name: Optional[str] = None
    total_amount: Optional[float] = None
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}

