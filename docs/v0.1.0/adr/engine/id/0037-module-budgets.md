> Terjemahan turunan — jangan sunting; sumber: engine/0037-module-budgets.md (sha256: 56cfaecfa174)

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** memperluas ADR-0031 (struktur engine, cap modul); mengikuti ADR-0001 (satu port memori), ADR-0013 (siklus turn), ADR-0032 (pintu dispatch)
- **Supersedes:** —
- **Terkait:** ADR-0007 (izin), ADR-0016 (skema), ADR-0024 (input tool), ADR-0035/0036 (keputusan fitur, bukan struktur)
- **Implementasi:** draft — seam dan cap baru diputuskan di sini; `agent.py` (1.800), `jobs/automation.py` (1.971), dan `memory/store.py` (2.376) masih tiga modul terkonsentrasi.
- **Catatan lokasi:** keputusan engine (ia memiliki batas modul dan gate ukuran), jadi duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0037-module-budgets.md`.

---

## 1. Konteks

ADR-0031 menetapkan aturan: satu modul punya satu alasan untuk berubah, tinggal di 800 baris atau
kurang, dan modul stateful di atasnya membutuhkan satu baris di ADR yang memperkenalkannya
(`0031 §4.1`). Ia juga menamai tiga modul terkonsentrasi dengan cap privat dan empat pengecualian
permanen:

| Modul | Baris | Cap | Sisa |
| --- | --- | --- | --- |
| `eidesoul/engine/agent.py` | 1.800 | 1.800 | **0** |
| `eidesoul/engine/memory/store.py` | 2.376 | 2.400 | 24 |
| `eidesoul/engine/jobs/automation.py` | 1.971 | 2.000 | 29 |

Empat modul lain di atas 800 (`config.py`, `stores/affect.py`, `jobs/cognition.py`, `schema.py`)
adalah pengecualian eksplisit ADR-0031 dan tidak termasuk cakupan ini.

Cap ditegakkan oleh `scripts/selfcheck.py`: `LINE_CAPS` memegang tiga plafon privat dan
`OVER_CAP_ALLOWED` memegang empat pengecualian (`selfcheck.py:36-74`). Setiap edit fitur ke
`agent.py` hari ini adalah gate merah; edit berikutnya ke `automation.py` atau `memory/store.py`
akan sama dalam satu atau dua commit. Modul-modul itu tidak melewati cap karena cap-nya salah; mereka
melewati cap karena beberapa alasan untuk berubah masih berbagi satu berkas:

- `agent.py` memegang intake, state machine turn, delivery, dispatch command, notice, checkpoint,
  reload, dan pintu publik (`_run_turn` sendiri `:1005-1640`).
- `automation.py` memegang validasi manifest dan otoritas, store persistensi, dan pipeline worker
  (`:93-599`, `:600-1255`, `:1256-1971`).
- `memory/store.py` memegang write dan kandidat, read, retensi, privacy/rekey, refleksi, dan sengketa
  di balik satu port (`:314-1145`, `:1597-1762`, `:1334-1596`, `:1972-2363`).

ADR-0031 sudah membuktikan bahwa memecah di tempat, satu commit hijau setiap kali, adalah bentuk
yang aman. ADR ini menerapkan aturan itu ke tiga modul terkonsentrasi yang tersisa dan menurunkan
cap mereka, bukan menaikkannya.

## 2. Decision drivers

1. **Cap adalah ratchet.** Plafon yang hanya bergerak naik berhenti menjadi sinyal. Setiap split yang
   selesai menurunkan cap di commit yang sama; tidak ada split yang boleh menaikkannya.
2. **Satu alasan untuk berubah per modul.** Pecah berdasarkan perilaku, bukan lapisan teknis: "modul
   database" yang memegang semua statement tetap satu berkas dengan banyak alasan untuk berubah.
3. **Pemindahan yang mempertahankan perilaku.** Tidak ada perubahan signature publik, langkah turn,
   checkpoint, atau manifest. Suite adalah buktinya, bukan rewrite.
4. **Port tunggal bertahan.** `MemoryStore` tetap satu port tulis (ADR-0001); helper adalah fungsi
   internal, bukan backend kedua atau jalur baca paralel.
5. **Turn tetap terbaca.** State machine boleh pindah, tetapi fase dan nama checkpoint-nya tetap
   identik; turn yang di-resume harus menempuh jalur yang sama.
6. **Tanpa top-level baru.** Modul baru tinggal di `turn/`, `jobs/`, dan `memory/`; cap top-level (16)
   tidak tersentuh.

## 3. Opsi yang dipertimbangkan

**A. Naikkan tiga cap.** Ditolak: itu menghapus satu-satunya tekanan yang menjaga modul agar tidak
tumbuh, dan edit berikutnya membuka pertanyaan yang sama dengan sisa ruang lebih kecil.

**B. Pecah per lapisan teknis (semua SQL, semua delivery, semua validasi).** Ditolak: split lapisan
menciptakan modul dengan banyak pemanggil tak terkait dan tanpa satu alasan untuk berubah; ia
memindahkan masalah, bukan menyelesaikannya.

**C. Pecah di seam dan fase yang sudah ada (dipilih).** Automation sudah punya tiga bagian yang
dipisahkan batas kelas; turn sudah punya fase; port memori sudah mendelegasikan SQL ke
`queries.py`. Setiap ekstraksi mekanis dan hijau secara mandiri.

**D. Tulis ulang tiga modul.** Ditolak: rewrite menyembunyikan perubahan perilaku di dalam
pemindahan dan membuat invarian checkpoint/resume serta port tunggal sulit dibuktikan.

## 4. Keputusan

### 4.1 Aturannya

- Pecah sebelum menambah fitur yang akan melewati cap.
- Satu commit per modul yang diekstrak. Kelas publik tetap fasad; pemanggil tidak mengubah import
  selain ke fasad yang didokumentasikan.
- Tanpa perubahan perilaku: nama langkah, checkpoint, signature tool/command, dan skema tetap sama.
- Turunkan cap di commit yang sama dengan split. Cap sebuah modul tidak boleh naik lagi.
- Split yang tidak dapat mencapai 800 baris tidak diterima diam-diam; ADR diamendemen dengan
  pengecualian bernama dan alasannya, persis seperti yang diwajibkan ADR-0031.
- Modul baru adalah modul biasa: satu alasan untuk berubah, 800 baris atau kurang, tanpa berkas
  top-level baru, tanpa store/port baru.

### 4.2 Automation: tiga modul di batas kelas yang ada

`jobs/automation.py` dihapus dan diganti:

| Modul baru | Isi | Target |
| --- | --- | --- |
| `jobs/automation_manifest.py` | `StepError`/`StepSkipped`, validasi manifest dan kondisi, `describe`, `evaluate_condition`, helper otoritas | ≤800 |
| `jobs/automation_store.py` | `AutomationStore`: persistensi, penjadwalan, transisi status, run, budget | ≤800 |
| `jobs/automation_worker.py` | `AutomationWorker`: loop, pipeline, langkah, delivery, permintaan maaf, notifikasi | ≤800 |

`AutomationStore` dan `AutomationWorker` mempertahankan nama dan metode publiknya. Import di
`jobs/cognition.py`, `jobs/supervisor.py`, command, dan tes pindah di commit yang sama. Entri
`LINE_CAPS` untuk `jobs/automation.py` dihapus; tiga modul baru dicakup gate 800 biasa.

### 4.3 Agent: turn menjadi fasad atas fase

`agent.py` mempertahankan handle publik, pintu dispatch, identitas, lookup body/address, konteks
tool dan command, serta siklus hidup. Fase turn pindah:

| Modul baru | Isi | Target |
| --- | --- | --- |
| `turn/loop.py` | `TurnRunner`: claim/resume, fase keputusan, draft/review, checkpoint, `_keep_typing` | ≤800 |
| `turn/intake.py` | `_identify`, cek privacy-pending, niat forget, intake `_handle_message` | ≤800 |
| `turn/delivery.py` | `deliver_*`, `send_*`, notice, typing; `reply.py` tetap text shaping | ≤800 |
| `turn/commands.py` | `_command`, `command_context`, persiapan dispatch command | ≤800 |
| `turn/steps.py` | recorder/lookup langkah | ≤800 |

`TurnRunner` menerima host sempit (dipenuhi `Agent`) dan tidak pernah mengimpor `agent.py` balik;
arah dependensi adalah `agent.py → turn/loop.py` dan `turn/loop.py → turn/*`, tanpa siklus.
`Agent._run_turn` menjadi delegasi singkat; `Agent.handle` dan `Agent.dispatch` mempertahankan
signature. `turn/loop.py` boleh memakai `turn/assembly.py`, `turn/prompts.py`, dan `turn/reply.py`
seperti sekarang.

Target `agent.py` adalah 800 baris atau kurang. Jika pintu/siklus hidup yang tersisa tidak muat,
fallback-nya adalah satu pengecualian bernama di ADR ini (tidak lebih dari 1.000), bukan kenaikan
diam-diam. Hasil yang diutamakan adalah 800.

### 4.4 Memori: port tetap, alasan untuk berubah pindah

`memory/store.py` mempertahankan `MemoryStore` sebagai satu port dan mendelegasikan ke helper
internal:

| Modul baru | Isi | Target |
| --- | --- | --- |
| `memory/writes.py` | persiapan dan validasi add/write, pemilihan supersede | ≤800 |
| `memory/candidates.py` | intake, capture, transition, label/pending, fail/defer | ≤800 |
| `memory/reads.py` | readable/admin/list/get/context/search/backlog/scopes/counts | ≤800 |
| `memory/maintenance.py` | forget, purge, veto, privacy, rekey | ≤800 |
| `memory/disputes.py` | apply/merge refleksi, kandidat supersede, sengketa, soul rules | ≤800 |

Helper menerima dependensi eksplisit (`conn`, `Database`, policy, guard, index) atau objek
dependensi frozen kecil; mereka tidak pernah membangun `MemoryStore` kedua, membuka pool kedua,
atau menulis `memories` di luar statement yang ada di `memory/queries.py`. `MemoryStore` tetap
satu-satunya objek yang dipanggil engine; read untuk prompt tetap lewat `memories_readable` dan
`memory/queries.py`. ADR-0001 tidak berubah.

Target `memory/store.py` adalah 800 baris atau kurang setelah kelima ekstraksi. Urutannya read,
maintenance, candidates, writes, disputes, sehingga setiap commit kecil dan fasad menyusut monoton.

### 4.5 Cap setelah split

| Modul | Cap sebelum | Cap setelah |
| --- | --- | --- |
| `agent.py` | 1.800 | 800 |
| `jobs/automation.py` | 2.000 | dihapus |
| `memory/store.py` | 2.400 | 800 |
| setiap modul baru di §4.2–§4.4 | — | 800 (gate biasa) |
| `config.py`, `stores/affect.py`, `jobs/cognition.py`, `schema.py` | diizinkan | tidak berubah |

`scripts/selfcheck.py` kehilangan tiga entri `LINE_CAPS`; `MODULE_CAP` biasa adalah satu-satunya gate
ukuran untuk berkas-berkas ini. `TOP_LEVEL_CAP` dan `OVER_CAP_ALLOWED` tidak berubah.

### 4.6 Yang tidak berubah

- Nama langkah turn dan payload checkpoint (ADR-0013).
- Pintu dispatch, address, dan tipe receipt (ADR-0032).
- Port memori tunggal, jalur tulis, dan jalur baca (ADR-0001).
- Skema manifest automation dan error validasinya; hanya modul pemiliknya yang pindah.
- API publik `Agent`, `AutomationStore`, `AutomationWorker`, dan `MemoryStore`.
- Tanpa skema, migrasi, key config, atau dokumen tracked selain pohon ADR dan `AGENTS.md`.

## 5. Konfigurasi

Tidak ada. ADR ini tidak mengubah key config, skema, atau kontrak kabel.

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Metrik | Hari ini | Target |
| --- | --- | --- |
| modul terkonsentrasi ber-cap | 3 | 0 |
| modul di atas 800 baris di luar empat pengecualian ADR-0031 | 3 | 0 |
| entri cap privat di `selfcheck.py` | 3 | 0 |
| berkas top-level engine baru | 0 | 0 |

Gate, per commit split:

1. `pytest -q` (dengan `EIDESOL_TEST_DATABASE_URL`) lolos; tidak ada tes yang mengubah assertion.
2. `ruff check eidesoul scripts tests plugins migrations` bersih.
3. `python -P scripts/types.py --check` melaporkan tidak ada error baru.
4. `python scripts/selfcheck.py --check` lolos dengan cap yang diturunkan.
5. `python scripts/adr.py --check` dan `--translate-check` lolos.
6. `git diff` commit hanya berisi pemindahan import, pemindahan kelas, dan delegasi; tidak ada diff
   logika di body yang dipindah selain prefiks modul dan panggilan fasad.
7. Tes automation dan turn menjalankan turn yang di-resume dan run automation yang di-claim;
   pemindahan kode tidak boleh mengubah urutan langkah/claim.

## 7. Kriteria penerimaan

- `agent.py`, `memory/store.py`, dan `jobs/automation.py` tidak lagi muncul di `LINE_CAPS`; dua yang
  pertama berada di 800 baris atau kurang, dan yang terakhir dihapus.
- Setiap modul baru di §4.2–§4.4 berada di 800 baris atau kurang dan punya satu alasan untuk
  berubah.
- `Agent`, `AutomationStore`, `AutomationWorker`, dan `MemoryStore` mempertahankan signature publik;
  pemanggil mengimpor fasad, bukan helper internal.
- `MemoryStore` tetap satu port; tidak ada store, pool, atau jalur baca kedua.
- Turn yang di-resume dan run automation yang di-claim menempuh jalur yang sama seperti sebelumnya
  (dibuktikan tes).
- Tanpa berkas top-level engine baru, tanpa perubahan skema atau config, dan tanpa dokumen tracked
  baru selain ADR dan pohon `AGENTS.md`.
- Jika modul hasil ekstraksi melewati 800, ADR ini mencatat pengecualian bernama dan alasannya; cap
  tidak naik diam-diam.

## 8. Risiko dan mitigasi

- **Pemindahan menyembunyikan perubahan perilaku.** Mitigasi: pemindahan murni, satu modul per
  commit, dan review diff yang mengabaikan body yang dipindah; tes mencakup resume, claim, dan
  delivery.
- **Import melingkar setelah split turn.** Mitigasi: `turn/loop.py` menerima protokol host sempit
  dan tidak pernah mengimpor `agent.py`; arah dependensi satu arah.
- **Fasad memori menjadi port kedua.** Mitigasi: helper menerima dependensi, bukan `MemoryStore`;
  satu-satunya objek publik tetap `MemoryStore`, dan `memories_readable` tetap SQL baca.
- **Over-splitting modul kecil.** Mitigasi: 800 adalah plafon, bukan target; modul yang jauh di
  bawahnya boleh digabung dengan saudaranya di commit yang sama jika alasan untuk berubah sama.
- **Import churn merusak skrip eksternal.** Mitigasi: pertahankan path kelas publik; hanya import
  helper internal yang pindah.
- **Ratchet cap memblokir perbaikan mendesak.** Mitigasi: perbaikan yang harus mendarat lebih dulu
  boleh di atas cap saat ini dengan catatan di PR/commit; split menyusul segera. Cap tidak pernah
  dinaikkan.

## 9. Interaksi dengan ADR lain

- ADR-0031: aturan, pohon top-level, dan empat pengecualian tetap; ADR ini menghapus tiga cap privat
  dan hanya menambah modul di dalam folder.
- ADR-0001: port tunggal `MemoryStore` dan jalur tulis kanonik tetap; modul memori baru adalah
  helper internal.
- ADR-0013: checkpoint dan semantik resume turn tetap; state machine pindah di balik langkah yang
  sama.
- ADR-0032: pintu dispatch tetap di `Agent`; helper delivery tidak menjadi pintu kedua.
- ADR-0035/0036: skill app dan role menambah kolom/config, bukan modul; keduanya tidak bergantung
  pada split ini.

## 10. Referensi kode

- `scripts/selfcheck.py:36-74` — `LINE_CAPS`, `OVER_CAP_ALLOWED`, `MODULE_CAP`, dan gate ukuran.
- `eidesoul/engine/agent.py:753-818,1005-1640,1646-1707,1774-1800` — delivery, `_run_turn`, dispatch
  command, settings/close.
- `eidesoul/engine/jobs/automation.py:75-599,600-1255,1256-1971` — manifest/otoritas, store, worker.
- `eidesoul/engine/memory/store.py:314-1145,1334-1596,1597-1762,1972-2363` — write/kandidat,
  retensi, read, refleksi/sengketa.
- `eidesoul/engine/memory/queries.py` — statement SQL; helper memanggilnya, bukan menggantikannya.
- `eidesoul/engine/turn/` — folder yang menerima modul turn baru; `reply.py` tetap text shaping,
  `assembly.py`/`prompts.py` mempertahankan perannya.
- `tests/test_turn_lifecycle.py`, `tests/test_automation.py`, `tests/test_memory.py` — suite yang
  membuktikan resume, claim, dan perilaku memori di seluruh pemindahan.
