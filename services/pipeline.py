# ============================================================
# services/pipeline.py — OCR + AI İşleme Pipeline Orkestratörü
# ============================================================
# Bu modül, fatura işleme sürecinin tüm adımlarını bir araya getirir:
#
#   1. Invoice.status = "processing" → Kullanıcı işlemin başladığını görür
#   2. OCR       → Ham metni çıkar, Invoice.raw_ocr_text'e yaz
#   3. AI Parse  → Yapılandırılmış veri çıkar (vendor, date, amount, items)
#   4. Invoice   → Çıkarılan verilerle güncelle
#   5. Kategori  → Her line_item için kategori ata
#   6. LineItem  → Veritabanına kaydet
#   7. Invoice.status = "processed"
#
#   Herhangi bir adımda hata → Invoice.status = "failed" + error_message
#
# NEDEN AYRI PIPELINE MODÜLÜ?
# routers/invoices.py HTTP katmanından sorumludur.
# Bu iş mantığını (OCR, AI, DB güncelleme) router'a gömmek:
#   - Router'ı karmaşıklaştırır (Tek Sorumluluk Prensibi ihlali)
#   - Test etmeyi zorlaştırır (HTTP bağımlılığı)
#   - Farklı tetikleyicilerden (API, CLI, cron) çağırmayı engeller
#
# Pipeline, saf bir Python fonksiyonu: HTTP'yi bilmez,
# sadece invoice_id ve db session alır.
# ============================================================

import traceback
from typing import Optional

from sqlalchemy.orm import Session

from models import Category, Invoice, InvoiceStatus, LineItem
from services.ai_extraction_service import (
    TimeoutException,
    categorize_line_items,
    extract_invoice_data,
)
from services.ocr_service import extract_text


from database import SessionLocal

# ============================================================
# Ana Pipeline Fonksiyonu
# ============================================================

def process_invoice_pipeline(
    invoice_id: int,
    file_path: str,
    db: Optional[Session] = None
) -> None:
    """
    Fatura işleme pipeline'ını çalıştırır.

    Bu fonksiyon FastAPI BackgroundTasks tarafından arka planda çağrılır.
    HTTP isteği tamamlandıktan SONRA, ayrı bir thread'de çalışır.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        _run_pipeline(invoice_id, file_path, db)
    finally:
        if own_session and db:
            db.close()


def _run_pipeline(invoice_id: int, file_path: str, db: Session) -> None:
    # ── Adım 0: Invoice'u bul ───────────────────────────────
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        print(f"[PIPELINE ERROR] Invoice #{invoice_id} bulunamadı!")
        return

    try:
        # ── Adım 1: Durumu "processing" yap ─────────────────
        # Kullanıcı arayüzde "İşleniyor" badge'ini hemen görür.
        # Polling mekanizması bu değişikliği 5 saniye içinde yakalar.
        _update_status(invoice, InvoiceStatus.processing, db)
        print(f"[PIPELINE] Invoice #{invoice_id} işleme başladı: {file_path}")

        # ── Adım 2: OCR ─────────────────────────────────────
        # Dosya tipine göre otomatik yönlendirme (ocr_service dispatcher)
        try:
            raw_text = extract_text(file_path)
            invoice.raw_ocr_text = raw_text
            db.commit()
            print(f"[PIPELINE] OCR tamamlandı. Karakter sayısı: {len(raw_text)}")
        except Exception as ocr_err:
            # OCR başarısız: Hata mesajını kaydet ama pipeline'ı durdurma.
            # AI adımı boş metinle de çalışabilir (tüm alanlar None döner).
            raw_text = ""
            invoice.raw_ocr_text = ""
            invoice.error_message = f"OCR hatası: {str(ocr_err)}"
            db.commit()
            print(f"[PIPELINE WARNING] OCR hatası: {ocr_err}")

        # ── Adım 3: AI Veri Çıkarma ──────────────────────────
        try:
            extracted = extract_invoice_data(raw_text)
            print(f"[PIPELINE] AI çıkarma tamamlandı: vendor={extracted.vendor_name}")
        except TimeoutException as te:
            raise RuntimeError(
                f"AI servisi {te} — Lütfen fatura bilgilerini manuel olarak girin."
            )
        except Exception as ai_err:
            raise RuntimeError(
                f"AI veri çıkarma başarısız: {str(ai_err)[:200]}\n"
                "Lütfen fatura bilgilerini manuel olarak girin."
            )

        # ── Adım 4: Invoice'u güncelle ───────────────────────
        # Sadece AI'ın doldurabildiği alanları yaz.
        # Mevcut manuel veriler varsa (vendor_name gibi) üzerine yazma!
        # null check: Kullanıcı önceden manuel giriş yapmış olabilir.
        if extracted.vendor_name and not invoice.vendor_name:
            invoice.vendor_name = extracted.vendor_name
        if extracted.invoice_date and not invoice.invoice_date:
            invoice.invoice_date = extracted.invoice_date
        if extracted.total_amount is not None and invoice.total_amount is None:
            invoice.total_amount = extracted.total_amount

        db.commit()

        # ── Adım 5: Kategorilendirme ─────────────────────────
        if extracted.line_items:
            category_map = _categorize_items(extracted.line_items, db)
        else:
            category_map = {}

        # ── Adım 6: LineItem'ları kaydet ─────────────────────
        if extracted.line_items:
            _save_line_items(invoice, extracted.line_items, category_map, db)
            print(f"[PIPELINE] {len(extracted.line_items)} kalem kaydedildi.")

        # ── Adım 7: Başarı ───────────────────────────────────
        invoice.error_message = None  # Önceki hata varsa temizle
        _update_status(invoice, InvoiceStatus.processed, db)
        print(f"[PIPELINE] Invoice #{invoice_id} başarıyla işlendi.")

    except Exception as err:
        # Herhangi bir adımda beklenmedik hata
        # Kısa hata mesajı (DB'ye kaydedilecek)
        short_msg = str(err)[:500]

        # Uzun stack trace (loglama için)
        full_trace = traceback.format_exc()
        print(f"[PIPELINE ERROR] Invoice #{invoice_id}: {full_trace}")

        # Invoice'u "failed" yap
        try:
            invoice.error_message = short_msg
            _update_status(invoice, InvoiceStatus.failed, db)
        except Exception as db_err:
            # DB güncelleme de başarısız olursa en azından logla
            print(f"[PIPELINE ERROR] Status güncellenemedi: {db_err}")


# ============================================================
# Yardımcı Fonksiyonlar
# ============================================================

def _update_status(invoice: Invoice, status: InvoiceStatus, db: Session) -> None:
    """Invoice durumunu günceller ve DB'ye commit eder."""
    invoice.status = status
    db.commit()
    db.refresh(invoice)


def _categorize_items(line_items, db: Session) -> dict:
    """
    Veritabanındaki kategori listesini alır ve AI kategorilendirme yapar.

    Kategorilendirme başarısız olursa boş dict döner (kalemler "Diğer"e düşer).
    Bu fonksiyon hiçbir zaman exception fırlatmaz — kategorizasyon hatası
    pipeline'ı durdurmamalıdır.
    """
    try:
        # Mevcut kategorileri DB'den al
        categories = db.query(Category).all()
        category_names = [c.name for c in categories]

        if not category_names:
            print("[PIPELINE] Veritabanında kategori yok. Kategorilendirme atlandı.")
            return {}

        return categorize_line_items(line_items, category_names)

    except Exception as e:
        print(f"[PIPELINE WARNING] Kategorilendirme hatası (görmezden geliniyor): {e}")
        return {}


def _save_line_items(
    invoice: Invoice,
    extracted_items,
    category_map: dict,
    db: Session
) -> None:
    """
    AI'ın çıkardığı kalemleri LineItem tablosuna kaydeder.

    Önce mevcut kalemler temizlenir (idempotent davranış):
    Pipeline tekrar çalıştırılırsa (retry) çift kayıt olmaz.

    category_map: {description: category_name} dict'i
    """
    # Mevcut kalemleri temizle (yeniden işleme durumunda çift kayıt önlemi)
    db.query(LineItem).filter(LineItem.invoice_id == invoice.id).delete()
    db.commit()

    for item in extracted_items:
        # Kategoriyi bul
        category_name = category_map.get(item.description, "Diğer")
        category = db.query(Category).filter(Category.name == category_name).first()

        # "Diğer" kategorisi de yoksa None bırak
        if category is None and category_name != "Diğer":
            category = db.query(Category).filter(Category.name == "Diğer").first()

        line_item = LineItem(
            invoice_id=invoice.id,
            description=item.description,
            amount=item.amount,
            category_id=category.id if category else None,
        )
        db.add(line_item)

    db.commit()
