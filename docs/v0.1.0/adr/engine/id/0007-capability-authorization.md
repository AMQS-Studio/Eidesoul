> Terjemahan turunan — jangan sunting; sumber: engine/0007-capability-authorization.md (sha256: 4c2c36c0a238)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0007: Capability vs Authorization — Tiga Kelas Izin dan Approval sebagai Kelanjutan Izin Tool

- **Status:** accepted
- **Implementasi:** implemented — bukti: `guard/policy.py` (`consent`/`authorization`, `scope_proof`, `recheck_scope`, `credential_read`), kolom `approval_class`/`scope_proof` di `approvals` (revisi 7), cek ulang saat eksekusi di `commands/approve.py`, `memory_admin`, penolakan keanggotaan di `chat_history`/`send_to`, `tests/test_safety.py`; hasil gate: `pytest -q` 753 lulus
- **Tanggal:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical), ADR-0002 (gate capture), ADR-0004 (labeling), ADR-0005 (retention), ADR-0006 (privasi grup)
- **Supersedes:** —
- **Terkait:** ADR-0003 (worker), ADR-0008 (integritas), ADR-0011 (crash safety), ADR-0012 (lease)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0007-capability-authorization.md`.
- **Amandemen:** 2026-11-02 — §4.5 tangga recall: memori sebagai payung, arsip sebagai bukti, presence penanya wajib untuk lintas room (gateway-first, fail closed), ceiling `visibility` hanya untuk pembacaan lintas room; §4.6 penolakan: keputusan deterministik, kalimat sepenuhnya dari LLM (variatif, tidak kaku), tanpa kunci katalog; invariant no-oracle dijaga di sisi data; normalisasi kerangka (ADR-0009 §4.4): `### 4.4 Konfigurasi` dipromosikan menjadi `## 5. Konfigurasi`, subbagian 4.5–4.7 → 4.4–4.6, top-level 5–9 → 6–10. 2026-11-03 — audit: klaim "izin hanya di `execute`" diperbaiki menjadi "satu pintu per jenis kapabilitas" (tool di `execute`, command di `command_allowed`), karena gerbang command sudah ada di `agent.py:961`. 2026-11-03 — keputusan pemilik: `cross_send` didefinisikan di §4.5 sebagai pasangan `cross_read` — peran mana pun yang punya izin kirim boleh mengirim lintas room **hanya bila penanya terbukti ada di room tujuan** (gateway `participants`, fallback `rooms_of`, fail closed) dan target ada di manifest; tidak ada izin global. 2026-11-03 — `cross_send` disamakan bentuknya dengan `cross_read`: flag peran (default `false`) **plus** presence penanya di room tujuan; izin melekat pada peran penanya, bukan pada langkah `send`. 2026-11-03 — satu guard untuk semua jalur kirim (tool gateway `send` dari turn/recipe maupun langkah otomasi): `cross_send` + presence, `trusted`/owner tidak lagi menjadi syarat kirim. 2026-11-03 — **model diganti**: `cross_read`/`cross_send` dihapus sebagai permission. Izin baca/kirim ditentukan **keanggotaan penanya** (`rooms_of`), bukan flag peran; `memory.cross_read.*` → `memory.rooms.*`, `cross_send.max_per_chat_hour` → `send.max_per_chat_hour`; dan isi kiriman dinyatakan tunduk pada ceiling room tujuan (bukan hanya frekuensinya). Alasan: "cross" relatif pada room percakapan, padahal izin seharusnya relatif pada keanggotaan penanya. 2026-11-03 — ADR-0020: `skill` dicabut dari role `member` (menjadi command owner-only). 2026-11-03 — keputusan pemilik: approval dipecah menjadi kelas `consent` (penanya sendiri, bila `scope_proof` terbukti) dan `authorization` (role approver); `sandbox` non-trusted boleh `consent`, `shell`/`settings`/sandbox trusted/kirim lintas room tidak pernah. **2026-11-03** — §4.8 kelas kredensial: membaca rahasia (`env`/`printenv`, `.env`, `/proc/*/environ`, kunci SSH/AWS, `docker inspect`) selalu `authorization`, tidak pernah `consent`, termasuk bila penanya `administrator`; kelas hanya bisa naik, tidak pernah turun.

---

## 1. Konteks

**Recall selalu aktif, capture justru authorization-gated.** `agent.py:619-628` membangun blok
memori tanpa memeriksa role, jadi role tanpa tool `memory` tetap menerima memori (hanya dibatasi
ceiling ADR-0006). Sebaliknya capture mensyaratkan tool `memory` ada di role:
`can_memory = any(spec.name == "memory" for spec in specs)` (`agent.py:614`).

**Consent sudah deterministik dan tersebar di beberapa tempat:**

- `forget`: person hanya oleh pemiliknya (`mem0.py:487-489`), chat harus chat yang sama (`:489`),
  global butuh `allow_global` (`:491`) yang berasal dari `memory_admin` (`tools/memory.py:129`,
  `commands/memori.py:92`).
- `veto`: hanya subjeknya (`mem0.py:527-530`, `tools/memory.py:175-183`).
- `add` scope `global`: `memory_admin` (`tools/memory.py:69-70`, `commands/memori.py:122`).
- `level >= ask_level`: wajib `confirmed=true` (`tools/memory.py:71-73`).
- `supersedes`: harus scope sama dan bukan milik orang lain (`mem0.py:330-338`).

**Approval:** sensitive tools, destructive actions, dan shell mencurigakan jadi `pending`
(`guard/policy.py:198-215`); `/approve` diikat chat+sender+TTL, tidak memanggil LLM
(`commands/approve.py`), dan `approved=True` memang mem-bypass kedua lapisan safety.

**Dua celah:**

1. **`/approve` tidak memverifikasi keanggotaan role saat ini.** `commands/approve.py:41-49`
   mengambil nama role dari payload lalu `agent.config.roles.get(name)` — itu memastikan role
   **ada**, bukan bahwa pengirim **masih** memegangnya. Role yang dicabut setelah approval dibuat
   (dalam TTL 900 detik, `config.py:152`) tetap menjalankan tool dengan permission penuh.
2. **Capture ikut mati** ketika role tidak diberi tool `memory`, padahal capture adalah kapabilitas
   soul, bukan aksi sadar.

## 2. Decision drivers

1. **Soul tetap punya ingatan dan ingatan itu milik orangnya** — capture dan recall tidak boleh
   bergantung pada daftar tool yang kebetulan diberikan.
2. **Aksi sadar tetap bisa dibatasi** — `add`/`forget`/`veto` yang dipanggil model boleh mengikuti
   grant tool.
3. **Consent tidak bisa didelegasikan ke role** — hanya pemilik/subjek yang boleh menentukan
   hidup-matinya memori sendiri.
4. **Tidak ada jalur eskalasi** — approval tidak boleh memberi izin baru.
5. **Satu pintu per jenis kapabilitas** — tool di `tools/__init__.py:execute`, command di `permissions.command_allowed`; tidak ada gerbang ketiga.
6. **Akses room mengikuti keanggotaan penanya**, bukan flag izin: kalau ia anggota room itu, itu ruangannya.
7. **Bisa diaudit** — setiap keputusan izin punya jejak di `ActionLedger`/log.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Semua role-gated** | Capture, recall, forget, veto, export semua dari daftar tool role | Sederhana secara mental model | Soul kehilangan ingatan kalau role salah set; consent jadi hak role, bukan hak orang | Ditolak |
| **B. Semua capability** | Semua jalan, keamanan hanya dari guard/level | Paling “hidup” | `forget`/`veto`/`global` jadi tak terkendali; bocor lintas room | Ditolak |
| **C. Tiga kelas tanpa penegakan approval** | Capability/consent/role dipisah, approval dibiarkan | Cukup untuk operasi normal | Celah role dicabut tetap terbuka | Ditolak |
| **D. Tiga kelas + approval sebagai kelanjutan izin tool** | Capability selalu aktif; consent milik subjek; role/flag untuk global dan export; akses room dari keanggotaan penanya; approval mewarisi izin tool-nya dan dicek ulang saat approve | Model jelas, deterministik, tiap kelas bisa diuji | Perlu `rooms_of` + pengecekan ulang di `/approve` | **Dipilih** |

## 4. Keputusan

> **Ada tiga kelas izin — capability, consent, authorization. Approval bukan command pemberi akses: ia hanya kelanjutan izin tool yang sudah dipanggil, dan izin itu dihitung ulang atas role pengirim saat ini.**

### 4.1 Peta kelas

| Perilaku | Kelas | Penegakan |
|---|---|---|
| Recall memori diri (blok prompt) | **Capability** | Selalu aktif; dibatasi ceiling privasi ADR-0006 |
| Capture otomatis | **Capability** | Selalu aktif; gate struktural ADR-0002 + hard exclusion + `memory.auto_capture` |
| Krisis & hard exclusion | **Capability** | Selalu jalan, tidak bisa dimatikan role |
| `add`/`search`/`list` person+chat lewat tool `memory` | **Authorization** | Grant tool `memory` |
| `forget` memori sendiri (person/chat) | **Consent** | Owner/subjek; tanpa role |
| `veto` memori diri sendiri | **Consent** | Hanya `subject_id` |
| `add`/`search`/`list`/`forget` scope `global` | **Authorization** | `memory_admin` |
| `forget`/`veto` memori orang lain | **Dilarang mutlak** | Bukan consent, bukan role |
| `add` fakta tentang orang lain lewat tool (aksi sadar, mis. `subject=<nama>`) | **Consent** | Boleh disimpan hanya sebagai aksi sadar; wajib `subject_id`; hanya subjek itu yang bisa veto |
| `level >= ask_level` | **Consent** | `confirmed=true` + pertanyaan (di grup → `pending`, ADR-0006) |
| Baca arsip **ruangan penanya** (`chat_history chat=<lain>`) | **Authorization** | Keanggotaan penanya di room itu (`participants` → `rooms_of`, fail closed); tidak ada flag peran |
| Sensitive tools (`shell`, `sandbox`) | **Authorization** | Grant tool + approval |
| Perubahan internal (`settings`) | **Authorization** | Grant tool + approval + audit. **Membaca** (`schema`/`get`/`reload`) bukan aksi sensitif: tanpa approval, tetapi wajib diredaksi (ADR-0015 §4.7) |
| Export memori | **Authorization** | `memory_admin` + audit + L4/kredensial dikecualikan |
| `/approve` | — | **Bukan pemberi izin**; hanya melanjutkan tool yang sudah diizinkan |

### 4.2 Approval = kelanjutan izin tool

- Saat membuat approval: simpan `tool`, `args`, `kind`, chat, sender, TTL, `roles` (snapshot nama
  role sebagai **batas atas**), dan fingerprint (sudah berjalan).
- Saat `/approve`:
  1. Hitung role pengirim **saat ini** untuk chat itu (jalur yang sama dengan turn biasa).
  2. Tool harus **masih allowed** untuk role saat ini; kalau tidak → `denied` + catat di ledger.
  3. Role tersimpan hanya dipakai untuk memastikan approval tidak pernah melampaui konteks saat
     dibuat; ia **bukan** sumber izin.
  4. `kind=settings` mengikuti aturan yang sama: pengirim harus masih boleh memanggil tool
     `settings` (termasuk ketika `approval.settings` true).
- `/approve` tetap tidak memanggil LLM.
- `approved=True` tetap mem-bypass `safety.check` (itu definisi approval), tetapi **tidak** mem-bypass
  permission check tool.
- Approval kedaluwarsa/milik chat atau sender lain tetap ditolak seperti sekarang.

### 4.3 Satu pintu per jenis kapabilitas

- Permission tool tetap hanya diperiksa di `tools/__init__.py:execute` (`allows(ctx.allowed, spec.name)`).
- Permission command diperiksa di `permissions.command_allowed` dari jalur command (`agent.py:961`) — sudah ada, dan tidak menambah jalur untuk tool.
- Penambahan: `chat_history` menolak `chat=` yang **bukan ruangan penanya** (lihat §4.5).
- Tidak ada default allow: role yang tidak mencantumkan `tools`/`commands` tetap ditolak semua
  (sudah).
- Global memory tetap `memory_admin` di kedua pintu (tool dan command).

### 4.4 Export memori

- Wajib `memory_admin`, selalu lewat approval, dan setiap export menulis entri audit.
- L4 dan apa pun yang mengandung kredensial dikecualikan; tombstone tanpa isi tidak diekspor.
- Export belum punya implementasi hari ini (yang ada baru `documents.export_to_files` untuk mirror
  dokumen config, `stores/documents.py:137`). ADR ini menetapkan aturannya; implementasi menyusul.

### 4.5 Tangga recall: memori payung, arsip bukti

**Prinsip:** soul tidak "melintasi" room. Ia bekerja di **ruangan penanya** — himpunan room tempat
orang yang bertanya benar-benar menjadi anggota, ditambah room tempat percakapan ini terjadi.
DM atau grup tidak mengubah izin; itu hanya mengubah ceiling privasi (ADR-0006). Contoh: A anggota
grup PDD lalu bertanya lewat DM soal PDD → PDD adalah ruangannya, bukan "room lain".

| Langkah | Sumber | Kapan |
|---|---|---|
| 1 | Blok memori (canonical + index) | Selalu, tiap turn |
| 2 | Arsip **room ini** (`chat_history`, default) | Wajar dan otomatis saat butuh detail percakapan sekarang |
| 3 | Arsip **ruangan penanya yang lain** (`chat_history chat=<lain>`) | Saat soul memutuskan memori yang dipegang hanya sepotong; cukup keanggotaan, tanpa flag |

**Sinyal “sepotong”.** Tidak ada classifier baru. Blok memori mencantumkan `id`, `scope`, tanggal,
dan level, dan prompt sudah menyuruh soul tidak mengarang: kalau yang diingat hanya serpihan —
mis. hanya tema, tanpa waktu/pelaku/detail — soul membaca ulang room asalnya lewat `chat_history`.
Keputusan itu milik soul (tool call), persis seperti manusia yang ingat sepotong lalu membuka
grupnya lagi. Yang deterministik hanya batasnya:

- maksimum 2 ruangan per turn dan 20 pesan per query;
- hanya room tempat **penanya** benar-benar ada (`rooms_of(requester)`; gateway-first, fail closed);
- room di luar himpunan itu → tool menolak; tidak ada override;
- setiap pembacaan di luar room ini masuk `ctx.cross_reads` dan ledger.

**Keanggotaan penanya, dua lapis, netral di core.** Yang bertanya harus benar-benar ada di room itu;
kalau tidak, tidak ada pembacaan dan tidak ada kiriman. Tidak pernah ditebak dari nama atau alamat:

| Lapis | Sumber | Sifat |
|---|---|---|
| 1 | Kapabilitas gateway `participants(thread_id) -> set[person_id] \| None` (`None` = tidak tahu) | **Otoritatif**: fakta transport, milik gateway. Bila ia menjawab dan penanya tidak ada → **tolak**, apa pun kata lapis 2 |
| 2 | Presence tercatat (`HistoryStore.rooms_of(person_id)`): `messages.sender_id` di chat itu ∪ `memories.owner_id/subject_id` di chat itu | Fallback netral: hanya dipakai saat gateway tidak tahu (mis. console/CLI). Lebih lemah — orang yang sudah keluar grup masih terlihat ada |

- **Fail closed**: presence tidak terbukti dari lapis mana pun → tolak.
- **Tanpa override**: `memory_admin` tidak bisa membaca room yang ia sendiri tidak masuki.
- Seam core: `HistoryStore.recent_chats()` (`stores/history.py:343-352`) memberi daftar `thread_id`,
  dan pola turunan person→room sudah ada di `_chats(db, person_id)` (`memory/rekey.py:91`); yang
  baru hanya `rooms_of(person_id)` sebagai irisan eksplisit untuk penanya.
- `thread_id` tetap opak; **nama** room hanya datang dari `gateway.chat_name()`
  (`gateways/whatsapp/tools/chat_history.py`). Gateway tetap appendage: ia memberi label dan fakta keanggotaan,
  bukan aturan izin. Aturannya tetap di core.

Catatan: scope `person` tidak butuh keanggotaan — memori itu ikut orangnya. Keanggotaan hanya
relevan untuk arsip dan kirim di luar room ini. Tidak ada field baru di `Role`: himpunan itu dihitung
dari fakta gateway/arsip, bukan disimpan sebagai flag peran.

**Ceiling untuk arsip di luar room ini.** `HistoryStore.search` hari ini tidak menyaring
`visibility` (`stores/history.py:318-341`) — filter hanya chat, waktu, dan kata kunci. Itu lubang
yang sama kelasnya dengan yang ditutup ADR-0006, jadi ditutup di sini:

- arsip di luar room ini: `visibility <= (dm_max_level bila DM, group_max_level bila grup)` — L3/L4
  tidak pernah ikut keluar dari room asalnya;
- room sendiri: tidak difilter — soul memang berada di sana, dan balasannya kembali ke room itu;
- guard `cross_chat_leak` (`agent.py:857-861`, `guard/patterns.py:45`) tetap sebagai lapisan
  terakhir terhadap kutipan verbatim.

**Arah kebocoran yang tetap dilarang:** konten yang lahir di satu room tidak menjadi konteks ambient
room lain (ADR-0006). Aturan ini hanya membuka *pembacaan sadar* oleh penanya yang memang anggota
room itu, bukan sapuan otomatis tiap turn.

**Kiriman: default di room, `cross_send` lintas room, dan `cross_read` yang tidak ada.** Tool gateway
`send` menerima `chat`; `chat` kosong berarti room ini. Kelas ditentukan **keanggotaan penanya**, bukan
tekad model. Hanya kasus lintas yang butuh nama, karena hanya ia yang butuh perlakuan khusus:

| Kasus | Syarat | Atribusi (embedded) |
|---|---|---|
| kiriman biasa (`send.class=room`) | target ∈ `rooms_of(requester)` atau room percakapan ini | **opsional** — konteks room sudah jelas |
| `cross_send` (`send.class=cross`) | target **bukan** room penanya (termasuk DM orang lain) | **wajib**, dirender engine |
| `cross_read` | — | **tidak ada kapabilitas ini**: baca lintas room ditolak `rooms_of`, tanpa override |

**Baca lintas room bukan "belum diizinkan", melainkan tidak ada.** `cross_read` adalah invarian privasi,
bukan flag yang bisa dinyalakan: ia yang membuat sistem lolos audit privasi enterprise, karena tidak ada
jalur kode yang bisa membaca room tanpa keanggotaan. Sebaliknya, **`cross_send` tidak memerlukan
`cross_read`**: mengirim tidak memerlukan membaca, asalkan pesan jelas siapa pengirimnya dan kenapa —
dan itu dijamin blok atribusi di bawah.

**Blok atribusi wajib dan tidak boleh dihilangkan model.** Untuk `cross_send`, engine menyusun blok
dari data, bukan dari teks LLM:

- `author` — **wajib, out of the box**, termasuk untuk target DM: id orang kanonik
  (`inbound.person_id`) + nama tampilan. Tidak boleh diisi dari teks model dan tidak boleh diganti nama
  orang lain — percobaan impersonasi ditolak.
- `reason` — **wajib** untuk `cross_send`: alasan yang dikatakan penanya. Tidak ada alasan → kiriman
  ditolak, bukan dikarang.
- `sent_at` dan nama room tujuan — dari engine.
- **field tambahan fleksibel**: penanya boleh meminta field lain ("sebutkan ini soal proyek X"),
  dirender sebagai data di dalam blok yang sama, disanitasi dan dibatasi panjang. Tidak ada daftar
  field yang di-hardcode di kode; engine hanya **mewajibkan** `author` (dan `reason` untuk
  `cross_send`).

LLM menulis **badan** pesan; engine menempelkan blok dan memvalidasi kehadirannya. Bila model lupa,
blok tetap ditambahkan; bila model mencoba memalsukan `author` atau menghapus penanda, kiriman
**gagal** — bukan dikirim tanpa atribusi.

**Batas dan konsekuensi.** `max_per_chat_hour` tetap; setiap kiriman dicatat di `ActionLedger`
(pelaku, tujuan, kelas, alasan). Penanya **tidak menerima balasan** dari room yang bukan ruangnya, dan
soul mengatakan itu apa adanya bila ditanya. Isi kiriman tunduk pada ceiling room tujuan dan guard
`cross_chat_leak`: fakta tentang orang lain di atas ceiling tidak boleh ikut; fakta tentang diri
penanya sendiri boleh. Langkah otomasi `send` (ADR-0014) memakai aturan yang sama, dengan `administrator`
sebagai `author`.

**Isi kiriman tunduk pada ceiling room tujuan.** Guard `cross_chat_leak` diperluas ke keluaran `send`,
bukan hanya balasan turn. Tanpa ini `send` menjadi kanal eksfiltrasi: baca di room 1, publikasikan di
room 2. Fakta tentang diri penanya sendiri tidak dibatasi ceiling — ia boleh menceritakan dirinya.

**Satu guard, semua jalur.** Pemeriksaan kelas dan atribusi dipakai oleh **setiap** kapabilitas kirim:
tool gateway `send` (dari turn maupun dari recipe) dan langkah otomasi `send` (ADR-0014). Tidak ada
pengecualian atribusi — `administrator` pun tidak bisa `cross_send` tanpa `author` + `reason`.

Satu-satunya pesan tanpa blok ini adalah **DM protokol** kepada subjek data tentang **datanya sendiri**
(pertanyaan konfirmasi L3/L4 dan opsi veto, ADR-0006): itu pesan engine, bukan `cross_send` dari
penanya, isinya terbatas, dan satu pesan.

### 4.6 Penolakan: kalimat dari LLM, keputusan dari core

Keputusan izin deterministik; **penyampaian kalimat diserahkan ke LLM** supaya tidak seragam dan
tidak kaku, tanpa mengorbankan invariant.

| | Deterministik (core) | Didelegasikan (LLM) |
|---|---|---|
| Boleh/tidak baca | keanggotaan penanya di room itu + ceiling `visibility`, fail closed | — |
| Informasi yang sampai ke model | Ya: sinyal penolakan **bebas informasi** — tanpa jumlah pesan, tanpa ada/tidaknya isi, tanpa petunjuk topik | — |
| Kalimat yang dikirim ke pengguna | — | Parafrase bebas: persona, bahasa, konteks. Variasi bentuk boleh; **informasi tidak boleh bertambah** |

- **Tool mengembalikan sinyal, bukan kalimat.** Kontraknya mis. `denied: cross-room` tanpa isi
  apa pun. Karena model tidak pernah menerima fakta tentang room itu, ia **tidak bisa** membocorkan
  yang tidak pernah ia lihat. `error:` internal dilarang — itu bocor ke pengguna sebagai teks.
- **Satu aturan prompt.** Kalau sebuah room di luar jangkauan: katakan singkat bahwa kamu tidak bisa
  membantu karena penanya tidak punya akses ke sana. Jangan berspekulasi, jangan mengonfirmasi atau
  menyangkal keberadaan apa pun di sana, jangan menawarkan jalan lain ke isi yang sama.
- **Tanpa oracle tetap berlaku.** Respons tidak membedakan room berisi dari room kosong; bertanya
  berkali-kali dengan kata kunci berbeda tidak menghasilkan informasi baru. Variasi kalimat bukan
  kebocoran — selama fakta yang diberikan ke model identik.
- **Tidak ada kunci katalog khusus.** Penolakan selalu lahir dari LLM, karena LLM-lah yang memanggil
  tool dan menyusun balasan. Degradasi tidak mengubah itu: kunci provider tidak valid, provider mati,
  atau kuota habis tetap berakhir sebagai pesan error di jalur LLM (`agent.api_error`), bukan sebagai
  penolakan tool yang perlu teks deterministik. Command deterministik yang hidup tanpa LLM
  (`/help`, `/memori`, `/status`, `/cari`) tidak ada yang membaca lintas room, jadi tidak ada string
  chat yang perlu ditulis di kode maupun di katalog.
- **Konsisten dengan jalur lain:** permintaan yang sama lewat `memory` (scope `thread` room lain) juga
  ditolak, karena scope `thread` hanya dibaca di room-nya (ADR-0006 §4.4). Satu perilaku, dua pintu.

### 4.7 Approval: sensitivitas menentukan *adanya* approval, target menentukan *kelasnya*

Approval bukan turunan izin tool, dan bukan "sandbox selalu minta izin". Dua pertanyaan dipisah:

1. **Apakah aksinya sensitif?** → menentukan perlu approval atau tidak. Sensitif = destruktif (hapus/timpa/truncate), akses kredensial, ubah konfigurasi, egress jaringan, kirim lintas room, atau pola perintah yang masuk daftar sensitif. Deteksinya deterministik (`guard/policy.py`: `sensitive_tools`, `sensitive_commands`, `DESTRUCTIVE_ACTIONS`).
2. **Di mana targetnya?** → menentukan siapa yang menyetujui.

| Target aksi | Kelas | Penyelesai |
|---|---|---|
| Di dalam scope penanya (`scratch/<chat>`, memorinya sendiri, DM-nya) | `consent` | penanya sendiri (`member` pun boleh) |
| Di luar scope penanya (`public/`, room lain, baris orang lain, konfigurasi host, kredensial) | `authorization` | role approver (administrator) |

Contoh yang menjadi acuan:

- "tolong hapus workspace saya" → **destruktif**, jadi approval ada; seluruh target di `scratch/<chat>` → **`consent` ke penanya**, bukan ke administrator.
- "tambahkan stiker ini ke `public`" → target area bersama → **`authorization` ke administrator**, dengan pesan berbentuk usulan: *"Si ingin menambahkan stiker ini ke public. Setujui?"* + artefak/diff.
- `sandbox` non-trusted tidak bisa menyentuh `public` (mount `ro`), jadi aksi destruktifnya selalu berakhir `consent`.
- `shell` (host, tidak dijail), `settings`, kredensial, dan kirim lintas room selalu `authorization`, karena targetnya tidak pernah terbukti berada di dalam scope penanya.

Syarat `consent` adalah **bukti**, bukan niat baik:

- `scope_proof` dihitung deterministik saat request — daftar target aksi (path, room, kolom) + snapshot roots role penanya — dan **dicocokkan ulang saat eksekusi**; bila role berubah sehingga jangkauan meluas, approval naik ke `authorization` atau ditolak, tidak pernah dieksekusi diam-diam.
- Pesan approval memuat ringkasan target ter-resolve ("12 berkas di `scratch/<chat>`; `public` read-only") dan dikirim **di DM penanya**, bukan di grup; untuk `authorization`, pesan menyebut penanya dan artefaknya ("Si ingin menambahkan…").
- `/approve` untuk `consent` mewajibkan `sender == requester` dan `thread_id` sama; untuk `authorization` butuh role approver. Tidak ada jalur "menyetujui milik orang lain".
- `mode` tetap berlaku: `yolo` (default) tidak memakai approval; `off` mematikan; `strict` memakai kelas di atas, bukan "semua ke administrator".
- Izin tool tetap diperiksa ulang saat eksekusi (kelas approval tidak menambah izin apa pun).

### 4.8 Kredensial: membaca rahasia selalu `authorization`

Kalau targetnya rahasia host, bukan scope penanya, kelasnya **`authorization`** (role approver) —
tidak pernah `consent`, termasuk bila penanya `administrator` dan ruangnya DM sendiri. Yang diserang
bukan jangkauan penanya, melainkan kredensial instance.

| Pola | Contoh |
|---|---|
| Dump environment | `env`, `printenv`, `set` tanpa argumen, `export -p` |
| Berkas rahasia | `.env`, `*.env`, `*.pem`, `*.key`, `~/.aws/credentials`, `~/.ssh/id_*`, `~/.netrc` |
| Proses lain | `/proc/*/environ`, `/proc/*/cmdline` |
| Metadata runtime | `docker inspect`, `docker compose config` (memuat env container) |
| Config host | `settings get` pada kunci sensitif (DSN, API key, path kredensial) |

- ADR-0024 §4.6 sudah menutup kebocoran lewat pewarisan `env` pada `shell`; kelas ini menutup
  pembacaan **berkas**.
- `scope_proof` mencatat pola yang cocok + path target, lalu diperiksa ulang saat eksekusi; perluasan
  scope membuka ulang approval.
- `engine.guard.credential_patterns` hanya **menambah** pola; mencabut pola bawaan butuh ADR baru.
- Kelas hanya bisa naik: pola ini tidak pernah menurunkan kelas aksi yang sudah `authorization`.

## 5. Konfigurasi

```yaml
approval:
  mode: yolo
  consent_max_per_hour: 20      # anti-spam konfirmasi sendiri; kelas diturunkan dari target, bukan dari daftar tool

roles:
  member:
    tools: [read, grep, fetch, web_search, sandbox, chat_history, memory, google, send]
    memory_admin: false
  administrator:
    tools: ["*"]
    memory_admin: true

memory:
  rooms:
    max_rooms_per_turn: 2    # batas deterministik; keputusan membaca tetap milik soul
    max_messages: 20
    require_membership: true # participants → rooms_of → fail closed
    source: gateway_first    # gateway_first | recorded (fallback tercatat)

send:
  max_per_chat_hour: 10      # anti-spam untuk menfess/kirim berulang

guard:
  credential_patterns: []    # tambahan (§4.8); kelas selalu authorization, pola bawaan tidak bisa dicabut
```

Tidak ada field baru di `Role`: keanggotaan dihitung saat dipakai (`rooms_of`), bukan disimpan di
peran. Implementasi menyusul di `chat_history`/`send` dengan satu helper bersama.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| Approval ditolak karena role dicabut | Terlihat di ledger, bukan nol saat ada pencabutan |
| Approval dijalankan dengan role yang sudah tidak memegang tool | 0 (diuji) |
| Capture dari role tanpa tool `memory` | Bukan nol (bukti capability bekerja) |
| Pembacaan room di luar ruangan penanya | 0 (diuji) |
| Export tanpa `memory_admin` | 0 (diuji) |
| Kebocoran verbatim antar-room | 0 (guard `cross_chat_leak` + test) |
| Ruangan dibaca per turn | ≤ 2 (batas deterministik) |
| Baris L3/L4 room lain yang terbaca dari grup | 0 (diuji) |
| Jawaban yang membedakan room berisi vs kosong | 0 (diuji) |
| Penolakan lintas room yang menyebut keberadaan isi | 0 (probe) |
| Spekulasi LLM tentang isi room lain | 0 (eval probe) |
| `chat_history`/`send` ke chat di luar ruangan penanya | 0 (diuji) |
| Kebocoran lewat `send` (fakta pihak ketiga di atas ceiling ke room lain) | 0 (diuji) |

## 7. Acceptance criteria

- [ ] Capture berjalan untuk role mana pun selama gate struktural lolos; grant tool `memory` tidak lagi menentukan.
- [ ] Recall memori diri tetap selalu aktif; tidak ada role yang bisa mematikannya.
- [ ] `/approve` menolak bila tool-nya tidak lagi allowed untuk role pengirim saat ini.
- [ ] `/approve` menolak bila sender/chat tidak cocok atau approval kedaluwarsa (sudah, dipertahankan).
- [ ] `forget`/`veto` memori orang lain tidak punya jalur apa pun, termasuk lewat approval.
- [ ] Fakta tentang orang lain hanya bisa masuk lewat aksi sadar tool `memory` dengan `subject_id`, dan hanya subjeknya yang bisa veto.
- [ ] `chat_history chat=<lain>` ditolak bila room itu di luar **ruangan penanya** (tanpa flag).
- [ ] Teks yang dikirim lewat `send` ke room lain tidak memuat fakta pihak ketiga di atas ceiling room tujuan (guard yang sama dengan balasan turn).
- [ ] `global` tetap butuh `memory_admin` di tool dan command.
- [ ] `level >= ask_level` tetap butuh `confirmed=true`; di grup jadi `pending` (ADR-0006).
- [ ] Export memori butuh `memory_admin` + approval + audit, dan menolak L4/kredensial.
- [ ] `HistoryStore.search` menerapkan ceiling `visibility` saat `thread_id != ctx.thread_id`; room sendiri tidak difilter.
- [ ] Test: baris L4 room lain tidak muncul saat dibaca dari grup, tetapi muncul saat dibaca dari DM.
- [ ] `HistoryStore.rooms_of(person_id)` hanya mengembalikan room tempat orang itu tercatat muncul (irisan `messages.sender_id` dan `memories.owner_id/subject_id`).
- [ ] Tidak ada sapuan room otomatis per turn; arsip di luar room ini hanya lewat tool call soul dan dibatasi `max_rooms_per_turn`.
- [ ] Lintas room ditolak bila penanya tidak ada di room tujuan (gateway bilang tidak → tolak walau presence tercatat setuju).
- [ ] Presence tak terbukti (`None` dari gateway dan tidak ada catatan core) → tolak (fail closed).
- [ ] `memory_admin` juga ditolak saat membaca room yang ia tidak masuki.
- [ ] `rooms_of(person_id)` menghitung presence dari `messages.sender_id` ∪ `memories.owner_id/subject_id` di chat itu.
- [ ] Nama room hanya dari `gateway.chat_name()`; core tidak memanggil API keanggotaan gateway.
- [ ] Tool mengembalikan sinyal `denied: room` tanpa isi; tidak ada `error:` internal yang sampai ke pengguna sebagai teks.
- [ ] Kalimat penolakan disusun LLM (persona + bahasa), dan fakta yang diterima model tidak lebih dari “akses ditolak”.
- [ ] Tidak ada string chat untuk penolakan room di kode maupun katalog; kalimat selalu dari LLM.
- [ ] No-oracle: jawaban tidak membedakan room berisi vs kosong, tidak mengonfirmasi keberadaan isi, dan probe dengan kata kunci berbeda tidak menambah informasi.
- [ ] Tidak ada handler yang punya default permission.
- [ ] `cross_send` ke room yang penanya bukan anggota (termasuk "CP si B") **terkirim dengan** blok atribusi `author` + `reason`; `cross_read` ke room itu tetap ditolak.
- [ ] Model menghapus blok atau memalsukan `author` → kiriman gagal; tidak ada pesan terkirim tanpa atribusi.
- [ ] `cross_send` tanpa `reason` → ditolak; kiriman biasa (`send.class=room`) tanpa atribusi → tetap boleh.
- [ ] Field tambahan yang diminta penanya dirender di dalam blok (data, disanitasi, dibatasi panjang); tidak ada daftar field yang di-hardcode.
- [ ] Relay isi room X ke room Y tetap dibatasi ceiling + `cross_chat_leak`; fakta pihak ketiga di atas ceiling tidak ikut.
- [ ] DM protokol (ADR-0006) tetap satu pesan konfirmasi tentang data subjek, tanpa isi room lain, dan bukan `cross_send` dari penanya.
- [ ] `cross_read` tidak punya handler atau flag: tidak ada jalur kode yang membaca room non-anggota; test akses menolak tanpa override, termasuk untuk administrator.
- [ ] `send.class` tercatat di ledger (`room`/`cross`), sehingga kiriman lintas bisa dihitung tanpa istilah ketiga di bahasa manusia.
- [ ] `env`/`printenv`/`cat .env`/`cat /proc/self/environ` → kelas `authorization`, bukan `consent`, dan tetap begitu bila penanya `administrator`; pola tambahan tidak pernah menurunkan kelas.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Capture selalu aktif menambah biaya | Gate struktural ADR-0002 + rate limit + metrik biaya |
| Role dicabut tapi approval masih hidup | Pengecekan ulang permission saat `/approve` + TTL 900 detik |
| Akses room dianggap remeh | Keanggotaan dihitung saat dipakai (`rooms_of`), fail closed; guard anti-kutipan verbatim; audit `cross_reads` |
| Consent dianggap bisa “dibantu” role | Tabel kelas jadi acuan; test yang menolak jalur role untuk `forget`/`veto` orang lain |
| Export jadi jalur kebocoran | `memory_admin` + approval + audit + pengecualian L4/kredensial |
| Dua pintu untuk global (tool & command) | Keduanya membaca `memory_admin` yang sama; test menguji keduanya |
| Soul terasa “sepotong” dan mengarang | Blok memori membawa `id`/scope/tanggal/level; prompt menyuruh baca ulang room asalnya, bukan menebak |
| Akses room dipakai untuk mengintip room lain | Keanggotaan penanya (gateway-first, fail closed) + ledger; tanpa override admin, tanpa flag untuk ditebus |
| Arsip dipakai sebagai memori kedua | Tangga recall eksplisit: memori payung; arsip hanya bukti; arsip di luar room ini dibatasi 2 ruangan/turn |
| Baris sensitif room lain ikut terbaca | Ceiling `visibility` di luar room ini (setara ADR-0006) + guard `cross_chat_leak` |
| Keberadaan isi room lain bocor lewat bentuk jawaban | Penolakan bebas informasi + test “berisi vs kosong” |
| LLM berspekulasi atau mengarang tentang room lain | Model tidak pernah menerima fakta room itu; prompt melarang spekulasi; eval probe |
| Variasi kalimat dianggap inkonsisten | Variasi bentuk disengaja; yang diukur invarian informasi, bukan keseragaman kata |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical tetap sumber kebenaran; kelas izin hanya menentukan siapa boleh menyentuhnya.
- **ADR-0002** — capture selalu aktif memperkuat gate struktural sebagai satu-satunya penyaring biaya.
- **ADR-0003** — worker tidak memeriksa role; ia mengerjakan antrean.
- **ADR-0004** — `pending`/`level NULL` tidak bisa dibaca siapa pun, termasuk `memory_admin`.
- **ADR-0005** — retention berjalan terlepas dari kelas izin.
- **ADR-0006** — ceiling grup dan capture diri-sendiri berlaku sebelum kelas izin mana pun; §4.5 menerapkan ceiling yang sama pada arsip di luar room ini.
- **ADR-0008** — provenance/trust tidak menaikkan kelas izin.
- **ADR-0011/0012** — approval dan lease sama-sama CAS/klaim; kegagalan tidak boleh memberi izin.
- **ADR-0015** — index kapabilitas menampilkan izin per penanya tanpa penyembunyian; gerbang izin tetap `execute`, dan aktivasi tool bukan pemberi izin.
- **ADR-0023** — meneruskan jawaban pribadi ke pihak lain memakai `relay_grant` (izin pemilik isi), bukan `cross_read` dan bukan `cross_send` biasa.

## 10. Referensi kode

- `eidesoul/engine/agent.py:614-628,857-861` — capture gate, blok memori, guard cross-chat.
- `eidesoul/engine/tools/__init__.py:180-235` — satu pintu `execute` + `allows`.
- `eidesoul/engine/tools/memory.py:64-73,104-119,127-183` — `global`, `confirmed`, `forget`, `veto`.
- `eidesoul/engine/commands/memori.py:41-43,83-92,121-123` — pintu command untuk scope/`memory_admin`.
- `eidesoul/engine/commands/approve.py:20-80` — binding chat/sender/TTL dan re-run lewat `execute`.
- `eidesoul/engine/guard/policy.py:105-215` — `sensitive_tools`, destructive actions, shell scan.
- `eidesoul/engine/memory/mem0.py:476-530` — `forget`, `set_privacy`, `veto`.
- `eidesoul/engine/roles.py:18-28,100-116` — `Role` (tanpa flag room; keanggotaan dihitung saat dipakai).
- `eidesoul/gateways/whatsapp/tools/chat_history.py` — baca room lain + buffer `cross_reads` untuk guard kebocoran.
- `eidesoul/engine/tools/settings.py:294-413` — approval untuk perubahan internal.
- `eidesoul/engine/stores/documents.py:137` — `export_to_files` (mirror dokumen config).
- `eidesoul/engine/stores/history.py:318-352` — `search` (tempat ceiling `visibility` dipasang) dan `recent_chats`.
- `eidesoul/gateways/whatsapp/tools/chat_history.py` — `chat_history` dan pemisahan room ini vs room lain.
- `eidesoul/engine/memory/rekey.py:83-97` — pola `_chats(db, person_id)` untuk `rooms_of(person_id)`.
- `eidesoul/engine/inbound.py:29-70` — kontrak `Gateway` (tempat kapabilitas `participants(thread_id)` ditambahkan, opsional).
- `eidesoul/engine/context.py:72-81,288-300` — `clamp_visibility`, `entry_tag`, `set_visibility`.
- `eidesoul/engine/config.py:148-152` — `ApprovalConfig` (mode, ttl, settings).
- `eidesoul/engine/prompts.py:195-355` — `system_prompt` (tempat aturan “di luar jangkauan → tolak singkat, jangan berspekulasi”).
- `eidesoul/engine/guard/patterns.py:12,14` — `SENSITIVE`/`INJECTION`, bentuk pola yang sudah ada; kelas kredensial §4.8 **belum ada di kode** (usulan, ikut ADR-0024 saat implementasi).
- `eidesoul/engine/config.py:227-232` — `GuardConfig`, tempat `credential_patterns` ditambahkan.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0007-capability-authorization.md`.*
