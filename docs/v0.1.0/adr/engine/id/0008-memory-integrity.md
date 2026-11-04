> Terjemahan turunan — jangan sunting; sumber: engine/0008-memory-integrity.md (sha256: 89a1dddbacb4)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0008: Integritas Memori — Provenance, Trust, Karantina Instruksi, Anti-Poisoning

- **Status:** accepted
- **Implementasi:** implemented — bukti: `via`/`refs`/`asserted_at`/`trust` T0-T3, `integrity.py` deterministik, karantina pada tulis kanonik dan pada `label_candidate`, tombstone, bukti supersede, kuota bertingkat ke `staged`, promosi recall-miss, penanda trust di prompt, dan `kind=rule` yang lahir hanya dari `MemoryStore.sync_soul_rules`/`promote_soul_rules` (`soul.rules`, T0, corroboration dua hari UTC); gate: `pytest -q` 831 lulus, `scripts/adr.py --check`
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical), ADR-0002 (gate capture), ADR-0004 (labeling), ADR-0005 (retention), ADR-0006 (privasi grup), ADR-0007 (capability vs authorization)
- **Supersedes:** —
- **Terkait:** ADR-0003 (worker), ADR-0010 (reflection), ADR-0011 (crash safety)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0008-memory-integrity.md`.
- **Amandemen:** 2026-11-02 — §4.4 A: overflow kuota masuk state `staged` (berlabel, TTL ikut `kind`, promosi saat recall-miss), bukan `pending`. 2026-11-03 — audit: acceptance `kind=rule` disamakan dengan §4.3 (`via=soul_doc`).

---

## 1. Konteks

Soul engine ini punya satu memori yang hidup di banyak ruangan. Konsekuensinya: **siapa pun yang
bisa mengobrol dengan soul, bisa menulis ke memorinya.** Serangan termurah bukan membobol server,
cukup berbicara — menjejali klaim, menimpa fakta stabil, atau menyelundupkan instruksi supaya
disimpan sebagai aturan.

**Fondasi yang sudah ada dan dipertahankan:**

| Kontrol | Bukti |
|---|---|
| Pola injeksi deterministik | `guard/patterns.py:16-26` — `INJECTION` (ignore previous, system prompt, jailbreak, “abaikan instruksi”, “mulai sekarang kamu”, developer mode) |
| Capture menolak injeksi | `agent.py:914` |
| Hard exclusion kredensial & krisis | `SENSITIVE` (`patterns.py:11-14`), `safe_text` (`:29-35`), aturan prompt krisis (`prompts.py:54-55`) |
| Prompt: ucapan orang lain = data | `prompts.py:266` |
| Provenance tersimpan sebagian | metadata `scope, kind, person_id, thread_id, audience, source, level, owner_id, subject_id, confirmed` (`mem0.py:340-392`) |
| Nilai `source` yang sudah dipakai | `turn` (`privacy.py:260`), `tool` (`tools/memory.py:94`), `command` (`commands/memori.py:131`), `ambient:<thread_id>` (`ambient.py:255,314`) |
| Veto hanya oleh subjek | `mem0.py:527-530` |
| Supersede manual + validasi scope/pemilik | `mem0.py:323-338` |

**Empat lubang:**

1. **`kind=rule` bisa lahir dari ucapan pengguna.** Prompt mengizinkan simpan “a short proven
   procedure ... as `kind=rule`” (`prompts.py:314-315`), dan tool menerima `kind` apa pun dari model
   (`tools/memory.py:75`). Rule ikut memengaruhi perilaku, jadi ini jalur eskalasi.
2. **Rule tak bisa dibedakan dari fakta saat dibaca.** `render()` hanya menampilkan
   `[scope/tanggal L{level}{?}]` + teks (`memory/context.py:105-110`); `kind` tidak muncul.
3. **Tidak ada trust per baris memori.** “trust” yang ada hanya relasi affect (`schema.py:130`);
   pernyataan diri, klaim tentang orang lain, dan sintesis ambient (`ambient.py:255,314`) masuk ke
   blok prompt yang sama tanpa pembeda.
4. **Referensi `supersedes=<id>` tanpa id terlihat.** Prompt menyuruh memakai id dari blok memori
   (`prompts.py:313-314`), tetapi blok tidak menampilkan id — model hanya bisa menebak atau mencari
   dulu lewat `memory search`.

**Empat skenario serangan:**

| # | Skenario | Yang diserang |
|---|---|---|
| S1 | Satu orang mengirim ratusan klaim dalam sejam sampai retrieval jenuh dan fakta lama tenggelam | memori sebagai keseluruhan |
| S2 | Seseorang melihat id memori lalu membujuk soul memakai `supersedes=<id>` untuk menimpa fakta stabil | baris spesifik |
| S3 | Dua klaim bertentangan tentang orang yang sama; sistem diam-diam memilih yang terbaru | kebenaran fakta |
| S4 | Klaim lemah (sintesis ambient/reflection) naik pelan-pelan jadi “inti” karena sering muncul | Tier 1 (ADR-0005) |

## 2. Decision drivers

1. **Trust harus bisa dihitung deterministik** dari fakta yang tersimpan, bukan dinilai model.
2. **Aturan dan persona tidak boleh lahir dari ucapan orang lain** — hanya dari soul dan dokumennya.
3. **Tidak ada kehilangan senyap**: yang ditahan harus terlihat dan bisa hidup lagi.
4. **Jalur tulis tetap murah**: tanpa panggilan model per pesan (semangat ADR-0002/0003).
5. **Kontradiksi tidak diselesaikan dengan “yang terbaru menang”.**
6. **Bisa diaudit**: tiap baris bisa ditelusuri asal-usulnya.
7. **Tidak mengubah** hard exclusion, ceiling privasi (ADR-0006), dan kelas izin (ADR-0007).

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Semua tulisan setara (status quo)** | Semua baris diperlakukan sama | Paling sederhana | Instruksi bisa menyamar jadi rule; klaim lemah setara fakta diri | Ditolak |
| **B. Trust hanya bobot ranking** | Trust menurunkan skor retrieval saja | Tidak menyentuh perilaku | Fakta lemah tetap bisa mengubah perilaku kalau skornya tinggi | Ditolak (keputusan: batas keras) |
| **C. Trust keras + karantina + anti-poisoning A/B/D, `disputed` menyusul** | Provenance → trust → batas perilaku; instruksi dikarantina; volume dibatasi; penimpaan butuh bukti; promosi ketat | Tiap serangan punya pemilik; jalur tulis tetap murah | Perlu kolom/atribut baru + state `superseded` | **Dipilih** |
| **D. Semua tulisan butuh approval** | Manusia menyetujui tiap simpanan | Paling aman | Tidak praktis; soul jadi tidak punya ingatan otomatis | Ditolak |

## 4. Keputusan

> **Setiap baris memori membawa provenance. Trust dihitung dari provenance dan membatasi pengaruh ke perilaku. Teks imperatif tidak pernah menjadi aturan. Volume tulisan dibatasi dan penimpaan butuh bukti. Promosi hanya untuk provenance kuat.**

### 4.1 Provenance

Setiap baris menyimpan, di atas field yang sudah ada:

| Atribut | Isi | Field yang ada |
|---|---|---|
| `via` | `turn` / `tool` / `command` / `reflection` / `ambient` / `automation` / `soul_doc` | turunan `source` (nilai lama dipetakan, bukan kolom kedua) |
| `stated_by` | pembicara yang menyatakan | `owner_id` |
| `about` | orang yang dibicarakan | `subject_id` |
| `thread_id` | ruangan asal | `thread_id` |
| `asserted_at` | waktu pernyataan | waktu baris |
| `refs` | rujukan pesan/event bila ada | metadata |

Provenance adalah **satu-satunya** sumber trust — tidak ada penilaian model di jalur ini.

### 4.2 Trust tier dan batas keras

| Tier | Nama | Sumber | Boleh dipakai untuk |
|---|---|---|---|
| **T0** | `self_stated` | orang menyatakan tentang dirinya (`about == stated_by`) | perilaku: preferensi, gaya, prosedur, keputusan tool |
| **T1** | `stated_about_other` | orang menyatakan tentang orang lain | konteks dan pengetahuan; **tidak** mengubah aturan |
| **T2** | `soul_inference` | ambient/reflection (sintesis soul) | petunjuk/topik; tidak mengubah aturan; tidak bisa jadi Tier 1 |
| **T3** | `unlabeled` | belum dilabeli (`level NULL`, ADR-0004) | tidak dibaca sama sekali |

- **Batas keras:** aturan, persona, gaya, dan keputusan tool hanya boleh bersumber dari **T0** +
  dokumen soul (`soul.yaml`/dokumen `soul`, `engine`). T1 dan T2 boleh diingat dan dikutip, tidak
  pernah mengatur.
- **Titik penegakan:** (a) funnel retrieval (ADR-0006) membuang T3; (b) penyusun blok prompt
  menandai tier; (c) penulisan `kind=rule` dibatasi (4.3); (d) test: baris T1/T2 yang memengaruhi
  aturan dinyatakan gagal.
- **Trust ≠ level privasi.** Dua sumbu berbeda: trust menjawab “siapa yang bilang”, level menjawab
  “seberapa sensitif”. Keduanya tidak saling menggantikan.

### 4.3 Karantina instruksi

- **Pola deterministik selalu jalan:** perluasan `INJECTION` + pola imperatif (“mulai sekarang”,
  “jangan pernah”, “aturan baru”, “always/never ... you”).
- **Hanya yang tak terklasifikasi** dilihat klasifier LLM kecil — pola yang sama dengan sandbox
  guard (`guard/sandbox.py:139-192`). Tidak ada panggilan model untuk teks yang sudah jelas.
- **Hasil:** teks imperatif dari pengguna paling jauh tersimpan sebagai **fakta bersumber** (“X
  bilang ...”), tidak pernah sebagai rule, dan tidak pernah menyentuh dokumen soul. Jalur tulis
  dokumen tetap hanya `settings`/`language` dengan izin dan approval (ADR-0007).
- **`kind=rule` hanya dari dokumen soul:** satu jalur, `via=soul_doc` (ekstraksi dokumen soul), provenance T0, dan corroboration ≥ 2 hari. Reflection atas memori **tidak pernah** menulis rule (ADR-0010 §4.5); ucapan pengguna tidak punya jalur ke sana.
- **Blok memori menampilkan `kind` untuk rule**, supaya rule asli bisa dibedakan dari fakta biasa.

### 4.4 Anti-poisoning

**A. Batas volume tulisan (rate limit).** Kuota **per kategori**, bukan satu angka. Kelebihan
**tidak** masuk `pending` — itu ember untuk data *belum berlabel* (ADR-0004) dan TTL-nya 7 hari,
terlalu pendek untuk baris yang sudah berlabel. Kelebihannya masuk state **`staged`**: sudah
berlabel (kategori, level, trust, provenance), tidak terbaca, dan TTL-nya mengikuti `kind`
(365/180/90 hari, ADR-0005).

| Pengaman | Isi |
|---|---|
| Kuota berjenjang | `health`/`safety`/`identity`/`commitment` **tidak kena kuota**; `event`/`note`/obrolan yang pertama ditahan |
| `staged` ≠ `pending` | `pending` = belum berlabel, TTL 7 hari. `staged` = sudah berlabel, TTL ikut `kind`. Tidak dibuang, tidak dibaca |
| Promosi saat recall-miss | Kalau retrieval normal (`state='ok'`) tidak menemukan apa pun, baris `staged` yang cocok dengan query dipromosikan ke `ok` **lebih dulu**, baru dipakai — jadi fakta yang baru dibutuhkan tiga minggu kemudian tetap hidup |
| Dinding waktu tetap ada | `staged` mengikuti TTL kind-nya; tak pernah dipakai atau di-promote → habis dengan tombstone seperti baris lain |
| Sinyal sadar menembus | `ingat ini` (prototipe MiniLM, ADR-0005), `pin`, dan tulisan lewat `tool`/`command` tidak kena kuota |
| Burst memicu batch | Volume ≥ kelipatan kuota → worker meninjau `staged` (promosi/purge) lebih awal, bukan menunggu idle timer |

Invariant tetap utuh: **hanya `state='ok'` yang bisa dibaca** (ADR-0004 §5.3). Promosi terjadi
sebelum dipakai, dan saat promosi ceiling privasi (ADR-0006) serta trust (bagian 4.2) tetap
diberlakukan. Jalur miss dibatasi: maksimum satu kali per turn dan maksimum beberapa baris, supaya
ia tidak menjadi pintu belakang untuk membuka seluruh `staged`.

**B. Supersede berbukti.** `supersedes` sah hanya bila: `stated_by` sama, `about` sama, scope dan
`kind` sama, dan klaim baru benar-benar bertentangan. Baris lama tidak dihapus: ia menjadi
`superseded` dengan tanggal dan penunjuk ke penggantinya (TTL 30 hari, ADR-0005), sehingga bisa
dibatalkan dan diaudit. `level` dan `refs` baris lama **dipertahankan** (CHECK di ADR-0016 §4.3).
Baris Tier 1/`pinned` tidak bisa ditimpa pihak lain. Blok memori menampilkan
**id pendek** (mis. `#a1b2`) supaya `supersedes` tidak menebak — melengkapi temuan lubang #4.

**B2. Perubahan status bukan kontradiksi.** Tugas dan komitmen berubah keadaan tanpa ada klaim yang
bertentangan: “proyeknya sudah selesai”, “rencananya dibatalkan”, “digeser ke minggu depan”. Jalur
`supersedes` karena itu menerima **dua sebab** dengan syarat bukti yang sama (`stated_by` sama,
`about`/scope/`kind` sama):

1. **Kontradiksi** — klaim baru meniadakan yang lama.
2. **Perubahan status** — pernyataan baru secara eksplisit menyatakan keadaan baru (`done`,
   `cancelled`, `postponed`), bukan pernyataan berbeda tentang hal lain.

Baris lama tetap menjadi `superseded` bertanggal; yang berubah hanya `status` baris baru. Kalau
pernyataan baru hanya menambah keterangan tanpa mengubah keadaan, ia disimpan sebagai baris biasa —
tidak menimpa apa pun.

**B3. `commitment` punya siklus hidup.** Tugas **selesai atau dibatalkan tetap diingat**, tetapi
**tidak lagi dipin**: ia kehilangan status Tier 1 dan mengikuti TTL `kind`-nya seperti baris lain
(ADR-0005). Supaya tugas yang menggantung tidak abadi:

- `commitment` menerima `due_at` opsional (tenggat, bila disebutkan).
- Lewat tenggat tanpa pernyataan selesai: status ditandai `overdue`, pin dilepas, dan TTL normal
  berlaku — bukan dipin selamanya, bukan dihapus diam-diam.
- Pin (Tier 1) hanya berlaku selama tugas **terbuka**.

**D. Promosi butuh provenance kuat.** Tier 3 → 2 hanya untuk T0/T1 + corroboration ≥ 2 hari
(ADR-0005); Tier 2 → 1 hanya untuk T0 + corroboration, dan T2 tidak pernah ke Tier 1; T1 tentang
orang lain butuh konfirmasi subjek untuk naik.

**C. `disputed` dijalankan di ADR-0010.** Menandai kontradiksi butuh deteksi yang sifatnya batch;
menaruhnya di jalur tulis berarti menambah panggilan model di setiap capture. ADR-0010 sudah
menutupnya: metadata `dispute_state`/`disputed_with` (bukan nilai `state` baru), detektor
deterministik dulu lalu LLM hanya untuk kandidat, dan resolusi lewat subjek di DM. Sampai pass
pertama berjalan, kontradiksi tetap ditangani lewat B (supersede berbukti) dan D (promosi ketat):
klaim lemah tidak bisa menggeser yang kuat, dan penimpaan tanpa bukti ditolak.

### 4.5 Grounding reflection

Reflection (ADR-0010) hanya boleh menyimpulkan dari baris canonical yang `state='ok'` dan
berprovenance T0/T1, mencatat `refs` berisi id baris sumber (nama yang dipakai ADR-0010), dan tidak
menciptakan fakta baru. Baris hasil reflection selalu T2 — petunjuk, bukan aturan — dan tidak bisa
mempromosikan dirinya sendiri. `state` dan trust adalah dua sumbu terpisah: baris turunan boleh
`state='ok'` (terbaca sebagai konteks) sambil tetap T2 (tidak pernah mengikat perilaku), sama seperti
`via=automation`.

### 4.6 Perbaikan kecil yang menyertai

- Blok memori menampilkan id pendek (agar `supersedes` bisa dipakai tanpa pencarian tambahan).
- Blok memori menampilkan `kind` untuk baris `rule` (agar rule asli tidak menyamar).
- Kalau kedua penanda itu tidak diinginkan, alternatifnya memperbaiki kalimat prompt agar tidak
  mengasumsikan id terlihat dan tidak menyebut rule yang tidak bisa dibedakan.

## 5. Konfigurasi

```yaml
memory:
  integrity:
    trust_behavior_gate: true     # aturan/persona hanya dari T0 + dokumen soul
    rules_from_users: false       # kind=rule tidak pernah dari ucapan pengguna
    quarantine: { enabled: true, classifier: true }
    write_limit:
      enabled: true
      per_person: { "1h": 20, "1d": 60 }
      per_chat: { "1h": 50 }
      exempt_categories: [health, safety, identity, commitment]
      exempt_via: [tool, command]
      conscious_intent_bypass: true
      burst_trigger: 3            # kelipatan kuota -> batch lebih awal
      overflow_state: staged      # bukan pending; TTL ikut kind
      staged_ttl: kind            # 365/180/90 hari mengikuti kind (ADR-0005)
      promote_on_miss: { enabled: true, max_per_turn: 1, max_rows: 3 }
    supersede:
      require_proof: true
      keep_old_as: superseded
      ttl_days: 30
      allow_status_change: true   # B2: selesai/dibatalkan/ditunda bukan kontradiksi
    commitment:
      due_at: true                # B3: tenggat opsional
      overdue_unpin: true          # lewat tenggat -> pin dilepas, ikut TTL kind
    promotion:
      require_trust: [T0]
      corroboration_days: 2
    render:
      show_short_id: true
      show_kind_for_rules: true
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Baris T1/T2 yang memengaruhi aturan | 0 (diuji) |
| `kind=rule` yang berasal dari ucapan pengguna | 0 (diuji) |
| Overflow ke `staged` per jam + jumlah `staged` yang belum dipakai | Terlihat di `/status` |
| Promosi `staged` lewat recall-miss | Terukur; 0 berarti jalur miss tidak bekerja |
| Baris `staged` yang habis TTL tanpa pernah dipakai | Terlihat, bukan nol — bahan evaluasi ambang kuota |
| Supersede lolos tanpa bukti | 0 (diuji) |
| Baris Tier 1 dengan provenance T2 | 0 (diuji) |
| Kehilangan senyap (baris hilang tanpa tombstone) | 0 (diuji) |
| False negative karantina (instruksi lolos jadi fakta) | Diukur lewat eval probe |

## 7. Acceptance criteria

- [ ] Setiap baris baru punya `via`, `stated_by`, `about`, `thread_id`, `asserted_at`.
- [ ] Trust dihitung deterministik dari provenance; tidak ada panggilan model pada jalur ini.
- [ ] Aturan/persona/gaya/keputusan tool tidak pernah bersumber dari T1/T2/T3.
- [ ] Teks imperatif dari pengguna tidak pernah tersimpan sebagai `kind=rule`.
- [ ] `kind=rule` hanya lahir dari `via=soul_doc` + T0 + corroboration ≥ 2 hari.
- [ ] Blok prompt menampilkan penanda trust/kind sehingga pembaca bisa membedakan rule asli dan fakta bersumber.
- [ ] Kuota tulisan berjenjang kategori; `health`/`safety`/`identity`/`commitment` tidak tertahan kuota.
- [ ] Overflow menjadi `state='staged'` berlabel dengan TTL mengikuti `kind`; **bukan** `pending`, dan tidak dibuang.
- [ ] Baris `staged` tidak pernah muncul di retrieval/prompt/disclosure normal.
- [ ] Recall-miss mempromosikan baris `staged` yang cocok ke `ok` sebelum dipakai, lewat ceiling privasi dan trust yang sama.
- [ ] Jalur miss dibatasi satu kali per turn dan maksimum `max_rows` baris.
- [ ] Burst (≥ kelipatan kuota) memicu batch `staged` lebih awal, bukan menunggu idle timer.
- [ ] `supersedes` ditolak tanpa bukti; baris lama menjadi `superseded` bertanggal, bukan hilang.
- [ ] Perubahan status (selesai/dibatalkan/ditunda) sah sebagai `supersedes` tanpa harus berupa kontradiksi.
- [ ] Pernyataan yang hanya menambah keterangan tidak menimpa baris mana pun.
- [ ] `commitment` selesai/dibatalkan/`overdue` tidak lagi dipin dan mengikuti TTL `kind`.
- [ ] Promosi Tier 3 → 2 dan 2 → 1 mengikuti syarat provenance + corroboration.
- [ ] Reflection mencatat `derived_from` dan hasilnya selalu T2.
- [ ] Hard exclusion, ceiling privasi, dan kelas izin (ADR-0006/0007) tidak berubah.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Lupa saat percakapan intensif | Jalur prioritas kategori + `staged` berlabel dengan TTL ikut `kind` + promosi saat recall-miss + burst memicu batch + sinyal sadar menembus kuota; ambang di config |
| Fakta dibutuhkan berminggu-minggu kemudian | `staged` tidak dihukum TTL 7 hari seperti `pending`; ia hidup sampai TTL kind-nya dan dipromosikan saat topiknya muncul |
| `staged` menumpuk tanpa pernah dipakai | TTL kind + purge batch + metrik “habis TTL tanpa dipakai” sebagai bahan setel ambang |
| Jalur recall-miss jadi pintu belakang | Dibatasi satu kali per turn + `max_rows`; promosi tetap lewat ceiling privasi dan trust; tercatat di metrik |
| Kuota terlalu ketat untuk chat sah | Angka kuota dan kategori pengecualian dapat disetel; overflow terlihat, bukan hilang |
| Instruksi lolos karantina (false negative) | Pola deterministik + klasifier untuk sisa + aturan prompt + eval probe |
| Fakta biasa salah dikarantina (false positive) | Karantina hanya menurunkan status menjadi fakta bersumber; isi tidak dibuang |
| Dispute/kontradiksi belum tertangani sistemik | Ditangani lewat B/B2 dan D sampai ADR-0010 menambahkan `disputed` |
| Automation menulis memori sendiri tanpa henti | Lihat ADR-0014 §4.6: `via=automation`, trust T2, ikut kuota volume pemilik |
| Tugas menggantung menjadi abadi | B3: `due_at` + status `overdue` melepas pin dan mengembalikan ke TTL `kind` |
| `supersede` dipakai untuk membungkam fakta yang tidak disukai | Syarat bukti (pembicara/subjek/scope sama) + baris lama tetap tersimpan sebagai `superseded` bertanggal, bisa dibatalkan |
| Penambahan atribut provensi | Perubahan schema tanpa shim kompatibilitas (ADR-0001: `SCHEMA_VERSION` naik, install baru) |
| Operator butuh aturan dari pengguna | Tuas `rules_from_users: true` tersedia dengan risiko terdokumentasi dan approval |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical tetap sumber kebenaran; provenance adalah kolomnya, index hanya bayangan.
- **ADR-0002** — kuota volume masuk gerbang struktural (murah, tanpa model); assess tetap batch.
- **ADR-0003** — worker menjalankan batch `staged`/`pending` dan burst tanpa memeriksa trust sendiri.
- **ADR-0004** — state `staged` ditambahkan di sana: berlabel, tidak terbaca, promosi lewat recall-miss atau batch; `pending` tetap untuk data belum berlabel dengan TTL 7 hari.
- **ADR-0005** — TTL `staged` mengikuti `kind`; promosi memakai sinyal durability/corroboration; `superseded` sudah ada di sana.
- **ADR-0006** — ceiling privasi tetap di depan, termasuk saat promosi `staged`; trust tidak menaikkan ceiling.
- **ADR-0007** — kelas izin tidak berubah; `kind=rule` bukan jalur izin baru.
- **ADR-0010** — `disputed`, deteksi kontradiksi batch, dan grounding reflection ada di sana (**accepted** — `ADR-0010-reflection-disputed.md`); ia juga meninjau `staged`. Bentuknya: metadata `dispute_state`/`disputed_with` (bukan nilai `state` baru), dispute tidak memperpanjang TTL, dan resolusi hanya lewat subjek di DM.
- **ADR-0014** — memori hasil automation tunduk pada ADR ini: `via=automation`, trust T2, ikut kuota volume pemilik, tidak boleh `kind=rule`.
- **ADR-0011** — penulisan `pending`, `staged`, dan `superseded` harus bertahan setelah restart.

## 10. Referensi kode

- `monoagens/engine/guard/patterns.py:11-35,16-26` — `SENSITIVE`, `safe_text`, `INJECTION`.
- `monoagens/engine/guard/sandbox.py:139-192` — pola deterministik + klasifier kecil.
- `monoagens/engine/agent.py:906-918` — gerbang capture (injeksi, panjang, `can_memory`).
- `monoagens/engine/access/privacy.py:225-277,295-310` — capture turn dan `_assess`.
- `monoagens/engine/memory/mem0.py:308-392,323-338,527-530` — metadata, supersede, veto.
- `monoagens/engine/memory/context.py:91-133` — `render` (titik penambahan id/kind) dan `build`.
- `monoagens/engine/tools/memory.py:69-105,175-183,230-234` — `add`, `kind`, `supersedes`, `veto`.
- `monoagens/engine/commands/memori.py:83-134` — pintu command (source `command`).
- `monoagens/engine/jobs/ambient.py:251-256,310-315` — sintesis soul (source `ambient:<chat>`).
- `monoagens/engine/turn/prompts.py:53-56,266,300-316` — aturan prompt memori dan krisis.
- `monoagens/engine/schema.py:129-142` — tabel `people` (trust relasi, bukan trust memori).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0008-memory-integrity.md`.*
