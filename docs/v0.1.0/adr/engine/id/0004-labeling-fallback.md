> Terjemahan turunan — jangan sunting; sumber: engine/0004-labeling-fallback.md (sha256: 6f681b689e74)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0004: Fallback Labeling — Provisional Canonical dengan `level NULL` sebagai Sentinel

- **Status:** accepted
- **Implementasi:** partial — ada: state machine enam status + view `memories_readable`, sentinel `level NULL`, drop output parsial, `staged`, TTL 7 hari + tombstone, CHECK, tes; sisa: retry berbatas dengan backoff (5 percobaan) milik worker capture (W3.2)
- **Tanggal:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical + Mem0 adapter), ADR-0002 (gate capture + assess batch), ADR-0003 (cognition worker)
- **Supersedes:** —
- **Terkait:** ADR-0005 (retention), ADR-0006 (privasi grup), ADR-0008 (integritas), ADR-0011 (crash safety)
- **Amandemen:** 2026-11-02 — state `staged` ditambahkan (ADR-0008 §4.4 A): baris berlabel yang ditahan kuota volume tulisan, TTL mengikuti `kind`, promosi lewat recall-miss atau batch; constraint §5.2 disesuaikan (`state IN ('ok','staged')` ⇔ `level IS NOT NULL`), view §5.3 tidak berubah sehingga `staged` tidak pernah terbaca di jalur normal, dan baris `pending` tidak diperlakukan sebagai fakta tepercaya. 2026-11-03 — audit: rentang `_assess` diselaraskan ke `privacy.py:282-309`.
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0004-labeling-fallback.md`.

---

## 1. Konteks

- `_assess` mengembalikan `None` setelah dua percobaan gagal, lalu `capture_turn` berhenti tanpa menyimpan (`privacy.py:282-309`). Artinya setiap gangguan LLM menghapus memori secara diam-diam.
- Default privasi berbahaya untuk fallback: `default_level: 2`, `group_max_level: 2` (`config.py:118-126`). Menjatuhkan data belum dilabeli ke `default_level` berarti konten yang sebenarnya sensitif **lolos ke grup**.
- Output LLM bisa parsial: `worth=true` tetapi `level`/`subject` tidak valid. Menyimpan setengah matang akan menghidupkan kembali kelas bug yang sedang diberantas (metadata keamanan tidak lengkap).
- Raw kandidat (`memory_events`) sudah durable; yang belum diputuskan adalah bagaimana merepresentasikan kandidat yang belum terlokalisasi di canonical.

## 2. Decision drivers

1. **Tidak ada kehilangan memori** saat LLM bermasalah.
2. **Tidak ada bocor**: data belum dilabeli tidak boleh direcall atau diungkap.
3. **Invariant yang bisa diuji**, bukan disiplin manusia.
4. **Satu lifecycle baris**: hindari race insert-vs-update.
5. **Pemulihan otomatis**: retry, terlihat, dan bisa diproses ulang.
6. **Data minimization**: raw yang tak pernah terlokalisasi punya TTL.
7. **Biaya terkendali**: retry dibatasi.

## 3. Koreksi pipeline

Tiga tahap dibedakan tegas:

| Tahap | Sifat | Kapan |
|---|---|---|
| **Hard exclusion** — kredensial, krisis, instruksi berbahaya | Tidak boleh disimpan sama sekali | **Sebelum** `memory_events` ditulis |
| **Gate struktural** — panjang, command, dedupe, rate limit | Boleh disimpan, hanya tidak layak diproses sekarang | Saat job dijalankan |
| **Labeling** — worth, level, subject | Menentukan kegunaan dan keamanan | Setelah `memory_events` ada |

Amandemen ADR-0002: “`memory_events` ditulis apa pun hasil gate” diganti menjadi “ditulis untuk kandidat yang lolos **hard exclusion**; gate struktural dievaluasi saat job.”

## 4. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Drop** | Gagal label → tidak disimpan | Paling aman | Kehilangan memori diam-diam; klaim “menyimpan” bohong | Ditolak |
| **B. Deferred canonical** | Raw di `memory_events`; baris `memories` hanya setelah label sukses | Invariant sederhana | Admin harus join; dedupe tidak melihat pending | Ditolak demi C |
| **C. Provisional canonical (`level NULL`)** | Baris `memories` dibuat `pending` dengan `level NULL`; hanya `state='ok'` yang bisa dibaca | Satu lifecycle; dedupe melihat pending; admin melihat antrean label | Butuh penegakan view/port agar pending tidak pernah terbaca | **Dipilih** |
| **D. Provisional + injeksi owner-only** | Seperti C, tapi boleh masuk DM pemilik | Paling tidak “pelupa” | Risiko tertinggi; pihak ketiga bisa terpapar; sulit diuji | Ditolak |

## 5. Keputusan

> **`level NULL` adalah sentinel “tidak boleh dipakai”. Ia tidak pernah menjadi state yang bisa dipakai sementara. Satu-satunya transisi ke “bisa dipakai” adalah `state = ok` dengan `level` valid.**

### 5.1 State dan kolom

| Kolom | Aturan |
|---|---|
| `state` | enam nilai: `pending` / `ok` / `staged` / `failed` / `not_worth` / `superseded` (enum & CHECK di ADR-0016 §4.3) |
| `level` | NULL kecuali `state = ok` |
| `confirmed` | Hanya bermakna saat `state = ok` |
| `label_attempts` | Jumlah percobaan labeling |
| `last_error` | Alasan kegagalan terakhir (dipotong) |
| `label_after` | Waktu percobaan berikutnya (backoff) |
| `expires_at` | TTL untuk state non-`ok` |

`vetoed` bersifat ortogonal: bisa terjadi pada `state='ok'` dan langsung mengeluarkan baris dari view baca.

### 5.2 Constraint

```sql
CHECK ( state IN ('pending','ok','staged','failed','not_worth','superseded') )
CHECK ( (state IN ('ok','staged','superseded')) = (level IS NOT NULL) )
CHECK ( level IS NULL OR level BETWEEN 0 AND 4 )
CHECK ( state <> 'ok' OR confirmed IS NOT NULL )
```

Bentuk final DDL dan index ada di ADR-0016 §4.2-§4.3; baris `staged` (ADR-0008 §4.4 A) membawa level
karena ia sudah berlabel, dan `superseded` **mempertahankan** level baris yang digantikan (ADR-0008 B).
Semua tetap tidak bisa dibaca selama state bukan `ok`.

### 5.3 Satu jalur baca

```sql
CREATE VIEW memories_readable AS
SELECT * FROM memories
WHERE state = 'ok' AND vetoed = false;
```

- Semua retrieval, prompt, dan disclosure **hanya lewat view ini**.
- Worker dan admin memakai tabel dasar.
- Port: `MemoryStore.read()` → `memories_readable`; `MemoryStore.admin()` → `memories`.
- Opsional (lapisan tambahan): RLS pada tabel dasar sehingga role aplikasi hanya melihat baris `state='ok' AND vetoed=false`.

### 5.4 State machine

| State | Level | Recall? | Diungkap? | Transisi |
|---|---|---|---|---|
| `pending` | NULL | Tidak | Tidak | → `ok` / `failed` setelah retry |
| `staged` | 0–4 | Tidak (kecuali jalur recall-miss) | Tidak | → `ok` saat promosi (recall-miss atau batch) |
| `ok` | 0–4 | Ya, sesuai scope | Sesuai kebijakan level | → `superseded` (digantikan) / `vetoed` (kolom) / purge |
| `failed` | NULL | Tidak | Tidak | TTL purge + alert |
| `not_worth` | NULL | Tidak | Tidak | TTL purge (backfill/eval) |
| `superseded` | 0–4 | Tidak | Tidak | TTL 30 hari lalu purge + tombstone |

`vetoed` bukan state: ia kolom ortogonal (0–4 tetap level-nya) yang mengeluarkan baris dari view baca
tanpa mengubah `state`. Purge menghapus baris dan menyisakan tombstone audit; tidak ada state
`expired`.

`staged` (ADR-0008 §4.4 A): baris **sudah berlabel** tetapi ditahan karena kuota volume tulisan. Ia
tidak dibaca di jalur normal — invariant view tetap “hanya `ok`”. Bedanya dengan `pending`: `pending`
untuk data yang belum terlokalisasi (TTL 7 hari), `staged` untuk data berlabel (TTL mengikuti `kind`,
ADR-0005).

### 5.5 Lifecycle

1. Turn lolos hard exclusion → `memory_events` + baris `memories(state='pending', level=NULL)` dalam transaksi yang sama dengan pesan.
2. Worker mencoba labeling → sukses: `state='ok'`, `level` diisi, `confirmed` sesuai hasil.
3. Output parsial (`worth=true` tanpa level valid, atau level di luar 0–4) diperlakukan sebagai **pending**, bukan tersimpan setengah matang.
4. Gagal → retry dengan backoff eksponensial; maksimum **5 percobaan**; setelah habis → `state='failed'` + alert di `/status`.
5. `pending`/`failed`/`not_worth` melewati TTL **7 hari** → purge + tombstone audit tanpa isi.
6. `ok` → retensi mengikuti level (ADR-0005).
7. `veto` kapan saja mengeluarkan baris dari view; baris tetap ada untuk audit.
8. `staged` (kuota volume, ADR-0008 §4.4 A) dipromosikan ke `ok` oleh: (a) **recall-miss** — retrieval
   normal tidak menemukan apa pun, lalu baris `staged` yang cocok diangkat sebelum dipakai, dibatasi
   satu kali per turn dan `max_rows`; atau (b) batch worker saat burst. Kalau tidak pernah dipakai
   maupun dipromosikan, ia habis oleh TTL `kind`-nya (tombstone tanpa isi).

### 5.6 Kalau LLM tidak tersedia

- Raw tetap ditulis (kecuali hard exclusion); baris tetap `pending` dengan `level NULL`.
- Soul tidak akan mengingat hal baru selama LLM mati, dan tidak akan mengarang memori lama.
- Backlog terlihat di `/status`; worker memproses ulang saat LLM hidup.
- Ini degradasi jujur, bukan kehilangan: tidak ada baris yang bisa dibaca sebelum terlokalisasi.

## 6. Konfigurasi

```yaml
memory:
  labeling:
    max_attempts: 5
    backoff_seconds: 5
    pending_ttl_days: 7
    staged_ttl: kind        # baris staged (ADR-0008) mengikuti TTL kind
    alert_backlog: 100
```

## 7. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Label success rate | ≥ 99% |
| Backlog `pending` p95 age (LLM sehat) | < 10 menit |
| Raw pending dipurge karena TTL | ~0 saat LLM sehat |
| Retry per item | ≤ 2 panggilan |
| Kebocoran dari state non-`ok` | 0 (diuji) |

## 8. Acceptance criteria

- [ ] Tidak ada cara membaca `memories` untuk prompt selain lewat `memories_readable`.
- [ ] Baris `state='pending'` tidak pernah muncul di retrieval/prompt/disclosure, bahkan jika pemanggil lupa memfilter.
- [ ] Constraint CHECK menolak baris `state='ok'` tanpa level, dan baris non-`ok` yang punya level.
- [ ] Output parsial tidak pernah menjadi baris `ok`.
- [ ] Hard exclusion terjadi sebelum `memory_events` ditulis.
- [ ] Retry terbatas 5 percobaan dengan backoff; kegagalan terlihat di `/status`.
- [ ] TTL 7 hari untuk state belum berlabel (`pending`/`failed`/`not_worth`); `staged` mengikuti TTL `kind` (ADR-0008), dengan tombstone audit.
- [ ] Test: veto pada baris `ok` langsung mengeluarkannya dari view.
- [ ] Baris `staged` tidak pernah muncul di retrieval/prompt/disclosure normal; promosi hanya lewat recall-miss (dibatasi) atau batch.
- [ ] Constraint menolak `state='staged'` tanpa level.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Ada jalur baca yang melewati view | Port `read()` sebagai satu-satunya akses; test yang gagal jika tabel dasar diakses untuk prompt |
| Pending menumpuk saat LLM lama mati | Alert ambang; raw TTL; degradasi jujur terdokumentasi |
| `level NULL` disalahartikan sebagai level rendah | CHECK constraint + view; tidak ada default 2 untuk fallback |
| Biaya retry | Batas percobaan + backoff + metrik |
| Data pihak ketiga tersimpan mentah | Hard exclusion + TTL + ADR-0006 |

## 10. Interaksi dengan ADR lain

- **ADR-0002** — diamandemen: hard exclusion sebelum `memory_events`.
- **ADR-0003** — retry dan alert dijalankan worker C; item gagal masuk dead-letter.
- **ADR-0005** — retensi untuk `ok` mengikuti level; non-`ok` memakai TTL 7 hari.
- **ADR-0006** — pre-filter L3/L4 tetap berlaku setelah pelabelan.
- **ADR-0008** — provenance/trust; pending tidak diperlakukan sebagai fakta tepercaya.
- **ADR-0011** — crash safety: baris `pending` harus bertahan dan dipulihkan setelah restart.

## 11. Referensi kode

- `eidesoul/engine/privacy.py:222-303` — `capture_turn`, `_assess`.
- `eidesoul/engine/privacy.py:133-140` — `_risky` (level dan confirmed).
- `eidesoul/engine/memory/privacy.py:52-71` — `show`, `disclose`.
- `eidesoul/engine/memory/mem0.py:360-380` — guard `capture`.
- `eidesoul/engine/config.py:118-126` — `PrivacyConfig` (default 2, group max 2).
- `eidesoul/engine/agent.py:906-921` — gate capture saat ini.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0004-labeling-fallback.md`.*
