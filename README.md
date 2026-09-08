# QuickScan

<p align="center">
  <img src="static/img/screenshot-logo.png" alt="QuickScan logosu" height="60">
</p>

<p align="center">
  <strong>Faturalarınızı yükleyin → OCR ile otomatik veri çıkarın → AI ile kategorilendirin</strong>
</p>

<p align="center">
  <img src="static/img/screenshot-dashboard.png" alt="QuickScan fatura listesi ana ekranı" width="820">
</p>

---

## QuickScan Nedir?

**QuickScan**, elle fatura girmenin muhasebe süreçlerinde en çok zaman kaybettiren ve en çok hata yapılan adım olmasından yola çıkarak yazıldı. Fikir basit: kullanıcı bir fatura ya da makbuz görselini (PNG, JPG veya PDF) sisteme yükler, QuickScan de gerisini halleder.

Yükleme anında devreye önce **Tesseract OCR** giriyor ve görseldeki ham metni çıkarıyor. Ardından bu metin **Google Gemini** modeline gönderiliyor; Gemini tedarikçi adını, tarihi, toplam tutarı ve fatura kalemlerini ayıklayıp yapılandırılmış, doğrulanmış bir veriye dönüştürüyor. En sonunda her kalem, önceden tanımlı muhasebe kategorilerinden birine otomatik olarak eşleniyor.

Bu işlemin hiçbiri kullanıcıyı bekletmiyor. Dosya yüklendiği anda arka planda bir görev (`BackgroundTask`) başlıyor ve API anında yanıt dönüyor; arayüz de sonucu birkaç saniyede bir polling ile kontrol edip OCR + AI işlemi bittiğinde ekranı güncelliyor.
---

## Kullanılan Teknolojiler


| Teknoloji | Ne İşe Yarıyor | Neden Bu Seçildi |
|---|---|---|
| **FastAPI** | HTTP API framework'ü; endpoint'ler, request/response validasyonu ve Swagger dokümantasyonu | Pydantic entegrasyonu, native async desteği ve otomatik OpenAPI belgesi |
| **SQLAlchemy** | ORM; Python sınıflarını SQL tablolarına bağlar | Raw SQL yerine tip güvenceli sorgular; Alembic ile sorunsuz migration desteği |
| **Alembic** | Veritabanı şema migration aracı | Model değişikliklerini otomatik SQL migration'ına dönüştürür; üretimde güvenli şema güncellemesi sağlar |
| **JWT (python-jose)** | Stateless kimlik doğrulama token'ı | Sunucu tarafında session saklamaya gerek yok; yatay ölçeklenebilir |
| **passlib/bcrypt** | Şifre hash'leme | bcrypt kasıtlı olarak yavaştır; bu da GPU ile brute-force saldırısını pratikte imkânsız kılar |
| **Tesseract + pytesseract** | Görsel → metin OCR motoru | Açık kaynak, Türkçe dahil 100+ dil desteği, tamamen ücretsiz |
| **Pillow** | Görüntü ön işleme (gri ton, kontrast, keskinleştirme) | OCR öncesi görüntü kalitesini artırarak tanıma doğruluğunu yükseltir |
| **pdf2image** | PDF sayfalarını PIL Image nesnesine çevirir | Tesseract PDF okuyamadığı için her sayfa önce görüntüye çevrilip OCR'dan geçirilir |
| **LangChain + Gemini** | AI iş akışı; OCR metnini yapılandırılmış JSON'a dönüştürür | `with_structured_output` + Pydantic şemasıyla doğrulanmış, garantili çıktı |
| **Jinja2** | HTML template motoru | Miras tabanlı şablonlar (`layout.html`) tekrar eden kodu önler |
| **pytest + httpx** | Test framework'ü | FastAPI `TestClient` ile in-memory DB üzerinde izole, hızlı testler |

---

## Mimari Diyagram

Bir fatura yükleme isteğinin baştan sona akışı şöyle:

```mermaid
sequenceDiagram
    participant B as Tarayıcı
    participant R as FastAPI Router
    participant A as Auth (JWT)
    participant DB as Veritabanı
    participant BG as BackgroundTask
    participant OCR as OCR Servisi
    participant AI as AI Extraction

    B->>R: POST /invoices/upload (dosya + JWT token)
    R->>A: Token doğrula (get_current_active_user)
    A-->>R: Kullanıcı nesnesi
    R->>DB: Dosyayı diske yaz, Invoice kaydı oluştur (status=processing)
    R-->>B: 202 Accepted {invoice_id}

    Note over BG: HTTP yanıtı döndükten SONRA arka planda başlar
    R->>BG: add_task(process_invoice_pipeline, invoice_id)

    BG->>OCR: extract_text(file_path)
    OCR->>OCR: Gri ton + kontrast + keskinleştirme
    OCR->>OCR: Tesseract image_to_string()
    OCR-->>BG: raw_ocr_text
    BG->>DB: Invoice.raw_ocr_text kaydet

    BG->>AI: extract_invoice_data(raw_text)
    AI->>AI: LangChain + Gemini API (with_structured_output)
    AI-->>BG: InvoiceExtracted (vendor, date, amount, line_items)
    BG->>AI: categorize_line_items(items, categories)
    AI-->>BG: kalem-kategori eşleşmeleri
    BG->>DB: Invoice güncelle + LineItem kaydet
    BG->>DB: Invoice.status = "processed"

    loop Her 5 saniyede (frontend polling)
        B->>R: GET /invoices/{id}/status
        R-->>B: {status, vendor_name, total_amount}
    end
    B->>B: status="processed" → Polling durdur, detay göster
```

---

## Veritabanı Şeması

```mermaid
erDiagram
    User {
        int id PK
        string email UK
        string username UK
        string hashed_password
        bool is_active
        datetime created_at
    }
    Invoice {
        int id PK
        int owner_id FK
        string vendor_name
        string invoice_date
        float total_amount
        text raw_ocr_text
        string status
        string file_path
        text error_message
        datetime created_at
        datetime updated_at
    }
    LineItem {
        int id PK
        int invoice_id FK
        string description
        float amount
        int category_id FK
    }
    Category {
        int id PK
        string name UK
    }

    User ||--o{ Invoice : "owns (owner_id)"
    Invoice ||--o{ LineItem : "contains (invoice_id)"
    Category ||--o{ LineItem : "classifies (category_id)"
```

---

## HTTP İstek / Yanıt Örnekleri

### 1. Kullanıcı Kaydı — `POST /auth/register`

```http
POST /auth/register
Content-Type: application/json

{
  "email": "muhasebe@firma.com",
  "username": "muhasebeci1",
  "password": "guvenli_sifre_123"
}
```

**Yanıt — 201 Created:**
```json
{
  "id": 1,
  "email": "muhasebe@firma.com",
  "username": "muhasebeci1",
  "is_active": true,
  "created_at": "2024-03-15T10:30:00"
}
```

---

### 2. Giriş ve Token Alma — `POST /auth/login`

```http
POST /auth/login
Content-Type: application/json

{
  "username": "muhasebe@firma.com",
  "password": "guvenli_sifre_123"
}
```

**Yanıt — 200 OK:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

### 3. Fatura Yükleme — `POST /invoices/upload`

```http
POST /invoices/upload
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: multipart/form-data

file: fatura.pdf
```

**Yanıt — 202 Accepted** *(hemen döner, arka plan başlar)*:
```json
{
  "message": "Dosya yüklendi. OCR + AI işlemi arka planda başlatıldı.",
  "invoice_id": 7,
  "file_path": "uploads/1_a3f8b2c1d9e4.pdf",
  "status": "processing"
}
```

---

### 4. İşleme Durumu Sorgulama — `GET /invoices/{id}/status`

```http
GET /invoices/7/status
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Yanıt — işlem devam ederken:**
```json
{
  "id": 7,
  "status": "processing",
  "vendor_name": null,
  "total_amount": null,
  "error_message": null
}
```

**Yanıt — işlem tamamlandığında:**
```json
{
  "id": 7,
  "status": "processed",
  "vendor_name": "ABC Ofis Malzemeleri Ltd.",
  "total_amount": 4250.00,
  "error_message": null
}
```

---

### 5. Fatura Listesi — `GET /invoices`

```http
GET /invoices
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Yanıt — 200 OK:**
```json
[
  {
    "id": 7,
    "vendor_name": "ABC Ofis Malzemeleri Ltd.",
    "invoice_date": "2024-03-10",
    "total_amount": 4250.00,
    "status": "processed",
    "created_at": "2024-03-15T10:35:00",
    "owner_id": 1
  }
]
```

---

## Kurulum Adımları

### 1. Ön Koşullar

```bash
# Tesseract OCR (Windows)
winget install UB-Mannheim.TesseractOCR
# Kurulum sırasında "Additional language data" > "Turkish" seçin

# Tesseract OCR (Ubuntu/Debian)
sudo apt install tesseract-ocr tesseract-ocr-tur

# Poppler — PDF desteği için (Windows)
# https://github.com/oschwartz10612/poppler-windows/releases
# bin/ klasörünü PATH'e ekleyin

# Poppler (Ubuntu/Debian)
sudo apt install poppler-utils
```

### 2. Projeyi Klonla ve Sanal Ortam Oluştur

```bash
git clone https://github.com/KULLANICI_ADI/quickscan.git
cd quickscan

# Sanal ortam oluştur
python -m venv venv

# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate
```

### 3. Bağımlılıkları Yükle

```bash
pip install -r requirements.txt
```

### 4. Ortam Değişkenlerini Yapılandır

```bash
# Şablon dosyasını kopyala
cp .env.example .env   # Linux/Mac
copy .env.example .env  # Windows
```

`.env` dosyasını düzenleyin:

```env
# Güvenli rastgele anahtar üret:
# python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=buraya-uzun-rastgele-bir-string-girin

# Google AI Studio'dan ücretsiz API key: https://aistudio.google.com/
GOOGLE_API_KEY=AIzaSy...

# Windows Tesseract yolu (genellikle):
TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe
```

### 5. Veritabanı Migration'ını Çalıştır

```bash
alembic upgrade head
```

### 6. Geliştirme Sunucusunu Başlat

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- Uygulama: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`

---

## Testleri Çalıştırma

```bash
# Tüm testleri çalıştır
pytest tests/ -v

# Belirli bir dosya
pytest tests/test_auth.py -v

# Sadece güvenlik testleri (IDOR)
pytest tests/test_invoices.py::TestOwnerIsolation -v

# Coverage raporu
pip install pytest-cov
pytest tests/ --cov=. --cov-report=term-missing
```

**Test yapısı:**

```
tests/
├── conftest.py          # Ortak fixture'lar (in-memory DB, client, kullanıcılar)
├── test_auth.py         # Kayıt, giriş, JWT doğrulama testleri (14 test)
├── test_invoices.py     # CRUD + IDOR güvenlik testleri (13 test)
└── test_services.py     # OCR/AI servisleri mock testleri (9 test)
```

---

## Bu Projede Öğrenilen Kavramlar

| Kavram | Bu Projede Nerede Kullanıldı |
|---|---|
| **Dependency Injection** | `Depends(get_current_active_user)` ve `Depends(get_db)` ile her endpoint'e otomatik kullanıcı ve DB oturumu enjekte edilir |
| **JWT Authentication** | `python-jose` ile token üretimi (`auth.py`), `OAuth2PasswordBearer` ile token çıkarma (`dependencies.py`) |
| **ORM İlişkileri** | `User → Invoice → LineItem → Category` one-to-many ilişkileri; `cascade="all, delete-orphan"` ile otomatik temizlik |
| **Background Tasks** | `FastAPI.BackgroundTasks` ile OCR+AI işlemi HTTP yanıtından sonra ayrı thread'de çalıştırılır; kullanıcı beklemez |
| **Dış API Entegrasyonu** | `langchain-google-genai` ile Gemini API; 60 saniyelik thread-safe timeout yönetimi |
| **Structured Output** | `with_structured_output(InvoiceExtracted)` ile LLM'den garanti edilmiş Pydantic nesnesi alınır |
| **Mock ile Test** | `unittest.mock.patch` ile Gemini/Tesseract çağrıları mock'lanır; testler internetsiz ve ücretsiz çalışır |
| **IDOR Koruması** | Her sorguda `owner_id == current_user.id` filtresi; `get_invoice_or_404()` yardımcı fonksiyonu tüm endpoint'leri korur |
| **Alembic Migration** | Model değişiklikleri `alembic revision --autogenerate` ile SQL migration'ına dönüştürülür |
| **Görüntü İşleme** | Pillow ile gri ton + kontrast + keskinleştirme pipeline'ı OCR doğruluğunu artırır |

---

## Bilinen Sınırlamalar

- **Düşük çözünürlüklü görüntüler:** OCR 150 DPI altındaki görüntülerde güvenilmez sonuç verir. El yazısı faturalar başarısız olabilir.
- **Dil sınırlaması:** Varsayılan `tur+eng` paketi; Arapça, Çince vb. için Tesseract dil paketleri ayrıca kurulmalı.
- **BackgroundTask güvenilirliği:** FastAPI'nin yerleşik BackgroundTasks aynı process'te çalışır; sunucu yeniden başlatılırsa yarıda kalan işlemler kaybolur. Üretim için **Celery + Redis** gerekir.
- **Para birimi normalleştirme:** OCR çıktısında "₺", "TL", "$" karışabilir; AI her zaman doğru ayrıştıramayabilir.
- **SQLite sınırlaması:** Eşzamanlı yazma işlemlerinde darboğaz oluşabilir. Üretim için **PostgreSQL** önerilir.
- **Token yenileme yok:** JWT süresi dolunca kullanıcı yeniden giriş yapmak zorunda; refresh token mekanizması eklenmedi.

---

## Proje Yapısı

```
ocr/
├── main.py                       # FastAPI uygulaması (QuickScan), router kaydı, lifespan
├── database.py                   # SQLAlchemy engine, SessionLocal, get_db
├── models.py                     # User, Invoice, LineItem, Category ORM modelleri
├── schemas.py                    # Pydantic request/response şemaları
├── dependencies.py               # JWT doğrulama, get_current_user dependency
├── requirements.txt              # Python bağımlılıkları
├── .env.example                  # Ortam değişkeni şablonu
├── alembic/                      # Veritabanı migration dosyaları
├── routers/
│   ├── auth.py                   # Kayıt, giriş, /me endpoint'leri
│   └── invoices.py               # Fatura CRUD, upload, status, detail
├── services/
│   ├── ocr_service.py            # Tesseract OCR + görüntü ön işleme
│   ├── ai_extraction_service.py  # LangChain + Gemini yapılandırılmış çıktı
│   └── pipeline.py               # OCR → AI → DB orkestratörü
├── templates/                    # Jinja2 HTML şablonları
│   ├── layout.html               # Ana şablon (QuickScan marka/navbar/footer)
│   ├── login.html / register.html
│   ├── invoices.html
│   ├── add_invoice.html
│   └── invoice_detail.html
├── static/
│   ├── css/main.css
│   ├── js/base.js
│   └── img/                      # README görselleri (logo, ekran görüntüsü)
├── tests/
│   ├── conftest.py               # Ortak fixture'lar
│   ├── test_auth.py              # Auth akış testleri
│   ├── test_invoices.py          # CRUD + güvenlik testleri
│   └── test_services.py          # OCR/AI mock testleri
└── uploads/                      # Yüklenen fatura dosyaları (gitignore'da)
```
