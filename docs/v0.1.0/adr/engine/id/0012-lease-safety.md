> Terjemahan turunan — jangan sunting; sumber: engine/0012-lease-safety.md (sha256: 36f681ebae8c)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0012: Lease Safety — CAS, Fencing Token, dan Jam Database

- **Status:** accepted
- **Implementasi:** implemented — bukti: CAS acquire, `generation`, jam DB, audit takeover, renew/release berkunci generasi, `LeaseGuard.require()` terikat ke `MemoryStore` (`_fence` pada setiap mutasi kanonik, dibaca di koneksi penulis itu sendiri) dan ke klaim/complete/fail `CognitionQueue`, revisi 2 dan 13; gate: `pytest -q` 831 lulus, `scripts/adr.py --check`
- **Tanggal:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0011 (crash safety & recovery) dan temuan review arsitektur: lease tanpa compare-and-swap, memakai jam lokal
- **Supersedes:** —
- **Terkait:** ADR-0011 (recovery), ADR-0013 (resume turn)
- **Amandemen:** 2026-11-02 — normalisasi kerangka (ADR-0009 §4.4): menambah bagian Interaksi dengan ADR lain; Referensi bergeser ke §11. 2026-11-03 — audit: rentang `supervisor.py` diperbaiki menjadi `36-129`; baris basi "menunggu keputusan ADR-0009" diselaraskan (ADR-0009 sudah accepted, migrasi `docs/v0.1.0/adr/`). 2026-11-03 — audit: kolom fencing diseragamkan menjadi `generation` (bukan `lease_generation`); jalur migrasi menunjuk ADR-0016. 2026-11-03 — §4.3 kini berjalan di koneksi penulis itu sendiri: `MemoryStore._fence` memanggil `LeaseGuard.require` (yang mencatat penolakan) lalu membaca `engine_lease.generation` di dalam transaksi yang sama dengan tulisannya, sehingga cek dan mutasi memakai satu snapshot; update job membawa generasi klaim dan menolak saat generasi hidup sudah bergerak.
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0012-lease-safety.md`.

---

## 1. Konteks

- `Lease.acquire` membaca baris, memeriksa `heartbeat_at < now - ttl`, lalu **UPDATE tanpa syarat** (`lease.py:76-101`). Tidak ada CAS, tidak ada `RETURNING` yang memverifikasi pemenang.
- Dua standby yang sama-sama melihat lease kedaluwarsa bisa **sama-sama menjadi primary**; yang kalah baru tahu saat `renew` gagal, lalu freeze setelah 3 kegagalan (~15 detik).
- Expiry memakai `time.time()` masing-masing proses (`lease.py:57,84,104`), sehingga skew jam antar host dapat mendeklarasikan primary sehat sebagai mati.
- `frozen()`, `renew()`, dan `holder()` tidak menangkap exception; error koneksi keluar dari `Supervisor.serve()` dan mematikan proses (`supervisor.py:93-104`). Hanya `rowcount == 0` yang dihitung sebagai kegagalan renew.
- Recovery ADR-0011/0013 bergantung pada asumsi “hanya satu primary” — tanpa perbaikan ini, resume bisa jalan di dua instance sekaligus.

## 2. Decision drivers

1. **Mutual exclusion**: tepat satu primary pada satu waktu.
2. **Fencing**: pemegang lama tidak boleh menulis mengirim setelah digantikan.
3. **Ketahanan jam**: keputusan berbasis waktu database, bukan jam aplikasi.
4. **Degradasi anggun**: error DB dihitung sebagai kegagalan renew, bukan kematian proses.
5. **Kompatibilitas**: freeze/takeover tetap seperti sekarang.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Status quo** | Read-check-update, jam lokal | Tidak ada perubahan | Split brain; sensitif skew; error DB mematikan proses | Ditolak |
| **B. CAS saja** | `UPDATE ... WHERE heartbeat_at < now() - ttl RETURNING` | Menutup race acquire | Masih rentan clock skew; belum ada fencing untuk penulis lama | Kurang |
| **C. CAS + jam DB + fencing token** | CAS dengan `clock_timestamp()`; `generation` naik setiap acquire; penulisan/pengiriman membawa generation | Mutual exclusion + fencing + tahan skew | Menambah kolom dan pemeriksaan generation di jalur tulis | **Dipilih** |

## 4. Keputusan

### 4.1 Acquire dengan CAS

```sql
UPDATE engine_lease
SET instance_id = :me,
    host = :host,
    pid = :pid,
    mode = :mode,
    generation = generation + 1,
    started_at = clock_timestamp(),
    heartbeat_at = clock_timestamp()
WHERE id = 1
  AND (instance_id = :me OR heartbeat_at < clock_timestamp() - (:ttl || ' seconds')::interval)
RETURNING instance_id, generation;
```

- Pemanggil hanya menjadi primary kalau `RETURNING` mengembalikan dirinya sendiri.
- Baris dibuat dengan `INSERT ... ON CONFLICT (id) DO NOTHING` bila belum ada, lalu CAS yang sama.
- Takeover eksplisit tetap dihormati (`takeover=true` melewati syarat expiry), tetapi tetap melalui CAS dan tetap menaikkan `generation`.

### 4.2 Renew dengan generation

```sql
UPDATE engine_lease
SET heartbeat_at = clock_timestamp()
WHERE id = 1 AND instance_id = :me AND generation = :gen
RETURNING generation;
```

`rowcount = 0` atau exception = satu kegagalan renew. Tiga kegagalan berturut-turut → primary berhenti melayani dan freeze.

### 4.3 Fencing di jalur tulis

- Setiap penulisan yang mengubah dunia nyata membawa `generation`:
  - mengirim pesan (`outbound_messages.generation`),
  - menulis canonical memori (kolom `generation` di `turns`/job),
  - menandai job selesai.
- Sebelum menulis, periksa `generation` saat ini di DB. Kalau sudah berubah, batalkan operasi itu dan tandai `superseded` — pemegang lama tidak boleh menyelesaikan pekerjaan setelah digantikan.
- Untuk pengiriman: `send_text` tidak bisa “diurungkan”, jadi pemeriksaan generation harus terjadi **sebelum** panggilan transport, bukan sesudahnya.
- Cek berjalan di koneksi penulis itu sendiri: `MemoryStore._fence` membaca `engine_lease.generation` di dalam transaksi yang sama dengan tulisannya, setelah `LeaseGuard.require` mencatat penolakan, sehingga perbandingan dan mutasi memakai satu snapshot.

### 4.4 Jam

- Semua perbandingan waktu lease memakai `clock_timestamp()` (nilai nyata saat itu), bukan `now()` (waktu mulai transaksi) dan bukan jam aplikasi.
- Kolom `heartbeat_at` tetap float, tetapi selalu diisi dari database.

### 4.5 Error DB

- `frozen()`, `renew()`, `holder()`, dan `acquire()` membungkus error koneksi sebagai kegagalan lease, bukan pengecualian yang membunuh supervisor.
- Kalau error berlanjut melewati ambang, primary masuk mode `frozen`/`standby` dan mencoba lagi dengan backoff.

### 4.6 Release

```sql
UPDATE engine_lease
SET instance_id = '', heartbeat_at = 0
WHERE id = 1 AND instance_id = :me AND generation = :gen;
```

Release hanya oleh pemilik generation saat ini.

## 5. Skema

```sql
engine_lease(
  id           int primary key check (id = 1),
  instance_id  text not null default '',
  host         text not null default '',
  pid          int  not null default 0,
  mode         text not null default 'standby',
  generation   bigint not null default 0,
  started_at   double precision not null default 0,
  heartbeat_at double precision not null default 0
)
```

Kolom `generation` ditambahkan; `SCHEMA_VERSION` naik dan memerlukan migrasi (ADR-0005/0011 untuk jalur migrasi).

## 6. Konfigurasi

```yaml
lease:
  ttl_seconds: 30
  renew_interval_seconds: 5
  failures_before_freeze: 3
  acquire_retry_seconds: 5
```

## 7. Metrik dan gate

| Metrik | Target |
|---|---|
| Dua primary bersamaan | 0 (diuji dengan konkurensi) |
| Renew gagal saat DB sehat | 0 |
| Error DB mematikan proses | 0 |
| Penulisan dengan generation basi | ditolak dan tercatat |
| Waktu takeover setelah crash | ≤ ttl + renew interval |

## 8. Acceptance criteria

- [ ] Dua `acquire` bersamaan → tepat satu pemenang (`RETURNING`).
- [ ] Acquire dengan baris expired dua kali → hanya satu yang boleh menulis generation baru.
- [ ] Skew jam host tidak memengaruhi expiry (jam DB).
- [ ] `renew` memakai `instance_id` + `generation`.
- [ ] Penulisan/pengiriman memeriksa generation sebelum eksekusi.
- [ ] Error DB tidak mematikan proses; dihitung sebagai kegagalan renew.
- [ ] `release` hanya oleh owner generation.
- [ ] Freeze/takeover tetap berfungsi seperti sebelumnya.
- [ ] Test konkurensi + test skew + test error DB ada di CI.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Generation tidak diperiksa di semua jalur tulis | Port tunggal (`LeaseGuard`) yang wajib dipakai sebelum efek samping; test yang gagal jika transport dipanggil tanpa guard |
| Migrasi kolom `generation` | Alembic + baseline; ADR-0016 (skema & migrasi) |
| Retry acquire terlalu rapat | Backoff + jitter |
| Freeze tidak berlaku pada pekerjaan yang sedang jalan | Cek generation di batas langkah; job diserahkan ke owner baru |

## 10. Interaksi dengan ADR lain

- **ADR-0011 (crash safety & recovery)** — ADR ini adalah lanjutan langsungnya: recovery-nya bergantung pada asumsi “hanya satu primary”, sehingga lease tanpa CAS/skew jam membatalkan jaminan tersebut; jalur migrasi kolom `generation` ada di ADR-0016.
- **ADR-0013 (resume turn)** — resume turn juga mengasumsikan hanya satu primary; tanpa perbaikan ini resume bisa jalan di dua instance sekaligus. Keduanya terdaftar pada header `Terkait:` sebagai ADR-0011 (recovery) dan ADR-0013 (resume turn).
- **ADR-0016 (skema & migrasi)** — pemilik DDL kanonik dan Alembic baseline: kolom `generation`, kenaikan `SCHEMA_VERSION`, serta constraint/index yang ADR ini butuhkan.
- **ADR-0009 (rumah ADR)** — sudah diputuskan: ADR hidup di `docs/v0.1.0/adr/`; migrasi menyentuh lokasi berkas, bukan keputusan lease di sini.

## 11. Referensi kode

- `eidesoul/engine/lease.py:56-129` — acquire/renew/release.
- `eidesoul/engine/lease.py:145-163` — freeze.
- `eidesoul/engine/supervisor.py:36-129` — loop primary/standby.
- `eidesoul/engine/schema.py:270-290` — `engine_lease`, `engine_control`.
- `eidesoul/engine/db.py:57-86` — create_all/ensure vector.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0012-lease-safety.md`.*
