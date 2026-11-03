> Terjemahan turunan — jangan sunting; sumber: engine/0005-retention-decay.md (sha256: c4d4d21baa2c)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0005: Retention & Decay — Hybrid TTL, Renewal Saat Dipakai, dan Pin Otomatis

- **Status:** accepted
- **Implementasi:** implemented — bukti: `pinned`/`durability`/`category`/`use_*`/`expires_at`/`max_lifetime_at`, renewal per turn delivered, auto-pin, `memory purge` dengan `--dry-run` dan penghapusan salinan library, prototipe MiniLM remember/forget (`eidesoul/library/intent/`, `eidesoul/engine/memory/intent.py`, `scripts/intent.py --check`), hint remember di capture, jalur deterministik "lupakan itu …", dan purge terjadwal dengan `memory.retention.purge.{grace_days,batch}` pada job audit harian; gate: `pytest -q` 831 lulus, `scripts/adr.py --check`
- **Tanggal:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical), ADR-0003 (worker & konsolidasi), ADR-0004 (TTL state non-`ok`); memori default-on memerlukan kebijakan umur
- **Supersedes:** —
- **Terkait:** ADR-0006 (privasi grup), ADR-0008 (integritas), ADR-0010 (reflection), ADR-0011 (purge job), ADR-0016 (kolom retensi)
- **Amandemen:** 2026-11-03 — keputusan pemilik: purge/`forget` juga menghapus **salinan library** (`mem0_history.db`: baris `history`/`messages`), karena berkas itu menyimpan `old_memory`/`new_memory` termasuk teks yang sudah dihapus. 2026-11-03 — audit: `commitment` dikeluarkan dari `core_durability_pin_categories` (pin tanpa syarat bertentangan dengan aturan "pin hanya selama tugas terbuka"); ditambah `commitment_pin_when_open: true`. Kolom retensi kini berbentuk final di ADR-0016 §4.2. 2026-11-03 — artefak `skills`/`skill_revisions` (ADR-0020) masuk §4.7: arsip saat tak dipakai, revisi append-only. 2026-11-03 — rename ADR-0000 §4.4: thread_id → thread_id, chats/thread_eras/thread_people → threads/thread_eras/thread_people, scope 'chat' → 'thread'.
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0005-retention-decay.md`.

---

## 1. Konteks

- Tidak ada retention sama sekali: tidak ada `delete(messages)`; memori, approval, `thread_eras`, `config_changes`, dan media workspace tumbuh selamanya.
- `veto` menyembunyikan, `forget` menghapus lewat adapter — tetapi tidak ada kebijakan umur.
- ADR-0004 menetapkan TTL 7 hari untuk `pending`/`failed`/`not_worth`; ADR-0005 melengkapi kebijakan untuk baris `state='ok'`.
- Konsolidasi (ADR-0003 cool/cold, ADR-0010 reflection) akan makin matang, sehingga TTL dasar boleh panjang (365 hari) tanpa membuat memori kotor.
- Tanpa decay: retrieval noisy, index membengkak, biaya naik, dan janji “lupa yang disengaja” tidak punya mekanisme.

## 2. Decision drivers

1. **Data minimization** dan hak dilupakan.
2. **Kualitas memori** — fakta basi menurunkan jawaban; yang penting harus tetap hidup.
3. **Biaya & operasional** — DB, index, backup, media.
4. **Auditability** — jejak tanpa isi.
5. **Keandalan** — yang penting tidak boleh hilang hanya karena kebetulan tidak dipakai.
6. **Determinisme** — kebijakan yang bisa dihitung ulang dan diuji.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Selamanya** | Tanpa purge | Sederhana | Tumbuh tanpa batas; retrieval memburuk | Ditolak |
| **B. TTL per level** | L0 panjang → L4 pendek | Mudah | Mengabaikan jenis memori | Ditolak |
| **C. TTL per jenis** | Preferensi panjang, kejadian pendek | Lebih tepat | Mengabaikan sensitivitas | Ditolak |
| **D. Decay by usage saja** | Sering dipakai → hidup | Mirip memori manusia | Tidak deterministik; yang penting bisa hilang | Ditolak |
| **E. Hybrid + renewal + pin otomatis** | TTL dasar (jenis × level) + renew saat dipakai + pin/perpanjangan senyap untuk sumber eksplisit | Ramping tapi andal | Sedikit lebih rumit | **Dipilih** |

## 4. Keputusan

### 4.1 Tier keandalan

"Tier" di sini berarti **kelas ketahanan retensi** (seberapa mudah baris dipurge) — bukan lapis payload
(ADR-0015 "Lapis 1/2") dan bukan provenance trust (ADR-0008 `T0–T3`).

“Selalu ingat” di sini berarti **selalu ingat yang penting**, bukan menyimpan segalanya.

| Tier | Isi | Kebijakan |
|---|---|---|
| **1 — Inti** | Identitas (nama, pronoun, bahasa), `pinned`, pernyataan eksplisit “ingat ini” | Tanpa TTL; hanya dihapus manual atau di-veto |
| **2 — Berharga** | `preference`, `fact`, `rule`, `commitment` | TTL dasar panjang + renew saat dipakai + perpanjangan senyap untuk sumber eksplisit |
| **3 — Musiman** | `event`, `topic`, `note` | TTL pendek; boleh decay |

Konsolidasi (ADR-0010) boleh **promote**: fakta yang muncul konsisten dari beberapa event dapat diangkat dari Tier 3 ke Tier 2, dengan bukti sumber. Identitas yang terbukti stabil masuk Tier 1.

### 4.2 TTL dasar

| Jenis | L0–L2 | L3 | L4 |
|---|---:|---:|---:|
| `identity` | Tier 1 (tanpa TTL) | — | — |
| `preference` | 365 | 180 | 90 |
| `fact` | 365 | 120 | 60 |
| `rule` | 365 | 180 | 90 |
| `commitment` | 365 | 180 | 90 |
| `event` / `topic` | 90 | 60 | 30 |
| `note` | 30 | 14 | 7 |
| `superseded` | 30 setelah digantikan | — | — |
| `pending` / `failed` / `not_worth` | 7 (ADR-0004) | — | — |

Prinsip: makin sensitif → makin pendek; makin “kejadian” → makin pendek.

### 4.3 Renewal saat dipakai

“Touch” = memori benar-benar masuk blok memori di prompt.

```sql
memory_uses(
  memory_id text not null,
  day       date not null,      -- hari menurut jam DB (UTC)
  turn_id   text not null,
  primary key (memory_id, day, turn_id)
)
```

Aturan:

1. Satu touch per memori per turn; **dihitung hanya saat turn `delivered`** (turn yang crash tidak memperpanjang umur).
2. Worker konsolidasi mengagregasi: `last_used_at`, `use_count`, `distinct_use_days`.
3. Renewal: `expires_at = min(now + base_ttl × multiplier, max_lifetime_at)`.
4. `renew.min_interval_hours` (mis. 12) mencegah renewal berkali-kali dalam sehari.
5. **`distinct_use_days`** dipakai, bukan jumlah kemunculan mentah — supaya satu percakapan panjang tidak membuat memori abadi.
6. Renewal tidak berlaku untuk `superseded`, `vetoed`, `failed`, `not_worth`.
7. L4 hanya renewable bila `pinned` atau dikonfirmasi; kalau tidak, TTL habis.

Strength bertingkat (≥3 hari unik → ×1,5; ≥10 hari → ×2) disiapkan sebagai opsi v2, **default off** sampai ada data.

### 4.4 Keandalan tanpa bertanya

Keputusan: **tidak ada pertanyaan expiry review.** Kepentingan memori dinilai dari sinyal otomatis:

| Sinyal | Sumber | Efek |
|---|---|---|
| **Durabilitas semantik** | Assess LLM: `durability = core \| durable \| episodic` | `core` → `max_lifetime`; `durable` → Tier 2; `episodic` → Tier 3 |
| **Kategori deterministik** | Assess: `category` | `identity`/`health`/`safety` → Tier 1 (pin); `commitment` → Tier 1 **hanya selama tugas terbuka** (selesai/dibatalkan/di atas tenggat: pin dilepas, ikut TTL `kind`, lihat ADR-0008 §4.4 B2/B3) |
| **Corroboration & re-evidence** | Fakta muncul ulang di hari berbeda; merge konsolidasi | Promote Tier 3 → Tier 2; reset TTL survivor |
| **Hit** | Masuk blok prompt pada turn `delivered` | Renewal TTL (bagian 4.3) |
| **Soft-hit** | Kemiripan tinggi tetapi tidak masuk top-k | Kredit parsial (mis. 25% base TTL), dibatasi |

**Remember-intent lewat MiniLM, bukan kata kunci.** Karena pengguna tidak tahu isi memori dan jarang mengucapkan perintah eksplisit, sinyal ini bersifat bonus, bukan jalur utama:

- Prototipe multilingual di `library/intent/remember.<lang>.json` — mis. “ingat ini”, “remember this”, “catat ya”, “jangan lupa”, “simpan ini” — plus prototipe **negatif**: pertanyaan (“kamu ingat ini?”), kutipan (“dia bilang ingat ini”), dan negasi (“jangan ingat ini”).
- Deteksi = cosine ke prototipe, dengan floor + margin (pola yang sama seperti prototipe affect, `scripts/affect.py --check`).
- Jika terdeteksi sebagai imperatif kepada soul: `durability=core` hint → `expires_at = max_lifetime_at`. **Pin penuh** hanya bila assess juga `core`/kategori inti, atau corroboration ≥ 2 hari.
- Hard exclusion tetap di atas segalanya: intent tidak bisa memaksa menyimpan kredensial, konten krisis, atau instruksi berbahaya.
- `forget`-intent juga lewat prototipe multilingual, masuk jalur `forget`/`veto` deterministik dengan pemeriksaan owner/subject.
- Data prototipe digerbangi bahasa dan divalidasi script (`--check`, `--embed`), bukan string di kode.

Risiko yang **diterima**: fakta penting yang tidak pernah disebut lagi lebih dari TTL-nya bisa hilang. Mitigasi: TTL dasar 365 hari, sinyal durability/kategori/corroboration, `soft_hit`, pin manual, dan feedback pengguna sebagai sinyal penyesuaian.

### 4.5 Purge

- Job terjadwal nightly; juga manual: `eidesoul memory purge [--dry-run]`.
- Memilih `expires_at <= now - grace_days` (grace 1 hari) dan `pinned=false`.
- Urutan per batch: hapus index → hapus canonical → **hapus salinan library** (baris `history`/`messages`
  di `mem0_history.db` untuk id/sesi itu) → tulis tombstone; satu transaksi per batch.
- Idempotent; kegagalan masuk dead-letter (ADR-0011).
- Memakai jam DB (`clock_timestamp()`), tidak menahan event loop.
- Klaim baris sebelum purge agar tidak balapan dengan pemakaian.

### 4.6 Tombstone

```sql
memory_tombstones(
  id           uuid primary key,
  kind         text not null,
  level        smallint,
  scope        text not null,
  content_hash text not null,
  reason       text not null,   -- forgot | expired | vetoed | superseded
  deleted_at   double precision not null
)
```

Tanpa isi. Untuk audit dan menjawab “apakah dulu kami punya ini?”.

### 4.7 Artefak lain

| Artefak | Default | Catatan |
|---|---|---|
| Transkrip (`messages`) | Selamanya | Arsip sejarah soul; bisa dipurge manual per chat |
| `llm_calls` | 90 hari (`engine.metrics.retention_days`) | Metrik biaya; isinya id + angka, tanpa konten |
| Media workspace | 30 hari | Besar, jarang dipakai setelah turn |
| `outbound_messages` | 30 hari | Untuk dedupe; setelah itu tidak perlu |
| `turn_steps` | 30 hari | Audit efek samping |
| Approvals | 30 hari setelah selesai | TTL eksekusi tetap 900 detik |
| `config_changes` | 365 hari | Audit operator |
| `thread_eras` | Mengikuti level isinya | Memuat konten, bukan metadata netral |
| `skills` / `skill_revisions` | Arsip saat tak dipakai (`skills.use_renewal_days`) | Prosedur turunan; revisi append-only, `archived` bukan hapus (ADR-0020) |

## 5. Konfigurasi

```yaml
memory:
  retention:
    enabled: true
    renew_on_use: true
    renew:
      min_interval_hours: 12
      count_only_delivered: true
      strength: { enabled: false }
      max_lifetime_days: { "0-2": 3650, "3": 1095, "4": 365 }
    pin: true
    silent:
      core_durability_pin_categories: [identity, health, safety]
      commitment_pin_when_open: true
      corroboration_promote: { min_days: 2 }
      renew_on_consolidation_merge: true
    intent:
      data: library/intent
      remember: { enabled: true, floor: 0.62, margin: 0.06 }
      forget:   { enabled: true, floor: 0.64, margin: 0.06 }
      pin_when: core_or_corroborated
    soft_hit: { enabled: true, credit: 0.25, max_days: 90 }
    purge: { schedule: nightly, grace_days: 1, batch: 500, dry_run: false }
    ttl_days:
      preference: { "0-2": 365, "3": 180, "4": 90 }
      fact:       { "0-2": 365, "3": 120, "4": 60 }
      rule:       { "0-2": 365, "3": 180, "4": 90 }
      commitment: { "0-2": 365, "3": 180, "4": 90 }
      event:      { "0-2": 90,  "3": 60,  "4": 30 }
      note:       { "0-2": 30,  "3": 14,  "4": 7 }
  transcripts: { ttl_days: 0, media_ttl_days: 30 }
  approvals: { keep_days: 30 }
  audit: { keep_days: 365 }
```

## 6. Metrik dan gate

| Metrik | Fungsi |
|---|---|
| Renewal rate (hidup karena dipakai vs base TTL) | Menilai kebijakan |
| Distribusi `distinct_use_days` | Menilai strength |
| Rasio `expired` vs `renewed` | Keseimbangan |
| “Forget yang tidak diharapkan” (feedback) | Menyesuaikan TTL |
| Umur rata-rata per level | Beban |
| Purge failure / dead-letter | Kesehatan |
| Auto-pin / auto-extend per bulan | Keandalan senyap bekerja |
| Renewal dari re-evidence & merge | Efektivitas konsolidasi |

## 7. Acceptance criteria

- [ ] Setiap baris `ok` punya `expires_at` deterministik dari `(kind, level)`.
- [ ] Touch dicatat hanya saat turn `delivered`; satu per memori per turn.
- [ ] Renewal memakai `distinct_use_days` dan `min_interval_hours`, dibatasi `max_lifetime_at`.
- [ ] L4 tidak renewable tanpa pin/konfirmasi.
- [ ] `durability=core` memberi `max_lifetime`; pin penuh hanya untuk kategori inti atau corroboration ≥ 2 hari.
- [ ] `commitment` hanya dipin selama tugas terbuka; selesai/dibatalkan/`overdue` melepas pin dan mengikuti TTL `kind` (ADR-0008 §4.4 B2/B3).
- [ ] `durable` masuk Tier 2; `episodic` masuk Tier 3.
- [ ] Remember/forget intent dideteksi lewat prototipe MiniLM multilingual (bukan kata kunci), dengan prototipe negatif.
- [ ] Intent tidak bisa melewati hard exclusion.
- [ ] Soft-hit memberi kredit parsial terbatas, bukan renewal penuh.
- [ ] Re-evidence dan merge konsolidasi mereset TTL survivor.
- [ ] Tidak ada pertanyaan expiry review yang dikirim ke pengguna.
- [ ] Purge idempotent, `--dry-run` tidak mengubah apa pun, memakai grace 1 hari.
- [ ] `forget` menghapus canonical + index + Mem0 + **salinan library** (`history`/`messages`), lalu menulis tombstone tanpa isi.
- [ ] Media workspace dipurge sesuai `media_ttl_days`.
- [ ] Tidak ada konten di tombstone/audit **dan** tidak ada teks tersisa di berkas derived (`mem0_history.db`) setelah `forget`.
- [ ] Test: expire → tidak terlihat di `memories_readable`; purge → hilang dari canonical, index, dan salinan library; pin → tidak dipurge.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Menghapus fakta yang masih penting | Tier 1 pin, auto-pin/auto-extend senyap, `renew_on_use`, feedback metrik |
| Pengguna kaget soul lupa | Dokumentasi + `pin` manual + tombstone yang bisa dilihat |
| Purge balapan dengan pemakaian | Claim/lease + grace_days |
| Index dan canonical tidak sinkron | Urutan purge tetap + job rekonsiliasi |
| Renewal membuat memori abadi | `max_lifetime_at` + `min_interval_hours` + distinct days |
| Auto-pin berlebihan | Hanya kategori inti atau corroboration ≥ 2 hari; `core` sendirian hanya `max_lifetime` |
| Intent misfire (kutipan, negasi, pertanyaan) | Prototipe negatif + floor/margin + intent hanya bonus + hard exclusion |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical menyimpan `expires_at`, `last_used_at`, `use_count`, `distinct_use_days`, `pinned`, `durability`, `category`.
- **ADR-0002** — assess mengembalikan `durability`/`category`; remember/forget intent lewat prototipe MiniLM hanya memberi hint, bukan keputusan akhir.
- **ADR-0003** — worker C menjalankan agregasi touch dan purge ringan.
- **ADR-0004** — TTL 7 hari untuk state non-`ok`.
- **ADR-0006** — level tinggi tidak masuk prompt grup; retention tetap berlaku.
- **ADR-0008** — provenance/trust memengaruhi apakah renewal otomatis boleh.
- **ADR-0010** — reflection menjalankan promote Tier 3 → Tier 2 dan merge yang merenew survivor; tidak mengirim pertanyaan expiry — **accepted** (`ADR-0010-reflection-disputed.md`); dispute tidak memperpanjang TTL dan tidak mengirim pertanyaan umur.
- **ADR-0011** — purge memakai klaim job, idempotency, dead-letter.

## 10. Referensi kode

- `eidesoul/engine/memory/mem0.py:496-531` — `set_privacy`, `veto`.
- `eidesoul/engine/memory/context.py:48-108` — retrieval dan render (titik sentuh “used”).
- `eidesoul/engine/memory/rekey.py` — contoh migrasi metadata yang harus menghormati retention.
- `eidesoul/engine/privacy.py:222-278` — capture dan level.
- `eidesoul/engine/config.py:118-138` — `PrivacyConfig`, `MemoryConfig`.
- `eidesoul/engine/db.py:112-145` — `upsert` untuk batch purge.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0005-retention-decay.md`.*
