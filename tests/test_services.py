# ============================================================
# tests/test_services.py — OCR ve AI Servis Testleri
# ============================================================
# Bu dosya, services/ katmanındaki OCR ve AI fonksiyonlarını test eder.
#
# NEDEN MOCK KULLANIYORUZ? (Gerçek API çağrısı yapmak yerine)
# ─────────────────────────────────────────────────────────────
# Gerçek Tesseract veya Gemini API'sini test sırasında çağırmak
# birkaç kritik soruna yol açar:
#
#   1. PARA MALİYETİ: Her test çalıştırmasında Gemini API'ye gerçek
#      istek gönderilir. CI/CD pipeline'da günde yüzlerce test
#      çalışabilir → beklenmedik API faturası.
#
#   2. YAVAŞLIK: Gemini API yanıtı 5-30 saniye sürebilir. 10 test
#      fonksiyonu × 15 saniye = test suite'i 2.5 dakika sürer.
#      İyi bir test suite saniyeler içinde tamamlanmalı.
#
#   3. İNTERNET BAĞIMLILIĞI: Test ortamları (CI/CD, offline çalışma)
#      internete erişemeyebilir. Gerçek API çağrıları bu ortamlarda
#      başarısız olur; testlerin güvenilirliği zayıflar.
#
#   4. DECORATİVİZM: Harici API'nin davranışını test etmiyoruz —
#      bizim kodumuzun doğru çalıştığını test etmek istiyoruz.
#      Gemini API'nin güvenilir olduğunu VARSAYIYORUZ; biz sadece
#      "ondan gelen yanıtı doğru işliyor muyuz?" sorusunu soruyoruz.
#
#   5. TEKRARLANABILIRLIK: Gerçek API'ler farklı çalıştırmalarda
#      farklı sonuçlar döndürebilir (network timing, model güncellemeleri).
#      Mock her seferinde aynı sonucu verir → test güvenilir olur.
#
# unittest.mock.patch ile ilgili temel kavramlar:
#   - @patch("modül.yol.FonksiyonAdı"): İlgili fonksiyonu sahte
#     versiyonuyla (MagicMock) geçici olarak değiştirir.
#   - Test fonksiyonu bittiğinde orijinal fonksiyon geri yüklenir.
#   - mock.return_value: Mock çağrıldığında ne döndüreceğini belirler.
# ============================================================

import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# OCR Servis Testleri
# ============================================================

class TestOCRService:
    """services/ocr_service.py testleri"""

    def test_extract_text_from_image_calls_tesseract(self, tmp_path):
        """
        extract_text_from_image, pytesseract'ı doğru parametrelerle çağırmalı.
        
        tmp_path: pytest'in sağladığı geçici dizin fixture'ı.
        Test bittikten sonra otomatik temizlenir.
        """
        # Tesseract'ı mock'la — gerçek OCR yapmadan simüle et
        with patch("services.ocr_service.pytesseract") as mock_tess, \
             patch("services.ocr_service.Image") as mock_img, \
             patch("services.ocr_service.TESSERACT_AVAILABLE", True):

            # Mock görüntü nesnesi hazırla
            mock_image_obj = MagicMock()
            mock_img.open.return_value = mock_image_obj
            mock_image_obj.convert.return_value = mock_image_obj

            # ImageEnhance mock'u
            with patch("services.ocr_service.ImageEnhance") as mock_enhance:
                mock_enhancer = MagicMock()
                mock_enhance.Contrast.return_value = mock_enhancer
                mock_enhancer.enhance.return_value = mock_image_obj
                mock_image_obj.filter.return_value = mock_image_obj

                # Tesseract'ın döndüreceği metni ayarla
                mock_tess.image_to_string.return_value = "  Test Fatura Metni  "

                # Geçici dosya oluştur
                test_image = tmp_path / "test_invoice.png"
                test_image.write_bytes(b"fake-image-content")

                from services.ocr_service import extract_text_from_image
                result = extract_text_from_image(str(test_image))

                # Tesseract çağrıldı mı?
                mock_tess.image_to_string.assert_called_once()
                # strip() uygulandı mı?
                assert result == "Test Fatura Metni"

    def test_extract_text_unsupported_format(self, tmp_path):
        """
        Desteklenmeyen dosya uzantısı ValueError fırlatmalı.
        
        extract_text() fonksiyonu .xlsx gibi desteklenmeyen dosyaları
        reddetmeli — güvenlik ve kullanıcı deneyimi için önemli.
        """
        from services.ocr_service import extract_text

        # .xlsx uzantılı sahte dosya
        bad_file = tmp_path / "fatura.xlsx"
        bad_file.write_bytes(b"excel-content")

        with pytest.raises(ValueError, match="Desteklenmeyen dosya uzantısı"):
            extract_text(str(bad_file))

    def test_extract_text_file_not_found(self):
        """Var olmayan dosya FileNotFoundError fırlatmalı."""
        with patch("services.ocr_service.TESSERACT_AVAILABLE", True):
            from services.ocr_service import extract_text_from_image
            with pytest.raises(FileNotFoundError):
                extract_text_from_image("/tmp/var_olmayan_dosya_xyz.png")

    def test_tesseract_not_available_raises_runtime_error(self, tmp_path):
        """Tesseract kurulu değilse RuntimeError fırlatmalı."""
        with patch("services.ocr_service.TESSERACT_AVAILABLE", False):
            test_image = tmp_path / "test.png"
            test_image.write_bytes(b"content")
            from services.ocr_service import extract_text_from_image
            with pytest.raises(RuntimeError, match="pytesseract"):
                extract_text_from_image(str(test_image))

    def test_dispatcher_routes_pdf_to_pdf_function(self, tmp_path):
        """
        extract_text() .pdf dosyasını extract_text_from_pdf()'e yönlendirmeli.
        
        Bu test, "dispatcher" pattern'in doğru çalıştığını doğrular:
        Dosya uzantısına göre doğru fonksiyon çağrılıyor mu?
        """
        pdf_file = tmp_path / "fatura.pdf"
        pdf_file.write_bytes(b"pdf-content")

        with patch("services.ocr_service.extract_text_from_pdf") as mock_pdf_fn, \
             patch("services.ocr_service.PDF_SUPPORT", True):
            mock_pdf_fn.return_value = "PDF OCR Metni"

            from services.ocr_service import extract_text
            result = extract_text(str(pdf_file))

            mock_pdf_fn.assert_called_once_with(str(pdf_file))
            assert result == "PDF OCR Metni"


# ============================================================
# AI Extraction Servis Testleri
# ============================================================

class TestAIExtractionService:
    """
    services/ai_extraction_service.py testleri.
    
    NEDEN BU TESTLERDEKİ MOCK'LAR ÖNEMLİ?
    Gemini API'yi her test çalıştırmasında gerçekten çağırsaydık:
    - ~$0.001 per call × 100 test çalıştırması = beklenmedik maliyet
    - Ortalama 10-20 saniye yanıt süresi × test sayısı = yavaş CI/CD
    - İnternet olmadan testler başarısız olur
    - Gemini'nin değişen yanıtları testleri güvenilmez yapar
    
    Mock ile sadece "bizim kodumuzu" test ediyoruz.
    """

    def test_extract_invoice_data_empty_text_returns_empty_model(self):
        """
        Boş OCR metni ile AI çağrısı yapılmamalı, boş model dönmeli.
        
        Bu önemli bir edge case: OCR bazen hiç metin üretemez.
        Bu durumda gereksiz AI API çağrısı yapmamalıyız.
        """
        from services.ai_extraction_service import extract_invoice_data, InvoiceExtracted

        result = extract_invoice_data("")
        assert isinstance(result, InvoiceExtracted)
        assert result.vendor_name is None
        assert result.total_amount is None
        assert result.line_items == []

    def test_extract_invoice_data_none_text(self):
        """None veya whitespace-only metin de boş model döndürmeli."""
        from services.ai_extraction_service import extract_invoice_data

        result = extract_invoice_data("   ")  # Sadece boşluk
        assert result.vendor_name is None

    def test_extract_invoice_data_calls_gemini_with_mock(self):
        """
        Ham metin verildiğinde Gemini API çağrılmalı.
        
        patch("services.ai_extraction_service._get_llm") ile LLM nesnesini
        mock'luyoruz. with_structured_output chain'i sahte bir LLM döndürür.
        Bu sayede gerçek API çağrısı yapılmadan kod akışı test edilir.
        """
        from services.ai_extraction_service import InvoiceExtracted, LineItemExtracted

        # AI'ın döndüreceği sahte sonuç
        mock_result = InvoiceExtracted(
            vendor_name="Mock Tedarikçi Ltd.",
            invoice_date="2024-03-15",
            total_amount=2500.0,
            line_items=[
                LineItemExtracted(description="Ofis Sandalyesi", amount=1500.0),
                LineItemExtracted(description="Klavye", amount=1000.0),
            ]
        )

        with patch("services.ai_extraction_service._get_llm") as mock_get_llm:
            # LLM zincirini mock'la
            mock_llm = MagicMock()
            mock_get_llm.return_value = mock_llm
            mock_structured = MagicMock()
            mock_llm.with_structured_output.return_value = mock_structured
            mock_structured.invoke.return_value = mock_result

            from services.ai_extraction_service import extract_invoice_data
            result = extract_invoice_data("Fatura No: 12345 Toplam: 2500 TL")

            # Sonuç doğru parse edildi mi?
            assert result.vendor_name == "Mock Tedarikçi Ltd."
            assert result.total_amount == 2500.0
            assert len(result.line_items) == 2
            # with_structured_output ile InvoiceExtracted şeması kullanıldı mı?
            mock_llm.with_structured_output.assert_called_once_with(
                InvoiceExtracted,
                method="json_schema"
            )

    def test_date_validator_rejects_invalid_format(self):
        """
        Pydantic date validator YYYY-MM-DD dışındaki formatları reddetmeli.
        
        AI modeli bazen yanlış tarih formatı döndürebilir (örn: "15/03/2024").
        validate_date() bu tür girişleri None'a çevirir — hata fırlatmaz.
        """
        from services.ai_extraction_service import InvoiceExtracted

        # Yanlış format → None'a çevrilmeli
        invoice = InvoiceExtracted(invoice_date="15/03/2024")
        assert invoice.invoice_date is None

        # Doğru format → olduğu gibi kalmalı
        invoice_valid = InvoiceExtracted(invoice_date="2024-03-15")
        assert invoice_valid.invoice_date == "2024-03-15"

    def test_categorize_line_items_returns_empty_for_empty_input(self):
        """Boş kalem listesi ile kategorizasyon boş dict döndürmeli."""
        from services.ai_extraction_service import categorize_line_items

        result = categorize_line_items([], ["Ofis", "Ulaşım"])
        assert result == {}

    def test_categorize_line_items_with_mock(self):
        """
        Kategorizasyon fonksiyonu Gemini'yi doğru biçimde çağırmalı.
        
        Bu test, kategorilerin AI'a gönderilip sonuçların dict'e
        dönüştürüldüğünü doğrular.
        """
        from services.ai_extraction_service import (
            LineItemExtracted, CategorizationResult, LineItemCategorized
        )

        mock_items = [
            LineItemExtracted(description="Uçak Bileti İstanbul-Ankara", amount=850.0),
            LineItemExtracted(description="Laptop Şarj Aleti", amount=450.0),
        ]
        mock_categories = ["Ulaşım", "Elektronik", "Ofis Malzemesi"]

        mock_result = CategorizationResult(items=[
            LineItemCategorized(description="Uçak Bileti İstanbul-Ankara", category="Ulaşım"),
            LineItemCategorized(description="Laptop Şarj Aleti", category="Elektronik"),
        ])

        with patch("services.ai_extraction_service._get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_get_llm.return_value = mock_llm
            mock_structured = MagicMock()
            mock_llm.with_structured_output.return_value = mock_structured
            mock_structured.invoke.return_value = mock_result

            from services.ai_extraction_service import categorize_line_items
            result = categorize_line_items(mock_items, mock_categories)

            assert result["Uçak Bileti İstanbul-Ankara"] == "Ulaşım"
            assert result["Laptop Şarj Aleti"] == "Elektronik"

    def test_missing_api_key_raises_runtime_error(self):
        """
        GOOGLE_API_KEY tanımlanmamışsa RuntimeError fırlatmalı.
        
        Bu test, eksik yapılandırmanın erken yakalandığını doğrular.
        Sessizce başarısız olup null sonuç döndürmek yerine açık hata vermek
        daha iyi hata yönetimidir.
        """
        with patch("services.ai_extraction_service.GOOGLE_API_KEY", ""):
            with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
                from services.ai_extraction_service import _get_llm
                _get_llm()
