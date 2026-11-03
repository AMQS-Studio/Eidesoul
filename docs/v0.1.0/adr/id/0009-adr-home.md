> Terjemahan turunan — jangan sunting; sumber: 0009-adr-home.md (sha256: fbca2fee5a8b)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0009: Rumah ADR, Folder Dokumen Berversi & Penjaganya

- **Status:** accepted
- **Implementasi:** implemented — bukti: `docs/v0.1.0/adr/`, `scripts/adr.py --check`, `tests/test_adr.py`; hasil run dicatat di `docs/v0.1.0/README.md`
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** menutup antrean ADR-0001 §14; bersinggungan dengan ADR-0015 §4.9 (kapabilitas `docs`)
- **Supersedes:** —
- **Terkait:** ADR-0001 (§14 antrean), ADR-0015 (§4.9 dokumen produk)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/` dan tidak punya salinan kedua.
- **Amandemen:** 2026-11-03 — ADR-0031/0032 implemented: pohon engine 15 berkas level atas dengan empat folder domain, floor toolchain 3.14 dengan ratchet pyright, dan `dispatch`/`receipt` masuk register. 2026-11-02 — §4.4 kontrak kerangka dipertegas (judul kanonik, urutan relatif, wilayah bagian tambahan, `Amandemen` sebagai baris header, rujukan `§…` wajib terselesaikan) dan ditambah **audit 15 ADR**: 7 sesuai, 8 kurang bagian; **perbaikan diterapkan pada hari yang sama → 15/15 sesuai** (`0002`/`0004` sekaligus memindahkan `Amandemen` ke header); §4.10 kini hanya memindahkan berkas. 2026-11-03 — audit: aturan check ditambah "heading tidak boleh di dalam fence; pemeriksaan wajib fence-aware"; audit sebelumnya (2026-11-02) melaporkan 15/15 padahal ADR-0015 punya heading di dalam fence — kelas cacat itu kini tercatat, dan §4.4 menyebutnya. 2026-11-03 — **ADR-0016** (skema & migrasi) masuk set: hitungan menjadi 16 berkas, kepemilikan DDL `memories` pindah dari ADR-0001 ke ADR-0016 §4.1, dan ADR-0001 §4 kini hanya memuat daftar tabel + peran. 2026-11-03 — allowlist dokumen ADR-0015 berbasis path, jadi `docs/v0.1.0/README.md` (indeks ADR) dapat dibedakan dari `README.md` produk. 2026-11-03 — keputusan pemilik: §4.12 **register istilah** ditambahkan (istilah kanonik + pemilik + sinonim terlarang), dan `--check` menolak sinonim di fence YAML/SQL; akar masalahnya bukan satu pasangan istilah, melainkan tidak ada pemilik kosakata. 2026-11-03 — register diperbarui: `send` satu-satunya kapabilitas kirim (tool + langkah otomasi memakai implementasi yang sama); `send_to` dicatat sebagai nama yang **dilebur setelah kode menyusul**, bukan sinonim terlarang hari ini. 2026-11-03 — audit: §4.4 menambah aturan label `Referensi kode` (sitasi nyata atau label `usulan`). **2026-11-03** — set pindah ke repo (`docs/v0.1.0/adr/` + indeks `docs/v0.1.0/README.md`); header `Implementasi` (`implemented`/`sebagian`/`draft` + bukti) masuk kontrak §4.4; sel sinonim register diberi lingkup (tanpa kurung = semua baris definisi, `(kolom)` = kolom SQL, `(nama role)` = blok `roles:`, sebutan entitas lain = prosa yang tidak digerbang) dengan pengecualian penanda `kolom lama`/`legacy`; `scripts/adr.py` (`--check`/`--new`/`--translate`/`--translate-check`, tanpa LLM) dan `tests/test_adr.py` menjadi penegaknya. **2026-11-03** — §4.7/§4.8: dua backend turunan (`glossary` = struktur saja, jujur `konten 0/N`; `opus` = konten via NMT lokal, perkakas dev tanpa dependency), plus batas bentuk yang dikunci test: baris fence tidak pernah dikirim, marker (`###`/`>`/`1.`/`-`) dan jumlah kolom tabel terjaga, penanda `**tebal**` dilindungi sementara teksnya tetap diterjemahkan, paragraf >400 karakter dipecah per kalimat, cap keluaran mengikuti `model.config.max_length`, dan memori dibatasi (thread 4, batch 8). 2026-11-03 — penerjemah mesin dan backend-nya dihapus; salinan Indonesia ditulis tangan dan `--translate-check` satu-satunya gate. 2026-11-03 — register: `Address` (pemilik ADR-0032) ditambahkan; ADR-0031 dan ADR-0032 masuk sebagai `draft`.

---

## 1. Konteks

**16 ADR hidup di luar repositori.** Semuanya ada di `/home/muqsith/Documents/`. Akibatnya
keputusan arsitektur tidak ikut `git log`/`git blame`, tidak bisa ditaut dari `README.md` atau
`AGENTS.md`, tidak ikut review perubahan, dan hilang bersama folder pribadi.

**Aturan repo hari ini melarangnya masuk.** `AGENTS.md:17-18`: “Tracked docs are `TASK.md`,
`README.md`, `README.id.md`, `LOG.md`, `AGENTS.md`, dan `soul.yaml`. Do not create other tracked
`.md` files.” Bukti keadaan sekarang: `git ls-files '*.md'` hanya mengembalikan lima berkas itu.
Larangan itu masuk akal (mencegah dokumen liar yang basi), tetapi justru **keputusan arsitektur** —
artefak termahal kalau hilang — yang tertahan di luarnya.

**Bahasa pun belum punya izin.** `AGENTS.md:7-12` menjadikan English sebagai default dan membatasi
permukaan non-English pada katalog `eidesoul/lang/`, `soul.yaml`, `eidesoul/library/**`, dan
`README.id.md`. Prosa ADR hari ini Indonesia; tanpa perubahan aturan, memindahkannya ke repo berarti
melanggar aturan sendiri secara diam-diam.

**Belum ada aturan bentuk, status, dan penomoran.** Dua kelas kejadian sudah nyata di 15 ADR ini:
(a) ADR-0004, ADR-0005, ADR-0007, dan ADR-0008 diamandemen setelah diterima, jadi butuh aturan
amandemen yang eksplisit; (b) satu ADR (0015) sempat memuat tanda pagar kode telanjang di dalam sel
tabel — merusak rendering Markdown dan hanya tertangkap karena kebetulan diperiksa manual, bukan
karena ada gerbang.

**Antrean menumpang di ADR memori.** ADR-0001 §14 berfungsi ganda sebagai antrean ADR berikutnya dan
daftar status seluruh ADR. Daftar keputusan proyek menumpang pada satu ADR tentang sumber kebenaran
memori — dua peran dalam satu berkas.

**Dokumen rilis belum punya rumah.** Arsitektur, kapabilitas, dan catatan rilis hari ini tersebar di
`/home/muqsith/Documents/` dan `references/` (yang isinya log chat, bukan dokumentasi).

**Yang sudah diverifikasi agar keputusan ini aman:**

| Fakta | Bukti | Akibat |
|---|---|---|
| `docs/` belum ada | `ls` root repo | Membuat direktori baru di root |
| Docs tidak ikut distribusi | `pyproject.toml:47-48` — `include = ["eidesoul*"]` | Nol dampak ke wheel/Docker |
| Docs tidak dipindai analisis | `sonar-project.properties:5` — `sonar.sources=eidesoul,scripts` | Tidak perlu exclusion baru |
| Pola penjaga sudah ada | `README.md:254-258` dan `scripts/{patterns,fakta,affect}.py --check` | Skrip penjaga ADR mengikuti konvensi yang sudah terbukti |
| Versi paket masih pra-rilis | `pyproject.toml` `version = 0.0.0.dev0`, `AGENTS.md` §Shape | Nama folder versi harus jujur: penanda **target**, bukan klaim rilis |

## 2. Decision drivers

1. **Keputusan ikut riwayat kode** — blame, review, dan `grep` yang sama seperti kode.
2. **Satu tempat untuk satu hal** — satu set ADR hidup, satu daftar, tanpa dua salinan yang bisa drift.
3. **Dokumen berversi, keputusan tidak** — folder rilis boleh dibekukan; nomor ADR global dan tidak
   pernah dipakai ulang.
4. **Aturan repo harus jujur** — kalau aturan lama menghalangi, aturannya diubah eksplisit, bukan
   dilanggar diam-diam.
5. **Kalau bisa dicek skrip, jangan andalkan disiplin** — pola `scripts/*.py --check` yang sudah ada.
6. **Nol dependensi baru, nol CI baru** — repo ini tanpa `.github`; gerbang harus jalan lewat pytest
   dan skrip yang bisa dijalankan operator.
7. **Satu sumber bahasa** — prosa Indonesia sebagai sumber, English sebagai turunan; dua berkas tidak
   boleh saling menyaingi kebenaran.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Status quo** | ADR tetap di luar repo | Nol perubahan | Tidak ikut riwayat; hilang bersama folder pribadi; tidak bisa ditaut | Ditolak |
| **B. `adr/` di root** | Direktori ADR sejajar `eidesoul/`, `tests/`, `deploy/` | Paling terlihat | Root sudah berisi 21 entri dan bercampur kode; tidak ada tempat untuk dokumen rilis | Ditolak |
| **C. `docs/adr/` versionless + `docs/v0.1.0/` untuk naratif** | Dua rumah dokumen | Keputusan tidak pernah perlu dipindah | Dua tempat dokumen untuk satu rilis; pemilik memilih satu folder versi sebagai rumah dokumen | Ditolak |
| **D. `docs/<versi>/` sebagai rumah dokumen + `adr/` di dalamnya** | Satu folder per siklus rilis; `docs/v0.1.0/README.md` jadi indeks; satu set ADR hidup dipindah dengan `git mv` saat siklus baru | Satu rumah untuk satu rilis; riwayat utuh; nol dampak distribusi | Perlu aturan pemindahan yang tegas supaya tidak ada dua set | **Dipilih** |
| **E. Wiki / GitHub Discussions** | Keluar dari berkas | Enak dibaca | Butuh jaringan, tidak bisa di-`grep` offline, tidak ikut review perubahan | Ditolak |

## 4. Keputusan

> **ADR hidup di dalam folder dokumen berversi di repo — hari ini `docs/v0.1.0/adr/`, dengan indeks
> `docs/v0.1.0/README.md` — satu set hidup yang dipindah utuh dengan `git mv` saat siklus rilis baru
> dibuka, dijaga `scripts/adr.py` yang mengecek bentuk, status, nomor, tautan, dan kesegaran
> terjemahan. `AGENTS.md` diamandemen: `docs/**` masuk daftar dokumen tracked dan permukaan
> non-English yang sah.**

### 4.1 Rumah dan penamaan

```
docs/v0.1.0/            # dokumen siklus rilis v0.1.0 (target pertama)
├── README.md           # indeks: daftar dokumen + daftar ADR (nomor · judul · status · tanggal · berkas)
└── adr/
    ├── 0001-initial-invariants.md   # root: lintas-potong (fondasi semua permukaan)
    ├── 0009-adr-home.md             # root: rumah ADR dan penjaganya
    ├── engine/                      # ADR yang mengikat runtime engine
    │   ├── 0001-memory-source-of-truth.md
    │   ├── id/                      # salinan Indonesia folder ini
    │   └── ...
    ├── web/                         # ADR permukaan web/panel
    │   ├── id/
    │   └── ...
    └── id/                          # salinan Indonesia untuk ADR root
        └── 0001-initial-invariants.md
```

- Nama berkas `NNNN-slug.md`: empat digit, nol depan, slug kebab huruf kecil, tanpa tanggal.
- **Folder adalah kategori, nomor adalah urutan keputusan**: akar rumah ADR memuat keputusan
  lintas-potong (fondasi, rumah ADR, distribusi, plugin), `engine/` memuat yang mengikat runtime
  engine, `web/` memuat permukaan web/panel.
- Nomor **unik di seluruh rumah ADR, kronologis, dan tidak pernah dipakai ulang**; nomor `superseded`
  tetap terpakai. Memindahkan ADR antar folder **tidak** menomori ulang yang lain: lubangnya dibiarkan
  sengaja (`engine/` tidak punya `0009` karena rumah ADR pindah ke root, dan `web/` mulai kosong).
- ADR fondasi bernomor `0000`, satu-satunya nomor di bawah `0001`, karena ia mengikat semua permukaan;
  `0001` ke atas adalah seri kronologisnya.
- Rujukan ditulis `ADR-NNNN`. Bentuk eksplisit `ADR-<folder>/NNNN` diterima bila pembaca ingin menyebut
  foldernya; ia wajib hanya kalau suatu saat ada nomor yang bertabrakan antar folder, yang ditolak gate
  sebagai rujukan telanjang yang ambigu.
- Nama folder `v<MAJOR>.<MINOR>.<PATCH>` menandai **target rilis** folder itu. Selama belum dirilis,
  `pyproject.toml` tetap memakai versi dev (`0.0.0.dev0`) sesuai `AGENTS.md` §Shape — folder versi
  bukan klaim rilis, hanya penanda siklus.
- Berkas ADR tidak pernah dijilid ulang ke nomor lain; referensi `ADR-XXXX` selalu ke nomor asli.

### 4.2 Satu set hidup, dipindah antar siklus

Hanya boleh ada **satu** direktori `docs/*/adr/`. Saat siklus rilis berikutnya dibuka
(`docs/v0.2.0/`), seluruh set dipindah dengan `git mv` — riwayat berkas terjaga — sehingga:

- dokumen rilis yang sudah keluar menjadi **beku** (tidak disunting lagi, kecuali perbaikan tautan);
- keputusan tetap satu daftar, tanpa salinan yang bisa drift;
- dokumen naratif rilis lama menaut ADR lewat **nomor + judul**, bukan path relatif ke folder yang
  bisa berpindah. Path relatif ke `adr/` hanya dipakai di dalam folder versi yang sedang hidup.

Aturan ini ditegakkan skrip: lebih dari satu `docs/*/adr` = gagal check.

### 4.3 Indeks sebagai satu-satunya daftar

`docs/v0.1.0/README.md` memuat: satu baris ringkas proyek, daftar dokumen naratif, dan daftar ADR
berformat `nomor · judul · status · tanggal · berkas`, dengan `berkas` adalah path relatif terhadap
rumah ADR (`engine/0016-schema-migration.md`). ADR-0001 §14 berhenti menjadi antrean dan
berubah menjadi **penunjuk** ke indeks ini — satu daftar, bukan dua.

### 4.4 Kerangka wajib

Setiap ADR punya header dan bagian wajib berikut, dengan **judul kanonik yang persis** dan urutan
relatif tetap:

| Bagian header | Keterangan |
|---|---|
| `Status` | `proposed` \| `accepted` \| `superseded` \| `deprecated` \| `rejected` |
| `Tanggal` | ISO `YYYY-MM-DD` |
| `Deciders` | pemilik + pemeriksa |
| `Konteks` | ADR/rancangan yang jadi latar |
| `Supersedes` / `Superseded-by` | timbal balik bila ada penggantian |
| `Terkait` | ADR lain yang relevan |
| `Amandemen` | opsional; satu baris per perubahan bertanggal |
| `Implementasi` | `implemented` \| `sebagian` \| `draft` + bukti atau sisa pekerjaan yang konkret |

Bagian wajib, berurutan: `Konteks` → `Decision drivers` → `Opsi yang dipertimbangkan` →
`Keputusan` → `Konfigurasi` → `Metrik dan gate` → `Acceptance criteria` → `Risiko dan mitigasi` →
`Interaksi dengan ADR lain` → `Referensi kode`.

Aturan bentuk:

- nomor bagian naik **monoton**, tanpa duplikat; lompatan nomor tidak diizinkan;
- bagian **tambahan** boleh ada, tetapi hanya di dua wilayah: setelah `Decision drivers` dan sebelum
  `Keputusan`, atau setelah `Keputusan` dan sebelum `Referensi kode`. Bagian bernomor terakhir selalu
  `Referensi kode`;
- `Amandemen` **bukan** bagian tersendiri: ia baris header, satu klausa bertanggal per perubahan.
  Satu tempat untuk satu hal - riwayat perubahan terbaca dari header tanpa ada dua daftar;
- heading `##` **tidak boleh berada di dalam fence**: saat dirender ia bukan bagian, dan penomoran
  tampil melompat. Pemeriksaan wajib **fence-aware** (heading yang "hilang" di dalam blok kode = cacat);
- identifier yang dipakai lintas ADR wajib memakai istilah dari **register §4.12**; sinonim
  terlarang di dalam fence YAML/SQL → gagal check;
- setiap ADR memuat baris header `Implementasi` bernilai `implemented`, `sebagian`, atau `draft`.
  `implemented` = keputusan sudah cocok dengan kode hari ini **dan** bukti disebut; `sebagian` =
  bagiannya ada dan sisanya disebut; `draft` = belum ada di kode. Nilai lain, atau klaim `implemented`
  tanpa bukti, dianggap cacat: status harus bisa diverifikasi, bukan diingat;
- bagian wajib yang memang kosong **tetap ditulis** sebagai satu baris jujur
  (“Tidak ada kunci konfigurasi baru dalam ADR ini.”), bukan dihapus — supaya urutan bisa dicek;
- rujukan antar-bagian (`§4.5`, `ADR-0008 §4.4B3`) wajib menunjuk judul yang benar-benar ada; check
  memverifikasi setiap rujukan `§...` terselesaikan di berkas tujuannya, dan setiap rujukan
  `ADR-XXXX` mengarah ke ADR nyata (nomor unik di seluruh rumah ADR, jadi nomor telanjang
  terselesaikan; `ADR-<folder>/NNNN` adalah bentuk eksplisit dan hanya wajib bila ada tabrakan).
  Dua pengecualian supaya check tidak salah tuduh: rujukan yang **menyebut berkas lain**
  (`ADR-0001 §14`, `ADR-0015 (§4.9)`) diperiksa terhadap berkas itu, bukan berkas ini; dan rujukan di
  dalam `code span` (contoh aturan, seperti `§11` di kalimat ini) tidak dipindai;
- setiap butir di `Referensi kode` wajib salah satu: sitasi yang benar-benar ada di repo, atau label
  `modul/skrip baru`/`usulan` — supaya klaim “keadaan hari ini” bisa dibedakan dari rencana.

**Audit 2026-11-02 atas 15 ADR** (prototipe aturan di atas: judul kanonik, penomoran, wilayah bagian
tambahan, resolusi rujukan `§…`):

| Keadaan awal | Berkas | Tindakan |
|---|---|---|
| Sesuai kontrak | 0002, 0004, 0005, 0008, 0009, 0010, 0014 (7) | — |
| Kurang `Konfigurasi`, `Metrik dan gate`, `Interaksi dengan ADR lain` | 0001 | tiga bagian satu-dua baris ditambahkan; tail dinomori ulang (Acceptance 9→11, Risiko 10→12, Pertanyaan 11→14, Referensi 12→15) |
| Kurang `Opsi yang dipertimbangkan` | 0003 | satu baris jujur; §4→§5 termasuk subbagian, sesudahnya bergeser |
| `Konfigurasi` bersarang sebagai subbagian | 0006 (`### 4.5`), 0007 (`### 4.4`) | dipromosikan ke bagian `## 5. Konfigurasi`; 0007 subbagian 4.5–4.7 → 4.4–4.6 |
| Kurang `Interaksi dengan ADR lain` | 0011, 0012 | bullet yang dilacak ke `Terkait`/teks yang sudah ada; Referensi bergeser |
| Kurang `Opsi`, `Keputusan`, `Interaksi` | 0013 | `Opsi` satu baris jujur; `Keputusan` ringkasan yang menunjuk bagian detail; `Interaksi`; penomoran 3–14 → 5–17 |
| Heading `##` tanpa nomor | 0015 | `Kapabilitas (index)` → `## 5.`; sesudahnya 6–11 |
| `Amandemen` sebagai bagian, bukan baris header | 0002, 0004 | isi dipadatkan ke baris header `Amandemen:`; bagian `## 12. Amandemen` dihapus |

**Hasil setelah perbaikan (2026-11-02): 15/15 sesuai kontrak** - penomoran monoton, seluruh judul
kanonik ada dan berurutan, tidak ada heading `##` tanpa nomor, tidak ada rujukan `§...` menggantung.
Perbaikan dilakukan di dokumen kerja; migrasi §4.10 berikutnya hanya memindahkan berkas.

**Audit 2026-11-03 menemukan satu kelas cacat yang lolos pemeriksaan 2026-11-02:** heading bernomor
yang berada **di dalam fence** (`ADR-0015` §5), yang membuat dokumen tampil 1-4 lalu 6-11 sementara
pemeriksa teks-menghitungnya sebagai bagian sah. Sudah diperbaiki (baris dihapus, bagian 6-11 menjadi
5-10); aturan fence-aware di §4.4 di atas adalah hasil pelajaran itu. Audit yang sama juga memverifikasi
129 klaim kode, dan menemukan 12 konflik semantik antar-ADR (daftar: `Eidesoul-Audit-ADRs-2026-11-03.md`).

Tiga kelas kesalahan yang tertangkap prototipe ini dan karena itu masuk daftar check: rujukan `§11`
yang sebenarnya menunjuk berkas lain (harus ditulis `ADR-0001 §14`), heading `##` tanpa nomor, dan
heading bernomor yang berada di dalam fence (butuh pemindaian fence-aware).




### 4.5 Siklus status dan amandemen

- `proposed` → `accepted` sekali. Setelah `accepted`, **keputusan tidak diubah di tempat**.
- Perubahan yang diizinkan lewat amandemen bertanggal: penajaman kalimat, detail konfigurasi/nama,
  tambahan kriteria uji, tautan silang. Amandemen **tidak boleh membalik keputusan**.
- Membalik keputusan = ADR baru dengan `Supersedes: ADR-XXXX`, dan ADR lama diberi
  `Superseded-by` + `Status: superseded`. Dua arah harus cocok.
- ADR yang keputusannya tidak lagi berlaku tanpa pengganti → `deprecated`, dengan alasan satu baris.

### 4.6 Kapan **tidak** menulis ADR

Satu ADR = satu keputusan yang tidak bisa dibalik tanpa jejak. Tidak perlu ADR untuk: penamaan
variabel, bentuk pesan log, pilihan library yang bisa dilepas dalam satu commit, atau apa pun yang
reversible dan lokal → cukup komentar kode atau `TASK.md`.

### 4.7 Bahasa: English sumber, Indonesia salinan

- **English adalah sumber.** `docs/v*/adr/NNNN-slug.md` ditulis dalam English, dan gate (§4.4) berdiri
  di atas judul bagian English (`Context` ... `Code references`) serta nilai `Implementation`
  `implemented | partial | draft`.
- **Salinan Indonesia** hidup di folder `id/` dalam folder yang sama —
  `docs/v*/adr/id/NNNN-slug.md` untuk ADR root, `docs/v*/adr/engine/id/NNNN-slug.md` untuk ADR engine —
  dan dibuka dengan header turunan yang menyebut sumber dan `sha256`-nya. Salinan **ditulis tangan**;
  tidak ada penerjemah mesin dan tidak ada dependency terjemahan di `pyproject.toml` atau `uv.lock`.
- Salinan **opsional** per berkas; salinan **basi** (hash sumber berubah) menggagalkan
  `--translate-check`. Aturannya: sunting sumber English, sunting salinan di baris yang sama, lalu
  stempel ulang hash. Salinan tidak pernah diregenerasi sebagai kerangka struktur saja.

### 4.8 `scripts/adr.py`

| Mode | Fungsi |
|---|---|
| `--check` | nama & format nomor berkas; nomor unik di seluruh rumah ADR, kronologis, tidak pernah dipakai ulang (pindah ADR meninggalkan lubang; `0000` adalah ADR fondasi); tepat satu `docs/*/adr`; bagian kanonik English ada dan berurutan; `Status` sah; tanggal ISO; sinonim terlarang (§4.12) di fence YAML/SQL; rujukan `ADR-XXXX`/`ADR-<folder>/XXXX`/`§N` terselesaikan (nomor telanjang wajib unik; `ADR-<folder>/XXXX` bentuk eksplisit); fence seimbang dan tidak ada pagar telanjang di sel tabel; path footer; `Implementasi` sah |
| `--new "<judul>" [--dir root\|engine\|web]` | menyusun kerangka ADR berikutnya di folder yang dipilih, dari template, tanpa mengarang isi |
| `--translate-check` | membandingkan hash tercatat dengan sumbernya; salinan basi atau tanpa hash = gagal |

`--check` mencetak setiap pelanggaran dengan berkas dan baris lalu keluar non-nol. `pytest -q`
menjalankannya lewat `tests/test_adr.py`, bersebelahan dengan penjaga `patterns.py`/`fakta.py`/
`affect.py`. Kelas cacat "pagar telanjang di dalam sel tabel" sudah pernah terjadi di ADR-0015.

### 4.9 Amandemen `AGENTS.md`

- `AGENTS.md:17-18` menjadi: dokumen tracked = lima berkas lama **+ `docs/v*/**`** (dokumen rilis dan
  ADR). Larangan dokumen liar tetap berlaku untuk sisanya.
- `AGENTS.md:7-12` menambah `docs/v*/**` sebagai permukaan non-English yang sah, dengan catatan
  turunan di setiap `adr/**/id/` adalah salinan, bukan suntingan manual atas sumbernya.
- Ditambah satu baris di bagian `Verify`: `python scripts/adr.py --check` ikut dijalankan seperti
  skrip penjaga lain.

### 4.10 Migrasi

1. Buat `docs/v0.1.0/adr/` dan pindahkan 16 ADR dengan penggantian nama `ADR-0001-…` → `0001-…`.
2. Buang baris `Catatan lokasi` di header (sudah tidak berlaku) dan footer
   “*Belum ada perubahan pada repositori.*” (menjadi basi begitu masuk repo).
3. Perbarui baris `Terkait`/`Konteks` yang menyebut “disimpan di luar repo sampai ADR-0009”.
4. Normalisasi kerangka (§4.4) **sudah dikerjakan di dokumen kerja pada 2026-11-02** (tabel di §4.4):
   delapan berkas dilengkapi tanpa menghapus konten, `0015` diberi nomor, dan `0002`/`0004`
   memindahkan `Amandemen` dari bagian ke baris header. Migrasi karena itu tidak lagi menyentuh isi —
   hanya nama berkas, `Catatan lokasi`, dan footer.
5. Setelah penomoran berubah, **perbarui rujukan `§…`** yang menunjuk nomor bagian yang bergeser
   (mis. rujukan ke antrean `ADR-0001 §14`) dan pastikan check rujukan lolos.
6. Tulis `docs/v0.1.0/README.md` sebagai indeks dari isi berkas (nomor, judul, status, tanggal).
7. ADR-0001 §14 menjadi penunjuk ke indeks.
8. Laporan review `Eidesoul-Review-Arsitektur-2026-11-01.md` **tetap di luar repo** — ia artefak
   kerja pribadi (pendapat + skor), bukan catatan keputusan; penunjuknya diarahkan ke
   `docs/v0.1.0/`.

### 4.11 Apa yang bukan ADR

`LOG.md` kronik kejadian, `TASK.md` daftar kerja, `README.md` wajah produk, `AGENTS.md` aturan
perubahan. ADR hanya keputusan yang mengikat dan mahal dibalik.

### 4.12 Register istilah & sinonim terlarang

Istilah yang dipakai lebih dari satu ADR dimiliki **satu** ADR; ADR lain memakai nama itu apa adanya.
Tabel ini sumbernya, dan istilah kanonik baru masuk di commit yang sama saat ia lahir.

| Istilah kanonik | Arti ringkas | Pemilik | Sinonim terlarang |
|---|---|---|---|
| `memory_events` | jurnal capture mentah | 0001 | `event_log`, `raw_events` |
| `memories` | baris canonical | 0016 (bentuk), 0004 (state) | `facts`, `memory_rows` |
| `cognition_jobs` | antrean pekerjaan kognisi, semua `kind` | 0003 | `memory_outbox`, `outbox`, `jobs` |
| `state` | status baris `memories` (enam nilai) | 0004 | `status` (untuk `memories`) |
| `status` | status entitas non-memori (`turns`, `cognition_jobs`, `automation_runs`) | 0013/0003/0014 | `state` (untuk entitas itu), `pending` (status turn) |
| `turns` / `turn_steps` | unit kerja percakapan dan langkahnya | 0013 | `turn_jobs`, `sessions`, `actions` |
| `outbound_messages` | ledger kiriman | 0013 | `ledger`, `sent_messages` |
| `scope_key` | kunci ledger: `turn:<id>` / `automation:<run>` | 0013 | `turn_id` (prosa), PK ledger yang salah |
| `automation` / `automation_runs` | otomasi dan riwayat run | 0014 | `rule` (prosa), `job` (prosa), `task` (prosa) |
| `send` | satu-satunya kapabilitas kirim: `path?`/`text?` + `chat?` (kosong = chat aktif); dipakai turn, recipe, dan langkah otomasi | gateway `whatsapp` (`tools/send.py`) + ADR-0014 | alias resmi: `send_to`, `kirim_file`; terlarang: `forward`, `notify` |
| `gateway` | sebuah body: transport plus aksi tubuhnya sendiri; dipasang sebagai gateway, bukan plugin atau tool | 0027 (kontrak: engine/0018) | `connector` (prosa), `bridge` (prosa) |
| `tool` | kapabilitas yang dipanggil engine; hanya diberikan role, tidak pernah oleh plugin; prosedur turunan adalah skill (ADR-0020), bukan tool | 0027 | `skill` (prosa), `action` (prosa) |
| `plugin` | unit kapabilitas terinstall yang diunduh saat diaktifkan; bukan gateway dan bukan tool | 0028 | `extension` (prosa), `addon` (prosa) |
| `plugin manifest` | `plugins/<name>/plugin.yaml`: name, version, rentang engine, entry, provides, requires, secrets, credits | 0028 | `metadata file` (prosa), `plugin.json` (prosa) |
| `plugins` | folder install workspace tempat kode plugin hasil unduhan dan dependency-nya | 0028 | `extensions` (prosa), `modules` (prosa) |
| `digest` | hash terpin yang harus cocok sebelum komponen terpasang menulis apa pun | 0027 (aturan: 0028) | `checksum` (prosa) |
| `panel` | permukaan operator web: proses terpisah di akar repo yang memakai jalur import config | 0027 | `dashboard` (prosa), `admin ui` (prosa) |
| `clean by default` | gateway, tool, dan plugin masing-masing mati sampai diaktifkan; spine satu-satunya pengecualian | 0027 | `opt-in` (prosa), `default off` (prosa) |
| `voice layer` | emosi, humanize, dan karakter: aktif secara default, ditanyakan sekali saat `init` dengan jawaban default tetap nyala, masing-masing bisa dimatikan; bukan kategori kapabilitas | 0027 | `persona layer` (prosa), `tone layer` (prosa) |
| `operator action` | install/enable/disable/remove yang dimulai manusia, tercatat di `config_changes` dan ledger | 0027 | `manual step` (prosa) |
| `test store` | suite jalan di store yang dikirim (Postgres+pgvector); SQLite bukan store | 0029 | `in-memory store` (prosa), `sqlite` (prosa) |
| `EIDESOL_TEST_DATABASE_URL` | DSN database tes sekali pakai; kosong berarti suite gagal dengan instruksi | 0029 | `TEST_DB` (prosa), `DATABASE_URL_TEST` (prosa) |
| `truncate per test` | aturan isolasi: satu DDL per sesi, `TRUNCATE ... RESTART IDENTITY CASCADE` per tes | 0029 | `rollback` (prosa, sebagai aturan isolasi), `recreate schema` (prosa) |
| `stub embedder` | embedder deterministik 384 dimensi yang dipakai hanya di dalam tes | 0029 | `fake model` (prosa), `dummy encoder` (prosa) |
| `install.sh` | installer on-machine tunggal di akar repo: preflight, `uv` yang dipin, `uv sync --locked`, lalu menyerahkan ke `init` | 0030 | `bootstrap.sh` (prosa), `setup.sh` (prosa) |
| `interactive init` | `eidesoul init` bertanya di TTY dan gagal tertutup di luarnya, menyebut flag yang dibutuhkan | 0030 | `wizard command` (prosa), `setup` (prosa, sebagai perintah terpisah) |
| `logo slot` | area banner yang dipesan untuk mark produk, diisi belakangan; tidak pernah jadi kunci config | 0030 | `theme` (prosa), `branding config` (prosa) |
| `dry run` | `--dry-run` mencetak setiap aksi dan tidak menulis apa pun | 0030 | `simulate` (prosa), `noop mode` (prosa) |
| `rooms_of(person_id)` | himpunan room tempat penanya menjadi anggota; dasar izin **baca** (kirim punya kelasnya sendiri, §4.5) | 0007 | `user_rooms` |
| `cross_send` | kelas kiriman lintas room (`send.class=cross`); atribusi wajib; default di room bernilai `send.class=room` | 0007 | `blind_send`, `anonymous_send` |
| `cross_read` | **invarian**: tidak ada kapabilitas baca lintas room; ditolak `rooms_of`, tanpa override (syarat privasi enterprise) | 0007 | `allow_cross_read`, `cross_read_enabled` |
| `attribution` | blok identitas pengirim + alasan pada kiriman; `author` wajib (termasuk DM), `reason` wajib untuk `cross_send`, field lain fleksibel | 0007 | `embedded`, `signature`, `header` |
| `quoted_refs` | buffer kutipan room lain untuk guard kebocoran verbatim (bukan izin) | 0007 | `cross_reads` (nama kode lama, di-rename saat implementasi) |
| `relay_grant` | izin pemilik isi untuk meneruskan jawaban pribadinya ke pihak lain, per tugas + mode | 0023 (bentuk: 0016) | `relay_permission`, `forward_consent` |
| `automation_state_log` | jejak transisi state automation | 0014 (bentuk: 0016) | `automation_events` |
| `draft`/`active`/`paused`/`stopped`/`expired` | lima state automation (terminal: `stopped`, `expired`) | 0014 | `disabled` (per automation), `denied` (sebagai state) |
| `occurrence` | satu kemunculan jadwal = satu run; `occurrence_key = hash(automation_id, scheduled_at_utc)` | 0014 | `tick`, `slot`, `instance` |
| `participants(thread_id)` | fakta keanggotaan dari gateway | 0007 (seam) | `members`, `roster` |
| `level` | privasi baris memori dan ceiling (0–4) | 0008/0006 | `privacy` (kolom), `sensitivity` (kolom) |
| `visibility` | level arsip pesan yang dibaca lintas room | 0007 | `level` (untuk arsip) |
| trust `T0`–`T3` | provenance kepercayaan | 0008 | `confidence`, `Tier` (untuk trust) |
| `Tier` 1–3 | kelas ketahanan retensi | 0005 | `retention_tier`, `priority` |
| `Lapis` 1–2 | lapisan payload prompt | 0015 | `Tier` (untuk payload) |
| `stated_by` / `about` | atribut provenance; kolom `owner_id` / `subject_id` | 0008 | `sender`, `subject` (sebagai kolom) |
| `via` | sumber tulisan (enum tujuh nilai) | 0008 | `source` (sebagai kolom baru), `origin` |
| `expires_at` / `max_lifetime_at` / `due_at` | TTL baris / batas absolut / tenggat tugas | 0005 / 0008 | `ttl_at`, `hard_ttl`, `deadline` |
| `pinned` | boolean pin | 0005 | `pin` (kolom), `sticky` |
| `quota` / `budget` | batas volume tulis / batas biaya LLM | 0008 / 0014 | `limit` (untuk keduanya) |
| `parameters` | skema argumen tool (subset §4.2 ADR-0024), sumber tunggal untuk manifest dan validator | 0024 | `args_schema`, `input_schema` |
| `additionalProperties` | kunci asing pada argumen tool; **absen = `false`** | 0024 | `strict_extra_args`, `allow_unknown_args` |
| `shell.env` | whitelist environment untuk tool `shell`; tidak pernah diwarisi, tidak menerima `*` | 0024 | `env_inherit`, `pass_env` |
| `prefix stabil` / `blok volatil` | dua bagian prompt: `system[0]` cacheable byte-identik, `system[1]` berubah per turn | 0025 | `static_prefix`, `dynamic_prompt` |
| `prompt_prefix_hash` | hash bagian stabil + skema tool aktif; metrik cache, bukan izin | 0025 | `cache_key`, `prefix_id` |
| `max_calls_per_turn` | plafon jumlah panggilan LLM dalam satu turn | 0025 | `call_budget`, `turn_calls` |
| `turn_attempts` / `worker_attempts` | jumlah percobaan ulang per jalur (turn vs pekerja latar) | 0017 | `max_retries` (nama lama), `retries` |
| `retry_after_max_seconds` | plafon tunggu `Retry-After` di jalur turn | 0017 | `wait_cap`, `backoff_max` |
| `text_tsv` | kolom turunan `tsvector` pencarian pesan | 0026 (bentuk: 0016) | `search_vector`, `tsv` |
| `trgm_threshold` | ambang `similarity()` untuk jaring trigram | 0026 | `fuzzy_threshold`, `similarity_min` |
| `search_backend` | backend pencarian aktif (`fts+trgm` / `like`), tampil di `/status` | 0026 | `search_mode`, `engine_type` |
| `credential_patterns` | pola pembacaan rahasia; kelasnya selalu `authorization` | 0007 | `secret_patterns`, `sensitive_files` |
| `notice` / `apology` | pesan status turn / pesan tugas terlewat | 0013 / 0014 | `announcement`, `reminder` |
| `dispute_state` | metadata sengketa (bukan `state`) | 0010 | `disputed` (sebagai state) |
| `superseded` | state baris yang digantikan | 0008/0016 | `replaced`, `obsolete` |
| `MemoryStore` | port tulis tunggal | 0001 | `MemoryRepo`, `memory_repository` |
| `GatewayCore` / `Rich` / `Directory` | tiga protocol gateway: wajib / opsional transport / opsional direktori | 0018 | `Gateway`, `Adapter` (untuk kontrak) |
| `dispatch` | satu-satunya panggilan engine→body: `dispatch(action) -> Receipt`; body merutekan per verb | 0032 | `emit`, `deliver` (sebagai anggota body), `submit`, `mailbox` |
| `receipt` | jawaban sebuah Command Message: id + state (`sent`/`unknown`/`pending`/`refused`) + reference | 0032 | `ack`, `confirmation`, `callback` |
| `send_sticker` | kirim sticker; **bukan** reaksi ke pesan | 0018 | `send_reaction` |
| `mode` | keadaan runtime instance: `primary` / `standby` | 0012 | `state` (untuk instance), `role` |
| `turn_id` | id turn; benang merah log dan `llm_calls` | 0013 | `trace_id`, `session_id` |
| `llm_calls` | catatan per panggilan LLM (token, latensi) | 0019 (bentuk: 0016) | `llm_log`, `usage_rows` |
| `chat_locks` | lock per chat, bukan per proses | 0017 | `turn_lock` (nama baru), `global_lock` |
| `episode` | rangkaian turn untuk satu pekerjaan di satu room; unit penilaian prosedur | 0020 | `task`, `session`, `thread` |
| `skills` / `skill_revisions` | prosedur turunan dan riwayat versinya | 0020 (bentuk: 0016) | `playbook`, `wiki` (untuk prosedur) |
| `distill` | `kind` job yang menilai satu episode | 0020 | `learn`, `train` |
| `signature` | rantai tool + jenis target penanda dua episode sebanding | 0020 | `fingerprint`, `goal_hash` |
| `grep` / `fetch` | dua tool baca hemat token (cari isi berkas / baca URL teks) | 0021 | `rg`, `curl`, `wget` |
| `consent` / `authorization` | kelas approval dari **target**: scope penanya (consent) / di luar scope (authorization); sensitivitas hanya menentukan perlu-tidak | 0007 | `self_approve`, `auto_approve` |
| `scope_proof` | bukti deterministik jangkauan aksi saat approval diminta | 0007 (bentuk: 0016) | `scope_hash` (sebagai kolom) |
| `administrator` | role tertinggi, terproteksi, `label` bebas | 0022 | `admin`, `root`, `master` (nama role) |
| `label` | sebutan **opsional** untuk pemegang role mana pun; dipakai prompt untuk memanggil mereka | 0022 | `nickname`, `title`, `alias` (untuk role) |

Aturan penegakan:

- `scripts/adr.py --check` menggerbangi sinonim pada **baris definisi** di dalam fence (kunci YAML,
  kolom SQL). Lingkup larangan diambil dari tanda kurung di sel sinonim: **tanpa** tanda kurung =
  berlaku di semua baris definisi; `(kolom)` = hanya kolom SQL; `(nama role)` = hanya di dalam blok
  `roles:`. Tanda kurung yang menyebut entitas lain (mis. `(untuk memories)`) berarti aturan itu
  **prosa** — dibaca manusia saat review, tidak digerbang, dan dilaporkan sebagai `baris prosa` di
  keluaran check. Baris definisi yang memuat penanda `kolom lama`/`legacy` dikecualikan (kolom warisan
  yang masih dipetakan). Prosa bebas menyebut nama lama
  (mis. "nama lama: `memory_outbox`") karena itu bagian dari penjelasan, bukan definisi; tabel
  register ini sendiri dikecualikan.
- Istilah kanonik baru hanya lahir lewat penambahan baris di tabel ini, di commit yang sama.
- Prosa manusia bebas ("kirim", "ruangan", "ingatan"); yang dikunci adalah **identifier**.
- Kolom "pemilik" menentukan siapa berhak mengubah arti; mengubah arti tanpa amandemen pemilik = gagal review.
- **Migrasi nama `send_to`**: kode hari ini punya `send` (berkas, chat aktif) dan `send_to` (teks/berkas,
  chat lain). Peleburan keduanya menjadi `send(chat=…)` adalah pekerjaan implementasi; sampai selesai,
  `send_to` tetap nama yang sah dan belum dihitung sebagai sinonim.
- Register memuat **dua kelas pemilik**: ADR (keputusan) dan gateway/kode (nama yang sudah hidup di repo).
  Nama milik kode tidak boleh diubah dari ADR tanpa perubahan kode; sebaliknya langkah ADR yang bertabrakan
  dengan nama tool wajib dibedakan (lihat baris `send` di atas).

## 5. Konfigurasi

Tidak ada kunci config engine baru: dokumentasi bukan bagian runtime. Satu-satunya konfigurasi
adalah argumen skrip.

```
python scripts/adr.py --check                        # gerbang bentuk (dipakai pytest)
python scripts/adr.py --new "Judul ADR" --dir engine # kerangka di folder engine/
```

Konteks dokumen yang sudah ada tidak berubah: `docs/v0.1.0/README.md` bisa masuk allowlist berbasis path
`engine.self_knowledge.docs` (ADR-0015 §4.9) kalau operator mau soul menjelaskan arsitektur sendiri.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| ADR lolos `scripts/adr.py --check` | 100% |
| Nomor duplikat / hilang / dipakai ulang | 0 |
| Nomor bagian tidak monoton / duplikat | 0 |
| ADR yang belum sesuai kontrak kerangka §4.4 | 0 — **16/16 sesuai per 2026-11-03** |
| Bagian `## … Amandemen` (riwayat harus di baris header) | 0 |
| Rujukan `§…` yang menggantung setelah penomoran ulang | 0 (diuji) |
| Rujukan `ADR-XXXX` yang tidak terselesaikan | 0 |
| Sinonim terlarang (§4.12) di fence YAML/SQL | 0 |
| Direktori `docs/*/adr` lebih dari satu | 0 |
| Turunan English basi (hash tidak cocok) | 0 |
| Kelas kesalahan “pagar telanjang di sel tabel” | 0 (diuji dengan fixture) |

## 7. Acceptance criteria

- [ ] `docs/v0.1.0/` berisi `README.md` dan `adr/`; 16 ADR hasil migrasi ada dengan nomor dan slug yang sama.
- [ ] `docs/v0.1.0/README.md` adalah satu-satunya daftar ADR; ADR-0001 §14 menunjuk ke sana.
- [ ] `AGENTS.md` mencantumkan `docs/v*/**` sebagai dokumen tracked dan permukaan non-English yang sah, serta menyebut `scripts/adr.py --check` di bagian Verify.
- [ ] Semua 16 ADR sesuai kontrak kerangka §4.4 — **diterapkan 2026-11-02/03** (delapan berkas dilengkapi tanpa menghapus konten; ADR-0013 dapat ringkasan `Keputusan`; `0015` dinomori).
- [ ] `Amandemen` hanya hidup sebagai baris header; tidak ada bagian `## Amandemen` (0002/0004 sudah dipindahkan).
- [ ] Nomor bagian naik monoton tanpa duplikat; bagian tambahan hanya di wilayah yang diizinkan; bagian bernomor terakhir selalu `Referensi kode`.
- [ ] Setiap rujukan `§…` (internal maupun `ADR-XXXX §…`) menunjuk judul yang ada; penomoran ulang sudah memperbarui rujukan yang bergeser.
- [ ] `scripts/adr.py --check` gagal pada: sinonim terlarang §4.12 di fence YAML/SQL, nama berkas salah, nomor duplikat/hilang, judul bagian tidak berurutan, `Status` tidak sah, tanggal bukan ISO, `Supersedes` tidak timbal balik, rujukan `ADR-XXXX` menggantung, pagar kode ganjil, pagar telanjang di sel tabel, dua direktori `adr/`, hash terjemahan basi.
- [ ] `scripts/adr.py --new` menghasilkan kerangka dengan sepuluh bagian wajib, dan angka romawi/nomor bernomor benar tanpa menebak isi.
- [ ] Turunan English ditandai jelas sebagai turunan beserta hash sumber; berkas tanpanya tidak dianggap gagal.
- [ ] `pyproject.toml` tetap `include = ["eidesoul*"]` sehingga `docs/` tidak masuk wheel; `uv sync --locked` dan `pytest -q` tetap hijau.
- [ ] Tidak ada `.md` baru di luar `docs/v*/`; `git ls-files '*.md'` tetap bersih dari dokumen liar.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Dua set ADR hidup setelah siklus rilis baru | Check menolak lebih dari satu `docs/*/adr`; pemindahan wajib `git mv` supaya riwayat terjaga |
| ADR basi dibiarkan `accepted` padahal kode berubah | Amandemen bertanggal wajib; perubahan keputusan harus ADR baru dengan `Supersedes` timbal balik |
| Penomoran ulang memutus rujukan `§…` | Check memverifikasi setiap rujukan `§…` terselesaikan; langkah migrasi §4.10.5 memperbarui rujukan yang bergeser sebelum commit |
| Normalisasi menyentuh ADR yang sudah `accepted` | Hanya penambahan (deklarasi kosong jujur) dan satu ringkasan `Keputusan` untuk ADR-0013; tidak ada keputusan yang diubah atau dihapus |
| Dokumen liar tumbuh lagi | Larangan `AGENTS.md` tetap, hanya `docs/v*/**` yang dikecualikan |
| Turunan English menyimpang dari sumber | Hash sumber di baris pembuka + check; turunan tidak pernah jadi sumber |
| Overhead menulis ADR untuk hal sepele | §4.6 menetapkan batas; `--new` hanya menyusun kerangka |
| Skrip penjaga sendiri dianggap “berat” | Nol dependensi baru (regex + `pathlib` + `hashlib`); mengikuti pola `patterns.py`/`fakta.py` yang sudah dipakai |
| Folder versi disalahpahami sebagai klaim rilis | Nama folder = target siklus; `pyproject.toml` tetap versi dev sampai rilis nyata; dicatat di §4.1 |

## 9. Interaksi dengan ADR lain

- **ADR-0001 §14**: antrean berhenti jadi daftar, berubah menjadi penunjuk ke `docs/v0.1.0/README.md`.
  Berlaku untuk semua ADR yang sudah `accepted`.
- **ADR-0015 §4.9**: `docs/v0.1.0/README.md` bisa masuk allowlist `docs` (berbasis path) sehingga soul bisa membaca
  indeks arsitektur sendiri; ADR (`adr/**.md`) bukan default allowlist — keputusan berisi catatan
  internal (path host, ambang) yang tidak perlu masuk payload.
- **ADR-0005/0008** (konsep): tidak terpengaruh; yang berubah hanya rumah berbagi dokumen.

## 10. Referensi kode

- `AGENTS.md:7-12` — aturan bahasa (permukaan non-English yang diizinkan; tempat amandemen §4.9).
- `AGENTS.md:17-18` — daftar dokumen tracked (tempat amandemen §4.9).
- `pyproject.toml:42-51` — build backend setuptools, `include = ["eidesoul*"]`, package-data
  (bukti `docs/` tidak ikut distribusi).
- `pyproject.toml` `version = 0.0.0.dev0` — versi pra-rilis sesuai `AGENTS.md` §Shape.
- `sonar-project.properties:5` — `sonar.sources=eidesoul,scripts` (bukti tanpa exclusion baru).
- `README.md:254-258` — dokumentasi `scripts/patterns.py`/`fakta.py --check` (preseden penjaga).
- `scripts/affect.py`, `scripts/patterns.py`, `scripts/fakta.py` — pola “data + skrip penjaga”.
- `tests/` — rumah uji yang memanggil `scripts/adr.py --check`.
- `LOG.md`, `TASK.md` — permukaan yang tetap bukan ADR (§4.11).

---

*ADR ini disimpan di `docs/v0.1.0/adr/0009-adr-home.md`.*
