> Terjemahan turunan — jangan sunting; sumber: engine/0038-privacy-layers.md (sha256: e4efa0dbfa6e)

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** memperluas ADR-0006 (privasi grup dan pre-filter), ADR-0008 (integritas memori), ADR-0007 (kelas approval), ADR-0010 (refleksi dan sengketa); mengikuti ADR-0033 (core netral)
- **Supersedes:** —
- **Terkait:** ADR-0001 (satu port memori), ADR-0011 (durabilitas), ADR-0015 (capability), ADR-0020 (skill), ADR-0035 (scope app), ADR-0036 (role)
- **Implementasi:** partial — evidence/policy, `privacy_evidence`/`privacy_policy` tersimpan (migrasi 0018), audit/jawaban tanpa LLM, kontrak `ask` milik policy, dan golden set ada di `memory/evidence.py`, `memory/candidates.py`, `access/privacy.py`, dan test. Lapisan entity kini memakai Presidio 2.2.366 + spaCy 3.8.18 + `xx_ent_wiki_sm` 3.8.0 dengan digest pohon model yang diverifikasi, load tanpa vocab, dan span `entity`; ukurannya 111 MiB incremental dan 40,4 MB di disk, di dalam baris §5.1. Status penuh masih menunggu anggaran cold-stack: MiniLM PyTorch bersama plus lapisan entity terukur sekitar 929 MiB di proses baru, di atas batas cold 700 MiB, jadi embedder bersama harus pindah ke int8/ONNX (atau satu detektor dihapus) sebelum ini sepenuhnya implemented; linear head semantik dan kalibrasi produksi juga masih terbuka.
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
3. **Lokal secara default.** PII dan secret diklasifikasi sebelum panggilan model apa pun; L4 dan
   kredensial tidak pernah keluar mentah.
4. **Gagal tertutup dan abstain.** Confidence rendah menjadi `needs_review`, tidak pernah `allow`.
5. **Model terkecil yang menutup gap terukur.** Regex dan NER dulu; encoder atau token classifier
   berikutnya. Tidak ada SLM generatif dalam keputusan ini; menambahkannya adalah ADR baru.
6. **Satu policy, satu versi.** Aturan disclosure tinggal di kode/policy-as-code, bukan di prompt.
7. **Lapisan yang bisa dilepas.** Setiap detektor adalah interface dengan digest; lapisan yang
   tidak menambah recall terukur dihapus.

## 3. Opsi yang dipertimbangkan

**A. Biarkan LLM menjadi classifier dan otoritas.** Ditolak: non-deterministik, tidak terkalibrasi
lintas tugas, bisa diinjeksi, dan tidak bisa diaudit.

**B. Satu SLM generatif lokal untuk semuanya.** Ditolak: berat untuk setiap turn, tetap tidak
terkalibrasi, dan tidak lebih baik daripada token classifier untuk span. Ditunda sepenuhnya; ADR ini
tidak mengizinkan SLM lebih dulu, dan menambahkannya membutuhkan keputusan sendiri.

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
| **Entitas/span** | Presidio + NER spaCy | evidence dengan span; tanpa verdict |
| **Semantik** | MiniLM + head terlatih | evidence dengan label; boleh abstain |
| **Policy** | `memory/privacy.py` dan modul policy berversi | level dan aksi final |
| **Manusia** | approval L4, konfirmasi L3 di grup, review queue | wajib untuk risiko tertinggi |

Tidak ada detektor lain dalam keputusan ini. GLiNER2, OpenAI Privacy Filter, dan SLM generatif
dievaluasi lalu ditunda; menambahkan salah satunya membutuhkan ADR baru dengan evaluasi, catatan
lisensi/digest, tinjauan alur data, dan anggaran memori sendiri.

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
  detector:   regex | checksum | ner | encoder
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

- Ketiga lapisan berjalan lokal. Tidak ada model eksternal dalam keputusan ini; tidak ada teks L4
  atau kredensial yang keluar dari host.
- Tidak ada LLM residual eksternal dan tidak ada SLM lokal. Model eksternal di masa depan
  membutuhkan ADR baru dengan redaksi/tokenisasi, DPA, region pinning, no-training, retensi yang
  dapat diaudit, dan tes provider palsu; model lokal di masa depan membutuhkan evidence dan
  tinjauan anggaran yang sama.
- Deployment sepenuhnya lokal (regex + NER + encoder + head) adalah satu-satunya bentuk yang
  didukung di sini.
- Bobot model, threshold, dan versi policy adalah data dengan digest; memuat model yang tidak
  terverifikasi adalah kegagalan boot.

### 4.6 Audit

Setiap disclosure dan setiap perubahan level mencatat:

- decision id, timestamp, actor, thread, id pesan sumber;
- hash konten, label, span, skor, versi dan digest detektor;
- versi dan hash policy;
- aksi, approver, expiry, dan kelas retensi;
- identitas, versi, dan digest model lokal yang menyumbang evidence.

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
evaluasi baru; update policy membutuhkan versi policy baru dan menjalankan ulang gate. Label yang
tidak memenuhi target recall diarahkan ke `needs_review` atau `drop`; ia tidak pernah "diperbaiki"
dengan menambah model. Akurasi adalah gate, bukan pilihan model.

### 4.8 Yang tidak berubah

- Empat level tersimpan dan plafon disclosure-nya (ADR-0006).
- Presedensi veto dan identitas subjek.
- `MemoryStore` sebagai satu port tulis dan `memories_readable` sebagai jalur baca (ADR-0001).
- Kelanjutan approval untuk L3/L4 (ADR-0007).
- Engine tetap program; ia tidak menjadi pengendali data atau control plane terhosting.
- Tidak ada kredensial yang disimpan.
- LLM masih boleh membantu merumuskan pertanyaan konfirmasi; ia tidak memutuskan level.

### 4.9 Batas deployment

Engine adalah program, bukan layanan terhosting. Deployer menjalankannya, memilih transport dan
provider LLM, dan menjadi pengendali data yang diprosesnya. Privacy notice, pencatatan consent,
permintaan subjek data, DPO, records of processing, DPIA, notifikasi insiden, dan kontrak transfer
lintas negara menjadi milik deployment itu, bukan engine. Deployment terhosting adalah aplikasi
terpisah; ia memiliki alur tersebut dan isolasi tenant.

Tanggung jawab engine lebih sempit dan dapat diuji: klasifikasi lokal, level deterministik,
evidence, audit, primitif retensi dan erasure, serta default aman (tanpa telemetri, tanpa panggilan
eksternal kecuali dikonfigurasi, tanpa kredensial bawaan). Engine tidak boleh menumbuhkan control
plane untuk layanan yang tidak ia operasikan.

### 4.10 Rasa bukan level

Sentimen dan afek adalah konteks, bukan kerahasiaan. Kalimat sedih tidak otomatis sensitif, dan
kalimat datar tidak otomatis publik. Level menjawab "siapa yang boleh melihat ini", bukan
"seberapa kuat pembicara merasakannya".

- **Tidak ada jalur afek-ke-level.** Prototipe afek di `eidesoul/library/affect/` boleh membentuk
  nada balasan, salience, atau jalur krisis; skornya tidak pernah masuk ke `level` atau verdict
  disclosure. Perubahan sentimen saja harus membuat level tetap.
- **Proposisi, bukan kata sifat.** Detektor melaporkan label, span, dan skor untuk apa yang
  dinyatakan: peristiwa kesehatan, paksaan, kejahatan, pihak ketiga, kredensial. "Terpaksa" saja
  ambigu; ia tidak mengidentifikasi pelaku, objek, atau korban.
- **Ambigu adalah hasil.** Model boleh mengembalikan `ambiguous`; policy boleh mengembalikan
  `abstain`, `staged`, atau `needs_review`. Ia tidak boleh menebak L4 dari distress. Satu pertanyaan
  klarifikasi netral diperbolehkan, tanpa mengulang detail sensitif; jika tidak selesai, memori
  tetap unconfirmed dan tidak masuk disclosure.
- **Tidak ada kategori khusus yang diinferensi.** Kata emosional tidak boleh menciptakan label
  kesehatan, seksual, atau pidana dengan sendirinya. Inferensi juga data pribadi; jangan simpan
  lebih dari yang didukung proposisi.
- **Jalur keselamatan terpisah.** Sinyal krisis atau self-harm masuk jalur keselamatan dan bahasanya
  sendiri; ia tidak menjadi level privasi.

Contoh:

| Ucapan | Evidence | Level |
| --- | --- | --- |
| "aku sedih banget" | afek saja; tidak ada proposisi sensitif | tidak dinaikkan oleh skor afek |
| "aku tadi jatuh" | kemungkinan peristiwa kesehatan/fisik, ambigu | tanpa level otomatis; klarifikasi atau review |
| "aku tadi nyuri" | `crime_admission` | L4, human review |
| "aku dipaksa ambil barang di toko" | paksaan plus kejahatan, pelaku/target tidak jelas | staged/review, bukan tebakan L4 |

Golden set memuat kontras ini: emosional-tapi-wajar, datar-tapi-rahasia, sarkasme, kutipan,
hipotetis, dan emosi pihak ketiga yang dilaporkan pembicara.

## 5. Konfigurasi

Aktivasi detektor dan threshold adalah data konfigurasi di bawah `privacy`, dengan default:

- deterministik + entitas lokal + semantik lokal: aktif;
- model eksternal: tidak ada; tidak ada switch di ADR ini;
- SLM generatif: tidak ada; menambahkannya adalah ADR baru, bukan toggle extra;
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
- **Tidak ada detektor opsional yang diizinkan lebih dulu.** Menambah model adalah ADR baru; ia
  bukan toggle config dan tidak mendapat baris anggaran sampai saat itu.
- **Batas teks:** 4.000 karakter per klasifikasi, 512 token per segmen, batch paling banyak 8; tidak
  pernah mengklasifikasi seluruh thread dalam satu panggilan.
- Jika stack default melewati 300 MB inkremental, responsnya adalah int8/ONNX atau lebih sedikit
  detektor, bukan anggaran yang dinaikkan.

| Komponen | Default | Cap RSS inkremental | Cap disk |
| --- | --- | ---: | ---: |
| Presidio + spaCy kecil | ya | 200 MB | 80 MB |
| MiniLM (dipakai ulang) | ya | 0 MB hangat / 400 MB dingin | 120 MB |
| Head linear | ya | 5 MB | 5 MB |

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Celah | Jumlah | Di mana |
| --- | --- | --- |
| LLM menulis `level` tanpa evidence | 1 jalur | jalur labeling `memory/store.py` |
| record keputusan tanpa versi policy | semua disclosure | `access/privacy.py`, `approvals.py` |
| label semantik untuk sensitivitas | 0 | belum ada taxonomy |
| panggilan model eksternal | 0 diizinkan | tidak ada model eksternal dalam keputusan ini |
| downgrade level turunan | 0 diizinkan | refleksi/merge memori |

Gate-nya:

1. Hasil detektor tidak dapat menetapkan `level`; hanya policy yang melakukannya.
2. Floor deterministik tidak dapat diturunkan model; downgrade manusia membutuhkan approver dan
   alasan di record.
3. Tidak ada provider eksternal yang dipanggil; provider palsu mencatat nol panggilan untuk teks
   L4/kredensial.
4. Skor rendah menghasilkan `needs_review`; tidak ada jalur yang default ke `allow`.
5. Memori turunan tidak pernah memiliki level lebih rendah daripada sumber tertingginya.
6. Fixture prompt injection tidak dapat mengubah policy, level, atau aksi disclosure.
7. Setiap disclosure memiliki field audit wajib dan versi/hash policy.
8. Golden set melaporkan precision/recall/FPR per label dan kalibrasi; regresi adalah gate.
9. Instalasi baru berjalan tanpa LLM eksternal dan tanpa SLM.
10. `pytest -q`, `ruff`, `pyright`, `selfcheck`, dan gate ADR tetap hijau.
11. Smoke privasi default tetap di dalam anggaran §5.1; tidak ada model di luar stack default di
    dalam proses.
12. Perubahan sentimen saja membuat `level` tetap; ucapan afektif yang ambigu diarahkan ke
    `staged`/`needs_review`, bukan tebakan L4.

### 6.1 Kewajiban test

Setiap gate bernomor dan setiap kriteria penerimaan adalah test otomatis, bukan checklist review.
Aturan tanpa test belum implemented; statusnya tetap draft.

- Test berada di `tests/` dan berjalan di `pytest -q` default; test gate yang di-skip atau xfail
  tidak dihitung.
- Perbaikan bug kontrak (`ask`, teks audit mentah, evidence hilang, policy version hilang) dikirim
  dengan regression test di commit yang sama; test harus gagal pada kode sebelum perbaikan dan
  lulus sesudahnya.
- Tidak ada test yang memanggil provider nyata dan tidak ada test yang butuh jaringan. Provider
  palsu mencatat panggilan dan memastikan nol payload L4/kredensial mentah.
- Golden set adalah fixture yang di-commit dengan label yang diharapkan; test mencatat
  precision/recall/FPR per label dan gagal saat regresi. Setelah baseline dicatat, floor numerik
  menjadi bagian dari versi policy; mengubahnya adalah perubahan policy, bukan edit test.
- Test menguji perilaku, bukan prosa: level, aksi, field evidence, rute, dan panggilan provider.
- Test wajib mencakup minimal: level hanya ditulis policy; `ask` diturunkan dari policy, bukan
  output model; perubahan sentimen saja tidak mengubah level; teks afektif ambigu diarahkan ke
  `staged`/`needs_review`; audit tidak mengirim teks L4/kredensial mentah; evidence dan policy
  version tersimpan; jalur lokal membuat nol panggilan eksternal; metrik golden set berjalan.

## 7. Kriteria penerimaan

- `memories.privacy_evidence` ada, diisi detektor, dan dapat di-query untuk audit.
- `level` hanya ditulis policy; setiap output model adalah evidence.
- Kredensial di-drop; keputusan L4 membutuhkan approval; disclosure L3 di grup membutuhkan
  konfirmasi; veto menang.
- Taxonomy semantik mencakup label di §4.4 dan berversi bersama policy.
- Tidak ada model eksternal atau SLM dalam ADR ini; menambahkannya membutuhkan ADR baru beserta
  tinjauan redaksi, alur data, dan anggarannya.
- Record audit dapat merekonstruksi keputusan tanpa memanggil model lagi.
- Golden set dan metrik per label menjadi bagian repositori.
- Deployment sepenuhnya lokal lolos smoke test tanpa model jaringan dan di dalam anggaran memori
  §5.1.
- Afek tidak pernah menentukan level: skor afek dan policy privasi punya input terpisah, dan
  perubahan sentimen saja adalah no-op untuk `level`.
- Setiap kriteria penerimaan di atas punya test di `pytest` default; test yang hilang atau
  di-skip/xfail berarti kriteria belum terpenuhi.

## 8. Risiko dan mitigasi

- **Over-classification memblokir percakapan biasa.** Mitigasi: threshold per label, review queue,
  dan target FPR terukur; L2 tetap fluid.
- **False negative semantik pada bahasa Indonesia kolokial.** Mitigasi: golden set lokal, encoder
  few-shot/SetFit head, abstention, default konservatif untuk label pihak ketiga dan pidana.
- **Update model diam-diam mengubah perilaku.** Mitigasi: pin digest/versi, gate evaluasi, versi
  policy, dan jalur rollback.
- **Model masa depan ditambahkan tanpa evidence.** Mitigasi: ADR baru, hasil golden set baru,
  catatan digest/lisensi, dan baris anggaran baru; tidak ada tambahan diam-diam.
- **Volume audit tak terkendali.** Mitigasi: hash konten, simpan span dan versi, bukan teks mentah;
  policy retensi untuk record itu sendiri.
- **Terlalu jauh secara hukum (data pidana/kesehatan).** Mitigasi: default policy, legal review,
  retention override, dan aksi `drop` untuk data yang tidak boleh disimpan operator.
- **Afek disalahartikan sebagai kerahasiaan.** Mitigasi: §4.10 menjauhkan afek dari jalur level;
  fixture emosional-tapi-wajar dan datar-tapi-rahasia masuk golden set.

## 9. Interaksi dengan ADR lain

- ADR-0006: level, plafon, dan pre-filter tetap; ADR ini memberi label jejak evidence.
- ADR-0008: provenance, karantina instruksi, dan anti-poisoning tetap; konten tidak tepercaya
  adalah evidence, bukan policy.
- ADR-0007: approval tetap kelanjutan untuk L3/L4; policy menamai kelasnya.
- ADR-0010: sengketa dan veto tetap menang atas setiap detektor.
- ADR-0001: port memori tetap tunggal; evidence adalah kolom, bukan store kedua.
- ADR-0033: detektor dan label adalah data; tidak ada default bahasa, vendor, atau transport di core.
  Prototipe afek tetap data untuk nada; §4.10 menjauhkannya dari level privasi.
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
  controls (https://learn.microsoft.com/en-us/purview/ai-m365-copilot).
