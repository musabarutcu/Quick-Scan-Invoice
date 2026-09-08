# ============================================================
# services/ocr_service.py — OCR Servis Katmanı
# ============================================================
# Bu servis, fatura görselinden ham metin çıkarma işini yapar.
# Desteklenen formatlar: PNG, JPG, WEBP (görüntüler) ve PDF.
#
# NEDEN GÖRÜNTÜ ÖN İŞLEME (PRE-PROCESSING)?
# Tesseract OCR, temiz, yüksek kontrastlı siyah-beyaz görüntülerde
# en iyi sonucu verir. Gerçek dünya fatura fotoğrafları ise:
#   - Sararmış kağıt, renkli arka plan içerebilir (renk gürültüsü)
#   - Yetersiz ışık altında çekilmiş, düşük kontrastlı olabilir
#   - Hafif bulanık veya handshake ile titremiş olabilir
#
# Ön işleme adımları bu sorunları çözer:
#   1. Gri Tonlama: Renk bilgisini kaldırır. OCR için gereksiz
#      renk katmanları Tesseract'ı yanıltabilir. Gri tonlama,
#      karakter kenarlarını belirginleştirir.
#   2. Kontrast Artırma: Metin ile arka plan arasındaki fark artar.
#      Düşük kontrastlı görüntülerde Tesseract karakterleri
#      arka plandan ayırt edemez.
#   3. Keskinleştirme: Odak dışı çekilmiş fotoğraflardaki
#      bulanıklığı giderir, karakter kenarlarını netleştirir.
#
# REFERANS: Tesseract resmi dokümantasyonu "Improving Quality" bölümü:
# https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html
# ============================================================

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

# ============================================================
# Tesseract Konfigürasyonu
# ============================================================
# Windows'ta Tesseract binary'si PATH'te olmayabilir.
# TESSERACT_PATH ortam değişkeni ayarlıysa, pytesseract'a
# doğrudan yolu bildiririz. Linux/Mac'te bu gerekli değildir.
TESSERACT_PATH = os.getenv("TESSERACT_PATH", "")
OCR_LANGUAGE   = os.getenv("OCR_LANGUAGE", "tur+eng")

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter

    # Windows için Tesseract binary yolunu ayarla
    if TESSERACT_PATH and Path(TESSERACT_PATH).exists():
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

    TESSERACT_AVAILABLE = True

except ImportError:
    TESSERACT_AVAILABLE = False
    print("[UYARI] pytesseract veya Pillow kurulu değil. OCR devre dışı.")
    print("         pip install pytesseract Pillow")
    print("         Ayrıca Tesseract binary kurulmalı: winget install UB-Mannheim.TesseractOCR")

try:
    from pdf2image import convert_from_path
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    print("[UYARI] pdf2image kurulu değil. PDF desteği devre dışı.")
    print("         pip install pdf2image")
    print("         Ayrıca poppler-utils kurulmalı.")


# ============================================================
# Görüntü Ön İşleme
# ============================================================

def preprocess_image(image: "Image.Image") -> "Image.Image":
    """
    Tesseract doğruluğunu artırmak için görüntüyü ön işler.

    Adımlar ve gerekçeleri:

    1. GRİ TONLAMA (Grayscale):
       RGB görüntüdeki her piksel (R, G, B) üçlüsünden tek bir
       parlaklık değerine dönüştürülür. Sarı kağıt, mavi logo gibi
       renkler OCR'yi yanıltabilir. Gri tonlama bu renk bilgisini
       nötralize eder ve karakter kenarlarını belirgin kılar.

    2. KONTRAST ARTIRMA (Contrast Enhancement):
       ImageEnhance.Contrast(2.0) — orijinal kontrastı 2 katına çıkarır.
       Zayıf ışıkta çekilmiş fotoğraflarda metin ve arka plan
       birbirine çok yakın gri tonlarda olabilir. Kontrast artırılınca
       metin siyaha, arka plan beyaza yaklaşır; Tesseract daha kolay okur.

    3. KESKİNLEŞTİRME (Sharpening):
       ImageFilter.SHARPEN filtresi, bulanık kenarlardaki piksel
       geçişlerini sertleştirir. Titrek el ile çekilmiş fotoğraflardaki
       "blur" etkisini azaltır, karakter sınırlarını netleştirir.

    Args:
        image: PIL Image nesnesi (herhangi bir modda)

    Returns:
        Ön işlenmiş PIL Image nesnesi (grayscale)
    """
    if not TESSERACT_AVAILABLE:
        raise RuntimeError("pytesseract veya Pillow kurulu değil.")

    # Adım 1: Gri tonlamaya çevir
    # "L" modu = 8-bit grayscale (her piksel 0-255 arası tek değer)
    gray = image.convert("L")

    # Adım 2: Kontrast artır
    # factor=2.0: Orijinalin 2 katı kontrast
    # factor=1.0 değişiklik yok, <1.0 azaltır, >1.0 artırır
    enhanced = ImageEnhance.Contrast(gray).enhance(2.0)

    # Adım 3: Keskinleştir
    # SHARPEN: Komşu piksellerle ağırlıklı fark hesaplayarak kenarları vurgular
    sharpened = enhanced.filter(ImageFilter.SHARPEN)

    return sharpened


# ============================================================
# Görüntü Dosyasından OCR
# ============================================================

def extract_text_from_image(file_path: str) -> str:
    """
    Görüntü dosyasından (PNG, JPG, WEBP) Tesseract ile metin çıkarır.

    Args:
        file_path: Görüntü dosyasının tam yolu

    Returns:
        Çıkarılan ham metin (str). Boş string döner ama exception fırlatmaz.

    Raises:
        RuntimeError: pytesseract kurulu değilse
        FileNotFoundError: Dosya bulunamazsa
        Exception: Tesseract binary bulunamazsa veya okuma hatası
    """
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(
            "pytesseract veya Pillow kurulu değil. "
            "pip install pytesseract Pillow komutu ile kurun."
        )

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Görüntü dosyası bulunamadı: {file_path}")

    # Görüntüyü aç
    image = Image.open(path)

    # Ön işleme uygula
    processed = preprocess_image(image)

    # Tesseract ile OCR
    # lang: Birden fazla dil "+" ile birleştirilir (tur+eng)
    # config: --oem 3 = LSTM (varsayılan), --psm 6 = Tek düzgün metin bloğu varsayımı
    # psm 6, fatura gibi yapısal belgeler için uygundur.
    # PSM değerleri: https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html
    text = pytesseract.image_to_string(
        processed,
        lang=OCR_LANGUAGE,
        config="--oem 3 --psm 6"
    )

    return text.strip()


# ============================================================
# PDF Dosyasından OCR
# ============================================================

def extract_text_from_pdf(file_path: str) -> str:
    """
    PDF dosyasından tüm sayfalardaki metni çıkarır.

    NEDEN PDF'İ GÖRÜNTÜYE ÇEVİRİYORUZ?
    Tesseract bir görüntü işleme aracıdır — PDF gibi vektör belgelerini
    doğrudan okuyamaz. pdf2image kütüphanesi, PDF'in her sayfasını
    yüksek çözünürlüklü PIL Image nesnesine çevirir. Sonra her sayfa
    aynı OCR + ön işleme akışından geçirilir.

    Çok sayfalı PDF: Her sayfa ayrı ayrı OCR'lanır,
    sonuçlar birleştirilir (sayfa aralarına "---" konulur).

    Args:
        file_path: PDF dosyasının tam yolu

    Returns:
        Tüm sayfaların birleştirilmiş ham metni

    Raises:
        RuntimeError: pdf2image kurulu değilse
        FileNotFoundError: Dosya bulunamazsa
    """
    if not PDF_SUPPORT:
        raise RuntimeError(
            "pdf2image kurulu değil. pip install pdf2image\n"
            "Ayrıca poppler-utils kurulmalı:\n"
            "  Windows: https://github.com/oschwartz10612/poppler-windows\n"
            "  Ubuntu:  apt install poppler-utils"
        )

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF dosyası bulunamadı: {file_path}")

    # PDF'i sayfa sayfa görüntüye çevir
    # dpi=300: 300 DPI, baskı kalitesi görüntüler — OCR için idealdir.
    # Düşük DPI (72-96) ekran için yeterlidir ama OCR doğruluğunu düşürür.
    pages: List["Image.Image"] = convert_from_path(file_path, dpi=300)

    page_texts = []
    for page_num, page_image in enumerate(pages, start=1):
        # Her sayfayı aynı ön işleme + OCR akışından geçir
        processed = preprocess_image(page_image)
        text = pytesseract.image_to_string(
            processed,
            lang=OCR_LANGUAGE,
            config="--oem 3 --psm 6"
        )
        if text.strip():
            page_texts.append(f"--- Sayfa {page_num} ---\n{text.strip()}")

    return "\n\n".join(page_texts)


# ============================================================
# Ana Dispatcher — Dosya Uzantısına Göre Yönlendirme
# ============================================================

def extract_text(file_path: str) -> str:
    """
    Dosya uzantısına bakarak uygun OCR fonksiyonunu çağırır.

    Bu "dispatcher" pattern, çağıran kodun (pipeline.py) dosya
    tipini bilmesini gerektirmez. Tek bir arayüz arkasında
    farklı implementasyonları gizler (Facade pattern).

    Desteklenen uzantılar:
      - .jpg, .jpeg, .png, .webp → extract_text_from_image()
      - .pdf                      → extract_text_from_pdf()

    Args:
        file_path: Dosyanın tam yolu

    Returns:
        Ham OCR metni

    Raises:
        ValueError: Desteklenmeyen dosya uzantısı
        RuntimeError: Gerekli kütüphaneler kurulu değilse
    """
    extension = Path(file_path).suffix.lower()

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    PDF_EXTENSIONS   = {".pdf"}

    if extension in IMAGE_EXTENSIONS:
        return extract_text_from_image(file_path)
    elif extension in PDF_EXTENSIONS:
        return extract_text_from_pdf(file_path)
    else:
        raise ValueError(
            f"Desteklenmeyen dosya uzantısı: '{extension}'. "
            f"Desteklenenler: {IMAGE_EXTENSIONS | PDF_EXTENSIONS}"
        )
