# ============================================================
# routers/auth.py — Kimlik Doğrulama Router'ı
# ============================================================
# Bu router, kullanıcı kayıt, giriş ve profil endpoint'lerini
# barındırır. JWT tabanlı authentication akışını uygular.
#
# AKIŞ:
#   1. Kayıt: Kullanıcı email+şifre gönderir → şifre hash'lenir →
#             veritabanına kaydedilir
#   2. Giriş: email+şifre gelir → hash doğrulanır → JWT üretilir
#             → istemciye gönderilir
#   3. Korumalı endpoint: İstemci JWT'yi header'da gönderir →
#             get_current_user decode eder → kullanıcı nesnesi gelir
#
# NEDEN JWT? (Session yerine)
#   Session-based auth: Sunucu oturumları hafızada/DB'de saklar
#   JWT: Token istemcide saklanır, sunucu stateless kalır
#   Stateless → Yatay ölçekleme (scaling) kolaydır
#   Dezavantaj: Token'ı iptal etmek daha zordur (blacklist gerekir)
# ============================================================

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import bcrypt as _bcrypt
from jose import jwt
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_active_user
from models import User
from schemas import Token, UserCreate, UserResponse

# Template motoru — HTML sayfaları render etmek için
templates = Jinja2Templates(directory="templates")

# ============================================================
# Şifre Hash'leme
# ============================================================
# NEDEN bcrypt?
#   MD5, SHA-1 gibi hızlı hash'ler GPU ile kısa sürede kırılabilir.
#   bcrypt kasıtlı olarak yavaştır (iş faktörü ayarlanabilir).
#   Bu, brute-force saldırılarını pratikte imkânsız kılar.
#
# NOT: bcrypt 4.x+ passlib uyumsuzluğu:
#   passlib'in bcrypt backend'i bcrypt 4.x ile uyumsuz hale geldi.
#   Bu yüzden bcrypt kütüphanesini doğrudan kullanıyoruz.
#   passlib bağımlılığını kaldırarak sürüm sorunlarından kaçınıyoruz.

# Ortam değişkenlerinden JWT yapılandırması
# SECRET_KEY dependencies.py'de de kontrol ediliyor; burada tekrar
# okuyoruz çünkü bu dosya token OLUŞTURMAK için de kullanıyor.
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# APIRouter: FastAPI'de endpoint gruplarını düzenlemek için kullanılır
# prefix: Tüm bu router'daki endpoint'ler "/auth" ile başlar
# tags: Swagger UI'da endpoint'leri gruplamak için etiket
router = APIRouter(prefix="/auth", tags=["Authentication"])


# ============================================================
# Yardımcı Fonksiyonlar
# ============================================================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Kullanıcının girdiği şifreyi veritabanındaki hash ile karşılaştırır.
    
    bcrypt.checkpw(): Timing-safe karşılaştırma yapar.
    Timing attack önlemi: Her iki durumda da aynı süre harcanır;
    saldırgan doğrulama süresini ölçerek şifreyi tahmin edemez.
    """
    password_bytes = plain_password.encode("utf-8")
    hash_bytes = hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password
    return _bcrypt.checkpw(password_bytes, hash_bytes)


def get_password_hash(password: str) -> str:
    """
    Düz metin şifreyi bcrypt ile hash'ler.
    
    gensalt(): Her çağrıda rastgele bir salt üretir.
    Aynı şifre → farklı hash. Rainbow table saldırılarına karşı koruma.
    rounds=12 (varsayılan): Hash hesaplama maliyeti; yükseldikçe brute-force zorlaşır.
    """
    password_bytes = password.encode("utf-8")
    salt = _bcrypt.gensalt()
    hashed = _bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    JWT access token üretir.
    
    data: Token payload'ına eklenecek veriler (genellikle {"sub": email})
    expires_delta: Token geçerlilik süresi (varsayılan: .env'den alınır)
    
    JWT yapısı: header.payload.signature
    - header: Algoritma bilgisi (base64url encoded)
    - payload: Kullanıcı verileri / claim'ler (base64url encoded)
    - signature: SECRET_KEY ile imzalanmış hash (değiştirilemez!)
    
    ÖNEMLİ: JWT payload şifrelenmiş DEĞİL, sadece imzalıdır.
    Herkes decode edebilir — içine hassas veri (şifre, kredi kartı) koyma!
    """
    to_encode = data.copy()

    # Token son kullanım zamanını hesapla
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    # "exp" (expiration) claim'i JWT standardının bir parçasıdır
    # jose kütüphanesi bu claim'i otomatik kontrol eder
    to_encode.update({"exp": expire})

    # Token'ı imzala ve string olarak döndür
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    Veritabanında kullanıcıyı bulur ve şifresini doğrular.
    
    Başarısızlık durumunda None döner. Hangi adımda başarısız
    olduğunu söylemeyiz — bu güvenlik pratiği:
    "Email yok" veya "Şifre yanlış" gibi spesifik mesajlar yerine
    genel hata mesajı göstermeliyiz. Spesifik mesajlar saldırganın
    hangi hesapların var olduğunu öğrenmesini sağlar
    (user enumeration attack).
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


# ============================================================
# Sayfa Endpoint'leri (HTML döndürür — Swagger'da gizli)
# ============================================================

@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    """Giriş sayfasını render eder."""
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
async def register_page(request: Request):
    """Kayıt sayfasını render eder."""
    return templates.TemplateResponse("register.html", {"request": request})


# ============================================================
# API Endpoint'leri (JSON döndürür)
# ============================================================

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Yeni kullanıcı kaydı oluşturur.
    
    Adımlar:
    1. Email benzersizlik kontrolü
    2. Kullanıcı adı benzersizlik kontrolü
    3. Şifreyi hash'le
    4. Veritabanına kaydet
    5. Kullanıcı nesnesini (şifresiz) döndür
    
    HTTP 201 Created: Yeni kaynak oluşturulduğunda kullanılır
    (GET gibi sorgular 200 döner, POST ile kaynak oluşturmak 201)
    """
    # Email zaten kayıtlı mı?
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu email adresi zaten kullanımda."
        )

    # Kullanıcı adı zaten alınmış mı?
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bu kullanıcı adı zaten kullanımda."
        )

    # Şifreyi hash'le — düz metin veritabanına asla gitmesin
    hashed_password = get_password_hash(user_data.password)

    # Yeni kullanıcı nesnesi oluştur
    new_user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)  # Veritabanından güncel veriyi (id, created_at) al

    return new_user


@router.post("/login", response_model=Token)
def login_user(
    email: str = Body(..., description="Kayıtlı email adresiniz"),
    password: str = Body(..., description="Şifreniz"),
    db: Session = Depends(get_db)
):
    """
    Kullanıcı girişi — Başarılı girişte JWT access token döndürür.
    
    Body parametreleri JSON olarak gelir:
    {"email": "user@example.com", "password": "şifre123"}
    
    Başarısız girişte kasıtlı olarak genel bir hata mesajı döndürürüz.
    "Email bulunamadı" veya "Şifre yanlış" gibi spesifik mesajlar
    saldırganın hangi adımda başarısız olduğunu öğrenmesini sağlar
    (user enumeration attack). Genel mesaj bu riski önler.
    """
    user = authenticate_user(db, email, password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email veya şifre hatalı.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hesap devre dışı bırakılmış."
        )

    # JWT token oluştur
    access_token = create_access_token(
        data={"sub": user.email}
        # "sub" (subject): JWT standardında token'ın kime ait olduğunu belirtir
        # Biz email kullanıyoruz — ID de kullanılabilir ama email daha okunabilir
    )

    return Token(access_token=access_token, token_type="bearer")


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_active_user)):
    """
    Mevcut oturum açmış kullanıcının bilgilerini döndürür.
    
    get_current_active_user dependency'si:
    1. Authorization header'dan token alır
    2. Token'ı decode eder
    3. Kullanıcıyı veritabanından getirir
    4. Aktif mi kontrol eder
    5. User nesnesini bu endpoint'e enjekte eder
    
    Bu endpoint yalnızca geçerli token ile erişilebilir.
    Token olmadan 401, geçersiz token ile de 401 döner.
    """
    return current_user
