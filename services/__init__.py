# ============================================================
# services/__init__.py — Servis Paketi
# ============================================================
# Bu dosya services/ klasörünü Python paketi yapar.
#
# NOT: Lazy import (geç yükleme) kullanıyoruz.
# pytesseract, Pillow, langchain gibi ağır bağımlılıklar
# her import'ta değil, gerçekten kullanıldığında yüklenir.
# Bu, bağımlılık eksik ortamlarda bile uygulamanın
# en azından başlamasını sağlar.
