# ============================================================
# dependencies.py — FastAPI Bağımlılıkları (Dependencies)
# ============================================================
# Bu dosya, birden fazla router'da ortak kullanılan "dependency"
# fonksiyonlarını barındırır. FastAPI'nin Dependency Injection (DI)
# sistemi sayesinde bu fonksiyonlar endpoint parametrelerine
# otomatik olarak enjekte edilir.
#
# NEDEN AYRI DOSYA?
#   get_current_user() fonksiyonu hem auth.py hem invoices.py
#   hem de gelecekte eklenecek tüm korumalı route'larda kullanılır.
#   Eğer auth.py içine yazarsak, invoices.py → auth.py import eder;
#   auth.py ileride invoices.py'ye bağımlı olursa döngüsel import
#   hatası (circular import) oluşur. Ayrı dosya bu riski önler.
#
# KULLANIM (herhangi bir router'da):
#   from dependencies import get_current_user
#   
#   @router.get("/protected")
#   def protected(current_user: User = Depends(get_current_user)):
#       return {"user": current_user.email}
# ============================================================

import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User
from schemas import TokenData

# .env dosyasını yükle — bu dosya doğrudan çağrılabileceğinden
# (main.py'den bağımsız) kendi load_dotenv() çağrısına ihtiyaç var
load_dotenv()

# ============================================================
# Ortam Değişkenlerinden Yapılandırma
# ============================================================
# SECRET_KEY asla kod içinde sabit yazılmamalı!
# Bu değer .env dosyasından gelir. Eğer tanımlanmamışsa
# uygulama başlarken hata verir — bu kasıtlı bir güvenlik önlemi.
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY ortam değişkeni tanımlanmamış! "
        ".env dosyanızı kontrol edin. "
        "Örnek: SECRET_KEY=python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# ============================================================
# OAuth2PasswordBearer — Token Çıkarıcı
# ============================================================
# Bu nesne, HTTP isteklerindeki "Authorization: Bearer <token>"
# başlığından token'ı otomatik olarak çıkarır.
#
# tokenUrl: Swagger UI'da "Authorize" butonuna basıldığında
# hangi endpoint'e istek atılacağını belirtir. Bu sayede
# /docs üzerinden doğrudan test yapabilirsiniz.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    JWT token'ını doğrulayarak mevcut oturum açmış kullanıcıyı döndürür.
    
    Bu fonksiyon üç adımda çalışır:
    1. Token'ı imza ve süre kontrolüyle decode eder
    2. Token payload'ından kullanıcı email'ini (sub claim) okur
    3. Veritabanında kullanıcıyı bulur ve döndürür
    
    Herhangi bir adımda hata olursa 401 Unauthorized döner.
    
    NEDEN 401 (Unauthorized) DEĞİL 403 (Forbidden)?
    401: "Kim olduğunu bilmiyorum" — kimlik doğrulama başarısız
    403: "Kim olduğunu biliyorum ama yetkisiz" — yetkilendirme başarısız
    Bu fonksiyon kimlik doğrulama yaptığı için 401 döndürür.
    """
    # Kimlik doğrulama hatası için standart yanıt hazırla
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Kimlik bilgileri doğrulanamadı. Lütfen tekrar giriş yapın.",
        headers={"WWW-Authenticate": "Bearer"},
        # WWW-Authenticate başlığı: OAuth2 standardı gereği eklenir
        # Tarayıcı ve istemcilere "Bearer token kullan" der
    )

    try:
        # JWT token'ını decode et — imza ve süre otomatik kontrol edilir
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # "sub" (subject) claim'i oku — biz bunu email olarak doldurduk
        email: Optional[str] = payload.get("sub")

        if email is None:
            raise credentials_exception

        # Decode edilmiş veriyi TokenData şemasına çevir
        token_data = TokenData(sub=email)

    except JWTError:
        # Token geçersiz, süresi dolmuş veya imza hatalı
        raise credentials_exception

    # Veritabanında kullanıcıyı bul
    user = db.query(User).filter(User.email == token_data.sub).first()

    if user is None:
        raise credentials_exception

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Mevcut kullanıcıyı döndürür, ancak hesap aktif değilse 400 döner.
    
    get_current_user üzerine inşa edilen bu dependency, hesap
    durum kontrolünü merkezi bir yerden yönetir.
    Kullanıcı banlı/deaktif edilmişse erişim engellenir.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hesap devre dışı bırakılmış."
        )
    return current_user
