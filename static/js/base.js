/**
 * static/js/base.js — Tüm Sayfalarda Çalışan Temel JavaScript
 * =============================================================
 * Bu dosya her sayfada yüklenir (layout.html'de <script> ile).
 * Görevleri:
 * 1. Kullanıcının giriş durumunu kontrol et
 * 2. Navigation bar'ı güncelle (hangi öğeler görünür?)
 * 3. Token işlemleri (kaydet, oku, sil)
 * 4. Ortak yardımcı fonksiyonlar (API fetch wrapper, vb.)
 *
 * NEDEN BASE.JS?
 * Her sayfa şablonunda aynı kodu tekrar yazmak yerine,
 * ortak mantığı burada merkezileştiriyoruz.
 * Sayfa özel kodlar ilgili şablon dosyasının
 * {% block extra_scripts %} bölümüne gidiyor.
 * =============================================================
 */

// ============================================================
// Token Yönetimi
// ============================================================

/**
 * LocalStorage'dan JWT access token'ı okur.
 * Token yoksa null döner.
 */
function getToken() {
  return localStorage.getItem('access_token');
}

/**
 * Kullanıcının giriş yapıp yapmadığını kontrol eder.
 * Token varlığını kontrol eder — geçerliliğini değil.
 * Geçerlilik sunucu tarafında kontrol edilir (401 dönerse).
 */
function isLoggedIn() {
  return !!getToken();
}

/**
 * Kullanıcıyı çıkış yaptırır:
 * 1. LocalStorage'daki token ve kullanıcı bilgilerini siler
 * 2. Giriş sayfasına yönlendirir
 */
function logout() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('token_type');
  localStorage.removeItem('username');
  window.location.href = '/auth/login';
}

// ============================================================
// Navigation Bar Güncelleme
// ============================================================

/**
 * Giriş durumuna göre navbar öğelerini göster/gizle.
 *
 * Jinja2 template'lerinde server-side'da kullanıcı durumunu
 * bilmek için oturumu sunucuda tutmak gerekir (session-based auth).
 * JWT ile stateless auth'da client-side JavaScript bu işi yapar:
 * - Token varsa → auth-required öğeleri göster
 * - Token yoksa → guest-only öğeleri göster
 *
 * Bu yaklaşımın sınırı: JavaScript devre dışıysa navbar yanlış
 * görünür. Üretim için server-side rendering veya httpOnly cookie
 * daha güvenilirdir.
 */
function updateNav() {
  const loggedIn = isLoggedIn();
  const username = localStorage.getItem('username');

  // Auth gerektiren öğeleri (Faturalar, Çıkış, kullanıcı adı)
  // NOT: style.display = '' CSS'teki .nav-auth-required { display: none }
  // kuralını override ETMEZ (inline stil boşaltılınca cascade'e geri döner).
  // Bu yüzden gösterilecek durumda gerçek display değeri (list-item) verilir.
  document.querySelectorAll('.nav-auth-required').forEach(el => {
    el.style.display = loggedIn ? 'list-item' : 'none';
  });

  // Misafir öğeleri (Giriş Yap, Kayıt Ol)
  document.querySelectorAll('.nav-guest-only').forEach(el => {
    el.style.display = loggedIn ? 'none' : 'list-item';
  });

  // Kullanıcı adını göster
  const usernameEl = document.getElementById('nav-username');
  if (usernameEl && username) {
    usernameEl.innerHTML = `<span data-lucide="user" style="width:13px;height:13px;"></span>${username}`;
    refreshIcons();
  }
}

// ============================================================
// İkon Yenileme (Lucide)
// ============================================================

/**
 * Lucide, sayfa ilk yüklendiğinde data-lucide="..." öznitelikli
 * elemanları SVG'ye çevirir. innerHTML ile içerik dinamik olarak
 * değiştirildiğinde (örn. fatura tablosu yeniden render edildiğinde)
 * yeni eklenen data-lucide etiketleri otomatik SVG'ye dönüşmez —
 * bu yüzden her dinamik render sonrası bu fonksiyon çağrılmalıdır.
 */
function refreshIcons() {
  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons();
  }
}

// ============================================================
// Korumalı Sayfa Kontrolü
// ============================================================

/**
 * Bu fonksiyon korumalı sayfalarda çağrılır.
 * Kullanıcı giriş yapmamışsa login sayfasına yönlendirir.
 *
 * Kullanım (sayfa template'lerinde):
 *   document.addEventListener('DOMContentLoaded', function() {
 *     requireAuth();
 *     // ... sayfa kodu
 *   });
 */
function requireAuth() {
  if (!isLoggedIn()) {
    window.location.href = '/auth/login';
    return false;
  }
  return true;
}

// ============================================================
// API İstek Yardımcısı
// ============================================================

/**
 * Authenticated API isteği yapar.
 * Authorization header'ı otomatik ekler.
 * 401 dönerse oturumu kapatır.
 *
 * @param {string} url - İstek URL'si
 * @param {object} options - fetch() seçenekleri
 * @returns {Promise<Response>}
 *
 * Kullanım:
 *   const response = await apiRequest('/invoices', { method: 'GET' });
 *   const data = await response.json();
 */
async function apiRequest(url, options = {}) {
  const token = getToken();

  // Varsayılan headers'ı token ile birleştir
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
    ...options.headers, // Çağıran tarafından gelen header'lar override eder
  };

  const response = await fetch(url, { ...options, headers });

  // Token süresi dolmuş veya geçersiz → oturumu kapat
  if (response.status === 401) {
    logout();
    throw new Error('Oturum süresi doldu. Lütfen tekrar giriş yapın.');
  }

  return response;
}

// ============================================================
// Sayfa Yüklendiğinde Çalış
// ============================================================

// DOMContentLoaded: HTML parse edildiğinde (CSS ve resimler
// beklenmeden) çalışır. Her sayfa için ortak başlangıç noktası.
document.addEventListener('DOMContentLoaded', function() {
  updateNav();
  refreshIcons();

  // Aktif nav link'ini işaretle (mevcut URL'ye göre)
  const currentPath = window.location.pathname;
  document.querySelectorAll('.nav-link').forEach(link => {
    if (link.getAttribute('href') === currentPath) {
      link.classList.add('active');
    }
  });
});
