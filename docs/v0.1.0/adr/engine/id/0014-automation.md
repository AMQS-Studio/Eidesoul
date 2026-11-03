> Terjemahan turunan — jangan sunting; sumber: engine/0014-automation.md (sha256: 4f870d79ef84)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0014: Automation — Event → Aksi, Wewenang Pemilik, dan Sandbox sebagai Langkah

- **Status:** accepted
- **Implementasi:** partial — ada: `automations`/`automation_runs`/`automation_state_log` (revisi 10), manifest deklaratif + cek otoritas, idempotensi occurrence, quiet hours/budget harian/kill switch, runner langkah (recipe/send/relay/sandbox/…), retry + dead-letter, `tests/test_automation.py`; sisa: sumber event `message`, counter kapabilitas gateway, routing target di gateway bukan pertama
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0003 (worker/outbox), ADR-0006 (privasi grup), ADR-0007 (capability vs authorization), ADR-0008 (integritas memori), ADR-0011 (crash safety), ADR-0012 (lease), ADR-0013 (turn lifecycle)
- **Supersedes:** —
- **Terkait:** ADR-0005 (retention), ADR-0010 (reflection), ADR-0019 (`llm_calls`), ADR-0020 (usulan dari tangga efisiensi)
- **Amandemen:** 2026-11-03 — audit: kolom fencing diseragamkan menjadi `generation` (bukan `lease_generation`); ledger menunjuk ADR-0013 §6 dengan `scope_key = automation:<run_id>`; kolom state/run mengikuti ADR-0016. 2026-11-03 — istilah langkah kirim disatukan: `send_to` dihapus (duplikat dari `send` + `to:`), dan `cross_send` ditegaskan sebagai aturan izin — bukan langkah/tool; `send` dieksekusi engine lewat `gateway.send_text`, tidak lewat `execute`. 2026-11-03 — keputusan pemilik: langkah `sandbox` tidak punya `network`/`timeout` sendiri; memakai `sandbox` host dengan timeout efektif `min(sandbox.timeout_seconds, turns.shutdown.drain_seconds)`. 2026-11-03 — langkah `send` memanggil **tool gateway `send`** yang sama lewat `execute` (tidak ada implementasi/pagar kedua); target tetap divalidasi terhadap manifest. 2026-11-03 — `catchup_minutes` disimpan hanya di `automations.resilience.*` (kunci kembar di akar dihapus). 2026-11-03 — §4.12: permintaan yang menuntut otonomi wajib dijawab dengan usulan automation atau penolakan eksplisit; tidak ada janji memantau tanpa automation, tidak ada pembuatan diam-diam, dan pertanyaan otonomi justru wajib. 2026-11-03 — §4.13: siklus hidup state (`draft/active/paused/stopped/expired`), status run kanonik, retry dua kelas (transient/permanent), dan kondisi dinamis dari komposisi evaluator tertutup; `denied`/`disabled` tidak lagi dipakai sebagai state automation.
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0014-automation.md`.

---

## 1. Konteks

Kebutuhan pengguna tidak berhenti di pengingat waktu. Bentuknya selalu sama — **event, kondisi,
aksi**:

| Ucapan pengguna | Event | Kondisi | Aksi |
|---|---|---|---|
| “nanti jam 11 kamu rekap lagi ya” | jadwal | — | kumpulkan → olah → susun → kirim |
| “kalau si A bilang proyek selesai, kabari saya” | pesan baru | pengirim A + makna “proyek selesai” | kirim ke DM saya |
| “kalau grup ini bahas HB, ringkas, tulis di docs” | pesan baru | makna “HB” | ringkas → tulis ke workspace |

Pengingat jam 11 karena itu **kasus khusus** dari bentuk yang sama: event = jadwal.

**Kenapa bukan “Python penuh integrasi di latar belakang”.** Lima hal hilang sekaligus: ceiling
privasi grup dan presence penanya (ADR-0006/0007) bisa dilewati skrip yang membaca storage sendiri;
guard kita bekerja *per perintah di dalam turn dengan manusia di dekatnya*, sementara latar belakang
tidak punya jendela approval; API internal menjadi permukaan integrasi tak berversi; jaminan
lease/effectively-once/audit hilang; dan biaya bisa lepas kendali. Industri menjawab kebutuhan kode
arbitrer dengan **isolate + host functions** (WASM/Extism, Cloudflare Workers, Shopify Functions),
bukan dengan menjalankan kode pengguna di dalam proses.

**Bahan yang sudah ada dan dipakai ulang:**

| Bahan | Bukti |
|---|---|
| Turn + tool + izin | `agent.py` (jalur turn), `tools/__init__.py:execute` (satu pintu permission) |
| Worker, antrean, retry, dead-letter | ADR-0003 |
| Klaim job `SKIP LOCKED`, outbound ledger | ADR-0011 (klaim/reaper), ADR-0013 §6 (ledger) |
| Lease + fencing (hanya primary yang bertindak) | ADR-0012 |
| Approval | `approvals.py`, `/approve` (ADR-0007) |
| Sandbox terisolasi | `tools/sandbox.py:33-80` — `--read-only`, `--cap-drop ALL`, `no-new-privileges`, `--pids-limit`, memori/CPU dibatasi, `--network cfg.network`, env disaring (`CONTAINER_ENV = TZ, LANG`), hanya workspace di-mount, output dipotong 64 KB |
| Resep bernama | `stores/recipes.py` (`run_recipe`) |
| Ambient tidak pernah menyapa lebih dulu | `ambient.py:1-4` (“it never sends a message to any chat”) |

## 2. Decision drivers

1. **Ekspresif untuk keinginan nyata** — tanpa menuntut pengguna menulis program.
2. **Logika boleh bebas, kemampuan tetap tertutup** — principle of least authority.
3. **Privasi dan izin tidak bisa dilewati** oleh automation.
4. **Wewenang pemilik, diperiksa dua kali** (saat dibuat dan saat jatuh tempo).
5. **Effectively once, bisa diaudit dan diputar ulang**, plus kill switch.
6. **Biaya terkendali** — budget, quiet hours, batas per automation.
7. **Tidak ada jalur eksekusi kedua** — satu worker, satu jalur turn untuk yang butuh kalimat.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Automation deklaratif + langkah bernama** | `event` + `condition` + `steps` (manifest), aksi tertutup, sandbox hanya dengan program bernama | Ekspresif untuk kebutuhan nyata; semua invariant tetap berlaku; murah direview | Butuh bahasa kondisi bounded + tabel baru | **Dipilih** |
| **B. Hook Python terisolasi** | Proses terpisah, JSON masuk/keluar; skrip hanya *mengusulkan*, engine mengeksekusi aksi tervalidasi manifest | Logika paling bebas di antara yang bisa dipertanggungjawabkan | Permukaan lebih besar, butuh kontrak + sandbox proses | Menyusul (4.9) |
| **C. Python penuh, integrasi langsung, latar belakang** | Skrip dalam proses, akses storage + kirim sendiri | Tak terbatas | Melewati privasi/izin; tak ada approval; tak bisa diaudit; jaminan crash/lease hilang | Ditolak |

## 4. Keputusan

> **Automation adalah baris deklaratif milik seseorang: `event` → `condition` → `steps`, dengan daftar langkah tertutup, program sandbox bernama, wewenang pemilik yang diperiksa dua kali, dan eksekusi yang effectively once.**

### 4.1 Model

```yaml
automation:
  id: a-7f3a
  owner: person:xxx              # yang meminta; sumber wewenang
  event: { kind: schedule, at: "11:00", tz: Asia/Jakarta }
  condition: { all: [] }
  steps:
    - { kind: collect, source: chat, chat: <opaque-id>, window: last_24h, limit: 200 }
    - { kind: sandbox, program: scripts/rekap.py, args: { format: markdown }, timeout: 60, network: none }
    - { kind: compose }
    - { kind: send, to: owner_dm }
  policy: { budget_per_day: 4, quiet_hours: "22:00-07:00", on_error: notify_owner }
  state: active                  # active | paused | denied | disabled
```

Nama internal **`automation`** dipilih karena `rule` sudah dipakai untuk `kind=rule` di memori
(ADR-0008); `recipe` sudah dipakai untuk prosedur bernama; `hook` menyiratkan kode pengguna. Kata yang
diucapkan ke pengguna bebas — soul memparafrase lewat LLM.

### 4.2 Event (tertutup, v1)

| Kind | Isi | Catatan |
|---|---|---|
| `schedule` | `at` (HH:MM harian), `every` (durasi), `tz` | Diuji dengan jam DB + jendela catch-up |
| `message` | ruangan ini atau daftar ruangan; filter pengirim | Dari jalur gateway yang sudah ada, bukan polling baru |

Menyusul (bukan v1): `memory_write`, `file_change`, dan adapter eksternal (mis. `google`). Setiap
penambahan adalah kode core yang direview — pengguna tidak bisa menyuntik sumber event baru.

**Berulang: satu occurrence = satu run.** `schedule` menerima daftar waktu, bukan hanya satu:

| Field | Isi | Contoh |
|---|---|---|
| `at` | daftar `HH:MM` lokal (1–24) | `["10:00"]`, `["01:00","07:00","12:00"]` |
| `days` | `daily` / daftar hari / `every:N` | `daily`, `mon,wed`, `every:2` |
| `start` / `until` | batas tanggal | `until: 2026-11-06` |
| `count` | jumlah occurrence | `count: 3` |
| `tz` | zona waktu | `Asia/Jakarta` |

- "jam 10 dalam 3 hari" = `at: ["10:00"], count: 3` (atau `until` tiga hari).
- "jam 1, 7, 12" = `at: ["01:00","07:00","12:00"]` → tiga run per hari.
- "setiap hari jam 10" = `at: ["10:00"], days: daily`, tanpa `until`/`count`.
- Setiap occurrence dijadwalkan sebagai **baris run sendiri**; tidak ada loop di dalam satu run.
- `occurrence_key = hash(automation_id, scheduled_at_utc)` → idempotensi; restart/takeover tidak
  menggandakan, dan dua occurrence yang bertabrakan tetap dua run berbeda.
- Catch-up **per occurrence** (§4.10): masing-masing punya jendela sendiri; di luar jendela → `missed`
  untuk occurrence itu saja, bukan seluruh automation.
- `quiet_hours` dihormati per occurrence: `policy.quiet = skip` (default, dicatat `skipped`) atau
  `defer` (dijalankan sekali di batas akhir quiet hours). Tidak pernah dikirim di tengah quiet.
- Batas authoring: interval minimum 1 menit, maksimum 24 occurrence/hari per automation, dan
  `occurrences_per_day ≤ budget_per_day`. Pelanggaran **ditolak saat authoring**, bukan dipotong diam-diam.
- `until`/`count` habis → `expired` (terminal, §4.13).

### 4.3 Kondisi

- **Bounded dan berschema**, bukan bahasa pemrograman: filter bertipe (pengirim, ruangan, kata kunci,
  rentang waktu), operator `all`/`any`/`not`, penghitung (`count >= n`), dan penanda state
  (`never_notified`, `last_fired_at`). Tidak ada loop, tidak ada fungsi pengguna.
- **Dua tahap seperti pola guard:** lapis deterministik murah dulu (termasuk **cosine MiniLM** untuk
  makna seperti “bahas HB”), LLM kecil hanya untuk yang ambigu.
- **Kriteria bahasa alami opt-in per automation** untuk makna yang sulit distrukturkan (“secara
  negatif”), dengan budget token harian.

### 4.4 Langkah (tertutup)

| Langkah | Siapa yang bekerja | Boleh melakukan |
|---|---|---|
| `collect` | **core** | Membaca ruangan/memori **dengan ceiling privatasi (ADR-0006) dan presence penanya (ADR-0007)**; hasilnya ditulis ke berkas input untuk langkah berikutnya |
| `sandbox` | kontainer terisolasi | Mengolah: **program bernama** + argumen runtime. Tidak punya DB, memori, atau `send`. Jaringan & timeout mengikuti `sandbox` host; timeout efektif = `min(sandbox.timeout_seconds, turns.shutdown.drain_seconds)` sehingga shutdown rapi tidak pernah memotong langkah |
| `web_search` | tool | Pencarian web dalam batas izin pemilik |
| `compose` | soul (LLM) | Menyusun kalimat dengan voice soul, budgeted |
| `send` | **tool gateway** `send` | Satu implementasi untuk semua jalur: `send(path=…, text=…, chat=…)` — `chat` kosong = chat aktif. Langkah ini memanggilnya lewat `execute`; target tetap divalidasi terhadap manifest, dan pagar keanggotaan penanya (ADR-0007 §4.5) berlaku sama seperti dari turn |
| `write` | tool | Menulis di workspace pada root yang diizinkan |
| `remember` | tool | Menulis memori dengan `via=automation` (trust T2, lihat 4.6) |
| `queue_reflection` | worker | Menitipkan ke antrean reflection (ADR-0010) |
| `recipe` | `run_recipe` | Menjalankan prosedur bernama yang sudah direview |

**Syarat sandbox sebagai langkah:** yang dieksekusi hanya **program bernama** (mis.
`scripts/rekap.py`) dengan argumen dari runtime — bukan perintah yang dikarang saat jatuh tempo. Input
disiapkan oleh `collect` (core), jadi kontainer hanya mengolah. Skripnya sendiri ditulis dan disetujui
**di dalam turn** (lihat 4.8), sehingga review terjadi sekali di depan manusia.

### 4.5 Wewenang pemilik (diperiksa dua kali)

| Langkah | Kapabilitas yang harus dipunyai pemilik |
|---|---|
| `collect` | tool baca/`chat_history` **+ presence penanya di ruangan itu** (ADR-0007 §4.5) |
| `sandbox` | tool `sandbox` |
| `web_search` | tool `web_search` |
| `write` | tool `write` + root yang diizinkan untuk role-nya |
| `send` | tool `send` + target ada di manifest; bila `to:` di luar **ruangan penanya** → tolak (ADR-0007 §4.5) |
| `remember` | tool `memory`; scope `global` tambahan butuh `memory_admin` |
| `recipe` | role yang tercantum di resep |

- **Saat dibuat:** ada langkah yang tidak berhak → **ditolak**, tidak disimpan setengah jalan.
- **Sumber usulan:** automation boleh lahir dari usulan tangga efisiensi ADR-0020 §4.9 — LLM menilai
  paket bukti episode berulang, lalu **mengusulkan**; tidak ada konversi otomatis dan tidak ada
  daftar signature yang di-hardcode. Usulan tetap butuh manifest sah dan owner approval di jalur ini.
- **Saat jatuh tempo:** role pemilik dihitung **ulang**; tidak berhak → run ditandai `denied`, tidak
  ada langkah dieksekusi.
- **Saat `denied` pertama:** automation di-`paused` (`capability revoked`) dan pemilik dikabari
  **sekali** — bukan gagal diam-diam tiap hari.
- `owner_roles_at_creation` disimpan untuk diagnosis saja, **bukan** sumber izin (pelajaran dari celah
  `/approve`, ADR-0007 §4.2).
- **Pemilik dihapus** → automation-nya di-`paused`.
- `owner=system` (kerja internal engine) harus ditandai eksplisit dan butuh `memory_admin`; bukan
  jalur default.

### 4.6 Penulisan memori oleh automation

- Selalu `via=automation` → trust **T2** (`soul_inference`), tidak pernah T0.
- Tidak boleh menulis `kind=rule` (ADR-0008 §4.3).
- Tetap terkena **kuota volume pemilik** (ADR-0008 §4.4 A) — automation tidak punya jalur bebas.
- Hasil `collect` tidak otomatis menjadi memori; menulis memori harus langkah `remember` yang eksplisit.

### 4.7 Eksekusi

- Dijalankan **worker yang sudah ada** (ADR-0003) di bawah **lease** (ADR-0012): klaim baris jatuh
  tempo dengan `SKIP LOCKED`, idempotency key `(automation_id, fire_at)`, dan **outbound ledger**
  (ADR-0013 §6, `scope_key = automation:<run_id>`) untuk kiriman → *effectively once*.
- **Tidak ada jalur kirim kedua:** langkah `send` memanggil tool gateway `send` yang sama (`execute`),
  jadi tidak ada implementasi/pagar paralel yang bisa menyimpang. Deployment tanpa gateway → langkah
  ini mengembalikan `error:` yang jelas, bukan diam.
- Jalur eksekusi **ringan**: bukan turn penuh (4–6 panggilan), melainkan langkah-langkah di atas,
  memakai utilitas compose/guard/polish yang sama supaya voice tetap satu.
- **Tidak menyisip** turn yang sedang berjalan di ruangan yang sama (lock turn yang ada).
- **Waktu:** `due_at` disimpan UTC + timezone asal; jendela catch-up (`catchup_minutes`, mis. 15) →
  lewat itu ditandai `missed`, bukan dikirim telat berjam-jam; `quiet_hours`; `budget_per_day`.
- **Kegagalan:** langkah yang gagal menghentikan pipeline (tidak ada kiriman setengah matang),
  run dicatat `failed`, dan `on_error` menentukan kabar ke pemilik.
- **Batas eksekusi:** `run_timeout_seconds` (default 120; batas keras 300) membungkus seluruh run, dan
  `max_steps` (default 10) membatasi **entri `steps` di manifest** — bukan turn percakapan (ADR-0013),
  bukan putaran LLM di dalam satu langkah, dan bukan jumlah target `send`. Pelanggaran ditolak saat
  authoring, bukan dipotong saat berjalan. `condition` tidak dihitung sebagai langkah.
- **Kill switch:** global (`automations.kill_switch`) dan per automation (`state=stopped` untuk permanen,
  `paused` untuk sementara) — `disabled` bukan state automation (§4.13).
- Semua run tercatat di **`automation_runs`** (event, keputusan kondisi, langkah, hasil, waktu) agar
  bisa diputar ulang dan diaudit.

### 4.8 Authoring lewat turn + approval

1. Pengguna menyampaikan keinginan dalam bahasa alami (atau lewat intent prototipe “ingatkan/awasi”).
2. Soul menyusun `event`/`condition`/`steps`; kalau perlu skrip, ia menulisnya di workspace.
3. Isinya ditampilkan dan diminta approval (jendela manusia ada di sini).
4. Setelah disetujui, automation aktif; skrip menjadi artefak bernama yang dirujuk manifest.

### 4.9 Jalur penolakan eksplisit

Kalau permintaan tidak bisa diekspresikan jujur (event source atau aksi tidak ada, atau makna tidak
bisa distrukturkan), soul **menolak dengan jelas** dan mencatatnya sebagai celah kapabilitas —
**tidak** memetakan maksud halus menjadi kata kunci lemah yang nanti salah tembak. Menutup celah
berarti menambah adapter/aksi baru sebagai kode core yang direview.

### 4.10 Ketahanan terhadap mati mendadak & permintaan maaf atas tugas terlewat

Automation adalah **janji**, dan janji tidak boleh hilang karena prosesnya mati. Karena itu seluruh
keadaan jadwal hidup di Postgres (bukan memori proses), dan setiap kehilangan punya jejak.

**State run (durable, di `automation_runs`):** `scheduled → claimed → running → sent | failed |
missed | denied | interrupted`, dengan `fire_at`, `claimed_at`, `attempt`, dan `generation`.

| Kejadian | Perilaku |
|---|---|
| Bot hidup normal | Klaim baris jatuh tempo `SKIP LOCKED`, jalankan, tandai `sent` |
| Bot mati, hidup lagi **dalam** `catchup_minutes` | Jadwal yang terlewat dijalankan **terlambat**, dengan catatan singkat bahwa ini telat |
| Bot mati lebih lama dari jendela | Run ditandai **`missed`** — **tidak** ditembak basi (pengingat 3 hari lalu lebih buruk daripada tidak ada) |
| Proses mati di tengah run | Klaim `stale` dilepas reaper (pola ADR-0011) → dijalankan ulang; kiriman tetap terlindungi idempotency key + outbound ledger, jadi tidak ada kiriman dobel |
| Shutdown rapi | Run yang sedang jalan ditandai `interrupted` bersama notice shutdown/resumed (ADR-0013), supaya boot berikutnya tahu harus melanjutkan atau meminta maaf |
| Instance standby | Tidak pernah menembak (lease, ADR-0012). Saat takeover, primary baru melakukan pemindaian catch-up — jadi replika yang menggantikan bot mati tetap menepati jadwal |

**Permintaan maaf atas tugas terlewat:**

- Setiap run `missed` melahirkan **satu** pesan permintaan maaf kepada **pemiliknya** (target dari
  manifest), lewat jalur yang sama: `compose` lalu `send` — jadi kalimatnya tetap voice soul.
- **Satu pesan untuk satu rentang mati, bukan satu pesan per tugas.** Kalau bot mati 3 hari dan 4
  pengingat terlewat, yang terkirim satu pesan ringkas: rentang waktu mati, daftar tugas yang
  terlewat, lalu tawaran tindakan.
- **Sekali saja:** `apology_sent_at` pada tiap run; tidak pernah diulang.
- **Effectively once** seperti kiriman lain (idempotency key + outbound ledger).
- **Menghormati quiet hours:** permintaan maaf menunggu jendela bangun, tidak dikirim jam 3 pagi.
- **Isi tetap dijaga:** yang disebut hanya tugas pemilik itu sendiri dan kapan ia terlewat. Isi
  ruangan lain tidak ikut (ceiling ADR-0006).
- **Tawaran lanjut** setelah minta maaf: (a) jalankan sekarang, (b) jadwalkan ulang, (c) matikan
  automation. Jawabannya diproses sebagai turn biasa lewat jalur izin yang sama.
- **Batas `max_stale_days`:** di luar itu automation di-`paused` dengan alasan `stale` dan pemiliknya
  tetap dikabari sekali — bukan dibiarkan menembak jadwal yang sudah tidak relevan.

### 4.11 Opsi B menyusul

Kalau logika kondisi benar-benar eksotis, jalurnya adalah hook terisolasi: proses terpisah, JSON
masuk/keluar, dan **engine** yang mengeksekusi aksi tervalidasi manifest. Kontraknya belum dibuat;
sampai itu ada, batasnya adalah bahasa kondisi bounded di 4.3.

### 4.12 Permintaan yang menuntut otonomi: usulkan, jangan janjikan

Kalimat seperti "tolong pantau ini, kabari kalau job-nya selesai" bukan permintaan satu jawaban; ia
meminta **tindakan berulang di luar turn**. Turn tidak boleh berpura-pura memilikinya.

**Tiga respons yang sah, tidak ada yang keempat:**

| Keadaan | Jawaban soul |
|---|---|
| Bisa diekspresikan sebagai automation | "Bisa. Ini rencananya: <event/kondisi/langkah/target/budget>. Setujui?" + id approval |
| Bisa, tetapi penanya tidak berwenang | jelaskan syaratnya (owner/admin) + tawarkan draft; **tidak** membuat |
| Tidak bisa diekspresikan | penolakan eksplisit (4.9): bagian mana yang tidak ada, dan apa yang **bisa** dilakukan sekarang |

- **Dilarang menjanjikan**: "nanti kucek ya", "aku pantau terus", "akan kukabari" tanpa automation yang
  tersimpan dan disetujui.
- **Dilarang membuat diam-diam**: tidak ada automation tanpa approval, meskipun penanya administrator.
- **Pertanyaan ini sah dan wajib.** Ini pengecualian terhadap aturan "jangan bertanya": pertanyaan
  tentang **otonomi** (boleh dibuat otomasi atau tidak) wajib ketika permintaan menuntut perilaku
  berulang. Pertanyaan tentang preferensi/pembelajaran tetap dilarang (ADR-0020 §4.10).
- **Klasifikasi deterministik dulu**: penanda berulang ("berkala", "tiap jam", "cek terus", "kabari
  kalau selesai", "pantau") memicu jalur ini; LLM hanya menilai sisanya yang ambigu.
- **Kondisi harus punya sumber**: keadaan yang dievaluasi harus bisa dibaca deterministik
  (`collect`/tabel/tool) atau NL bounded (4.3). Bila "job selesai" berarti layanan eksternal yang tidak
  bisa diakses (`fetch` menolak SSRF/host internal), jawabannya penolakan + alternatif, bukan polling
  buta.
- **Satu turn, satu keputusan**: usulan manifest ditampilkan dalam bahasa manusia (event, kondisi,
  langkah, target, budget, quiet hours), approval dibuat di turn yang sama (4.8), dan setelah disetujui
  soul menyebut **sekali** bahwa otomasi aktif. Tawaran tidak diulang tiap turn.
- **Target kirim**: room tempat permintaan (atau DM penanya). Kirim ke room lain mengikuti ADR-0007 §4.5
  (`cross_send` + atribusi wajib).

**"Berkala" bukan jadwal.** Kata seperti "berkala", "rutin", "sesekali", "kadang-kadang" tidak punya arti
operasional. Soul **wajib menanyakannya dalam satu pertanyaan pilihan**, bukan menebak:

1. **interval** — 15 menit / 30 menit / tiap jam / 3× sehari / harian;
2. **jendela aktif** — mis. 08:00–22:00, supaya tidak berbunyi tengah malam;
3. **batas** — `count`/`until` atau "sampai kamu bilang stop";
4. **apa yang dihitung sebagai kejadian** — mis. "lampiran baru di grup A sejak run terakhir".

Bila penanya menyerahkan pilihan ("terserah"), soul **menyebutkan** default yang ia pilih
("tiap 30 menit, 08:00–22:00, sampai kamu bilang stop") dan tetap meminta konfirmasi — default tidak
pernah diterapkan diam-diam. Manifest tanpa `at`/`every` yang eksplisit ditolak saat authoring.

**Pertanyaan klarifikasi ditulis soul, bukan katalog.** Engine hanya menyiapkan **slot spec** dalam bentuk
data: daftar interval yang didukung, batas jendela aktif, cara mengakhiri (`count`/`until`/stop),
definisi kejadian, serta fakta yang relevan (bot ada/tidak di room sumber, keanggotaan penanya, budget
tersisa). LLM menyusun pertanyaannya sendiri — memilih urutan, menjelaskan arti tiap opsi, menyebut
konsekuensinya ("tiap 15 menit lebih sering, tapi lebih boros"), dan mengarahkan bila jawaban pengguna
masih kabur. Yang dijaga engine:

- hanya opsi yang ada di slot spec yang boleh ditawarkan (LLM tidak mengarang interval);
- jawaban pengguna dipetakan kembali ke slot spec dan divalidasi sebelum manifest dibuat;
- maksimum **dua putaran klarifikasi**; setelah itu soul menawarkan default eksplisit atau menolak —
  tidak ada pertanyaan ketiga yang berputar-putar;
- tidak ada string pertanyaan di katalog atau kode: teksnya dari LLM, selaras ADR-0007 §4.6;
- penjelasan memakai fakta dari engine, bukan karangan (mis. "aku belum ada di grup itu" hanya boleh
  dikatakan bila memang begitu).

**Sumber berupa room.** "Cek grup A" berarti membaca room, jadi syaratnya dua dan diperiksa dua kali:
penanya harus anggota grup A (`rooms_of`, saat authoring **dan** saat jatuh tempo), dan bot harus ada di
grup itu (gateway). Bila keanggotaan penanya hilang → `paused(authority)` + kabar sekali, bukan membaca
diam-diam. Ini bukan `cross_read`: yang membaca adalah task atas nama anggota, dan hasilnya kembali ke
penanya — room lain tetap tertutup.

### 4.13 Siklus hidup: state, transisi, dan retry

**Satu daftar state (milik ADR ini):**

| State | Arti | Schedulable | Dipindahkan oleh |
|---|---|---|---|
| `draft` | dibuat di turn, belum disetujui | tidak | engine saat authoring |
| `active` | menunggu/menjalankan jadwal | ya | approval / pemilik |
| `paused` | berhenti sementara; `reason`: `user`, `authority`, `stale`, `error_threshold`, `consent_revoked` | tidak | pemilik / engine |
| `stopped` | dihentikan permanen oleh pemilik | tidak | pemilik |
| `expired` | lewat `ends_at`, atau one-shot selesai | tidak | engine |

- `denied` **bukan** state automation; itu hasil run (`automation_runs.status`). `disabled` hanya kill
  switch global (konfigurasi host), bukan state per automation.
- Transisi yang sah: `draft→active` (approval), `active→paused`, `paused→active` (resume; wewenang
  diperiksa **ulang**, bukan memakai snapshot), `active|paused→stopped`, `active|paused→expired`.
- `stopped` dan `expired` **terminal**: menghidupkan lagi berarti membuat automation baru dengan manifest
  baru + approval baru, bukan membalik state.
- Setiap transisi menulis satu baris `automation_state_log` (`from`, `to`, `reason`, `actor`, waktu);
  tidak ada perubahan state tanpa jejak.

**Status run (kanonik):** `queued | running | succeeded | failed | skipped | missed | denied | interrupted | cancelled`.

- `failed` = gagal permanen setelah retry habis; `cancelled` = dihentikan pemilik atau consent dicabut;
  `skipped` = kondisi tidak terpenuhi atau occurrence jatuh di quiet hours dengan `policy.quiet=skip`;
  `missed` = jendela catch-up terlewat (tidak dijalankan basi); `denied` = wewenang tidak sah saat jatuh tempo.

**Retry, dua kelas:**

| Kelas | Contoh | Perlakuan |
|---|---|---|
| `transient` | timeout, 5xx, serialization failure, klaim lease hilang | ulang: `max_attempts` (default 3), backoff eksponensial + jitter, hormati `Retry-After`; habis → `failed` + satu kabar ke pemilik |
| `permanent` | 4xx, guard deny, permission ditolak, manifest tidak sah, consent dicabut | **tidak pernah diulang**; `denied`/`failed` sesuai kelas |

- `missed` tidak di-retry sebagai run baru (data basi); hanya jendela catch-up §4.10.
- Retry tidak boleh mengirim dobel: `scope_key=automation:<run_id>` + `step_index` di ledger (ADR-0013 §6).
- `budget_per_day` menghitung **run**, bukan attempt; `max_attempts` menghitung eksekusi. Keduanya
  berlaku, sehingga retry storm tidak menembus anggaran kirim.

**Kondisi dinamis, tetap tertutup.** Permintaan "jika jam 17 belum selesai", "kalau tiga orang belum
jawab", "jika X dan bukan Y" diekspresikan sebagai **komposisi evaluator** §4.3 (`all`/`any`/`not`,
jendela waktu, penghitung, penanda state, perbandingan). Bahasa alami diklasifikasikan ke bentuk itu;
bila tidak bisa dipetakan → `condition.unexpressible`, dijawab dengan penolakan eksplisit §4.9 +
tawaran aproksimasi yang jujur ("tiap jam 17", bukan "saat selesai"). Tidak ada `eval`, SQL bebas, atau
regex pengguna yang dijalankan tanpa batas; perubahan kondisi = manifest baru (revisi), bukan edit
diam-diam.

## 5. Konfigurasi

```yaml
automations:
  enabled: true
  kill_switch: false
  default_budget_per_day: 4
  run_timeout_seconds: 120               # default per run; batas keras 300
  max_steps: 10                          # batas langkah per manifest
  min_interval_seconds: 60               # batas keras; bukan setelan per automation
  max_occurrences_per_day: 24            # batas keras per automation
  default_quiet_hours: "22:00-07:00"
  default_quiet_policy: skip             # skip | defer (sekali di batas akhir quiet hours)
  max_active_per_person: 10
  sandbox:
    programs_root: workspace              # program harus berada di dalam workspace
    # network & timeout mengikuti `sandbox` host; timeout efektif =
    # min(sandbox.timeout_seconds, turns.shutdown.drain_seconds) supaya shutdown tidak memotong run
  condition:
    nl_criteria: true                      # kriteria bahasa alami opt-in per automation
    nl_budget_tokens_per_day: 20000
  retry:
    max_attempts: 3                        # hanya untuk kelas transient
    backoff_seconds: 30
    max_backoff_seconds: 900
    jitter: true
    respect_retry_after: true
  resilience:
    catchup_minutes: 15                    # lewat ini -> missed, bukan dikirim basi
    max_stale_days: 7                      # lewat ini -> paused (alasan stale) + kabar sekali
    stale_claim_seconds: 300               # klaim mati dilepas reaper
    missed_apology: true                   # minta maaf ke pemilik untuk tugas terlewat
    apology_digest: true                   # satu pesan per rentang mati, bukan per tugas
    apology_respect_quiet_hours: true
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Run `denied` karena wewenang dicabut | Terlihat, dan automation langsung `paused` |
| Kiriman dobel per `(automation, fire_at)` | 0 (diuji) |
| Run `missed` (di luar catch-up) | Terlihat; bahan setel jendela |
| Biaya per automation per hari | Di bawah budget; lonjakan memicu pause |
| Langkah sandbox dengan program di luar workspace | 0 (diuji) |
| Automation tanpa pemilik (`owner=system`) | 0 kecuali ditandai + `memory_admin` (diuji) |
| Memori hasil automation ber-trust T0 | 0 (diuji) |
| Run `missed` yang tidak pernah dimaafkan | 0 (diuji) |
| Permintaan maaf berulang untuk run yang sama | 0 (`apology_sent_at`) |
| Klaim `stale` yang menggantung | 0 setelah reaper berjalan |

## 7. Acceptance criteria

- [ ] Automation disimpan sebagai baris dengan `event`, `condition`, `steps`, `owner`, `policy`, `state`.
- [ ] Setiap langkah diperiksa terhadap wewenang pemilik **saat dibuat**; yang tidak berhak ditolak.
- [ ] Wewenang diperiksa **ulang** saat jatuh tempo; `denied` → `paused` + kabar sekali ke pemilik.
- [ ] `owner_roles_at_creation` tidak pernah dipakai sebagai sumber izin.
- [ ] `sandbox` hanya menjalankan program bernama di dalam workspace, dengan argumen runtime; tidak ada shell bebas.
- [ ] `collect` adalah satu-satunya pembaca ruangan/memori, dan ia menerapkan ceiling ADR-0006 + presence ADR-0007.
- [ ] `send` hanya ke target di manifest; engine yang mengeksekusinya.
- [ ] Memori hasil automation selalu `via=automation` dan ber-trust T2; tidak pernah `kind=rule`.
- [ ] Eksekusi memakai worker + lease + idempotency key + outbound ledger → effectively once.
- [ ] Run tercatat di `automation_runs` dan bisa diputar ulang; kill switch global dan per automation bekerja.
- [ ] `quiet_hours`, `budget_per_day`, dan jendela catch-up ditegakkan; lewat jendela → `missed`.
- [ ] Permintaan yang tak bisa diekspresikan ditolak eksplisit dan dicatat sebagai celah kapabilitas.
- [ ] Jadwal dan definisi automation bertahan setelah proses mati; tick pertama setelah boot melakukan pemindaian catch-up.
- [ ] Terlewat dalam jendela → dijalankan terlambat dengan catatan; di luar jendela → `missed`, tidak ditembak basi.
- [ ] Klaim `stale` dari proses yang mati dilepas reaper dan dijalankan ulang tanpa kiriman dobel.
- [ ] Setiap run `missed` menghasilkan **satu** permintaan maaf ke pemiliknya, lewat `compose` + `send`, dan `apology_sent_at` mencegah pengulangan.
- [ ] Beberapa run terlewat dalam satu rentang mati → satu pesan digest, bukan banjir.
- [ ] Permintaan maaf menghormati quiet hours dan tidak memuat isi ruangan lain.
- [ ] Permintaan maaf menawarkan lanjutan: jalankan sekarang / jadwalkan ulang / matikan automation.
- [ ] Lewat `max_stale_days` → `paused` (alasan `stale`) + kabar sekali.
- [ ] Shutdown rapi menandai run in-flight `interrupted`; boot berikutnya melanjutkan atau meminta maaf.
- [ ] Permintaan "pantau/kabari berkala" → jawaban berisi usulan automation atau penolakan eksplisit; tidak ada janji memantau tanpa automation tersimpan.
- [ ] Tidak ada baris automation tersimpan tanpa approval (test tabel), termasuk ketika penanya administrator.
- [ ] Kondisi tanpa sumber (deterministik atau NL bounded) → penolakan eksplisit + alternatif yang bisa dilakukan sekarang.
- [ ] Penanya tanpa wewenang → draft usulan + syarat wewenang; tidak ada automation dibuat.
- [ ] Usulan ditampilkan dalam bahasa manusia (event, kondisi, langkah, target, budget, quiet hours) dan tidak diulang setelah diputuskan.
- [ ] `stopped`/`expired` terminal; resume dari `paused` memeriksa ulang wewenang; `denied` hanya status run, bukan state automation.
- [ ] Setiap transisi state menulis `automation_state_log` (`from`/`to`/`reason`/`actor`); tidak ada perubahan tanpa jejak.
- [ ] Retry hanya untuk kelas `transient`; `permanent` tidak pernah diulang; attempt habis → `failed` + satu kabar ke pemilik.
- [ ] Retry tidak mengirim dobel (`scope_key=automation:<run_id>` + `step_index`).
- [ ] Kondisi dinamis dikomposisi dari evaluator tertutup; yang tidak bisa dipetakan → penolakan eksplisit + aproksimasi jujur.
- [ ] `budget_per_day` menghitung run dan `max_attempts` menghitung eksekusi; retry storm tidak menembus anggaran kirim.
- [ ] `at` berisi daftar waktu menghasilkan occurrence terpisah per waktu (3 waktu × 3 hari = 9 run), bukan satu run berisi loop.
- [ ] `occurrence_key` mencegah dobel saat restart/takeover; dua occurrence berbeda tetap dua run.
- [ ] `budget_per_day` < jumlah occurrence/hari → ditolak saat authoring, bukan dipotong diam-diam.
- [ ] Catch-up dihitung per occurrence; satu occurrence `missed` tidak membatalkan occurrence lain di hari yang sama.
- [ ] `policy.quiet=skip` mencatat `skipped`; `defer` menjalankan sekali di batas akhir quiet hours.
- [ ] `count`/`until` habis → `expired` (terminal), bukan terus berjalan.
- [ ] "berkala/rutin" tanpa `at`/`every` → soul bertanya dengan pilihan konkret (interval, jendela aktif, batas, definisi kejadian); manifest tanpa jadwal eksplisit ditolak saat authoring.
- [ ] Jawaban "terserah" → soul menyebut default yang dipilih dan meminta konfirmasi; default tidak pernah diterapkan diam-diam.
- [ ] Pertanyaan penjadwalan ditulis LLM (berubah sesuai konteks, tanpa string tetap di katalog/kode); engine hanya memberi slot spec.
- [ ] LLM tidak bisa menawarkan interval/jendela di luar slot spec; opsi asing ditolak sebelum manifest dibuat.
- [ ] Jawaban ambigu → maksimum dua putaran klarifikasi, lalu default eksplisit atau penolakan.
- [ ] Jawaban tidak valid / di luar spec → tidak ada manifest dibuat; soul menjelaskan alasannya dengan fakta dari engine.
- [ ] Sumber berupa room: penanya harus anggota saat authoring **dan** saat jatuh tempo; keanggotaan hilang → `paused(authority)` + kabar sekali, bukan membaca diam-diam.
- [ ] Bot tidak ada di room sumber → penolakan eksplisit + tawaran, bukan polling kosong yang selalu "tidak ada file".

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Automation dipakai melewati privasi | Tidak ada akses storage; `collect` satu-satunya pembaca dan ia menerapkan ceiling + presence |
| Skrip jadi jalur kode arbitrer | Hanya program bernama di dalam workspace; argumen runtime saja; ditulis & disetujui di turn |
| Kiriman dobel saat retry/takeover | Idempotency key + outbound ledger (ADR-0013 §6) + lease (ADR-0012) |
| Loop biaya (setiap pesan grup memicu LLM) | Lapis deterministik + MiniLM dulu; LLM hanya untuk yang ambigu; budget per automation per hari |
| Wewenang dicabut tapi automation tetap jalan | Pemeriksaan ulang saat jatuh tempo + `paused` otomatis |
| Automation menulis memori sendiri tanpa henti | `via=automation`, trust T2, ikut kuota volume pemilik, `remember` harus eksplisit |
| Aturan yang salah diekspresikan | Jalur penolakan eksplisit; tidak ada pemetaan diam-diam ke kondisi lemah |
| Janji tidak ditepati (pengingat tak datang) | Catch-up window + status `missed` + permintaan maaf ke pemilik + `automation_runs` yang bisa ditelusuri |
| Bot mati lama, tugas menumpuk | Satu digest permintaan maaf per rentang mati + tawaran jadwalkan ulang/matikan; lewat `max_stale_days` → `paused` |
| Run macet di `claimed` setelah mati | Reaper melepas klaim `stale` (`stale_claim_seconds`) |
| Permintaan maaf sendiri jadi spam | Digest + `apology_sent_at` + hormat quiet hours |

## 9. Interaksi dengan ADR lain

- **ADR-0002/0003** — automation memakai antrean dan worker yang sama; kondisi dua tahap mengikuti pola gate murah → model mahal.
- **ADR-0005** — automation tidak mengubah retensi; memori yang ditulisnya mengikuti aturan biasa.
- **ADR-0006** — `collect` menegakkan ceiling privasi; ringkasan ruangan → DM hanya karena pemiliknya ada di ruangan itu (ADR-0007 §4.5).
- **ADR-0007** — wewenang pemilik = kelas izin yang sama; automation tidak pernah menaikkan izin, dan `send` hanya boleh ke ruangan penanya (keanggotaan, bukan flag).
- **ADR-0008** — `via=automation` → T2; tidak boleh `kind=rule`; kuota volume tetap berlaku.
- **ADR-0010** — `queue_reflection` adalah jalan automation menitipkan kerja batch; `disputed` tetap di sana — **accepted** (`ADR-0010-reflection-disputed.md`); automation hanya mengantre, tidak menulis hasil dan tidak menyelesaikan dispute.
- **ADR-0011/0012/0013** — klaim `SKIP LOCKED`, idempotency, outbound ledger (`scope_key = automation:<run_id>`), lease/fencing; reaper melepas klaim mati.
- **ADR-0013** — shutdown rapi menandai run `interrupted`; pemindaian catch-up mengikuti pola resume/backlog yang sama; eksekusi automation tidak menyisip turn aktif di ruangan yang sama.
- **ADR-0015** — status automation (“aktif/belum”) muncul di index kapabilitas, jadi soul tidak menjanjikannya keliru; langkah `sandbox`/`recipe` yang jarang dipakai dimuat lewat `info` saat dibutuhkan.

## 10. Referensi kode

- `eidesoul/engine/tools/sandbox.py:33-80` — flag isolasi kontainer, env disaring, output dipotong.
- `eidesoul/engine/tools/__init__.py:180-235` — satu pintu `execute` + pemeriksaan permission.
- `eidesoul/engine/approvals.py` + `eidesoul/engine/commands/approve.py` — approval dan pemeriksaan ulang wewenang.
- `eidesoul/engine/stores/recipes.py` — `run_recipe` sebagai langkah `recipe`.
- `eidesoul/engine/ambient.py:1-4,103-160` — batas otonomi yang ada (tidak pernah menyapa lebih dulu).
- `eidesoul/engine/memory/mem0.py:308-392` — metadata penulisan memori (`source`, `owner_id`, `subject_id`).
- `eidesoul/engine/agent.py:440-460,600-640,900-918` — jalur turn, blok memori, gerbang capture.
- `eidesoul/engine/guard/policy.py:198-215` — pola kebijakan deterministik sebelum model kecil.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0014-automation.md`.*
