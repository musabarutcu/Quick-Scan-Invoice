# ============================================================
# services/ai_extraction_service.py — AI Destekli Veri Çıkarma
# ============================================================
# Bu servis, OCR'dan çıkan düzensiz ham metni yapılandırılmış
# JSON verisine dönüştürür. Google Gemini Flash modeli üzerinden
# LangChain kütüphanesi aracılığıyla çalışır.
#
# NEDEN PYDANTIC İLE AI ÇIKTISINI DOĞRULUYORUZ?
# Büyük Dil Modelleri (LLM) deterministik değildir. Aynı prompt'a
# farklı zamanlarda farklı formatlar döndürebilir:
#   - Bazen JSON, bazen JSON'u saran markdown kodu bloğu
#   - Bazen "total_amount": "1.500 TL" (string), bazen 1500.0 (float)
#   - Bazen beklenen alanlar eksik veya yanlış yazılmış
#
# Pydantic ile structured output (with_structured_output) kullandığımızda:
#   1. Model JSON Schema'yı alır, çıktısını bu şemaya göre üretir
#   2. LangChain otomatik parse edip Pydantic nesnesine çevirir
#   3. Doğrulama başarısız olursa exception fırlatır (yakalıyoruz)
#   4. Tip güvencesi: total_amount her zaman float gelir
#
# Bu yaklaşım olmadan AI çıktısını elle parse etmek:
#   - Güvenilmez (model formatı değiştirebilir)
#   - Güvensiz (tip dönüşüm hataları)
#   - Bakımı zor (farklı format varyantları için özel kod)
#
# NEDEN TIMEOUT EKLİYORUZ?
# Dış API çağrıları (Gemini) ağ gecikmesi, sunucu yüküne bağlı
# olarak bazen çok uzun sürebilir. Timeout olmadan BackgroundTask
# sonsuza kadar askıda kalır. Timeout'ta:
#   1. API çağrısı iptal edilir
#   2. Invoice.status = "failed" set edilir
#   3. Kullanıcı "manuel giriş" mesajı görür
# ============================================================

import os
import signal
import threading
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()

GOOGLE_API_KEY   = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
AI_TIMEOUT       = int(os.getenv("AI_TIMEOUT_SECONDS", "60"))

# ============================================================
# Pydantic Şemaları — AI Çıktı Yapıları
# ============================================================
# Bu şemalar iki amaç taşır:
# 1. LangChain'e "model tam olarak bu yapıyı döndürmeli" der
# 2. Dönen veriyi Python nesnesi olarak güvenle kullanabiliriz

class LineItemExtracted(BaseModel):
    """
    Tek bir fatura kalemi.
    description: Kalemin tanımı (ör: "Ofis koltuğu x2")
    amount: Kalemin tutarı (sayısal)
    """
    description: str = Field(description="Fatura kaleminin açıklaması veya adı")
    amount: float    = Field(description="Kalemin tutarı (yalnızca sayı, para birimi dahil etme)")


class InvoiceExtracted(BaseModel):
    """
    Faturadan çıkarılan yapılandırılmış veri.
    Tüm alanlar opsiyoneldir — OCR metni eksik bilgi içerebilir.
    """
    vendor_name:   Optional[str]                 = Field(None, description="Fatura satıcısı / tedarikçi adı")
    invoice_date:  Optional[str]                 = Field(None, description="Fatura tarihi, YYYY-MM-DD formatında")
    total_amount:  Optional[float]               = Field(None, description="Toplam tutar (yalnızca sayı, para birimi dahil etme)")
    line_items:    List[LineItemExtracted]        = Field(default_factory=list, description="Fatura kalemleri listesi")

    @field_validator("invoice_date")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        """
        Tarih formatını doğrular. Model yanlış format dönerse None'a çekeriz.
        Örn: "15/01/2024" → None (YYYY-MM-DD değil, AI'dan YYYY-MM-DD istedik ama dönmedi)
        Bu sayede hatalı tarih veritabanına gitmez.
        """
        if v is None:
            return v
        try:
            from datetime import datetime
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            # Hatalı format: None döneriz, fatura yine de kaydedilir
            return None


# ============================================================
# Kategorizasyon Pydantic Şeması
# ============================================================

class LineItemCategorized(BaseModel):
    """Bir kalemin hangi kategoriye atandığını tutar."""
    description: str
    category:    str


class CategorizationResult(BaseModel):
    """AI'ın tüm kalemlere yaptığı kategorizasyon sonucu."""
    items: List[LineItemCategorized] = Field(default_factory=list)


# ============================================================
# LangChain / Gemini Başlatma
# ============================================================

def _get_llm():
    """
    LangChain ChatGoogleGenerativeAI nesnesini oluşturur.

    Neden ayrı bir fonksiyon?
    Import zamanında LangChain import edilirse, GOOGLE_API_KEY
    olmayan ortamlarda uygulama başlatılamaz. Lazy initialization
    (geç yükleme) ile sadece gerçekten çağrıldığında import edilir.

    Bu pattern özellikle test ortamlarında faydalıdır:
    AI servisini mock'layabilmek için LangChain import'unu engelleyebiliriz.
    """
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        raise RuntimeError(
            "langchain-google-genai kurulu değil.\n"
            "pip install langchain-google-genai"
        )

    if not GOOGLE_API_KEY:
        raise RuntimeError(
            "GOOGLE_API_KEY ortam değişkeni tanımlanmamış!\n"
            ".env dosyanıza GOOGLE_API_KEY=... ekleyin.\n"
            "Ücretsiz API key: https://aistudio.google.com/"
        )

    return ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY,
        # temperature=0: Deterministik çıktı istiyoruz.
        # Fatura verisi çıkarmak yaratıcılık gerektirmez;
        # mümkün olan en tutarlı yanıtı istiyoruz.
        temperature=0,
    )


# ============================================================
# Timeout Yardımcısı
# ============================================================

class TimeoutException(Exception):
    """AI çağrısı zaman aşımına uğradığında fırlatılır."""
    pass


def _run_with_timeout(func, timeout_seconds: int, *args, **kwargs):
    """
    Verilen fonksiyonu timeout_seconds içinde çalıştırır.
    Süre aşılırsa TimeoutException fırlatır.

    Threading kullanarak platform bağımsız timeout implementasyonu:
    signal.alarm() yalnızca Unix'te çalışır; bu implementasyon
    Windows'ta da güvenilir şekilde çalışır.

    NEDEN TIMEOUT?
    Gemini API yoğun dönemlerde 30-60 saniye bekleme yapabilir.
    BackgroundTask'ın sonsuza kadar askıda kalmasını önleriz.
    Timeout sonrası Invoice.status="failed" set edilir ve
    kullanıcı "manuel giriş" mesajı görür — kullanıcı deneyimi korunur.
    """
    result = [None]
    exception = [None]

    def target():
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        raise TimeoutException(
            f"AI servisi {timeout_seconds} saniye içinde yanıt vermedi."
        )

    if exception[0] is not None:
        raise exception[0]

    return result[0]


# ============================================================
# Ana Fonksiyon: Fatura Verisi Çıkarma
# ============================================================

def extract_invoice_data(raw_text: str) -> InvoiceExtracted:
    """
    OCR'dan çıkan ham metni Gemini ile analiz edip yapılandırılmış
    fatura verisi döndürür.

    NEDEN WITH_STRUCTURED_OUTPUT?
    Serbest metin üretimi yerine, modele JSON Schema vererek
    çıktıyı garantilemek istiyoruz. LangChain'in with_structured_output()
    metodu şunları yapar:
      1. Pydantic modelinden JSON Schema üretir
      2. Bu schema'yı Gemini'ye "response schema" olarak gönderir
      3. Modelin döndürdüğü JSON'u otomatik parse edip Pydantic nesnesine çevirir
      4. Parse hatası olursa exception fırlatır

    Bu yaklaşım, manuel JSON parse'dan çok daha güvenilir.

    Args:
        raw_text: OCR'dan gelen ham metin (düzensiz, gürültülü olabilir)

    Returns:
        InvoiceExtracted: Doğrulanmış yapılandırılmış veri

    Raises:
        RuntimeError: LangChain kurulu değil veya API key eksik
        TimeoutException: AI yanıt süresi aşıldı
        Exception: AI parse hatası veya API hatası
    """
    if not raw_text or not raw_text.strip():
        # Boş OCR metni — tüm alanlar None olan boş veri dön
        return InvoiceExtracted()

    llm = _get_llm()

    # with_structured_output: Pydantic modelini JSON Schema'ya çevirip
    # modele bağlar. method="json_schema" en güvenilir yöntem.
    structured_llm = llm.with_structured_output(
        InvoiceExtracted,
        method="json_schema",
    )

    prompt = f"""Sen bir muhasebe uzmanısın. Aşağıdaki fatura metnini analiz et ve bilgileri çıkar.

Fatura metni (OCR ile çıkarılmış, hatalı karakterler içerebilir):
---
{raw_text[:4000]}
---

Lütfen şu bilgileri çıkar:
- vendor_name: Tedarikçi/satıcı firmanın adı
- invoice_date: Fatura tarihi (YYYY-MM-DD formatında)
- total_amount: Toplam tutar (yalnızca sayı, TL/₺/KDV dahil toplam)
- line_items: Faturadaki tek tek kalemler (description ve amount)

Eğer bir bilgi metinde yoksa veya bulamıyorsan, o alanı null bırak.
Para birimi sembollerini (TL, ₺, $) sayıya dahil etme.
"""

    def _call_api():
        return structured_llm.invoke(prompt)

    # Timeout ile çağır
    result = _run_with_timeout(_call_api, AI_TIMEOUT)
    return result


# ============================================================
# Kategorizasyon
# ============================================================

def categorize_line_items(
    line_items: List[LineItemExtracted],
    category_names: List[str]
) -> dict[str, str]:
    """
    Her fatura kalemini mevcut kategorilerden birine atar.

    NEDEN AI İLE KATEGORİZASYON?
    Kural tabanlı kategorizasyon (kelime eşleştirme) kırılgandır:
    "Uçak bileti İstanbul-Ankara" → "Ulaşım" eşleşmesini bulmak
    birçok varyasyon için ayrı kural gerektirir. AI bu bağlamsal
    anlamayı çok daha iyi yapar.

    Eşleşme bulunmayan kalemler "Diğer" kategorisine düşer.
    "Diğer" kategorisinin DB'de mevcut olduğu varsayılır.

    Args:
        line_items: AI'ın çıkardığı fatura kalemleri
        category_names: Veritabanındaki mevcut kategori adları listesi

    Returns:
        dict: {kalem_description: kategori_adı}

    Raises:
        TimeoutException: API zaman aşımı
        Exception: API veya parse hatası
    """
    if not line_items or not category_names:
        return {}

    llm = _get_llm()
    structured_llm = llm.with_structured_output(
        CategorizationResult,
        method="json_schema",
    )

    items_text = "\n".join(
        f"- {item.description} ({item.amount} TL)"
        for item in line_items
    )

    categories_text = ", ".join(f'"{c}"' for c in category_names)

    prompt = f"""Aşağıdaki fatura kalemlerini verilen kategorilerden birine ata.

Kategoriler: {categories_text}

Fatura kalemleri:
{items_text}

Her kalem için en uygun kategoriyi seç. Eğer uygun kategori yoksa "Diğer" kullan.
description alanında kalemin tam açıklamasını (yukarıdaki ile aynı) yaz.
"""

    def _call_api():
        return structured_llm.invoke(prompt)

    result = _run_with_timeout(_call_api, AI_TIMEOUT)

    # Sonuçları dict'e çevir: {description: category}
    return {item.description: item.category for item in result.items}
