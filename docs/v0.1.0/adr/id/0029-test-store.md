> Terjemahan turunan — jangan sunting; sumber: 0029-test-store.md (sha256: e4d36eb13c40)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0029: Store Tes Adalah Store Nyata — Postgres+pgvector, Satu Database Tes, Truncate per Tes

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0016 (skema kanonik & migrasi), ADR-0001 (memori sumber kebenaran), ADR-0011 (crash safety), ADR-0012 (lease safety), ADR-0026 (search), ADR-0027 (distribusi & kapabilitas clean by default)
- **Supersedes:** —
- **Terkait:** ADR-0009 (gate yang dijalankan tes ini), ADR-0019 (observability), ADR-0021 / ADR-0024 (kontrak tool)
- **Implementasi:** implemented — bukti: `tests/conftest.py`, `tests/support.py`, `tests/test_pgvector.py`, `eidesoul/engine/db.py`, `eidesoul/engine/schema.py`, `compose.yaml`; hasil gate: `pytest -q` 561 lulus dalam 31 dtk di Postgres+pgvector, kegagalan saat variabel kosong terverifikasi
- **Catatan lokasi:** ADR ini mengikat semua permukaan (ia menentukan arti "terverifikasi"), jadi ia duduk di akar rumah ADR. Salinan Indonesia di `docs/v0.1.0/adr/id/0029-test-store.md`.
- **Amandemen:** 2026-11-03 — versi pertama.

---

## 1. Konteks

Suite hari ini jalan di SQLite, artinya beberapa mekanisme yang dikirim ke produksi hanya **disimulasikan**, bukan diuji:

| Mekanisme | Kenapa SQLite tak bisa mewakilinya | Bukti |
|---|---|---|
pgvector dan Mem0 | memori semantik dilewati sepenuhnya saat URL-nya SQLite | `eidesoul/engine/memory/__init__.py:178` |
Semantik upsert | kode memilih insert per dialek, jadi hanya satu cabang yang teruji | `eidesoul/engine/db.py:12-13,123-124` |
DDL & migrasi nyata | tes membuat tabel lewat `ensure()`/`ensure_schemas()`, tidak pernah lewat jalur `db upgrade` produksi | `tests/support.py:88,539` |
Perilaku `JSONB` | turun jadi JSON biasa; operator containment dan path tak pernah diuji | `eidesoul/engine/schema.py:22` |
Pemeliharaan vektor | `ensure_vector()` best-effort dan tanpa extension ia tidak melakukan apa pun | `eidesoul/engine/db.py:76-90` |
`timestamptz`, row lock, balapan klaim | semantik Postgres tanpa padanan di SQLite (ADR-0011/0012) | `eidesoul/engine/db.py:46-49` |

Biaya peralihannya terbatas karena ketergantungannya terpusat: 522 tes di 56 berkas, hanya lima berkas menyebut SQLite, dan harness membangun store di satu tempat (`tests/support.py:71,88,515,539`). Memperbaiki harness memindahkan seluruh suite; mempertahankan SQLite mempertahankan jalur kode kedua yang dikirim tapi tak diuji.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Store yang diuji = store yang dikirim | Satu jalur engine, tanpa cabang dialek, tanpa "jalan di DB tes saya" |
| Cakupan jujur | pgvector, `ON CONFLICT`, operator JSONB, row lock, dan migrasi nyata benar-benar dijalankan |
| Determinisme | tanpa unduhan model dan tanpa jaringan di dalam menjalankan tes |
| Cepat dan terisolasi | satu database, DDL sekali per sesi, truncate per tes |
| Gagal eksplisit | database tes yang tidak ada = error yang lantang, tidak pernah turun diam-diam |
| Permukaan kecil | harness satu modul; suite tetap jalan tanpa menulis ulang tes |
| Siap CI | satu container service cukup; tidak ada kerangka fixture baru |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Pertahankan SQLite** | suite tetap memakai database in-memory | Ditolak: mekanisme tersimulasi di atas tetap tak teruji dan cabang dialeknya tetap hidup |
| **B. Postgres untuk semuanya** | satu store, yaitu store yang dikirim, dengan database sekali pakai per jalannya tes | **Dipilih** |
| **C. Dua lane (SQLite cepat, Postgres menyeluruh)** | subset cepat plus lane Postgres | Ditolak untuk sekarang: dua jalur berarti yang cepat bukan bukti, dan kode tetap membawa cabang SQLite-nya |
| **D. Container sekali pakai per jalannya (ala testcontainers)** | suite menyalakan dan mematikan servernya sendiri | Ditunda: menambah dependency dan biaya start untuk sesuatu yang sudah diberikan DSN dari operator |

## 4. Keputusan

### 4.1 Satu store

Suite jalan melawan **Postgres dengan `pgvector`**, store yang sama dengan yang dikirim engine.

- DSN-nya datang dari `EIDESOL_TEST_DATABASE_URL`.
- Saat variabel itu kosong, suite **gagal dengan menyebut nama variabelnya dan perintah tepat untuk menyalakan database tes** — ia tidak pernah jatuh ke SQLite dan tidak pernah melewati tes yang memakai store.
- Database tes itu sekali pakai: isinya tidak ada yang operator pedulikan, dan ia tidak pernah database dev atau produksi (nama beda, port beda, project compose terpisah).

### 4.2 Isolasi

| Tingkat | Aturan |
|---|---|
| Sesi | konek sekali, `CREATE EXTENSION IF NOT EXISTS vector`, lalu jalankan **jalur migrasi produksi** (`db upgrade`) sekali |
| Tes | `TRUNCATE <setiap tabel> RESTART IDENTITY CASCADE` sebelum badan tes, sehingga tak ada tes yang melihat baris tes lain |
| Tes migrasi | fixture khusus boleh membuat dan menghapus databasenya sendiri; ia tidak pernah men-truncate database bersama |
| Skema gateway | dibuat fixture sesi lewat `ensure_schemas` yang sama seperti dipanggil engine |

### 4.3 Determinisme

- Embedder diganti **stub deterministik 384 dimensi** di dalam tes, jadi tak ada model yang diunduh dan tak ada tes yang bergantung pada waktu atau jaringan. Produksi tetap memakai embedder bersama yang tunggal (ADR-0001); stub hanya hidup di harness tes.
- pgvector diuji dengan vektor stub itu: minimal satu tes memastikan kueri tetangga terdekat mengembalikan baris yang seharusnya, dan baris yang dihapus atau tak relevan tidak ikut.
- Tidak ada tes yang menyentuh jaringan; tes yang butuh layanan eksternal ditandai dan bukan bagian dari jalannya default.

### 4.4 SQLite dicabut, bukan disimpan sebagai cadangan

- `tests/support.py` berhenti menawarkan URL SQLite dan berhenti membangun config sementara di sekitarnya.
- Cabang dialek ikut pergi: pemilihan dialek insert di `eidesoul/engine/db.py`, lewatan SQLite di `eidesoul/engine/memory/__init__.py`, primary key varian integer di `eidesoul/engine/schema.py`, dan penyebutan `"sqlite"` di aturan penyimpanan `AGENTS.md`.
- `JSONB` tetap tipe Postgres; tidak ada lagi yang berpura-pura portabel.

### 4.5 Anggaran kecepatan

DDL sesi terjadi sekali dan isolasi per tes hanyalah truncate, jadi targetnya **≤ 180 s untuk suite penuh**. Tes yang memang lambat ditandai dan dilaporkan, tidak pernah dilewati diam-diam; tes yang butuh database baru menanggung biayanya secara eksplisit.

### 4.6 Yang tetap menjadi gate sebuah perubahan

`pytest -q` (kini berbasis store), `python -m eidesoul selftest`, dan `python scripts/adr.py --check` tetap tiga gate-nya. Sebuah perubahan tidak terverifikasi karena SQLite bilang begitu, dan tidak terverifikasi oleh subset yang hijau.

## 5. Konfigurasi

| Kunci | Default | Arti |
|---|---|---|
| `EIDESOL_TEST_DATABASE_URL` | kosong → suite gagal dengan instruksi | DSN database tes sekali pakai |
| `POSTGRES_DB` (compose) | `eidesoul` | diparameter agar stack tes memakai `eidesoul_test` |
| `POSTGRES_PORT` (compose) | `5432` | diparameter agar stack tes bisa jalan di samping dev pada `5433` |
| project compose | `eidesoul-test` untuk stack tes | volume, jaringan, dan database terpisah |

Stack dev mempertahankan nilainya sendiri; tidak ada di sini yang mengubah cara operator menjalankan engine.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Cabang dialek untuk store kedua di `eidesoul/engine` | 0 |
| Tes berbasis store yang bisa diam-diam jatuh ke SQLite | 0 |
| Mekanisme dari §1 yang tak punya tes di store nyata | 0 (pgvector, upsert, `db upgrade`, JSONB, lock) |
| Waktu suite penuh | ≤ 180 s |
| Unduhan model saat menjalankan tes | 0 (embedder stub) |
| Panggilan jaringan saat menjalankan tes | 0 |
| Tes yang membuat tabel dengan `metadata.create_all`, bukan `db upgrade` | 0 di luar fixture khusus migrasi |

## 7. Acceptance criteria

- [ ] `pytest -q` dengan `EIDESOL_TEST_DATABASE_URL` terisi menjalankan seluruh suite di Postgres+pgvector, dan hijau berarti hijau di store yang dikirim.
- [ ] `pytest -q` tanpanya gagal dengan satu pesan yang menyebut variabelnya dan perintah compose untuk menyalakan database tes; tidak ada tes yang lewat diam-diam.
- [ ] Sesi menjalankan `db upgrade` (jalur produksi) dan `CREATE EXTENSION vector`; skema yang dihasilkan cocok dengan `SCHEMA_VERSION`.
- [ ] Minimal satu tes memastikan hasil tetangga terdekat pgvector lewat jalur memori (bukan lewat string SQL yang ditulis di tes).
- [ ] Minimal satu tes memastikan `memory/__init__.py` tidak lagi melewati apa pun: jalur semantik benar-benar jalan di store tes.
- [ ] Tidak ada tes yang mengunduh model: embedder adalah stub 384 dimensi dan suite lulus saat offline.
- [ ] Dua kali berturut-turut berkas tes yang sama menghasilkan hasil identik (truncate, tanpa ketergantungan urutan).
- [ ] Grep tidak menemukan `sqlite` di `eidesoul/engine/**` maupun di `tests/**`; `AGENTS.md` tidak lagi menyebut SQLite sebagai store yang sah untuk tes.
- [ ] Waktu suite penuh di atau di bawah anggaran §6 di workstation ini, diukur sekali dan laporannya ditempel di perubahan.
- [ ] `python -m eidesoul selftest` dan `python scripts/adr.py --check` tetap lulus.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Jalannya pertama di Postgres membuat tes merah dan godaan untuk melemahkan tes | Merah berarti bug yang disembunyikan SQLite: perbaikannya masuk ke engine, tesnya tetap; memblokir tes berbasis store menuntut amandemen ADR, bukan skip |
| Suite jadi cukup lambat sampai tak ada yang mau menjalankannya | Satu DDL per sesi, truncate per tes, tanpa pemuatan model; anggaran §6 diukur dan dilaporkan |
| Developer tanpa database jadi terhalang | Pesan kegagalan membawa perintah compose satu baris; stack tes itu sekali pakai, bukan layanan yang harus dioperasikan |
| Truncate deadlock atau menyisakan baris | Satu koneksi per tes, `RESTART IDENTITY CASCADE`, dan satu tes yang memastikan store kosong setelah teardown |
| Menghapus cabang dialek mematahkan ketergantungan tersembunyi | Cabang dihapus pada langkah terpisah yang bisa di-grep; suite harus tetap lulus sebelum penghapusan mendarat |
| pgvector yang tidak ada di database operator disalahartikan sebagai kegagalan tes | `ensure_vector()` tetap best-effort di produksi (ADR-0001); database tes mensyaratkan extension itu dan mengatakannya saat absen |
| CI belum punya container service | Lane-nya didefinisikan di sini; menyambung CI adalah perubahan lanjutan, dan sampai itu perintah lokalnya sama dengan yang dicetak pesannya |

## 9. Interaksi dengan ADR lain

- **ADR-0001**: jalur pgvector dan Mem0 berhenti tak terlihat oleh tes; port tulis tunggal itulah yang diuji.
- **ADR-0011 / ADR-0012**: klaim antrean dan CAS lease diuji melawan row lock nyata, bukan database berkas satu penulis.
- **ADR-0016**: `db upgrade` menjadi satu-satunya cara tes memperoleh skema, jadi jalur migrasi teruji setiap kali tes jalan.
- **ADR-0026**: `tsvector` dan `pg_trgm` diuji di store yang memang bisa menjalankannya.
- **ADR-0027**: stack tes adalah project compose kedua dengan database dan port sendiri; stack dev tidak tersentuh.
- **ADR-0009**: gate ADR tetap independen dari store; pemeriksaan dokumen tak pernah butuh database.

## 10. Referensi kode

- `tests/support.py:71,88,515-539` — DSN SQLite, config sementara, dan helper yang akan diganti.
- `eidesoul/engine/db.py:12-13,46-49,76-90,123-124` — cabang dialek, `ensure_vector()`, pemilihan dialek insert.
- `eidesoul/engine/memory/__init__.py:178` — lewatan SQLite yang hilang bersama ADR ini.
- `eidesoul/engine/schema.py:22-24` — `JSONB` dan primary key varian integer.
- `compose.yaml` — parameterisasi `POSTGRES_DB` / `POSTGRES_PORT` dan project `eidesoul-test`.
- `AGENTS.md` §Storage, §Verify — teks aturan yang diamandemen ADR ini saat implementasi.
- `pyproject.toml` — tanpa dependency baru: `pytest` sudah runner-nya dan tidak ada pustaka container yang diperkenalkan.

---

*ADR ini disimpan di `docs/v0.1.0/adr/id/0029-test-store.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/0029-test-store.md`.*
