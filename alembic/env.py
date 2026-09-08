# ============================================================
# alembic/env.py — Alembic Migration Ortam Yapılandırması
# ============================================================
# Bu dosya Alembic'in nasıl çalışacağını belirler.
#
# EN ÖNEMLİ DEĞİŞİKLİK:
#   target_metadata = Base.metadata
#   Bu satır Alembic'in modellerimizi tanımasını sağlar.
#   Olmadan "autogenerate" çalışmaz — tablolar değişse bile
#   Alembic farkı göremez.
#
# DATABASE_URL .env'den okunur — alembic.ini'ye sabit yazmayız.
# Bu güvenlik ve esneklik açısından önemlidir:
# - Farklı ortamlar (dev/test/prod) farklı URL kullanabilir
# - Şifreli bağlantı stringi repoda görünmez
# ============================================================

import os
import sys
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context

# .env dosyasını yükle — DATABASE_URL için gerekli
load_dotenv()

# Proje kök dizinini Python path'ine ekle
# Bu olmadan "from database import Base" çalışmaz
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# models.py'deki tüm modelleri import et
# Bu import olmadan Alembic tablo yapısını bilemez
from database import Base
import models  # noqa: F401 — modellerin Base'e kaydedilmesi için gerekli

# Alembic Config nesnesi — alembic.ini değerlerine erişim
config = context.config

# Python logging yapılandırması
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ============================================================
# target_metadata — Kritik Satır
# ============================================================
# Alembic bu metadata sayesinde mevcut tabloları ve
# model tanımlarını karşılaştırarak farkları bulur.
# None olsaydı autogenerate çalışmazdı.
target_metadata = Base.metadata

# DATABASE_URL'yi ortam değişkeninden al
# alembic.ini'deki sqlalchemy.url'yi override eder
database_url = os.getenv("DATABASE_URL", "sqlite:///./ocr_app.db")
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """
    'Offline' modunda migration çalıştırır.
    
    Bu mod, veritabanına bağlanmadan SQL script'leri üretir.
    Üretim ortamında DBA'nın scripti elle çalıştıracağı durumlarda kullanılır.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # render_as_batch: SQLite'de ALTER TABLE desteklenmez.
        # Bu seçenek Alembic'in geçici tablo + kopyalama yöntemiyle
        # sütun değişiklikleri yapmasını sağlar.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    'Online' modunda migration çalıştırır.
    
    Bu mod veritabanına bağlanarak migration'ları doğrudan uygular.
    Geliştirme ortamında "alembic upgrade head" çalıştırıldığında
    bu fonksiyon devreye girer.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # render_as_batch: SQLite enum/sütun değişiklikleri için gerekli.
            # Her ALTER TABLE işlemi, SQLite'de geçici bir tablo üzerinden yapılır:
            #   1. Yeni yapıyla geçici tablo oluştur
            #   2. Veriyi kopyala
            #   3. Eski tabloyu sil, geçiciye eski adı ver
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# Mod seçimi: offline mi online mi?
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
