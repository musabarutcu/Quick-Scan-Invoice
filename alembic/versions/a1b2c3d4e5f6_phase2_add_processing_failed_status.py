"""phase2_add_processing_failed_status_error_message

Revision ID: a1b2c3d4e5f6
Revises: fc9c1b2c69fb
Create Date: 2026-09-06 17:25:00.000000

Aşama 2 değişiklikleri:
  1. Invoice.status enum'una 'processing' ve 'failed' değerleri eklendi
  2. Invoice.error_message TEXT alanı eklendi
  3. Varsayılan kategoriler eklendi (seed data)

NOT — SQLite ve Enum:
SQLite, ALTER TABLE ile enum değer eklemeyi desteklemez.
Bu migration'da SQLite için çalışma stratejisi:
  - Eski tabloyu rename et
  - Yeni enum değerleriyle yeniden oluştur
  - Veriyi kopyala
  - Eski tabloyu sil

PostgreSQL için normal ALTER TYPE ... ADD VALUE kullanılır.
Alembic'in render_as_batch=True özelliği SQLite için bu süreci yönetir.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'fc9c1b2c69fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Varsayılan kategoriler — AI kategorilendirme için
DEFAULT_CATEGORIES = [
    "Ofis Malzemeleri",
    "Ulaşım",
    "Yemek & İçecek",
    "Yazılım & Lisans",
    "Kira & Kira Giderleri",
    "Faturalar & Abonelikler",
    "Pazarlama & Reklam",
    "Personel & İnsan Kaynakları",
    "Ekipman & Donanım",
    "Diğer",
]

# Yeni enum değerleri (eski + yeni)
NEW_STATUSES = ('pending', 'processing', 'processed', 'reviewed', 'failed')


def upgrade() -> None:
    # ── Adım 1: invoices tablosunu güncelle ─────────────────────────────────
    # SQLite'de ALTER TABLE ... ALTER COLUMN desteklenmez.
    # batch_alter_table (render_as_batch) bu sorunu çözer:
    # Geçici bir tablo oluşturur, veriyi kopyalar, eski tabloyu siler.
    with op.batch_alter_table('invoices') as batch_op:
        # Yeni enum değerleriyle status sütununu yeniden oluştur
        batch_op.alter_column(
            'status',
            type_=sa.Enum(*NEW_STATUSES, name='invoicestatus'),
            existing_type=sa.Enum('pending', 'processed', 'reviewed', name='invoicestatus'),
            nullable=False,
            existing_nullable=False
        )
        # error_message alanı ekle
        batch_op.add_column(
            sa.Column('error_message', sa.Text(), nullable=True)
        )

    # ── Adım 2: Varsayılan kategorileri ekle ────────────────────────────────
    # categories tablosuna seed data ekliyoruz.
    # INSERT OR IGNORE: Kategori zaten varsa tekrar ekleme (idempotent).
    categories_table = sa.table(
        'categories',
        sa.column('name', sa.String)
    )

    # Mevcut bağlantı üzerinden seed data ekle
    connection = op.get_bind()
    for cat_name in DEFAULT_CATEGORIES:
        # SQLite ve PostgreSQL için uyumlu INSERT
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO categories (name) VALUES (:name)"
            ),
            {"name": cat_name}
        )


def downgrade() -> None:
    # ── Eski enum değerlerine dön ────────────────────────────────────────────
    # Önce 'processing' veya 'failed' durumundaki faturaları 'pending'e çek
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE invoices SET status = 'pending' "
            "WHERE status IN ('processing', 'failed')"
        )
    )

    with op.batch_alter_table('invoices') as batch_op:
        # error_message sütununu kaldır
        batch_op.drop_column('error_message')
        # Eski enum tipine geri dön
        batch_op.alter_column(
            'status',
            type_=sa.Enum('pending', 'processed', 'reviewed', name='invoicestatus'),
            existing_type=sa.Enum(*NEW_STATUSES, name='invoicestatus'),
            nullable=False,
            existing_nullable=False
        )

    # NOT: Eklenen kategoriler downgrade'de silinmez.
    # Mevcut verilere bağlı olabilecek kategorileri silmek veri kaybına yol açar.
