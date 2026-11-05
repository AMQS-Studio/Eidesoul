> Terjemahan turunan — jangan sunting; sumber: engine/0042-sync-on-start.md (sha256: 3f0c7d586896)

# ADR-0042: Sync saat Start — Perubahan File Berlaku Tanpa CLI

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0027 (distribusi), ADR-0030 (install dan init), ADR-0031 (struktur engine), ADR-0033 (core netral), ADR-0039 (setup terminal), ADR-0041 (konfigurasi voice), dan ADR-0045 (tuning voice di `soul.yaml`); menggantikan langkah manual `config import` untuk edit file normal.
- **Supersedes:** —
- **Terkait:** ADR-0027 (distribusi), ADR-0030 (install dan init), ADR-0031 (struktur modul), ADR-0033 (core netral), ADR-0039 (setup terminal), ADR-0041 (konfigurasi voice), ADR-0045 (tuning voice di `soul.yaml`)
- **Implementasi:** implemented — hash sync, advisory lock, konflik DB-menang, dan pemanggilan saat start sudah live.
- **Catatan lokasi:** document store mengikat runtime engine, jadi ADR ini duduk di `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0042-sync-on-start.md`.

---

## 1. Konteks

Dokumen database adalah sumber kebenaran; `config.yaml`, `soul.yaml`, dan `roles.yaml` adalah seed plus mirror. `DocumentStore.seed()` mengisi dokumen yang hilang dari file, dan dokumen yang sudah ada dibiarkan. Setelah itu, edit manual membutuhkan `monoagens config import`; arah sebaliknya membutuhkan `monoagens config export`.

Aturan itu aman tetapi mahal dalam operasi harian:

| Permukaan | Jalur saat ini | Friksi |
| --- | --- | --- |
| `roles.yaml` | edit file, lalu `monoagens config import` | role sering dituning; setiap putaran tuning butuh langkah CLI |
| `soul.yaml` | edit persona atau `voice`, lalu `monoagens config import` | satu langkah CLI untuk setiap tuning karakter atau voice |
| engine `config.yaml` | edit section, lalu `monoagens config import` | satu langkah CLI |

`sync_documents: true` tidak menyelesaikan ini: ia hanya mengisi dokumen yang hilang. Operator melihat file berubah dan mengharapkannya berlaku; langkah CLI mudah terlupa, dan langkah yang terlupa berarti engine berjalan dengan dokumen lama sementara file menunjukkan yang baru.

Arahan owner adalah membuat edit normal berlaku saat start, tanpa langkah CLI, sambil menjaga database tetap otoritatif agar perubahan sisi DB tidak pernah diam-diam ditimpa file basi.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Tanpa CLI untuk edit normal | `roles.yaml` atau `soul.yaml` yang berubah berlaku saat start berikutnya |
| DB tetap otoritatif | Konflik diselesaikan untuk database, dengan warning |
| Tanpa style turunan | `soul.style` dan resolver dihapus; seluruh file soul adalah unit |
| Startup terbatas | Hanya perbandingan hash; tanpa resolver dan tanpa embedding saat boot |
| Tanpa watcher | Tanpa dependensi baru dan tanpa pemantauan file jangka panjang; hanya saat start |
| Satu writer | Advisory lock Postgres membuat satu proses menerapkan perubahan file |
| Dapat diuji | Empat kasus sync, kasus first sync, dan kasus sisi hilang adalah test |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan `config import` manual** | aturan saat ini | Ditolak: friksinya adalah masalahnya; langkah yang terlupa menjalankan dokumen lama |
| **B. Pantau file** | watcher filesystem menerapkan edit langsung | Ditolak: dependensi baru, race dengan writer, dan instance standby |
| **C. Import setiap file setiap start** | file menang, tanpa perbandingan | Ditolak: file basi atau parsial menimpa perubahan sisi DB |
| **D. Hash sync saat start, DB menang konflik** | bandingkan file, DB, dan hash sync terakhir | **Dipilih** |
| **E. File menang konflik** | file adalah kata operator | Ditolak: membalik otoritas dokumen dan menyembunyikan perubahan sisi DB |
| **F. Pertahankan `soul.style` dan resolve saat start** | turunkan selector pack dari teks soul | Ditolak: ADR-0045 menghapus `soul.style` dan resolver |
| **G. Sync seluruh file soul** | satu hash, satu import, tanpa blok turunan | **Dipilih** |

## 4. Keputusan

### 4.1 Satu pass sync saat start

`DocumentStore.sync()` berjalan sekali per proses, setelah schema siap dan sebelum config dimuat. Ia membaca file host, membandingkannya dengan dokumen dan hash sync terakhir di `state`, lalu menerapkan arah yang hilang.

Pass ini dijaga advisory lock Postgres sehingga hanya satu instance yang menerapkan perubahan file; standby yang tidak mendapat lock melewati pass dan membaca dokumen.

| File | DB | Hash sync terakhir | Aksi |
| --- | --- | --- | --- |
| berubah | tidak | file != last, db == last | import file ke DB |
| tidak | berubah | db != last, file == last | export DB ke mirror file |
| berubah | berubah | keduanya != last | **DB menang**: export DB, warning |
| tidak | tidak | keduanya == last | no-op |
| ada | hilang | tanpa last | import file |
| hilang | ada | tanpa last | export DB |
| keduanya ada, tanpa last | — | first sync | **DB menang**: export DB, warning |
| hilang | hilang | — | no-op |

Hash adalah SHA-256 dari konten kanonik: seluruh `roles.yaml`, seluruh `soul.yaml`, dan section `engine` di `config.yaml`. Host key `config.yaml` bersifat host-local dan bukan dokumen; ia dibaca langsung dari file.

`sync_documents: false` menonaktifkan pass sepenuhnya, seperti menonaktifkan `seed`, `import`, dan `export` saat ini.

### 4.2 File soul

File soul adalah satu unit: `description`, `background`, `persona`, `voice`, `examples`, `pronouns`, dan
`rules`. Tidak ada blok `style:` turunan dan tidak ada resolver; ADR-0045 menghapus `soul.style`.

Saat file berubah dan DB tidak, sync meng-import seluruh file ke dokumen `soul`. Saat DB berubah dan file
tidak, sync mengekspor dokumen ke mirror. Konflik mengikuti aturan DB-menang yang sama seperti dokumen
lain. Tidak ada panggilan model, embedding, atau resolver keyword saat boot.

### 4.3 Role dan section engine

`roles.yaml` tidak punya resolver: file yang berubah di-import sebagai dokumen `role:<name>`. Mode file tetap `0600`; sync mencatat hash, bukan isi role.

Section `engine` di `config.yaml` mengikuti tabel empat kasus yang sama. Edit host key (database, workspace, sandbox, dan sisanya) bukan perubahan dokumen dan dibaca dari file saat start.

### 4.4 Yang tetap eksplisit

- `monoagens config import` dan `monoagens config export` tetap ada untuk arah paksa dan pemulihan.
- Proses yang berjalan tidak memantau file; edit berlaku saat start berikutnya. `/reload` di masa depan dapat memakai pass yang sama, tetapi bukan bagian dari keputusan ini.

## 5. Konfigurasi

Tidak ada config key baru. Host key yang ada mengontrol pass:

| Key | Default | Efek |
| --- | --- | --- |
| `sync_documents` | `true` | mengaktifkan `seed`, `sync`, `import`, dan `export`; `false` menonaktifkan semuanya |

State sync hidup di tabel `state` yang ada, jadi tidak ada perubahan schema:

| State key | Nilai |
| --- | --- |
| `sync:soul` | hash `soul.yaml` terakhir yang disinkronkan |
| `sync:engine` | hash section `engine` terakhir yang disinkronkan |
| `sync:role:<name>` | hash body role terakhir yang disinkronkan |

Nilainya hash saja; tidak ada isi file dan tidak ada secret yang disimpan di state row.

## 6. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Langkah CLI yang dibutuhkan untuk edit `roles.yaml` | 0 |
| Langkah CLI yang dibutuhkan untuk edit `soul.yaml` | 0 |
| Edit sisi DB yang hilang karena file basi | 0 |
| Instance yang menerapkan perubahan file pada pass yang sama | 1 |
| Kasus sync yang dicakup test (tabel empat kasus, first sync, sisi hilang) | 6 |
| Nilai hash yang ditulis memuat isi file | 0 |
| Operasi file saat `sync_documents: false` | 0 |
| `pytest -q` | lolos |
| `ruff check` | lolos |
| `python -P scripts/types.py --check` | lolos |
| `python -P scripts/adr.py --check` | lolos |
| `python -P scripts/adr.py --translate-check` | lolos |
| `python scripts/selfcheck.py --check` | lolos |

## 7. Kriteria penerimaan

- [x] `roles.yaml` yang berubah berlaku saat start berikutnya tanpa `monoagens config import`.
- [x] `soul.yaml` yang berubah — termasuk blok `voice` — di-import saat start berikutnya.
- [x] Tidak ada resolver, panggilan model, atau embedding saat sync.
- [x] Perubahan sisi DB mengekspor dokumen ke mirror file.
- [x] Perubahan file dan DB diselesaikan untuk DB, mengekspor mirror, dan memberi warning.
- [x] First sync tanpa state diselesaikan untuk DB dan memberi warning.
- [x] File yang hilang di-import dari DB; dokumen yang hilang di-import dari file.
- [x] Standby tidak dapat menerapkan pass selama instance lain memegang advisory lock.
- [x] `sync_documents: false` tidak melakukan seed, sync, import, atau export.
- [x] Tidak ada watcher atau dependensi runtime baru.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| File basi menimpa perubahan sisi DB | DB menang di setiap konflik; mirror ditulis ulang dan warning dicatat |
| Dua instance menerapkan edit yang sama | Advisory lock Postgres membuat satu writer; yang lain melewati |
| Startup menjadi lambat | Sync hanya perbandingan hash; tanpa resolver dan tanpa model load |
| State row melenceng atau hilang | Hash dihitung ulang dari file dan dokumen; state yang hilang mengikuti aturan first sync |
| `roles.yaml` terbaca world-readable | File tetap `0600`; sync mencatat hash, bukan body |
| Proses yang berjalan melewatkan edit | Hanya saat start; docs menyatakan restart diperlukan sampai `/reload` ada |

## 9. Interaksi dengan ADR lain

- **ADR-0027**: `sync_documents` tetap host key dengan default yang sama; pass menghormati aturan kapabilitas default-off.
- **ADR-0030**: `init` tetap menulis file seed; `run` pertama meng-importnya tanpa perintah kedua.
- **ADR-0031**: sync tetap di `engine/stores/documents.py`; tanpa modul baru dan tanpa perubahan budget.
- **ADR-0033**: sync tidak membawa kosakata bahasa, budaya, transport, atau vendor.
- **ADR-0039**: receipt setup tetap menulis file; operator tidak lagi butuh import lanjutan untuk edit berikutnya.
- **ADR-0041**: section voice adalah bagian dari `soul.yaml` setelah ADR-0045; sync memakai pass yang sama dan menerapkan seluruh dokumen soul.

## 10. Referensi kode

- `monoagens/engine/stores/documents.py` — `sync()`, `seed()`, `import_from_files()`, `export_to_files()`, dan perbandingan hash.
- `monoagens/engine/config.py` — host key `sync_documents`.
- `monoagens/engine/schema.py` — tabel `state` untuk hash sync.
- `monoagens/engine/jobs/lease.py` — advisory lock untuk membuat satu writer.
- `monoagens/__main__.py` — call site `run`, `console`, dan `database seed`.
- `monoagens/gateways/whatsapp/cli.py` — pemuatan dokumen gateway.
- `monoagens/admin/database.py` — jalur seed database.
- `monoagens/engine/soul.py` — dokumen soul, termasuk blok `voice` mendatang.
- `tests/` — tabel empat kasus, first sync, sisi hilang, dan cakupan lock.

---

*ADR ini tinggal di `docs/v0.1.0/adr/engine/0042-sync-on-start.md`.*
