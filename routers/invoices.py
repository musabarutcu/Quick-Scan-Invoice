# ============================================================
# routers/invoices.py — Fatura CRUD Router'ı (Aşama 2 Güncellemesi)
# ============================================================
# Bu router, fatura oluşturma, listeleme, güncelleme, silme ve
# dosya yükleme endpoint'lerini barındırır.
#
# AŞAMA 2 EKLENTİLERİ:
#   - POST /invoices/upload → BackgroundTasks ile OCR+AI pipeline tetikleme
#   - GET  /invoices/{id}/status → Polling için durum endpoint'i
#   - GET  /invoices/{id}/detail → Fatura detay HTML sayfası
#
# GÜVENLİK — IDOR KORUMASI:
#   IDOR (Insecure Direct Object Reference): Kullanıcının başka
#   bir kullanıcının kaynağına doğrudan ID ile erişmesi.
#   Örnek: /invoices/42 — kullanıcı 42 ID'li faturanın sahibi
#   değilse 403 almalıdır.
#
#   Bu korumanın her endpoint'te uygulanması zorunludur:
#   - GET /invoices       → owner_id filtresi ile sadece kendi listesi
#   - GET /invoices/{id}  → owner_id kontrolü, değilse 403
#   - PUT /invoices/{id}  → owner_id kontrolü, değilse 403
#   - DELETE /invoices/{id} → owner_id kontrolü, değilse 403
# ============================================================

import os
import uuid
from pathlib import Path

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException,
    Request, UploadFile, status
)
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_active_user
from models import Invoice, InvoiceStatus, User
from schemas import (
    InvoiceCreate, InvoiceDetailResponse,
    InvoiceResponse, InvoiceStatusResponse, InvoiceUpdate, UploadResponse
)
from services.pipeline import process_invoice_pipeline

templates = Jinja2Templates(directory="templates")

# Yükleme klasörü — .env'den al, yoksa "uploads" kullan
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))

router = APIRouter(prefix="/invoices", tags=["Invoices"])


# ============================================================
# Yardımcı Fonksiyonlar
# ============================================================

def get_invoice_or_404(invoice_id: int, current_user: User, db: Session) -> Invoice:
    """
    Faturayı getirir; bulunamazsa veya sahibi değilse hata fırlatır.
    
    Bu yardımcı fonksiyon, birden fazla endpoint'te aynı kontrolü
    tekrar yazmamak için DRY (Don't Repeat Yourself) prensibini uygular.
    
    Neden 404 (Not Found) + 403 (Forbidden) ayrımı yapıyoruz?
    - 404: Fatura hiç yok → bilgi sızdırmaz
    - 403: Fatura var ama yetkin yok → saldırgan hangi ID'lerin
           var olduğunu tahmin edebilir
    
    Güvenlik tercihi: Yetki yoksa 404 dön, 403 değil.
    Bu sayede saldırgan fatura var mı yok mu öğrenemez.
    """
    invoice = db.query(Invoice).filter(
        Invoice.id == invoice_id,
        Invoice.owner_id == current_user.id  # ← IDOR koruması
    ).first()

    if not invoice:
        # Hem "bulunamadı" hem "yetki yok" durumunda 404 dönüyoruz.
        # Saldırgan "bu ID var ama senin değil" bilgisini alamaz.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fatura bulunamadı."
        )

    return invoice


# ============================================================
# Sayfa Endpoint'leri (HTML)
# ============================================================

@router.get("/list", response_class=HTMLResponse, include_in_schema=False)
async def invoices_page(request: Request):
    """Fatura listesi sayfasını render eder."""
    return templates.TemplateResponse("invoices.html", {"request": request})


@router.get("/add", response_class=HTMLResponse, include_in_schema=False)
async def add_invoice_page(request: Request):
    """Fatura ekleme formunu render eder."""
    return templates.TemplateResponse("add_invoice.html", {"request": request})


@router.get("/{invoice_id}/detail", response_class=HTMLResponse, include_in_schema=False)
async def invoice_detail_page(invoice_id: int, request: Request):
    """
    Fatura detay sayfasını render eder.
    
    Sayfanın JavaScript'i bu route'un işaret ettiği fatura ID'sini
    kullanarak /invoices/{id} API endpoint'inden veriyi çeker.
    Status "processing" ise otomatik polling başlatılır.
    """
    return templates.TemplateResponse(
        "invoice_detail.html",
        {"request": request, "invoice_id": invoice_id}
    )


# ============================================================
# API Endpoint'leri — CRUD
# ============================================================

@router.get("", response_model=list[InvoiceResponse])
def list_invoices(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Kullanıcının kendi faturalarını listeler.
    
    KRİTİK GÜVENLİK NOTU:
    Burada `Invoice.owner_id == current_user.id` filtresi
    veritabanı sorgusunun parçası. Bu filtre olmadan tüm
    kullanıcıların faturaları döner — ciddi güvenlik açığı!
    
    SQLAlchemy filter() SQL'deki WHERE koşuluna çevrilir:
    SELECT * FROM invoices WHERE owner_id = :current_user_id
    """
    invoices = db.query(Invoice).filter(
        Invoice.owner_id == current_user.id  # ← Her zaman bu filtre!
    ).order_by(Invoice.created_at.desc()).all()

    return invoices


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_invoice(
    invoice_data: InvoiceCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Manuel fatura oluşturur (bu aşamada OCR olmadan).
    
    owner_id otomatik olarak current_user.id'ye ayarlanır.
    Kullanıcı başka birinin adına fatura oluşturamaz.
    """
    new_invoice = Invoice(
        owner_id=current_user.id,   # ← Sahiplik otomatik atanır
        vendor_name=invoice_data.vendor_name,
        invoice_date=invoice_data.invoice_date,
        total_amount=invoice_data.total_amount,
        status=InvoiceStatus.pending,
    )

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)

    return new_invoice


@router.get("/{invoice_id}/status", response_model=InvoiceStatusResponse)
def get_invoice_status(
    invoice_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Fatura işleme durumunu döndürür.

    Bu endpoint, frontend'in polling için kullandığı küçük ve hızlı bir endpoint'tir.
    Yanıt boyutu kasıtlı olarak küçük tutulmuştur:
      - Detaylı fatura verisi (line_items, raw_ocr_text) yok
      - Sadece durum, temel bilgiler ve hata mesajı döner

    NEDEN POLLİNG? (WebSocket veya SSE yerine)
    WebSocket: Çift yönlü kalıcı bağlantı — fatura durumu için overkill.
    SSE (Server-Sent Events): Sunucudan istemciye tek yönlü stream.
    Polling: Basit, HTTP üzerinde çalışır, stateless, debug kolaydır.

    Fatura işleme 10-30 saniyede tamamlanır, 5 saniyelik polling
    yeterli güncellik sağlar. Onlarca eşzamanlı kullanıcı için
    polling kabul edilebilir yük oluşturur.

    Frontend setInterval() ile 5 saniyede bir bu endpoint'i çağırır.
    Status "processed" veya "failed" olunca polling durur.

    IDOR koruması: get_invoice_or_404 içinde owner_id kontrolü var.
    """
    invoice = get_invoice_or_404(invoice_id, current_user, db)
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
def get_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Tek bir faturanın detaylarını döndürür.
    
    GET endpoint'inde de owner kontrolü yapılır!
    Bu kontrolü atlayan sistemlerde /invoices/1, /invoices/2...
    deneyen bir saldırgan tüm faturaları okuyabilir.
    """
    invoice = get_invoice_or_404(invoice_id, current_user, db)
    return invoice


@router.put("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(
    invoice_id: int,
    invoice_data: InvoiceUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Fatura bilgilerini günceller.
    Sadece gönderilen (None olmayan) alanlar güncellenir — partial update.
    
    Partial update pattern:
    Kullanıcı sadece status göndermek isteyebilir. Tüm alanları
    göndermek zorunda kalması kullanıcı deneyimini bozar.
    model_dump(exclude_unset=True) sadece gönderilen alanları alır.
    """
    invoice = get_invoice_or_404(invoice_id, current_user, db)

    # Pydantic v2'de exclude_unset=True: Kullanıcının göndermediği
    # (varsayılan None olan) alanları güncelleme verilerine dahil etme
    update_data = invoice_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(invoice, field, value)

    db.commit()
    db.refresh(invoice)

    return invoice


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(
    invoice_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Faturayı siler.
    
    HTTP 204 No Content: Silme işlemi başarılı ama döndürülecek
    içerik yok. REST standardına göre DELETE başarılıysa 204 dönmeli.
    
    Cascade delete (models.py'de tanımlı): Invoice silindiğinde
    bağlı LineItem'lar da otomatik silinir.
    """
    invoice = get_invoice_or_404(invoice_id, current_user, db)

    db.delete(invoice)
    db.commit()

    # 204 No Content: return None veya hiçbir şey döndürme
    return None


# ============================================================
# Dosya Yükleme — OCR + AI Pipeline Tetikleyici
# ============================================================

@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Fatura/makbuz dosyası (PDF, PNG, JPG)"),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Fatura dosyasını yükler ve OCR+AI işlemini arka planda başlatır.

    NEDEN BACKGROUNDTASKS?
    Senkron (bloklayan) tasarım şöyle çalışır:
      1. Kullanıcı dosyayı yükler
      2. Sunucu OCR çalıştırır (5-10 saniye)
      3. Sunucu AI çağırır (10-30 saniye)
      4. Tüm işlem bitince HTTP yanıtı döner

    Bu tasarımın sorunları:
      - Kullanıcı 30-40 saniye beyaz ekran görür (berbat UX)
      - Tarayıcı genellikle 30sn sonra timeout yapar, kullanıcı "hata" görür
      - uvicorn thread havuzu dolar, diğer kullanıcılar bekler

    BackgroundTasks çözümü:
      1. Kullanıcı dosyayı yükler
      2. Dosya diske kaydedilir (hızlı, <1sn)
      3. Invoice "processing" statüsüyle hemen oluşturulur
      4. HTTP 202 Accepted yanıtı döner — kullanıcı hemen yanıt alır!
      5. Arka planda OCR + AI çalışır (kullanıcıyı bloklamaz)
      6. Kullanıcı /status endpoint'i ile durumu takip eder

    HTTP 202 Accepted: "İstek alındı, işleniyor" anlamına gelir.
    201 Created'dan farkı: Kaynak henüz tam oluşturulmadı, işlem devam ediyor.

    BACKGROUNDTASKS vs CELERY:
    FastAPI'nin BackgroundTasks'ı aynı process'te, extra thread'de çalışır.
    Basit use case'ler için idealdir. Büyük ölçekte Celery + Redis gibi
    bir görev kuyruğu sistemi tercih edilir. Bu proje için BackgroundTasks yeterli.
    """
    # Kabul edilen dosya tipleri
    ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Desteklenmeyen dosya tipi: {file.content_type}. "
                   f"Kabul edilenler: JPEG, PNG, WebP, PDF"
        )

    # Yükleme klasörünü oluştur (yoksa)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Güvenli dosya adı oluştur — orijinal adı kullanma!
    # Neden? "../../.env" gibi path traversal saldırılarını önler.
    # uuid4(): Kriptografik olarak güvenli rastgele ID
    file_extension = Path(file.filename).suffix.lower() if file.filename else ".bin"
    safe_filename = f"{current_user.id}_{uuid.uuid4().hex}{file_extension}"
    file_path = UPLOAD_DIR / safe_filename

    # Dosyayı oku ve boyut kontrolü yap
    file_content = await file.read()

    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Dosya boyutu 10 MB'ı geçemez."
        )

    if len(file_content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Boş dosya yüklenemez."
        )

    # Dosyayı diske yaz
    with open(file_path, "wb") as f:
        f.write(file_content)

    # Veritabanında Invoice kaydı oluştur
    # status=processing: Arka plan işlemi hemen başlayacak
    new_invoice = Invoice(
        owner_id=current_user.id,
        status=InvoiceStatus.processing,
        file_path=str(file_path),
        # vendor_name, invoice_date, total_amount → OCR + AI dolduracak
    )

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)

    # ── BackgroundTask Ekle ──────────────────────────────────
    # Bu satır HTTP yanıtı döndükten SONRA pipeline'ı başlatır.
    # db session'ı BackgroundTask'a geçirmek önemli: FastAPI,
    # request bittikten sonra orijinal session'ı kapatır.
    # Bu yüzden pipeline kendi içinde db'yi yönetir.
    #
    # NOT: Büyük üretim sistemlerinde Celery + Redis kullanın.
    # BackgroundTasks, sunucu restart olursa yarıda kalabilir.
    background_tasks.add_task(
        process_invoice_pipeline,
        invoice_id=new_invoice.id,
        file_path=str(file_path)
    )

    return UploadResponse(
        message="Dosya yüklendi. OCR + AI işlemi arka planda başlatıldı.",
        invoice_id=new_invoice.id,
        file_path=str(file_path),
        status=new_invoice.status,
    )
