> Terjemahan turunan — jangan sunting; sumber: engine/0020-procedural-memory.md (sha256: 6b52ac71b01c)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0020: Prosedur Turunan — Skill sebagai Pertumbuhan Soul, Bukan Tool

- **Status:** accepted
- **Implementasi:** partial — ada: `episodes` + enam sinyal penutup, `distill` (satu revisi per episode), `skill_revisions`, retrieval sadar-scope + `uses`/`last_used`, global ditolak tanpa T0/approval, permukaan owner `/skill`, `tests/test_episodes.py`; sisa: perbandingan efisiensi §4.8/§4.9 + tangga automation (butuh `llm_calls`, W6); tool `skill` untuk model dihapus sesuai §4.1
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** tool `skill` yang eksplisit tidak terpakai, tetapi menagih indeks ±3000 karakter di setiap prompt untuk setiap role yang memilikinya; skema `skills` tanpa scope/provenance/versi membuatnya menjadi kanal instruksi global yang bisa ditulis model — jalur yang ADR-0008 tutup untuk memori
- **Supersedes:** —
- **Terkait:** ADR-0001 (pola capture), ADR-0003 (`cognition_jobs`), ADR-0005 (TTL/pin), ADR-0006 (ceiling privasi), ADR-0008 (karantina/trust), ADR-0010 (reflect & merge), ADR-0013 (`turn_steps`), ADR-0014 (otomasi & approval), ADR-0015 (anggaran payload), ADR-0016 (DDL), ADR-0017 (penutup episode), ADR-0019 (metrik biaya)
- **Amandemen:** 2026-11-03 — keputusan pemilik: penilaian dilakukan **saat satu tugas selesai** (episode ditutup), bukan setiap turn; LLM menilai isi episode di worker, bukan di jalur percakapan. 2026-11-03 — ditambah **penutup episode ke-6** (artefak terkirim) dan **§4.8 perbandingan antar-episode**. 2026-11-03 — ditambah **§4.9 tangga efisiensi**; keputusan promosi/otomasi **tidak boleh di-hardcode**. 2026-11-03 — ditambah **§4.10: belajar menyala secara default (out of the box)** — tidak ada opt-in, tidak ada pertanyaan setelah tugas selesai; approval hanya untuk perubahan tingkat otonomi (otomasi).
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0020-procedural-memory.md`.

---

## 1. Konteks

Skill hari ini adalah **tool eksplisit**: `list`/`view`/`read`/`save`/`archive`, dengan indeks dimasukkan ke prompt lewat `skills.index()` (`agent.py:640`) dan dibatasi `max_index_chars: 3000` (`config.py:157`).

| Fakta | Bukti |
|---|---|
| Indeks selalu ikut, walau kosong | `agent.py:637-640`; `stores/skills.py:88-102` |
| Pemakaian hanya tercatat saat model memanggil `view` | `stores/skills.py:149` |
| Skema tanpa scope, provenance, versi, trust | `schema.py` `skills(name, description, body, archived, created_at, updated_at)` |
| `member` memegang tool `skill` | `roles.yaml:8-14` |
| Prompt menyuruh model mengurusnya sendiri | `prompts.py:318` ("action=save; archive useless skills") |
| Tidak ada data pemakaian di lapangan | laporan pemilik: tool tidak pernah dipakai |

Bandingkan dengan memori, yang bekerja tanpa disuruh: capture berjalan di latar (`spawn`), penilaian di worker, retrieval yang menentukan apakah sesuatu berguna. Skill seharusnya mengikuti pola yang sama, bukan meminta model "ingat untuk menyimpan".

Konsep yang dipakai di sini: **skill = prosedur turunan** — "cara aku biasanya melakukan X di room/person ini", disimpulkan dari jejak kerja yang sudah ada, dibandingkan satu sama lain untuk menemukan cara yang lebih murah, dan akhirnya — bila memang bisa — berhenti menjadi percakapan sama sekali.

## 2. Decision drivers

- Prompt tidak boleh membayar kapabilitas yang tidak dipakai setiap turn.
- Belajar terjadi di **batas pekerjaan**, bukan di setiap kalimat: satu tugas selesai = satu kesempatan menilai.
- Instruksi persisten tidak boleh lahir tanpa scope, provenance, dan trust gate (ADR-0008).
- Biaya evaluasi harus di luar jalur percakapan: tidak menambah latensi dan tidak menahan lock (ADR-0017).
- **Efisiensi harus terukur**, bukan dirasakan: pertanyaan "cara mana yang lebih murah" hanya sah bila ada angka per episode (ADR-0019) dan perbandingan yang sebanding.
- Pembelajaran boleh mengubah **prosedur**, tidak pernah mengubah **aturan, persona, atau izin**.
- **Tidak ada angka ajaib.** Ambang tetap yang memicu promosi atau konversi otomasi adalah hardcode yang berbahaya: ia akan memindahkan pekerjaan yang belum stabil.
- **Out of the box.** Setelah tugas selesai, orang tidak peduli lagi. Karena itu belajar, membandingkan, dan menua berjalan sendiri dengan setelan default — tanpa opt-in, tanpa pertanyaan, tanpa tombol yang harus ditekan. Mematikan adalah keputusan operator, bukan syarat untuk memperoleh manfaatnya.

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Tool eksplisit (status quo)** | model memutuskan `save` | indeks 3000 char/turn | tidak terpakai; kanal instruksi global tanpa trust | Ditolak |
| **B. Prosedur turunan + retrieval** | worker menilai episode, prosedur disimpan di tabel `skills` dengan siklus hidup memori, diinjeksi saat relevan, dibandingkan lintas episode, dan bisa ditawarkan menjadi otomasi | worker + retrieval | noise/duplikasi | **Dipilih** |
| **C. `kind='procedure'` di `memories`** | satu tabel | kecil | badan prosedur panjang bercampur fakta dan merusak render blok memori | Ditolak |

## 4. Keputusan

### 4.1 Skill bukan tool

- Tool `skill` **dihapus** dari katalog dan dari role mana pun; indeks skill **tidak lagi** masuk prompt (`skills.index()` dihapus dari `agent.py`).
- Kontrol owner tetap eksplisit lewat satu command owner-only: inspeksi, pin, lupa, dan impor dari berkas. `member` tidak mendapat permukaan apa pun.
- Prosedur hanya masuk prompt lewat **retrieval** (§4.6), dengan anggaran karakter yang sama seperti blok memori.

### 4.2 Episode: unit penilaian

- **Episode** = rangkaian turn berurutan di satu room yang boleh mengerjakan satu pekerjaan; `episode_id` = id turn pertama.
- Episode **ditutup** oleh salah satu sinyal deterministik:
  1. jeda menganggur di room itu ≥ `skills.task_idle_minutes` (default 10),
  2. penanda selesai eksplisit dari pengguna ("selesai", "sudah bisa", "oke beres") — lewat prototipe MiniLM yang sama dengan sinyal sadar ADR-0005,
  3. perpindahan topik (`currents`/`topics` berganti) setelah minimal satu turn kerja,
  4. `commitment` yang ditutup, atau `automation_run` yang selesai,
  5. shutdown rapi: episode yang masih terbuka ditutup, bukan dibuang,
  6. **artefak terkirim**: rangkaian turn yang berakhir dengan kiriman berkas (`outbound_messages` dengan lampiran) dianggap pekerjaan selesai — dinilai setelah jeda pendek `skills.idle_after_send_minutes` (default 3), bukan menunggu 10 menit.
- Episode **tidak** menjadi tabel baru: penutupnya dihitung dari `turns`/`backlog`/`outbound_messages`/`thread_eras` yang sudah ada; yang dicatat hanya hasilnya.

### 4.3 Kapan dinilai

- Saat episode ditutup, engine memasukkan **satu** job `cognition_jobs(kind='distill')` dengan `episode_id` dan rujukan bukti (id turn, signature tool, hasil, penanda koreksi).
- Filter sebelum enqueue (deterministik, tanpa LLM): episode hanya masuk bila **ada kerja tool** (≥1 panggilan `execute`) atau **ada penanda koreksi** dari pengguna. Obrolan biasa tidak pernah dinilai.
- **LLM menilai isi episode di worker** (batch, lease, `SKIP LOCKED`, anggaran ADR-0003/0010) — bukan di jalur percakapan, bukan setiap turn. Evaluasi yang selesai menulis **paling banyak satu** prosedur per episode.
- Kegagalan evaluasi tidak memblokir apa pun (episode sudah selesai); job masuk dead-letter seperti job lain (ADR-0011).

### 4.4 Apa yang boleh menjadi prosedur

Kandidat hanya diterima bila memenuhi **salah satu** bukti deterministik:

| Bukti | Contoh |
|---|---|
| Rantai tool berulang | urutan `read → sandbox → write` dengan argumen serupa sukses ≥ 2 kali |
| Error → perbaikan | `execute` gagal dengan satu argumen, lalu sukses setelah argumen diubah |
| Koreksi pengguna | "bukan begitu, pakai X" diikuti hasil yang diterima |
| Prosedur lama yang menolong | skill yang diinjeksi, dipakai, dan episode sukses (menaikkan `uses`) |

Dan **semua** syarat ini:

- `via=reflection` dengan `refs` ke turn/episode sumber (tanpa rujukan → ditolak);
- isi lolos guard `SENSITIVE`/`INJECTION` (ADR-0008); prosedur tidak pernah memuat kredensial;
- tidak bisa menambah tool atau izin — prosedur hanya menyebut tool yang sudah ada di katalog;
- scope default `chat` atau `person`; `global` hanya bila sumbernya owner (T0) atau dipromosikan lewat approval;
- isi ≤ `skills.max_body_chars` (default 18 000) dan deskripsi ≤ 200 karakter.

### 4.5 Penyimpanan

- Tabel `skills` tetap, dengan kolom tambahan: `scope`, `via`, `stated_by`, `source_episode`, `uses`, `last_used`, `revision`, `trust`.
- Riwayat append-only di `skill_revisions(skill, revision, body, description, created_at, source_episode)`; `save`/merge tidak menghancurkan versi lama sehingga rollback mungkin.
- Bentuk kolom, constraint, dan index **milik ADR-0016** (§4.7); ADR ini hanya menetapkan isi dan pemakaiannya.
- **Biaya tidak disimpan di baris skill**: ia dihitung saat perbandingan (§4.8) dari `llm_calls`/`turn_steps`, supaya angka bisa dihitung ulang dan tidak menjadi metadata basi.

### 4.6 Retrieval, bukan indeks

- Pencocokan memakai embedder yang sama (`embed.py`, MiniLM 384-d) terhadap `description` + langkah, hanya di dalam scope yang boleh dibaca penanya (ADR-0006 ceiling).
- Prosedur diinjeksi sebagai blok referensi dengan batas `skills.max_chars` (default 800) untuk **seluruh** prosedur pada turn itu; satu prosedur penuh lebih baik daripada lima potongan.
- Prosedur yang membutuhkan tool yang **tidak** dimiliki role di turn itu tidak diinjeksi (bukan sekadar disembunyikan): soul tidak boleh menawarkan langkah yang pasti gagal.
- Injeksi menaikkan `uses`/`last_used` — sinyal renewal TTL (ADR-0005).

### 4.7 Siklus hidup

- TTL mengikuti pemakaian, bukan tanggal tulis: prosedur yang tidak pernah dipakai melewati `skills.use_renewal_days` (default 30) dan TTL-nya berakhir → `archived`, bukan dihapus, sehingga masih bisa dipulihkan owner.
- `pin` hanya untuk prosedur yang ditulis/diimpor owner; prosedur turunan tidak bisa mem-pin dirinya.
- Duplikat digabung saat pass konsolidasi (ADR-0010): deskripsi mirip + langkah mirip → satu survivor, revisi dicatat.

### 4.8 Perbandingan antar-episode: "caranya lebih efisien gimana?"

Ini jawaban untuk pertanyaan yang berbeda dari "tadi aku belajar apa": yang pertama mengingat **isi**, yang kedua membandingkan **metode**.

- **Signature** = nama tool berurutan + jenis target (room/berkas/dokumen) + scope. Bukan isi pesan, bukan argumen bebas — supaya "pekerjaan yang sama" tetap sama walau kalimatnya berbeda.
- **Sebanding dulu, baru dibandingkan.** Dua episode hanya boleh dibandingkan bila signature-nya sama. Perbandingan lintas signature dilarang — itu membandingkan apel dan jeruk.
- **Angka diambil dari sumber yang sudah ada**: `llm_calls` (token/`latency_ms`, ADR-0019) di-join ke episode lewat `turn_id`, ditambah `turn_steps` (ADR-0013) untuk jumlah langkah dan kegagalan, dan `uses` prosedur yang diinjeksi.
- **Hanya saat ada bukti cukup**: perbandingan berjalan saat `reflect` (ADR-0010) bila ada ≥ `skills.efficiency.min_evidence_episodes` (default 2) episode bersignature sama di dalam jendela `skills.efficiency.window_days` (default 30). Angka ini **batas bukti**, bukan pemicu keputusan.
- **Tindakan yang diizinkan** (hanya pada prosedur):
  1. **promosikan** prosedur dengan median token/langkah lebih rendah **bila suksesnya setara** → revisi baru + ranking retrieval lebih tinggi (bukan kekebalan),
  2. **gabungkan** dua prosedur mirip (konsolidasi ADR-0010),
  3. **arsipkan** prosedur boros yang selalu kalah, dengan revisi tetap tersimpan,
  4. **usulkan** koreksi langkah ke prosedur lama (revisi baru, bukan timpa diam-diam).
- **Batas keras**: hasil perbandingan **tidak pernah** mengubah aturan, persona, gaya, izin, atau default keamanan. Yang boleh berubah hanya langkah prosedur.
- **Deterministik di luar keputusan**: pemilihan pasangan episode, perhitungan median, dan kelengkapan angka tanpa LLM; LLM hanya dipakai bila dua prosedur harus diringkas menjadi satu teks prosedur.
- **Terlihat, bukan diam-diam**: setiap promosi/arsip menulis baris di `skill_revisions` + satu `notice` operator (bukan pesan ke room), sehingga owner bisa membaliknya.

### 4.9 Tangga efisiensi: prosedur → otomasi, diputuskan dari bukti (bukan angka tetap)

Pertanyaan "turn-nya bisa lebih rendah lagi?" hanya sah bila dijawab dari episode nyata, bukan dari aturan tetap.

- **Paket bukti** disusun worker per signature: daftar episode, variasi argumen, median turn/token/langkah, kegagalan, perbedaan langkah antar-episode, dan artefak yang dihasilkan.
- **Yang deterministik hanya menjaga kualitas bukti**, bukan memicu keputusan: minimal `min_evidence_episodes` episode, `failed_episodes = 0` di jendela, dan deliverable setara. Tidak ada aturan "N kali sukses → otomasi".
- **LLM menilai paket bukti** dan mengembalikan tepat satu keluaran dengan alasan + `refs`: `keep`, `merge`, `promote`, atau `propose_automation`.
- `promote` — dipakai bila langkah dapat dipendekkan tanpa kehilangan hasil; efeknya revisi prosedur + ranking retrieval.
- `propose_automation` — usulan `event` → `condition` → `steps` yang harus menangkap **semua** kasus di paket bukti (variasi argumen, kegagalan yang pernah terjadi). Usulan **tidak mengeksekusi apa pun**: ia masuk `approvals` dan menunggu owner (ADR-0014 §4.5, §4.9). Ditolak atau kedaluwarsa → tetap prosedur, tanpa percobaan ulang diam-diam.
- **Larangan eksplisit (hardcode):** tidak ada pemetaan `signature → automation`, tidak ada daftar rantai tool "boleh jadi otomasi", tidak ada target jumlah turn sebagai gate. Menambah tool baru tidak boleh menuntut perubahan kode di jalur ini; signature baru tetap dinilai dengan aturan yang sama.
- **Lantai percakapan**: 2-3 turn (retrieval + aksi + konfirmasi). Penurunan ke 0 turn hanya lewat otomasi yang **disetujui**, bukan dengan memotong verifikasi.
- **Reversibel**: prosedur hasil promosi yang kemudian gagal turun kembali ke revisi sebelumnya; otomasi yang gagal dinonaktifkan ke `paused` dan pekerjaan dikembalikan ke prosedur percakapan, dengan satu notice operator.

### 4.10 Default menyala: belajar tidak boleh menunggu dipinta

- `skills.enabled`, `skills.efficiency.enabled`, dan `skills.ladder.enabled` **menyala secara default**. Instalasi baru belajar tanpa mengubah konfigurasi; mematikan adalah opt-out operator, bukan syarat memakai.
- **Tidak ada pertanyaan belajar ke pengguna.** Setelah tugas selesai tidak ada "mau saya simpan cara ini?", tidak ada konfirmasi prosedur, tidak ada prompt persetujuan untuk distilasi. Pertanyaan hanya lahir dari hak (privasi L3/L4, veto) dan dari otonomi (usulan otomasi), bukan dari pembelajaran.
- **Tidak ada tombol yang harus ditekan.** Prosedur terbentuk, dipromosikan, dan diarsipkan tanpa interaksi; satu-satunya jejak yang dilihat pengguna adalah jawaban yang lebih pendek dan lebih tepat.
- **Operator tetap melihat**: setiap promosi/arsip/usulan otomasi muncul di `/status` dan notice operator, sehingga diamnya pembelajaran bukan berarti tak terawasi.
- **Degradasi tanpa bertanya**: bila provider tidak mengembalikan `usage`, perbandingan efisiensi dilewati (bukan meminta angka ke pengguna); bila embedder tidak tersedia, retrieval jatuh ke kata kunci. Tidak ada jalur yang berakhir dengan "mohon konfirmasi untuk melanjutkan belajar".

## 5. Konfigurasi

```yaml
skills:
  enabled: true              # default nyala; opt-out operator, bukan opt-in
  task_idle_minutes: 10      # penutup episode karena jeda
  idle_after_send_minutes: 3 # penutup episode setelah artefak terkirim
  per_episode: 1             # maksimum prosedur baru per episode
  max_chars: 800             # batas injeksi seluruh prosedur per turn
  max_body_chars: 18000      # batas badan satu prosedur
  use_renewal_days: 30       # TTL diperpanjang bila dipakai
  index_in_prompt: false     # tetap false: skill tidak pernah jadi indeks
  efficiency:
    enabled: true
    min_evidence_episodes: 2 # batas bukti, bukan ambang keputusan
    window_days: 30
  ladder:
    enabled: true
    proposal_cooldown_days: 14  # anti-spam usulan, bukan pemicu keputusan
```

Kunci lama `max_index_chars` dihapus bersama indeks.

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `episodes_closed_total` | episode yang ditutup per sinyal §4.2 | terlaporkan per jenis penutup (termasuk `artifact_sent`) |
| `distill_jobs_total` | job `distill` yang di-enqueue | hanya episode dengan kerja tool/koreksi |
| `skills_created_total` / `skills_merged_total` | prosedur baru / digabung | ≤ 1 per episode |
| `skill_hits_total` | injeksi yang dipakai di episode sukses | naik seiring waktu |
| `skill_chars_injected` | karakter prosedur per turn | ≤ `skills.max_chars` |
| `skill_decay_archived_total` | prosedur yang diarsipkan karena tak dipakai | terlihat, bukan nol-diam |
| `efficiency_compares_total` | pasangan episode bersignature sama yang dibandingkan | > 0 setelah bukti cukup |
| `skill_promoted_total` / `skill_archived_expensive_total` | promosi karena lebih murah / arsip karena boros | setiap tindakan punya baris revisi |
| `ladder_evaluations_total` | paket bukti yang dinilai untuk tangga efisiensi | hanya saat bukti cukup + cooldown |
| `automation_proposals_total` / `automation_proposals_approved_total` | usulan otomasi / yang disetujui owner | usulan tanpa approval tidak pernah dieksekusi |
| `episode_cost_tokens_p50/p95` | token per episode bersignature sama | dilaporkan; tren turun = pembelajaran bekerja |
| `learning_prompts_total` | pertanyaan belajar/konfirmasi skill yang dikirim ke pengguna | **0** (out of the box tanpa pintu) |
| `prompt_skill_chars` | karakter skill di prompt di luar retrieval | **0** (indeks dihapus) |

## 7. Acceptance criteria

- [ ] `skills.index()` tidak lagi dipanggil di jalur prompt; `prompt_skill_chars = 0` pada setiap turn.
- [ ] Tool `skill` tidak ada di katalog engine dan tidak diberikan ke role mana pun; command owner-only tersedia untuk inspeksi/pin/lupa/impor.
- [ ] Episode ditutup oleh keenam sinyal §4.2, termasuk `artifact_sent` dengan `idle_after_send_minutes`; episode tanpa kerja tool dan tanpa koreksi tidak menghasilkan job `distill`.
- [ ] Tepat satu job `distill` per episode tertutup; evaluasi berjalan di worker, bukan di jalur percakapan (tidak menambah latensi turn).
- [ ] Prosedur baru selalu punya `via=reflection`, `refs` episode, dan scope `thread`/`person`; `global` hanya dari owner atau approval.
- [ ] Prosedur yang memuat `SENSITIVE`/`INJECTION` ditolak; prosedur tidak pernah menambah tool/izin (test terhadap katalog).
- [ ] Injeksi retrieval ≤ `skills.max_chars`, hanya tool yang dimiliki role, dan menaikkan `uses`/`last_used`.
- [ ] Prosedur tak terpakai melewati `use_renewal_days` → `archived`, dapat dipulihkan, dan tidak muncul di retrieval.
- [ ] `save`/merge menulis baris `skill_revisions` sehingga versi sebelumnya bisa dipulihkan.
- [ ] Perbandingan efisiensi hanya terjadi antar-episode **bersignature sama**, dalam jendela, dan hanya saat bukti cukup; tidak ada perbandingan lintas signature (test dengan dua signature berbeda).
- [ ] Promosi/arsip tidak pernah mengubah aturan/persona/izin; test memastikan tidak ada jalur dari §4.8 ke `soul.yaml`/roles.
- [ ] Setiap promosi/arsip tercatat di `skill_revisions` dan menghasilkan satu `notice` operator yang bisa dibalik.
- [ ] Tangga efisiensi **tidak memakai angka tetap**: tidak ada aturan "N sukses → otomasi"; keluaran `ladder` selalu berasal dari penilaian atas paket bukti dan membawa `refs` (test: signature baru dinilai tanpa perubahan kode).
- [ ] `propose_automation` tidak mengeksekusi apa pun; tanpa approval owner ia tetap prosedur, dan usulan yang ditolak tidak diulang diam-diam.
- [ ] Instalasi baru dengan konfigurasi default menjalankan `distill` tanpa perubahan setelan; `learning_prompts_total = 0` (test memindai pesan keluar: tidak ada pertanyaan belajar/konfirmasi skill).
- [ ] Test kebocoran: prosedur ber-scope `person`/`chat` tidak pernah muncul di room lain.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Distilasi menghasilkan prosedur sampah | syarat bukti §4.4 + maksimum satu per episode + decay berbasis pemakaian |
| Prosedur salah bertahan lama | revisi append-only + `archived` + owner bisa lupa/pin; konsolidasi menggabungkan duplikat |
| Biaya evaluasi membengkak | hanya episode berkerja tool/koreksi; batch worker; `per_episode: 1` |
| Prosedur membocorkan isi room privat | scope default `chat`/`person`; ceiling ADR-0006 saat retrieval; test kebocoran |
| Prosedur menjadi jalur instruksi baru | isi tetap data sampai diinjeksi; karantina ADR-0008; tidak bisa menambah tool |
| Evaluasi menilai episode yang belum benar-benar selesai | penutup deterministik §4.2; episode yang dibuka paksa oleh shutdown tetap ditutup, bukan dinilai dua kali (idempoten per `episode_id`) |
| Perbandingan efisiensi membandingkan hal yang tidak sebanding | syarat signature + scope + jendela; tanpa kecocokan signature, tidak ada perbandingan sama sekali |
| "Lebih efisien" dipakai memangkas kehati-hatian | batas keras §4.8: prosedur boleh berubah, aturan/izin tidak; diuji, bukan hanya ditulis |
| Angka token hilang sebagian (provider tanpa usage) | episode tanpa `llm_calls` lengkap dikeluarkan dari perbandingan (bukan dianggap nol) |
| Penilaian tangga terlalu agresif mengusulkan otomasi | paket bukti wajib, approval owner, `proposal_cooldown_days`, dan reversibilitas |
| Hardcode terselubung (daftar signature/tool yang "boleh jadi otomasi") | dilarang eksplisit; test memastikan signature baru dinilai tanpa perubahan kode |
| Belajar diam-diam jadi tak terlihat | semua tindakan tampil di `/status` dan notice operator; metrik §6 diekspos |
| Default nyala dianggap "melakukan sesuatu tanpa izin" | yang menyala adalah **belajar**, bukan bertindak: kiriman/otomasi tetap butuh approval (ADR-0014) |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — pola capture (enqueue di batas, nilai di latar) dipakai apa adanya; tidak ada jalur tulis kedua.
- **ADR-0003** — `distill` adalah `kind` baru di `cognition_jobs`, ikut lease/`SKIP LOCKED`/retry/dead-letter.
- **ADR-0005** — TTL dan renewal prosedur memakai aturan yang sama (dipakai = diperpanjang), pin hanya owner.
- **ADR-0006** — retrieval prosedur tunduk pada ceiling yang sama dengan memori; `global` tidak membuka room lain.
- **ADR-0008** — prosedur adalah data; karantina, `SENSITIVE`, dan larangan menaikkan izin berlaku penuh.
- **ADR-0010** — konsolidasi menggabungkan prosedur duplikat; pass `reflect` adalah tempat §4.8/§4.9 dijalankan (satu jadwal, satu anggaran), tetapi aturannya dimiliki ADR ini.
- **ADR-0013** — `turn_steps` adalah sumber jumlah langkah/kegagalan untuk §4.8.
- **ADR-0014** — usulan dari §4.9 hanya bisa menjadi otomasi lewat jalur ADR-0014: manifest sah, wewenang owner diperiksa dua kali, approval, dan reversibel.
- **ADR-0015** — anggaran payload: prosedur masuk blok referensi, bukan Lapis 1; tidak ada indeks skill.
- **ADR-0016** — pemilik DDL `skills`/`skill_revisions` (§4.7).
- **ADR-0017** — episode ditutup memakai jeda/backlog yang sudah ada; tidak menambah lock atau task baru.
- **ADR-0019** — `llm_calls` (token, `latency_ms`, `turn_id`) adalah bahan baku perbandingan §4.8; tanpa itu pertanyaan efisiensi tidak bisa dijawab.

## 10. Referensi kode

- `eidesoul/engine/stores/skills.py:88` — `index()` (indeks yang akan dihapus dari prompt); `:144-149` — `view()` dan satu-satunya `_record_use`.
- `eidesoul/engine/commands/skill.py` — perintah owner; `eidesoul/engine/tools/skill.py` untuk model sudah dihapus.
- `eidesoul/engine/agent.py:637-640` — tempat indeks skill disusun (titik cutover).
- `eidesoul/engine/config.py:156-157` — `SkillsConfig.max_index_chars` (diganti §5).
- `eidesoul/engine/prompts.py:300,318` — aturan prompt yang menyuruh model mengurus skill sendiri (dihapus).
- `eidesoul/engine/schema.py` — tabel `skills`, `skill_files`, `skill_usage` (bentuk baru di ADR-0016 §4.7); `turns`/`turn_steps` sebagai sumber angka langkah.
- `eidesoul/engine/llm.py:206-255` — loop tool: sumber signature rantai tool per episode (bahan §4.8).
- `eidesoul/engine/embed.py:53-62` — embedder yang dipakai retrieval prosedur.
- `eidesoul/engine/approvals.py` — jalur approval yang dipakai usulan otomasi §4.9.
- `roles.yaml:8-14` — role `member` yang memegang tool `skill` (dicabut).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0020-procedural-memory.md`.*
