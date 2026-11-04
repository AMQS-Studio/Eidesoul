> Terjemahan turunan — jangan sunting; sumber: engine/0038-privacy-layers.md (sha256: 45c2cd52d1a6)

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** memperluas ADR-0006 (privasi grup dan pre-filter), ADR-0008 (integritas memori), ADR-0007 (kelas approval), ADR-0010 (refleksi dan sengketa); mengikuti ADR-0033 (core netral)
- **Supersedes:** —
- **Terkait:** ADR-0001 (satu port memori), ADR-0011 (durabilitas), ADR-0015 (capability), ADR-0020 (skill), ADR-0035 (scope app), ADR-0036 (role)
- **Implementasi:** draft — kontrak lapisan, record evidence, dan otoritas policy diputuskan di sini; engine masih membiarkan LLM mengusulkan level tanpa jejak evidence tersimpan, belum punya classifier sensitivitas semantik, dan belum mencatat versi policy per keputusan.
- **Catatan lokasi:** keputusan engine (ia memiliki klasifikasi, policy disclosure, dan audit), jadi duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0038-privacy-layers.md`.

---

## 1. Konteks

Engine sudah punya kerangka yang benar: level numerik 0–4 pada setiap memori (`memories.level`,
`schema.py:585`), matematika level deterministik, presedensi veto, dan verdict disclosure di
`memory/privacy.py`, hook agent-facing di `access/privacy.py`, serta konfirmasi L3/L4 lewat
`approvals`. Kredensial tidak pernah disimpan sebagai memori. Yang lemah adalah **dari mana label
berasal** dan **bukti apa yang bertahan**:

- LLM mengusulkan level sementara; lapisan deterministik memutuskan disclosure. Reviewer tidak
  dapat melihat detektor mana mengatakan apa, dengan skor berapa, di bawah versi policy mana.
- Kalibrasi skor LLM tidak universal: ia bergantung tugas dan domain (ICML 2026,
  `Confidence is Not Universal`). Satu skalar bukan argumen audit.
- Classifier semantik bisa over-block. SafeGPT melaporkan precision 40,5% dan false positive 78,6%
  pada skenario enterprise-nya untuk classifier knowledge-graph, sementara pattern matching
  deterministik menyumbang sebagian besar recall pada PII terstruktur (`arXiv:2601.06366`).
- Prompt injection dan sensitive information disclosure adalah dua risiko eksposur data teratas di
  OWASP LLM Top 10 2026; kontrolnya adalah policy berbasis klasifikasi, bukan instruksi di prompt.
- Regulator meminta record, bukan niat. EU AI Act Article 12 (logging otomatis) dan Article 19
  (field record) berlaku untuk sistem high-risk; fase transparency berlaku sejak 2 Agustus 2026,
  sementara tanggal high-risk Annex III bergeser ke 2 Desember 2027. ISO/IEC 42001:2026 dan
  panduan AI TRiSM Gartner 2026 sama-sama mendorong governance dari dokumen menjadi **kontrol
  runtime yang ditegakkan**.
- Enterprise sudah menjalankan DLP dan sensitivity label (misalnya Microsoft Purview
  mengklasifikasi prompt dan response, mengaudit aktivitas AI, dan menerapkan retensi). Lapisan
  privasi yang tidak bisa memberi feed ke sistem itu adalah silo baru, bukan kontrol.

Yang hilang bukan model yang lebih besar. Yang hilang adalah **control plane**: detektor berlapis,
record evidence, satu policy deterministik, dan jalur manusia untuk level tertinggi.

## 2. Decision drivers

1. **Otoritas deterministik.** Model boleh mengusulkan; hanya kode yang memutuskan level tersimpan
   dan aksi disclosure.
2. **Evidence di atas skor.** Setiap label membawa detektor, versi, span, dan skor.
   `L4` telanjang tidak reproducible.
3. **Lokal secara default.** PII dan secret diklasifikasi sebelum panggilan eksternal; L4 dan
   kredensial tidak pernah keluar mentah.
4. **Gagal tertutup dan abstain.** Confidence rendah menjadi `needs_review`, tidak pernah `allow`.
5. **Model terkecil yang menutup gap terukur.** Regex dan NER dulu; encoder atau token classifier
   berikutnya; SLM generatif hanya jika evaluasi membuktikan gap-nya.
6. **Satu policy, satu versi.** Aturan disclosure tinggal di kode/policy-as-code, bukan di prompt.
7. **Lapisan yang bisa dilepas.** Setiap detektor adalah interface dengan digest; lapisan yang
   tidak menambah recall terukur dihapus.

## 3. Opsi yang dipertimbangkan

**A. Biarkan LLM menjadi classifier dan otoritas.** Ditolak: non-deterministik, tidak terkalibrasi
lintas tugas, bisa diinjeksi, dan tidak bisa diaudit.

**B. Satu SLM generatif lokal untuk semuanya.** Ditolak sebagai default: berat untuk setiap turn,
tetap tidak terkalibrasi, dan tidak lebih baik daripada token classifier untuk span. Diterima hanya
sebagai detektor residual opsional di balik kontrak evidence yang sama.

**C. Aturan deterministik saja.** Ditolak: regex mencocokkan identifier, bukan makna; ia tidak bisa
mendeteksi pengakuan pidana, disclosure kesehatan, atau konflik interpersonal.

**D. DLP eksternal saja.** Ditolak sebagai jawaban tunggal: ia memindahkan data keluar dan tidak
melihat provenance turn, veto, atau subjek. Diterima sebagai target integrasi.

**E. Detektor berlapis, evidence, policy deterministik, human review (dipilih).**

## 4. Keputusan

### 4.1 Lapisannya

| Lapisan | Contoh | Otoritas |
| --- | --- | --- |
| **Deterministik** | regex, checksum, dictionary, pola kredensial | menetapkan floor; boleh menaikkan, tidak pernah menurunkan |
| **Entitas/span** | NER Presidio/spaCy, GLiNER2, OpenAI Privacy Filter | evidence dengan span; tanpa verdict |
| **Semantik** | MiniLM + head terlatih, klasifikasi GLiNER2, SLM lokal opsional | evidence dengan label; boleh abstain |
| **Policy** | `memory/privacy.py` dan modul policy berversi | level dan aksi final |
| **Manusia** | approval L4, konfirmasi L3 di grup, review queue | wajib untuk risiko tertinggi |
| **LLM residual** (opsional) | LLM eksternal atau lokal setelah redaksi | evidence saja; tidak pernah final |

Urutannya adalah urutan routing, bukan pipeline yang harus dilalui setiap teks. Lapisan murah
menyelesaikan mayoritas; hanya teks yang belum jelas yang naik. Sebuah lapisan boleh
mengembalikan `abstain`; ia tidak boleh mengembalikan `allow` di bawah floor deterministik.

Label semantik mencakup setidaknya: `credential`, `health`, `financial`, `crime_admission`,
`self_harm`, `violence`, `sexual`, `relationship_conflict`, `third_party_personal`, `legal`, dan
`none`. Taxonomy adalah data, berversi bersama policy; menambah label adalah perubahan policy,
bukan cabang kode.

### 4.2 Record evidence

Setiap detektor mengembalikan:

```text
Evidence:
  detector:   regex | checksum | ner | encoder | token_classifier | slm | llm
  version:    versi detektor atau model
  digest:     sha256 bobot/config saat model dipakai
  label:      label taxonomy
  skor: 0..1
  span:       [start, end] atau null untuk label seluruh teks
```

Sebuah memori menyimpan array evidence dan hasil policy final:

```text
PrivacyDecision:
  level:          0..4
  action:         allow | redact | block | ask | drop
  policy_version: string
  approver:       person id atau kosong
  expires_at:     epoch atau null
```

`memories.privacy_evidence` (JSONB, default `[]`) adalah array evidence yang dipersist;
`memories.level` tetap level final. Keputusan disclosure ditambahkan ke action ledger dengan versi
policy yang sama. Migrasi menambah kolom JSONB; tidak ada store kedua.

### 4.3 Komposisi deterministik

- Floor deterministik adalah `max()` dari semua evidence deterministik; tidak ada lapisan
  berikutnya yang menurunkannya.
- Model boleh menaikkan level, atau abstain; ia tidak boleh menulis `level` langsung.
- `needs_review` adalah state, bukan level. Confidence rendah diarahkan ke sana; reviewer boleh
  menurunkan dengan alasan dan approver yang tercatat.
- Memori turunan (ringkasan, refleksi, skill) mewarisi level **tertinggi** dari sumbernya; downgrade
  membutuhkan redaksi, bukan opini model baru.
- Veto oleh subjek menang atas setiap detektor dan setiap policy.
- Kredensial (`credential`/`secret`) tidak pernah disimpan; aksinya `drop`, bukan `L5`.

### 4.4 Sensitivitas semantik, bukan hanya PII

Deteksi entitas menjawab “siapa/apa yang disebut”. Ia tidak menjawab “apa yang orang ini akui atau
rasakan”. Lapisan semantik ada untuk gap itu:

| Contoh | Label | Policy default |
| --- | --- | --- |
| “aku tadi nyuri” | `crime_admission` | L4, review, retention override; legal review |
| “aku gak suka banget dia” | `relationship_conflict` tentang pihak ketiga | L3, veto subjek, tanpa disclosure ke pihak ketiga |
| “aku sakit…” | `health` | L4, terbatas |
| “passwordku …” | `credential` | drop, tidak pernah disimpan |

Ini adalah default policy, bukan fakta classifier. Sarkasme, kutipan, hipotetis, dan reported speech
membutuhkan label `ambiguous`/`hypothetical` atau abstention; cabang konservatif menang. Coreference
ke orang ketiga (“dia”) default ke `third_party_personal`, bukan ke fakta publik.

### 4.5 Perpindahan data

- Lapisan deterministik, entitas, dan semantik berjalan lokal secara default.
- L4 dan kredensial tidak pernah dikirim mentah ke model eksternal. Redaksi atau tokenisasi
  dilakukan sebelum panggilan; rehidrasi hanya di proses tepercaya.
- LLM residual eksternal mati secara default. Saat diaktifkan, ia menerima teks teredaksi, tanpa
  tool, output berskema, dan provider dengan DPA, region pinning, no-training, serta retensi yang
  dapat diaudit.
- Deployment sepenuhnya lokal (regex + NER + encoder + SLM lokal opsional) harus tetap mungkin;
  tidak ada fitur yang mewajibkan model eksternal.
- Bobot model, threshold, dan versi policy adalah data dengan digest; memuat model yang tidak
  terverifikasi adalah kegagalan boot.

### 4.6 Audit

Setiap disclosure dan setiap perubahan level mencatat:

- decision id, timestamp, actor, thread, id pesan sumber;
- hash konten, label, span, skor, versi dan digest detektor;
- versi dan hash policy;
- aksi, approver, expiry, dan kelas retensi;
- identitas model/provider saat model eksternal dipakai.

Record bersifat append-only (action ledger atau serial tamper-evident yang setara), dapat diekspor
ke SIEM, dan dipetakan ke EU AI Act Article 12/19, ISO/IEC 42001, dan NIST AI RMF. Record harus
cukup untuk merekonstruksi keputusan tanpa model.

### 4.7 Evaluasi

Golden set privasi mencakup, per bahasa dan per label:

- PII terstruktur, kredensial, dan identifier dengan checksum valid;
- disclosure kesehatan, keuangan, pidana, seksual, self-harm, kekerasan, dan relasi;
- bahasa Indonesia kolokial dan campur kode;
- sarkasme, kutipan, hipotetis, dan reported speech;
- prompt injection yang mencoba menurunkan level atau membocorkan policy;
- kebocoran lintas thread dan agregasi;
- false positive yang akan memblokir percakapan biasa.

Metrik: precision, recall, FPR per label, calibration error, abstention rate, dan volume review
queue. Mode detection-only 30 hari diperbolehkan sebelum enforcement. Update model membutuhkan
evaluasi baru; update policy membutuhkan versi policy baru dan menjalankan ulang gate.

### 4.8 Yang tidak berubah

- Empat level tersimpan dan plafon disclosure-nya (ADR-0006).
- Presedensi veto dan identitas subjek.
- `MemoryStore` sebagai satu port tulis dan `memories_readable` sebagai jalur baca (ADR-0001).
- Kelanjutan approval untuk L3/L4 (ADR-0007).
- Tidak ada kredensial yang disimpan.
- LLM masih boleh membantu merumuskan pertanyaan konfirmasi; ia tidak memutuskan level.

## 5. Konfigurasi

Aktivasi detektor dan threshold adalah data konfigurasi di bawah `privacy`, dengan default:

- deterministik + entitas lokal + semantik lokal: aktif;
- LLM residual eksternal: mati;
- SLM generatif: bukan bagian spine; extra/plugin opsional;
- threshold per label dan per bahasa, tidak pernah satu angka global;
- path model, digest, dan versi policy diperiksa saat boot; digest yang hilang adalah kegagalan.

Tidak ada secret yang masuk ke prompt, record evidence, atau model. Kredensial dalam teks di-drop
sebelum penyimpanan.

### 5.1 Anggaran sumber daya

Privasi harus muat dalam anggaran memori spine; ia tidak mengubah engine menjadi host model.

- **RSS inkremental default:** paling banyak 300 MB di atas proses yang sudah memuat MiniLM; proses
  dingin yang memuat encoder untuk privasi tetap di 700 MB atau kurang.
- **Satu model entitas dan satu model semantik per proses.** Tanpa instance ganda, tanpa load per
  request, tanpa model per thread.
- **Entitas default:** Presidio plus model spaCy kecil (paling banyak 80 MB di disk). Tanpa
  transformer NER secara default.
- **Semantik default:** MiniLM yang sudah dimuat `embed.py` plus head linear (paling banyak 5 MB);
  bukan encoder kedua.
- **Detektor opsional** (GLiNER2, OpenAI Privacy Filter, SLM lokal) adalah extra atau plugin, mati
  secara default, lazy-load, dan di-unload setelah sepuluh menit idle.
- **Batas teks:** 4.000 karakter per klasifikasi, 512 token per segmen, batch paling banyak 8; tidak
  pernah mengklasifikasi seluruh thread dalam satu panggilan.
- Jika stack default melewati 300 MB inkremental, responsnya adalah int8/ONNX atau lebih sedikit
  detektor, bukan anggaran yang dinaikkan.

| Komponen | Default | Cap RSS inkremental | Cap disk |
| --- | --- | ---: | ---: |
| Presidio + spaCy kecil | ya | 200 MB | 80 MB |
| MiniLM (dipakai ulang) | ya | 0 MB hangat / 400 MB dingin | 120 MB |
| Head linear | ya | 5 MB | 5 MB |
| GLiNER2 | opsional | 500 MB | 600 MB |
| OpenAI Privacy Filter | opsional | 2 GB | 2 GB |
| SLM lokal | opsional | bukan default | — |

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Celah | Jumlah | Di mana |
| --- | --- | --- |
| LLM menulis `level` tanpa evidence | 1 jalur | jalur labeling `memory/store.py` |
| record keputusan tanpa versi policy | semua disclosure | `access/privacy.py`, `approvals.py` |
| label semantik untuk sensitivitas | 0 | belum ada taxonomy |
| panggilan model eksternal tanpa redaksi | 0 diizinkan | gate baru |
| downgrade level turunan | 0 diizinkan | refleksi/merge memori |

Gate-nya:

1. Hasil detektor tidak dapat menetapkan `level`; hanya policy yang melakukannya.
2. Floor deterministik tidak dapat diturunkan model; downgrade manusia membutuhkan approver dan
   alasan di record.
3. Teks L4/kredensial tidak pernah mencapai provider eksternal; provider palsu mencatat nol
   panggilan mentah.
4. Confidence rendah menghasilkan `needs_review`; tidak ada jalur yang default ke `allow`.
5. Memori turunan tidak pernah memiliki level lebih rendah daripada sumber tertingginya.
6. Fixture prompt injection tidak dapat mengubah policy, level, atau aksi disclosure.
7. Setiap disclosure memiliki field audit wajib dan versi/hash policy.
8. Golden set melaporkan precision/recall/FPR per label dan kalibrasi; regresi adalah gate.
9. Instalasi baru berjalan tanpa LLM eksternal dan tanpa SLM.
10. `pytest -q`, `ruff`, `pyright`, `selfcheck`, dan gate ADR tetap hijau.
11. Smoke privasi default tetap di dalam anggaran §5.1; model opsional tidak ada di proses sampai
    diaktifkan secara eksplisit.

## 7. Kriteria penerimaan

- `memories.privacy_evidence` ada, diisi detektor, dan dapat di-query untuk audit.
- `level` hanya ditulis policy; setiap output model adalah evidence.
- Kredensial di-drop; keputusan L4 membutuhkan approval; disclosure L3 di grup membutuhkan
  konfirmasi; veto menang.
- Taxonomy semantik mencakup label di §4.4 dan berversi bersama policy.
- Model residual eksternal mati secara default dan tidak dapat menerima teks L4/kredensial mentah.
- Record audit dapat merekonstruksi keputusan tanpa memanggil model lagi.
- Golden set dan metrik per label menjadi bagian repositori.
- Deployment sepenuhnya lokal lolos smoke test tanpa model jaringan dan di dalam anggaran memori
  §5.1.

## 8. Risiko dan mitigasi

- **Over-classification memblokir percakapan biasa.** Mitigasi: threshold per label, review queue,
  dan target FPR terukur; L2 tetap fluid.
- **False negative semantik pada bahasa Indonesia kolokial.** Mitigasi: golden set lokal, encoder
  few-shot/SetFit head, abstention, default konservatif untuk label pihak ketiga dan pidana.
- **Update model diam-diam mengubah perilaku.** Mitigasi: pin digest/versi, gate evaluasi, versi
  policy, dan jalur rollback.
- **Model residual eksternal membocorkan data.** Mitigasi: mati default, redaksi sebelum panggilan,
  tanpa L4 mentah, DPA/region/no-training, dan tes provider palsu.
- **Volume audit tak terkendali.** Mitigasi: hash konten, simpan span dan versi, bukan teks mentah;
  policy retensi untuk record itu sendiri.
- **Terlalu jauh secara hukum (data pidana/kesehatan).** Mitigasi: default policy, legal review,
  retention override, dan aksi `drop` untuk data yang tidak boleh disimpan operator.

## 9. Interaksi dengan ADR lain

- ADR-0006: level, plafon, dan pre-filter tetap; ADR ini memberi label jejak evidence.
- ADR-0008: provenance, karantina instruksi, dan anti-poisoning tetap; konten tidak tepercaya
  adalah evidence, bukan policy.
- ADR-0007: approval tetap kelanjutan untuk L3/L4; policy menamai kelasnya.
- ADR-0010: sengketa dan veto tetap menang atas setiap detektor.
- ADR-0001: port memori tetap tunggal; evidence adalah kolom, bukan store kedua.
- ADR-0033: detektor dan label adalah data; tidak ada default bahasa, vendor, atau transport di core.
- ADR-0035/0036: scope app dan role ortogonal; role boleh membaca level, tetapi level bukan role.
- ADR-0034: teks prompt tidak pernah membawa policy; policy adalah kode.

## 10. Referensi kode

- `eidesoul/engine/memory/privacy.py` — `clamp_level`, `PrivacyVerdict`, `AuditResult`,
  `PrivacyPolicy`.
- `eidesoul/engine/access/privacy.py` — jalur review, audit, ask, dan disclosure agent-facing.
- `eidesoul/engine/memory/store.py` — jalur labeling/capture dan penulisan level.
- `eidesoul/engine/schema.py:575-620` — `memories.level` dan baris yang akan membawa
  `privacy_evidence`.
- `eidesoul/engine/access/approvals.py` — kelanjutan L3/L4.
- `eidesoul/engine/access/ledger.py` — action ledger yang membawa record keputusan.
- Eksternal: ICML 2026 `Confidence is Not Universal`
  (https://proceedings.mlr.press/v306/jang26a.html); SafeGPT (https://arxiv.org/html/2601.06366v3);
  OWASP LLM Top 10 2026 (https://www.forcepoint.com/blog/insights/owasp-llm-top-10); EU AI Act
  Articles 12/19 (https://artificialintelligenceact.eu/article/12/,
  https://artificialintelligenceact.eu/article/19/); ISO/IEC 42001:2026
  (https://www.iso.org/en/home/insights-news/resources/iso-42001-explained-what-it-is.html);
  Gartner AI TRiSM (https://www.gartner.com/en/articles/ai-governance-trism); Microsoft Purview AI
  controls (https://learn.microsoft.com/en-us/purview/ai-m365-copilot); OpenAI Privacy Filter
  (https://github.com/openai/privacy-filter); GLiNER2 (https://arxiv.org/html/2507.18546).
