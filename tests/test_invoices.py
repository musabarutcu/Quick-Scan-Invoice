# ============================================================
# tests/test_invoices.py — Fatura CRUD ve Güvenlik Testleri
# ============================================================
# Bu dosya fatura işlemlerinin doğruluğunu ve güvenliğini doğrular:
#
#   1. CRUD testleri: Oluşturma, listeleme, güncelleme, silme
#   2. IDOR güvenlik testi: Kullanıcı izolasyonu (en kritik test!)
#   3. Yetkilendirme: Token gerektiren endpoint'ler
#
# NEDEN IDOR TESTİ EN ÖNEMLİ?
#   IDOR (Insecure Direct Object Reference): Bir kullanıcının
#   başka bir kullanıcının verisine ID ile doğrudan erişmesi.
#   Örnek güvenlik açığı: GET /invoices/42 → ID 42'nin sahibi
#   olmayan kullanıcı bu faturayı görebilirse büyük sorun!
#
#   test_cannot_access_other_users_invoice() bu açığın olmadığını
#   kanıtlar. Gerçek projelerde bu tür testler "security test" veya
#   "authorization test" olarak ayrıca kategorize edilir.
# ============================================================

import pytest
from fastapi.testclient import TestClient


class TestInvoiceCRUD:
    """Fatura oluşturma, okuma, güncelleme, silme testleri"""

    def test_create_invoice_success(self, client: TestClient, registered_user):
        """Giriş yapmış kullanıcı fatura oluşturabilmeli."""
        response = client.post(
            "/invoices",
            json={
                "vendor_name": "Test Tedarikçi A.Ş.",
                "invoice_date": "2024-01-15",
                "total_amount": 1500.50
            },
            headers=registered_user["headers"]
        )
        assert response.status_code == 201
        data = response.json()
        assert data["vendor_name"] == "Test Tedarikçi A.Ş."
        assert data["invoice_date"] == "2024-01-15"
        assert data["total_amount"] == 1500.50
        assert data["status"] == "pending"
        # Sahiplik kontrolü: owner_id doğru kullanıcıya atanmış mı?
        assert "owner_id" in data

    def test_create_invoice_requires_auth(self, client: TestClient):
        """Token olmadan fatura oluşturma 401 dönmeli."""
        response = client.post(
            "/invoices",
            json={"vendor_name": "Anonim Firma"}
        )
        assert response.status_code == 401

    def test_create_invoice_invalid_date(self, client: TestClient, registered_user):
        """Yanlış tarih formatı 422 dönmeli."""
        response = client.post(
            "/invoices",
            json={
                "vendor_name": "Test Firma",
                "invoice_date": "15/01/2024",  # GG/AA/YYYY — geçersiz format
                "total_amount": 100.0
            },
            headers=registered_user["headers"]
        )
        assert response.status_code == 422

    def test_list_invoices_empty(self, client: TestClient, registered_user):
        """Yeni kullanıcının fatura listesi boş olmalı."""
        response = client.get("/invoices", headers=registered_user["headers"])
        assert response.status_code == 200
        assert response.json() == []

    def test_list_invoices_shows_own(self, client: TestClient, registered_user):
        """Kullanıcı sadece kendi faturalarını görmeli."""
        # 3 fatura oluştur
        for i in range(3):
            client.post(
                "/invoices",
                json={"vendor_name": f"Firma {i}", "total_amount": float(i * 100)},
                headers=registered_user["headers"]
            )

        response = client.get("/invoices", headers=registered_user["headers"])
        assert response.status_code == 200
        invoices = response.json()
        assert len(invoices) == 3

    def test_list_invoices_requires_auth(self, client: TestClient):
        """Token olmadan fatura listesi 401 dönmeli."""
        response = client.get("/invoices")
        assert response.status_code == 401

    def test_get_invoice_detail(self, client: TestClient, registered_user):
        """Kendi faturasının detayını görüntüleyebilmeli."""
        # Fatura oluştur
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Detay Test Firması", "total_amount": 999.99},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Detay sorgula
        response = client.get(
            f"/invoices/{invoice_id}",
            headers=registered_user["headers"]
        )
        assert response.status_code == 200
        data = response.json()
        assert data["vendor_name"] == "Detay Test Firması"
        assert "line_items" in data  # Detay şemasında kalemler var

    def test_update_invoice(self, client: TestClient, registered_user):
        """Kendi faturasını güncelleyebilmeli (partial update)."""
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Eski Ad", "total_amount": 100.0},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Sadece vendor_name güncelle (partial update)
        response = client.put(
            f"/invoices/{invoice_id}",
            json={"vendor_name": "Yeni Ad"},
            headers=registered_user["headers"]
        )
        assert response.status_code == 200
        assert response.json()["vendor_name"] == "Yeni Ad"
        # total_amount değişmemiş olmalı
        assert response.json()["total_amount"] == 100.0

    def test_delete_invoice(self, client: TestClient, registered_user):
        """Kendi faturasını silebilmeli."""
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Silinecek Firma"},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Sil
        delete_resp = client.delete(
            f"/invoices/{invoice_id}",
            headers=registered_user["headers"]
        )
        assert delete_resp.status_code == 204

        # Artık bulunamıyor olmalı
        get_resp = client.get(
            f"/invoices/{invoice_id}",
            headers=registered_user["headers"]
        )
        assert get_resp.status_code == 404

    def test_get_nonexistent_invoice(self, client: TestClient, registered_user):
        """Var olmayan fatura 404 dönmeli."""
        response = client.get(
            "/invoices/999999",
            headers=registered_user["headers"]
        )
        assert response.status_code == 404


class TestOwnerIsolation:
    """
    ══════════════════════════════════════════════
    OWNER_ID İZOLASYON TESTLERİ — GÜVENLİK KRİTİK
    ══════════════════════════════════════════════
    Bu test sınıfı, sistemin en kritik güvenlik özelliğini doğrular:
    Kullanıcılar YALNIZCA kendi faturalarına erişebilmeli.

    IDOR (Insecure Direct Object Reference) açığı:
    Eğer bu testler başarısız olursa, saldırgan tüm
    kullanıcıların faturalarına ID ile erişebilir — ciddi veri ihlali!

    Test senaryosu:
    - Kullanıcı A bir fatura oluşturur
    - Kullanıcı B o faturanın ID'sini tahmin eder (1, 2, 3...)
    - Kullanıcı B'nin GET/PUT/DELETE istekleri 404 dönmeli
    """

    def test_cannot_access_other_users_invoice(
        self, client: TestClient, registered_user, second_user
    ):
        """
        ⚠️ EN KRİTİK GÜVENLİK TESTİ ⚠️

        Kullanıcı A'nın faturasına Kullanıcı B erişememeli.
        
        Neden 404 (Not Found) bekliyoruz, 403 (Forbidden) değil?
        Güvenlik best practice: 403 vermek saldırgana "bu ID var,
        sadece senin değil" bilgisini sızdırır. 404 vermek bu
        bilgiyi gizler — saldırgan faturanın var olup olmadığını
        bile anlayamaz.
        """
        # Kullanıcı A bir fatura oluşturur
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Kullanıcı A'nın Gizli Faturası", "total_amount": 9999.0},
            headers=registered_user["headers"]
        )
        assert create_resp.status_code == 201
        invoice_id = create_resp.json()["id"]

        # Kullanıcı B aynı ID ile erişmeye çalışır → 404 beklenir
        response = client.get(
            f"/invoices/{invoice_id}",
            headers=second_user["headers"]  # Farklı kullanıcının token'ı!
        )
        assert response.status_code == 404, (
            "GÜVENLIK AÇIĞI: Başka kullanıcının faturasına erişildi! "
            "owner_id filtresi çalışmıyor!"
        )

    def test_cannot_update_other_users_invoice(
        self, client: TestClient, registered_user, second_user
    ):
        """Kullanıcı B, Kullanıcı A'nın faturasını gücelleyememeli."""
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Orijinal İsim"},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Kullanıcı B güncellemeye çalışır
        response = client.put(
            f"/invoices/{invoice_id}",
            json={"vendor_name": "Hacklenmiş İsim"},
            headers=second_user["headers"]
        )
        assert response.status_code == 404

        # Orijinal verinin değişmediğini doğrula
        original = client.get(
            f"/invoices/{invoice_id}",
            headers=registered_user["headers"]
        )
        assert original.json()["vendor_name"] == "Orijinal İsim"

    def test_cannot_delete_other_users_invoice(
        self, client: TestClient, registered_user, second_user
    ):
        """Kullanıcı B, Kullanıcı A'nın faturasını silemeyecek."""
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Silinmeyecek Fatura"},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Kullanıcı B silmeye çalışır → 404
        response = client.delete(
            f"/invoices/{invoice_id}",
            headers=second_user["headers"]
        )
        assert response.status_code == 404

        # Fatura hâlâ orada mı?
        still_exists = client.get(
            f"/invoices/{invoice_id}",
            headers=registered_user["headers"]
        )
        assert still_exists.status_code == 200

    def test_list_only_shows_own_invoices(
        self, client: TestClient, registered_user, second_user
    ):
        """
        Fatura listesi, başka kullanıcıların faturalarını içermemeli.
        
        Bu test, LIST endpoint'inin de owner_id filtresi uyguladığını
        doğrular. Filtresiz bir SELECT * FROM invoices felakete yol açar.
        """
        # Kullanıcı A: 2 fatura
        client.post("/invoices", json={"vendor_name": "A Faturası 1"}, headers=registered_user["headers"])
        client.post("/invoices", json={"vendor_name": "A Faturası 2"}, headers=registered_user["headers"])

        # Kullanıcı B: 3 fatura
        client.post("/invoices", json={"vendor_name": "B Faturası 1"}, headers=second_user["headers"])
        client.post("/invoices", json={"vendor_name": "B Faturası 2"}, headers=second_user["headers"])
        client.post("/invoices", json={"vendor_name": "B Faturası 3"}, headers=second_user["headers"])

        # Kullanıcı A sadece kendi 2 faturasını görmeli
        resp_a = client.get("/invoices", headers=registered_user["headers"])
        assert len(resp_a.json()) == 2

        # Kullanıcı B sadece kendi 3 faturasını görmeli
        resp_b = client.get("/invoices", headers=second_user["headers"])
        assert len(resp_b.json()) == 3

    def test_status_endpoint_isolation(
        self, client: TestClient, registered_user, second_user
    ):
        """Polling status endpoint'i de owner_id izolasyonu yapmalı."""
        create_resp = client.post(
            "/invoices",
            json={"vendor_name": "Durum Test Faturası"},
            headers=registered_user["headers"]
        )
        invoice_id = create_resp.json()["id"]

        # Kullanıcı B'nin status sorgusuna 404 dönmeli
        response = client.get(
            f"/invoices/{invoice_id}/status",
            headers=second_user["headers"]
        )
        assert response.status_code == 404
