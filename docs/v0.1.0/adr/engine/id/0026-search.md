> Terjemahan turunan — jangan sunting; sumber: engine/0026-search.md (sha256: 7b397bad3699)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0026: Pencarian — Full-Text Pesan, Trigram, dan Scope

- **Status:** accepted
- **Implementasi:** implemented — bukti: `messages.text_tsv` + index GIN/trigram (revisi 9), FTS di `stores/history.py` dengan fallback trigram + paging eksklusif, `stores/knowledge.py`, backend di `/status`, `tests/test_search.py`
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** pencarian arsip pesan hanya `ILIKE` per term tanpa index teks, sehingga biayanya tumbuh linear terhadap jumlah pesan dan recall-nya bergantung persis pada substring; jalur memori punya mesinnya sendiri (Mem0), dan scope baca tetap dibatasi keanggotaan room.
- **Supersedes:** —
- **Terkait:** ADR-0001 (Mem0 sebagai satu-satunya jalur memori), ADR-0006/0007 (privasi & scope baca, §4.5), ADR-0016 (pemilik DDL & index), ADR-0019 (metrik), ADR-0021 (kontrak keluaran tool)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0026-search.md`.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Pencarian pesan memakai `ILIKE` per term atas `text`/`sender_name`, `limit` ≤ 50 | `eidesoul/engine/stores/history.py:318-347` |
| Command `/cari` memanggil store itu untuk room aktif | `eidesoul/engine/commands/cari.py:25-40` |
| Tool `chat_history` bisa menelusuri arsip room lain, dengan buffer `cross_reads` | `eidesoul/gateways/whatsapp/tools/chat_history.py` |
| Index pesan: `(thread_id, ts, id)` dan `(sender_address)` — tidak ada index teks | `eidesoul/engine/schema.py:66-67` |
| Memori punya jalur sendiri (Mem0 + `memories_readable`), bukan `messages` | `eidesoul/engine/commands/memori.py:69,99`, ADR-0001 |
| Scope baca = keanggotaan room + gerbang `visibility` | ADR-0007 §4.5 |
| `pgvector` sudah jadi preseden extension di baseline | ADR-0001/0016 |
| Keluaran tool punya cap dan penanda `next` | ADR-0021 |

Dua hal yang **tidak** berubah oleh ADR ini: scope baca (tetap keanggotaan room, tanpa override) dan kepemilikan memori (tetap Mem0). ADR ini hanya mengganti cara teks dicari dan mengukur biayanya.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Biaya pencarian tidak linear | Full-text index, bukan `ILIKE` penuh tabel |
| Recall untuk bahasa Indonesia | FTS tanpa kamus + trigram sebagai jaring kedua |
| Scope tidak boleh melebar | Filter `thread_id` + keanggotaan tetap di query yang sama |
| Nol keputusan LLM | Ranking deterministik; tanpa rerank model |
| Satu pemilik DDL | Kolom turunan dan index lewat ADR-0016 |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Biarkan `ILIKE`** | Tanpa index | Ditolak: biaya linear; recall substring saja |
| **B. FTS saja (`simple`)** | `tsvector` + GIN | Ditolak sebagai satu-satunya jalur: morfologi Indonesia tidak tertangani |
| **C. FTS + `pg_trgm`** | FTS untuk token eksak, trigram untuk imbuhan/tipis salah tulis | **Dipilih** |
| **D. Pencarian semantik atas `messages`** | Embedding setiap pesan | Ditolak: biaya embed per pesan dan jalur retrieval kedua; memori tetap Milik Mem0 (ADR-0001) |

## 4. Keputusan

### 4.1 Full-text untuk pesan

Kolom turunan `messages.text_tsv = to_tsvector('simple', coalesce(text,'') || ' ' || coalesce(sender_name,''))` dengan index GIN. Query memakai `websearch_to_tsquery('simple', :q)`, ranking `ts_rank_cd` dengan tiebreak `ts DESC, id DESC`.

### 4.2 `simple`, bukan stemming bahasa

Katalog soul bisa multi-bahasa dan engine tidak memilih bahasa untuk operator. Konsekuensinya morfologi Indonesia tidak tertangani FTS; itu tugas §4.3, bukan alasan menambahkan kamus per bahasa ke dalam engine.

### 4.3 `pg_trgm` sebagai jaring kedua

`similarity()` + index `gin_trgm_ops` untuk: pesan (fallback bila FTS kosong), `people.display_name`, dan fakta pendek. Ambang `engine.search.trgm_threshold: 0.3`. Urutan tetap: FTS dulu; trigram hanya dipanggil bila FTS tidak memberi hasil, sehingga biaya dan kualitas keduanya bisa dibandingkan.

### 4.4 Scope tidak berubah

Pencarian pesan selalu dibatasi `thread_id` **dan** keanggotaan penanya (`rooms_of`, ADR-0007 §4.5) beserta gerbang `visibility`. Tidak ada pencarian lintas room di luar itu, termasuk untuk `administrator`. Memori tetap lewat Mem0 + filter privasi (ADR-0001/0008): ADR ini **tidak** menambah jalur memori kedua dan tidak mengubah `commands/memori.py`.

### 4.5 Keluaran mengikuti ADR-0021

Mode ringkas sebagai default (`ts`, pengirim, potongan ±`snippet_chars` karakter), `next: before=<ts>` bila hasil terpotong, `limit` default 20 dan maksimum 50 seperti sekarang, tanpa mengirim seluruh isi 50 pesan ke prompt.

### 4.6 DDL lewat ADR-0016

`CREATE EXTENSION IF NOT EXISTS pg_trgm` masuk baseline, kolom turunan + dua index masuk urutan migrasi. Bila extension tidak tersedia atau tidak bisa dibuat, boot **gagal** dengan `error:` yang jelas — bukan degradasi senyap ke `LIKE`.

### 4.7 Tanpa rerank LLM

Hasil pencarian deterministik. Mererank dengan model berarti panggilan kedua yang tidak diminta penanya dan tidak terlihat; kalau nanti memang perlu, itu ADR baru.

## 5. Konfigurasi

```yaml
engine:
  search:
    fts: true
    lang: simple
    trgm: true
    trgm_threshold: 0.3
    max_results: 50            # hard cap; default per panggilan 20
    snippet_chars: 240
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| `search_latency_ms` p95 | < 150 ms pada fixture 100 000 pesan |
| `fts_hits_total` / `trgm_fallback_ratio` | rasio terlihat; turun setelah index terisi |
| `search_no_result_total` | ada; dipakai untuk menyetel `trgm_threshold` |
| `search_backend` | `fts+trgm` (default) atau `like` (hanya bila config mematikannya) |
| `search_scope_denied_total` | > 0 hanya bila ada percobaan di luar scope — itu sinyal, bukan normal |

## 7. Acceptance criteria

- [ ] Query token eksak mengembalikan baris yang benar lewat FTS (bukan `ILIKE`).
- [ ] Query berimbuhan/tipis salah tulis mengembalikan hasil lewat trigram (fixture).
- [ ] Pencarian di room A tidak pernah mengembalikan baris room B; percobaan dari non-anggota ditolak, termasuk untuk `administrator`.
- [ ] Hasil terpotong menulis `next: before=<ts>` dan panggilan lanjutan tidak menduplikasi baris.
- [ ] Tanpa `pg_trgm` yang bisa dibuat, boot gagal dengan pesan jelas; `/status` melaporkan `search_backend`.
- [ ] Jalur memori tidak berubah: `commands/memori.py` tetap memakai Mem0 + privasi, tanpa query baru ke `messages`.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Kolom turunan menambah biaya tulis pesan (hot path) | Diukur sebelum/sesudah; pengisian index lewat migrasi ADR-0016 |
| `simple` memberi recall rendah untuk bahasa berimbuhan | Trigram menutup; `trgm_fallback_ratio` jadi metrik tetap |
| Trigram mencocokkan lintas baris di dalam room yang sama | Scope tetap `thread_id`; tidak ada perubahan jangkauan |
| Extension tidak tersedia di Postgres terkelola | Boot gagal eksplisit; `search.fts: false` hanya boleh dipilih sadar oleh operator |
| Index basi setelah migrasi | `db check` melaporkan versi skema + keberadaan index/extension |

## 9. Interaksi dengan ADR lain

- **ADR-0001**: satu jalur memori (Mem0, `memories_readable`) tetap utuh; ADR ini tidak menyentuhnya.
- **ADR-0007 §4.5**: `rooms_of` + `visibility` adalah gerbang scope yang dipakai apa adanya.
- **ADR-0016**: pemilik DDL; extension, kolom turunan, dan index masuk urutan migrasi di sana.
- **ADR-0021**: cap keluaran dan penanda `next` untuk hasil pencarian.
- **ADR-0019**: `search_latency_ms` dan rasio backend masuk permukaan metrik yang sudah ada.

## 10. Referensi kode

- `eidesoul/engine/stores/history.py:318-347` — `search()` hari ini (`ILIKE`, `limit` ≤ 50); titik ganti query.
- `eidesoul/engine/commands/cari.py:25-40` — `/cari` (satu-satunya pemakai `history.search` dari command).
- `eidesoul/gateways/whatsapp/tools/chat_history.py` — `_target`/`_remember_cross`/`_search` (arsip room lain + buffer `cross_reads`).
- `eidesoul/engine/schema.py:44-67` — tabel `messages` dan index `ix_messages_chat_ts`/`ix_messages_sender`.
- `eidesoul/engine/commands/memori.py:69,99` — jalur memori (Mem0) yang tidak diubah.
- `eidesoul/engine/config.py` — blok config baru `engine.search` mengikuti pola `SandboxConfig`/`WebSearchConfig`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0026-search.md`.*
