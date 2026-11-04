> Terjemahan turunan — jangan sunting; sumber: engine/0032-address-and-command.md (sha256: 8b8f5f823612)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0032: Satu Alamat, Satu Command — Keluaran Netral terhadap Body, Menolak daripada Menebak

- **Status:** accepted
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0000 (seam `Percept`/`Action`), ADR-0018 (kontrak gateway), ADR-0014 (routing durabel), ADR-0007 (kelas persetujuan), ADR-0031 (keputusan ini mendarat setelah pemecahan engine)
- **Supersedes:** —
- **Terkait:** ADR-0023 (persetujuan relay), ADR-0025 (prefix prompt stabil), ADR-0027 (gateway adalah body), ADR-0028 (unit plugin)
- **Implementasi:** implemented — satu pintu (`Agent.dispatch`); body mengekspos `dispatch` dan `GatewayCore` tidak mewajibkan anggota chat; receipt mendarat di `outbound_messages` dengan kunci `action.id`; `Address`/`Address.find` me-resolve target; indeks tool per body; body kedua yang mengklaim sebuah thread ditolak sebelum ada baris ditulis. Jalur turn tidak lagi mengutip pesan pemicu: kutipan adalah kerja khusus chat yang boleh ditambahkan body sendiri.
- **Catatan lokasi:** keputusan engine (mendefinisikan bagaimana engine mengalamati body mana pun dan apa yang boleh ia minta), jadi ia duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0032-address-and-command.md`.

---

## 1. Konteks

Seam masuk sudah netral: body menyerahkan `Percept` dan engine mengeluarkan `Action` (ADR-0000). Sisi keluar tidak pernah menyelesaikan gagasan itu.

- `GatewayCore` mewajibkan `send_text`, jadi setiap body harus bisa mengirim teks chat sebelum boleh menjadi body. `render`, chunk, typing, dan stiker adalah kebijakan chat yang duduk di kontrak yang sama.
- Engine membangun chunk dan memanggil body langsung: dua panggilan langsung di `agent.py`, dan lima pemula (turn, `automation`, `privacy`, `relay`, `/kirim`). Ada tiga resolver thread-ke-body (`_gateway_for`, `_thread_gateway`, salinan inline di `ask_memory`), dengan fallback berbeda; `_gateway_for` jatuh ke body pertama yang terdaftar.
- `Action` sudah ada dan `Agent.handle` sudah mengembalikannya, tapi hanya sebagai catatan yang ditulis setelah panggilan transport. Ia belum menjadi jalurnya.
- Registry tool bersifat datar: tool gateway dikumpulkan ke satu daftar saat boot, dan `Registry` menolak nama duplikat. Body kedua dengan `send` atau `chat_history` adalah kegagalan boot hari ini.
- `scope_proof` membawa target, root, dan kelas, tapi tidak membawa body. Persetujuan untuk satu body bisa diputar ulang ke body lain.
- `threads.gateway` membuat rute durabel (ADR-0014), dan `Directory` bersifat lokal body. Pembacaan me-resolve lewat `ctx.gateway`, jadi turn Discord tidak bisa membaca room WhatsApp meski keanggotaan si peminta mengizinkannya (ADR-0007 §4.5).
- Identitas sudah tersinkronisasi: `person_links` memetakan alamat WhatsApp dan alamat Discord ke satu id `per_…`, jadi "siapa kamu" tidak butuh registry baru.

## 2. Decision drivers

1. **Keluaran netral menyelesaikan ADR-0000.** Seam hanya netral kalau engine tidak pernah berasumsi teks.
2. **Satu policy untuk setiap pemanggil.** Turn, command, automation, dan robot harus dinilai fungsi yang sama dari input yang sama: subjek, alamat, origin.
3. **Tanda terima, bukan sinyal senyap.** Ledger kirim sudah membedakan intent, sent, dan unknown; pesan tanpa balasan akan menghapus jaminan itu.
4. **Tanpa state baru.** Rute, presence, dan identitas adalah fakta turunan yang sudah ada. Indeks dibangun saat boot, tidak pernah disimpan.
5. **Fail closed.** Body tak dikenal, kapabilitas tak dikenal, nama ambigu, keanggotaan tak terbukti: satu penolakan, tidak ada yang dieksekusi.
6. **Prompt tetap stabil** (ADR-0025). Kapabilitas body tidak boleh membuat prefix stabil berubah per turn.

## 3. Opsi yang dipertimbangkan

**A. Status quo: metode per body dan registry datar.** Bekerja untuk satu body; body kedua bertabrakan nama, dan engine tetap membawa semantik chat.

**B. Metode per kapabilitas (`perform`, protokol `Chat`).** Tipe lebih rapi, tapi engine tetap mendaftar verb, dan setiap body harus mengimplementasi satu metode per kapabilitas chat meski ia bukan chat.

**C. Command Message plus dispatcher plus balasan (dipilih).** `Action` adalah pesannya; body merutekannya ke handler sendiri berdasarkan verb dan menjawab dengan tanda terima. Engine tidak mengenal satu verb pun.

**D. Event atau sinyal saja.** Kabel paling sederhana, tapi tanpa balasan: ledger tidak bisa menyebut sent atau unknown, dan turn tidak bisa melaporkan kegagalan. Ditolak.

**E. Broker atau IPC antara engine dan body.** Menambah server dan domain kegagalan kedua untuk masalah yang sudah diselesaikan adapter in-process. Ditolak selama semua body berbagi proses (ADR-0027).

## 4. Keputusan

### 4.1 `Address` adalah satu-satunya target

`Address` adalah pasangan: nama body dan id thread, keduanya string, plus bukti yang menetapkan keanggotaan (`gateway`, `recorded`, atau kosong). Ia dibangun dari `threads.gateway`, tidak pernah diurai dari thread. Rute yang tak terselesaikan adalah penolakan, bukan fallback ke body pertama.

### 4.2 Pesan keluar adalah Command Message

`Action` adalah pesannya (ADR-0000 memiliki namanya). Sebuah `Command Message` adalah pesan biasa yang kebetulan membawa perintah (EIP); ia bukan tipe baru. Body mengekspos satu anggota:

`dispatch(action) -> str`

- Body menyimpan **Message Dispatcher**: tabel dari `action.capability` ke handler miliknya (`send_text`, `send_file`, `send_sticker`, dan, untuk robot, `speak` atau `move`).
- Verb tak dikenal ditolak body dengan satu alasan; engine tidak pernah mendaftar verb.
- `GatewayCore` hanya menyimpan anggota generik: `name`, `tools`, `commands`, `run`, `stop`, `dispatch`, `prompt_hints`, `self_handles`, `chat_name`. `send_text` dan `render` pindah ke protokol opsional `Chat`; body chat memilih ikut, robot tidak pernah.
- Pekerjaan khusus chat (chunking, rendering, typing, media, read receipt) tetap di jalur chat dan di `reply.py`; pintunya tidak tahu hal itu ada.

### 4.3 Balasannya adalah tanda terima

Request–Reply (EIP) butuh alamat balasan dan correlation identifier. Engine menyediakan keduanya:

- `Action` membawa `id` yang menjadi correlation id; kunci ledger diturunkan darinya.
- Pintu menulis `intent` di `outbound_messages` sebelum dispatch, lalu mencatat `sent` dengan referensi provider atau `unknown` saat timeout.
- Body adalah **Idempotent Receiver** berkunci id itu; dispatch ulang id yang sama adalah no-op.
- Retry tidak pernah implisit. Ia hanya terjadi lewat publisher durabel (`automation_runs`), dengan id yang sama.
- Aksi robot yang berjalan lama mengikuti bentuk ROS 2 action (goal, feedback, result, cancel) di dalam `Action` yang sama; ia tidak butuh protokol lebih besar.

### 4.4 Satu pintu untuk setiap pemanggil

`dispatch(subject, address, action, *, origin, reason)` adalah satu-satunya fungsi engine yang menjangkau `dispatch` milik body.

- `subject` adalah person id yang bertanggung jawab: pengirim untuk turn atau command, `automation.owner` untuk automation, aktor yang dipetakan untuk robot.
- `origin` adalah `Address` tempat permintaan lahir; itulah yang membuat aksi lintas body bisa dibedakan dari aksi lokal.
- Policy berjalan sebelum kabel: thread yang sama adalah `consent`; thread lain atau body lain adalah `authorization`, dengan `scope_proof` diperluas oleh body target dan origin, dicek ulang saat eksekusi (ADR-0007). `/approve` tidak berubah.
- Atribusi untuk kiriman lintas room dirakit engine dari data, tidak pernah dari teks model (ADR-0007).
- Pemanggil tanpa person id tidak boleh bertindak lintas body; penolakannya menyebut itu. Kepemilikan bukan izin: automation milik orang yang bukan anggota target ditolak saat eksekusi.

### 4.5 Pembacaan memakai alamat yang sama

Membaca adalah gerbang terpisah atas objek yang sama, karena pertanyaannya keanggotaan, bukan pengiriman.

- `policy.read(subject, address, origin)` menanyakan `Directory.participants` milik body **target** lebih dulu; bila body tidak bisa membuktikan keanggotaan (grup WhatsApp selalu menjawab `None`), ia jatuh ke presence tercatat (`rooms_of`); selain itu menolak.
- Batasnya tidak berubah: plafon `visibility` per DM atau grup, `memory.rooms.max_rooms_per_turn`, `max_messages`, dan buffer `ctx.cross_reads` yang menjadi masukan penjaga bocor verbatim.
- `chat_history` tetap tool body (ADR-0000 §4.5) dan menjadi sadar-alamat: `gateway` memilih body, body target membuktikan keanggotaan, dan arsip engine dibaca dengan batas yang sama.
- Tetap tidak ada kapabilitas membaca room yang bukan keanggotaan si peminta; `rooms_of` adalah dasar pembacaan, bukan jalan memutarnya.

### 4.6 Indeks tool bersifat per body

- Tool diindeks sebagai `core: {name}` plus `body: {gateway: {name}}`, dibangun ulang saat boot dari `gateway.tools()`; tidak ada yang dipersistensi.
- Tabrakan hilang karena kuncinya tidak lagi global. Panggilan lintas body memakai nama berkualifikasi (`whatsapp__send`), konvensi yang sudah dipakai MCP (`mcp__<server>__<tool>`), atau nama telanjang yang me-resolve ke body sekarang.
- Izin tetap pada kapabilitas dasar: role memberi `send`, dan body menjadi bagian scope proof, jadi `roles.yaml` tidak pernah butuh prefiks.
- Prompt memuat tool body sekarang plus satu baris yang menyebut body lain yang aktif. Skema diambil saat dibutuhkan lewat `info`; panggilan yang tidak terdaftar dijawab satu penolakan yang menyebut apa yang body itu punya. Ini menjaga prefix stabil tetap byte-identik (ADR-0025).
- Body yang dimatikan tidak ada di indeks, jadi tool-nya tidak tersedia, bukan ditolak (ADR-0027).

### 4.7 Sebuah thread punya tepat satu body pemilik

Rute diklaim sekali, oleh body yang pertama melihat thread itu. Body kedua yang mengklaim id thread yang sama ditolak sebelum ada baris yang ditulis, dengan satu baris log yang menyebut kedua body. Menyimpan pesan dan membalas lewat body yang salah adalah kegagalan senyap yang hendak dihapus ADR ini.

### 4.8 Kegagalan adalah satu string

Body tak dikenal, kapabilitas tak dikenal, thread tak dikenal, tanpa `Directory`, nama ambigu, rute bertabrakan, persetujuan hilang, keanggotaan tak terbukti: masing-masing mengembalikan satu alasan dan tidak mengeksekusi apa pun. Pemanggil boleh menampilkannya; ia tidak pernah mencoba ulang ke body lain atas inisiatif sendiri.

## 5. Konfigurasi

Tanpa kunci baru. Aturan kelas datang dari role dan `approvals`; rute dari `threads.gateway`; batas baca dari `memory.rooms`; daftar gateway dari body yang aktif.

## 6. Metrik dan gate

| Metrik | Hari ini | Target |
|---|---|---|
| Panggilan langsung ke metode kirim body (di luar tes) | 2, keduanya di `agent.py` | 1 (pintunya) |
| Modul engine yang memulai pengiriman | 5 (`agent`, `automation`, `privacy`, `relay`, `/kirim`) | semuanya lewat pintu |
| Resolver thread-ke-body | 3 | 1 |
| Anggota chat yang diwajibkan `GatewayCore` | 2 (`send_text`, `render`) | 0 |
| Tabrakan nama tool antar body | kegagalan boot | mustahil secara konstruksi |
| Kiriman lintas body yang melewati persetujuan | 0 (tanpa jalur) | setiap satunya |
| Body yang terjangkau lewat nama dari body lain | tidak ada | setiap body yang aktif |

Gate: `pytest -q`, `python scripts/adr.py --check`, `python scripts/adr.py --translate-check`, `python scripts/selfcheck.py --check`, tes conformance yang menjalankan skenario yang sama ke setiap body lewat pintu (thread lokal, room lain milik si peminta, room yang bukan miliknya, body tanpa `Directory`, nama ambigu), dan gate grep: `grep -rn "gateway\.send_text(\|gateway\.send_file(" monoagens/engine` hanya cocok dengan modul pintunya.

## 7. Acceptance criteria

- `GatewayCore` tidak mewajibkan anggota chat; body CLI, harness, dan WhatsApp lulus `ensure_core`, dan body robot palsu dengan `dispatch` dan handler `speak` juga lulus.
- Tepat satu modul engine memanggil `dispatch` milik body; `agent.py`, `automation.py`, `privacy.py`, `relay.py`, dan `/kirim` memanggil pintunya.
- `Address.find("grup B", gateway="whatsapp")` mengembalikan satu alamat; nama ambigu atau tidak ada mengembalikan penolakan dan tidak ada yang dikirim.
- Turn Discord bisa membaca atau mengingatkan room WhatsApp bila si peminta anggotanya; tanpa bukti jawabannya `denied: room` dan tidak ada konten yang keluar.
- Matriks skenario yang sama menghasilkan vonis yang sama untuk turn, command, automation, dan robot dengan subjek yang sama.
- Body kedua yang mengklaim thread yang ada ditolak dengan satu baris log, dan tidak ada baris pesan atau turn yang ditulis.
- Ledger menunjukkan `intent` lalu `sent` (atau `unknown`), dan id yang diulang adalah no-op.

## 8. Risiko dan mitigasi

- **Kapabilitas bertipe menjadi string.** Satu dataclass `Action`, satu tabel dispatcher per body, dan penolakan yang menyebut verb yang diterima menjaganya tetap bisa di-debug.
- **Body mulai menegakkan policy.** Tes struktur menjaga `guard` tetap di luar paket gateway; pintu satu-satunya tempat yang memutuskan.
- **Jalur chat diam-diam berasumsi chunk.** Chunking tetap di dalam jalur chat dan `reply.py`; body non-chat tidak pernah melihatnya.
- **Kelelahan persetujuan.** Kiriman ke thread yang sama tetap `consent` tanpa prompt; hanya lintas thread atau lintas body yang bertanya.
- **Ambiguitas nama menjadi terlihat pengguna.** Penolakan menyebut kandidatnya; pertanyaan klarifikasi diizinkan, pemilihan senyap tidak.
- **Indeks tool menyimpang dari registry.** Ia dibangun ulang dari `gateway.tools()` saat boot dan saat reload, tidak pernah disimpan.

## 9. Interaksi dengan ADR lain

ADR-0000 memiliki `Percept`, `Action`, dan penempatan `send`/`chat_history`; ADR ini tidak mengubah nama maupun penempatannya, hanya jalurnya. ADR-0007 memiliki kelas, keanggotaan, dan atribusi, semuanya dipakai ulang di sini. ADR-0014 memiliki rute durabel dan publisher automation. ADR-0018 memiliki protokol body; §4.2 mengubah anggota keluarnya dari `send_text` menjadi `dispatch`, dan itulah perubahan protokol yang diizinkan ADR ini. ADR-0023 menjaga persetujuan relay terpisah: relay adalah alasan pada sebuah kiriman, bukan rute. ADR-0025 melarang daftar kapabilitas per turn di prefix stabil, itulah sebab §4.6 menyembunyikan skema di balik `info`. ADR-0027 menjaga engine bebas dari broker. ADR-0031 mengurutkan pekerjaan ini setelah pemecahan engine.

## 10. Referensi

- G. Hohpe dan B. Woolf, *Command Message*. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/CommandMessage.html> — command adalah pesan biasa yang membawa pemanggilan prosedur.
- G. Hohpe dan B. Woolf, *Request-Reply*. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/RequestReply.html> — alamat balasan dan correlation identifier membuat balasan mungkin.
- C. Richardson, *Transactional Outbox*. <https://microservices.io/patterns/data/transactional-outbox> — tulis intent bersama perubahan state, terbitkan sesudahnya.
- *Idempotent Receiver*, Enterprise Integration Patterns. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html> — id pesan yang diulang tidak boleh dieksekusi dua kali.
- ROS 2, *Actions* (Jazzy). <https://docs.ros.org/en/jazzy/Concepts/Basic/About-Actions.html> — panggilan berjalan lama membawa goal, feedback, result, dan bisa dibatalkan.
- Akka, *Message Delivery Reliability*. <https://doc.akka.io/libraries/akka-core/current/general/message-delivery-reliability.html> — mailbox bersifat at-most-once; jaminan lebih kuat butuh pengakuan di level aplikasi.
- A. Cockburn, *Hexagonal Architecture (Ports and Adapters)*. <https://alistair.cockburn.us/hexagonal-architecture/> — port tetap di engine, setiap body adalah adapter.

## 11. Referensi kode

- `monoagens/engine/inbound.py` — `Percept`, `Action`, `GatewayCore`, `Directory`, `refusal`, `directory_refusal`.
- `monoagens/engine/agent.py` — `send_text`, `_gateway_for`, `ask_memory`, `ask_dispute`; dua panggilan body langsung.
- `monoagens/engine/jobs/automation.py` — `_deliver`, `_gateway`, `_thread_gateway`.
- `monoagens/engine/turn/reply.py` — kebijakan teks chat (chunking, rendering).
- `monoagens/engine/access/privacy.py`, `monoagens/engine/access/relay.py` — pemanggil yang menjadi pemanggil pintu.
- `monoagens/engine/tools/__init__.py` — `rooms_of`, `cross_refusal`, pintu registry datar.
- `monoagens/gateways/whatsapp/tools/chat_history.py`, `.../send_to.py` — tool body yang menjadi sadar-alamat.
- `monoagens/engine/stores/history.py` — `_route`, `gateway_of`, `rooms_of`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/id/0032-address-and-command.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/engine/0032-address-and-command.md`.*
