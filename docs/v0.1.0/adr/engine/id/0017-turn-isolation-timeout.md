> Terjemahan turunan — jangan sunting; sumber: engine/0017-turn-isolation-timeout.md (sha256: 0bed5d2bbe0c)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0017: Isolasi Turn, Batas Waktu, dan Gerbang Primary

- **Status:** accepted
- **Implementasi:** implemented — bukti: lock LRU per thread di `agent.py`, deadline turn + `min_call_seconds`, executor `monoagens/engine/io.py`, `_send_gate` (mode + generation), `tests/test_turn_lifecycle.py`, aturan async-DB di `tests/test_architecture.py`; hasil gate: `pytest -q` 604 lulus setelah wave ini
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P0-4 — `turn_lock` global, DB/embedder sinkron memblokir event loop yang sama dengan heartbeat lease, dan `agent.mode` tidak pernah dibaca sehingga instance standby masih bisa mengirim
- **Supersedes:** —
- **Terkait:** ADR-0011 (shutdown & ledger), ADR-0012 (lease, `generation`), ADR-0013 (`turns`, `notice`), ADR-0003 (worker & executor), ADR-0019 (`turn_id` di log)
- **Amandemen:** 2026-11-03 — pertanyaan pemilik: "kenapa bukan LLM yang mendaftarkan timeout/budget?" → batas waktu dipecah **dua lapis** (§4.3): timeout per request + retry eksplisit (jalur turn) dan `deadline` turn yang diteruskan sebagai `remaining_seconds`; `run_budget_seconds: 240` dinyatakan **tidak tercapai** di jalur turn dan di-rebase ke pekerjaan latar. **2026-11-03** — §4.6 retry per jalur: `turn_attempts: 1` (hanya HTTP call; tool yang sudah dieksekusi tidak diulang), `worker_attempts: 3` dengan backoff + jitter, `Retry-After` dihormati dengan plafon; kunci `llm.max_retries: 0` diganti blok `llm.retry.*`. **2026-11-05** — default deadline turn menjadi 300 detik, bukan 90: timeout per panggilan 120 detik dan satu ronde tool butuh panggilan model kedua, sehingga watchdog 90 detik memotong turn dua panggilan yang valid (latensi provider yang teramati: 62 detik per panggilan) sebelum model bisa menjawab.
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0017-turn-isolation-timeout.md`.

---

## 1. Konteks

Empat fakta kode, semuanya saling memperkuat:

| Fakta | Bukti |
|---|---|
| Satu lock untuk semua chat | `agent.py:143` `self.turn_lock = asyncio.Lock()`, dipakai `:448` untuk setiap turn non-command |
| DB sinkron di dalam `async def` | `db.py:53` `create_engine(...)`, `db.py:95` `connect()` sinkron; satu `to_thread` pun tidak ada di `monoagens/engine/` |
| Embedder sinkron | `embed.py:53-62` `SentenceTransformer.encode`, dipanggil `knowledge.py:147`, `patterns.py:235`, dan jalur mem0 |
| Standby tidak digerbang | `supervisor.py:62,111` menulis `agent.mode`; tidak ada satu pun pembacaan `self.mode` sebelum `send_text`/`send_file` (`agent.py:311,335`) |
| Batas waktu LLM **restart per panggilan** | `llm.py:126` `timeout=120` per request; `llm.py:165-169` `run_budget_seconds: 240` dihitung dari `start` **di dalam** `run()`; `max_retries` tidak diset (default library 2) |

Konsekuensinya bukan teoretis:

1. **Satu chat menahan semua chat.** Turn lambat di DM menahan turn ruangan lain, karena lock tidak per chat.
2. **Heartbeat berbagi loop dengan pekerjaan sinkron.** Blokir 1-2 detik pada query/encode menunda `Lease.renew()`; ADR-0012 memperlakukan telat renew sebagai kegagalan, jadi blokir kecil bisa berubah menjadi takeover dan dua instance aktif.
3. **Tidak ada batas kerja per turn.** Satu panggilan LLM yang menggantung menahan lock sampai `run_budget_seconds` (240 s) habis — dan karena satu turn memanggil `run()` lebih dari sekali (decision → reply → polish), batas efektifnya berlipat; dengan retry bawaan library (2) satu request bisa memakan 3×120 s.
4. **Demotion tidak menghentikan kiriman.** `/freeze` dan takeover menurunkan `mode`, tetapi jalur kirim tidak memeriksanya — fencing ADR-0012 jadi formalitas.

## 2. Decision drivers

- Heartbeat lease tidak boleh bergantung pada lamanya turn mana pun (invarian keselamatan, ADR-0012).
- Chat yang berbeda tidak boleh saling menunggu.
- Setiap turn harus punya akhir yang terdefinisi (selesai, gagal, atau `interrupted`), bukan menggantung.
- Batas harus **menjumlah** sepanjang turn, bukan restart di setiap panggilan.
- Tidak boleh ada jalur eksekusi kedua: mem0 tetap sinkron, sehingga "async penuh" akan memaksa dua jalur yang harus dijaga selamanya.

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Async penuh** | `create_async_engine` + semua store async | besar | mem0 tetap sinkron → jalur ganda; migrasi menyentuh setiap handler | Ditolak |
| **B. Offload di seam** | `to_thread` di `Database.connect`, `embed.encode`, wrapper mem0, satu executor berbatas | sedang | mudah dilupakan kode baru | **Dipilih** |
| **C. Lock per chat + batas waktu + gerbang `mode`** | murni kontrol alur, tanpa menyentuh I/O | kecil | tidak menghilangkan blokir loop | **Dipilih bersama B** |
| **D. Biarkan tiap panggilan LLM mendaftarkan budget sendiri** | hanya `timeout_seconds`/`run_budget_seconds` di lapisan LLM | nol | anggaran restart per `run()` → N×240 s per turn; retry tersembunyi melipatgandakan latensi | Ditolak sebagai satu-satunya lapis (dipakai sebagai lapis 1) |
| **E. Biarkan** | — | nol | risiko lease dan dua instance aktif | Ditolak |

## 4. Keputusan

### 4.1 Offload di seam, bukan di setiap handler

- Satu executor berbatas (`engine.io.workers`, default 8) dipakai bersama oleh `Database.connect` (seluruh blok query dalam satu panggilan kerja), `embed.encode`, dan panggilan mem0 yang sinkron.
- **Aturan keras:** `with db.connect()` tidak boleh muncul di dalam `async def` di `monoagens/engine/`; hanya boleh di dalam helper yang di-offload. Ditegakkan test AST (ADR-0019 menyediakan infrastruktur check yang sama dengan `topology`).
- Handler yang sudah async (LLM, sandbox, MCP) tidak diubah.

### 4.2 Lock per chat, bukan per proses

- `chat_locks: dict[str, asyncio.Lock]` dengan `max_chat_locks` (default 256, LRU): lock dibuat saat dipakai, dilepas saat tidak ada penunggu, sehingga tidak bocor pada ribuan chat.
- Command tetap di luar lock (perilaku sekarang dipertahankan: `nullcontext()`).
- Dua turn di chat yang sama tetap berurutan (satu percakapan = satu urutan); dua turn di chat berbeda berjalan paralel.

### 4.3 Batas waktu dua lapis: deadline turn yang diteruskan + timeout per panggilan

**Lapis 1 — per panggilan (cepat gagal, atribusi jelas):**
- `engine.llm.timeout_seconds` (sudah ada, 120) berlaku per HTTP request.
- `engine.llm.max_retries: 0` di jalur turn: 5xx/429 tidak diulang diam-diam, karena retry bawaan library (2) melipatgandakan latensi yang dilihat pengguna. Retry hanya untuk pekerjaan latar (digest/reflect) yang tidak menahan lock percakapan.
- `engine.llm.run_budget_seconds` **tidak lagi menjadi batas turn**: ia hanya diperiksa bila tidak ada deadline yang diteruskan (pekerjaan latar), sedangkan jalur turn selalu meneruskan deadline. Ia di-rebase menjadi anggaran pekerjaan latar; jalur turn memakai sisa deadline.

**Lapis 2 — per turn (yang menjumlah):**
- Di awal `handle()` dibuat satu `deadline = monotonic() + engine.turn.timeout_seconds` (default 300) dan **diteruskan** ke setiap panggilan sebagai `remaining_seconds`.
- Timeout efektif per panggilan = `min(llm.timeout_seconds, remaining_seconds)`.
- Bila `remaining_seconds < engine.turn.min_call_seconds` (default 5), **tidak ada panggilan LLM baru**: soul menjawab dari yang sudah ada (atau mengakhiri turn dengan `interrupted`), bukan memulai panggilan yang pasti terpotong.
- `asyncio.wait_for(self.handle(...), timeout)` tetap dipasang sebagai **watchdog**: banyak `await` bukan LLM (DB, mem0, tool, kirim) dan hanya watchdog yang bisa membatalkan yang menggantung di sana.

**Saat habis:** cancel task, `turns.status='interrupted'` (enum ADR-0013), satu `notice:timeout` per chat per turn dari katalog (tidak ada kalimat di kode), dan ledger `outbound_messages` tetap mencatat kiriman yang sudah terlanjur terjadi. Timeout **bukan** `failed`: turn yang sama tidak diulang otomatis; pesan berikutnya memakai backlog seperti biasa.

### 4.4 Gerbang primary di jalur kirim

- Satu helper `_send_gate()` dipanggil sebelum setiap `send_text`/`send_file`: `mode == 'primary'` **dan** `generation` cocok dengan `engine_lease`.
- Gagal: `StandbyError` internal, **diam** ke pengguna (tidak ada pesan "aku standby" — itu kebocoran topologi), dicatat sebagai counter `standby_refused` (ADR-0019).
- `supervisor` menurunkan `mode` **sebelum** melepaskan lease (urutan: gate turun → release), sehingga jendela kiriman setelah release nol.

### 4.5 Shutdown mematuhi drain

- `asyncio.wait_for(asyncio.gather(*tasks), drain_seconds)`; yang belum selesai di-cancel dan ditandai `interrupted` (ADR-0013 §12, angka 25/15/5 milik ADR-0011 — tidak diduplikasi di sini).
- Lease dipertahankan sampai fase `draining` selesai agar reaper instance lain tidak mengambil alih di tengah drain.

### 4.6 Retry per jalur

| Jalur | Percobaan ulang | Perilaku |
|---|---|---|
| Turn (pengguna menunggu) | `turn_attempts: 1` | Hanya panggilan HTTP model yang idempoten; 429/5xx; `Retry-After` ditunggu hanya bila ≤ `min(retry_after_max_seconds, remaining − min_call_seconds)`, kalau tidak → gagal terlihat (`agent.api_error`). Tool yang sudah dieksekusi pada iterasi itu **tidak** diulang |
| Pekerja latar (cognition/ambient/`distill`) | `worker_attempts: 3` | Backoff eksponensial `base_seconds × 2^n` dengan plafon `cap_seconds` + jitter ±`jitter`; `Retry-After` dihormati; habis → job `failed` dengan alasan yang terlihat di `/status` |
| Run otomasi | ikut ADR-0014 §4.7 | Tidak diduplikasi di sini — aturan retry milik otomasi |

- Tidak ada retry untuk 4xx selain 429. Pengecualian lama "buang gambar lalu ulang sekali" tetap ada
  dan **bukan** retry umum (`monoagens/engine/llm.py:195-198`).
- Setiap percobaan menulis baris `llm_calls` sendiri (ADR-0019), jadi retry tidak pernah tersembunyi
  dari hitungan biaya.
- Retry tidak pernah melewati `turn.timeout_seconds`: percobaan yang tidak muat tidak dimulai.

## 5. Konfigurasi

```yaml
engine:
  turn:
    timeout_seconds: 300       # batas satu turn (deadline, menjumlah); habis -> interrupted + notice:timeout
    min_call_seconds: 5        # sisa di bawah ini -> jangan mulai panggilan LLM baru
  llm:
    timeout_seconds: 120       # sudah ada: batas per HTTP request
    retry:                     # §4.6 — turn: maksimum satu ulang untuk HTTP call; tool yang sudah jalan tidak diulang
      turn_attempts: 1
      worker_attempts: 3       # pekerja latar: backoff eksponensial + jitter, `Retry-After` dihormati
      base_seconds: 2
      cap_seconds: 60
      jitter: 0.2
      retry_after_max_seconds: 10
  io:
    workers: 8                 # executor bersama untuk DB, embed, dan jalur mem0 sinkron
  concurrency:
    max_chat_locks: 256        # LRU; lock dilepas saat tak ada penunggu
```

Tidak ada kunci baru untuk shutdown: `grace_seconds`/`drain_seconds`/`notice_seconds` tetap milik ADR-0011/ADR-0013.
`run_budget_seconds` tetap ada untuk pekerjaan latar; ia tidak lagi dihitung sebagai batas turn.

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `loop_lag_ms` | penundaan `asyncio.sleep(0)` terhadap jadwal | p95 < 100 ms saat smoke 2 chat paralel |
| `turn_timeout_total` | turn yang berakhir `interrupted` karena deadline | 0 pada smoke normal, naik saat gangguan LLM |
| `standby_refused_total` | kiriman yang ditolak gerbang | > 0 pada test demotion; 0 pada operasi normal |
| `chat_wait_ms` | waktu menunggu lock per chat | tidak boleh dijumlahkan antar chat |
| `heartbeat_delay_ms` | jarak `renew` terjadwal vs nyata | < 25% interval TTL (ADR-0012) |
| `llm_calls_per_turn` | jumlah panggilan LLM dalam satu turn | dilaporkan; lonjakan = tanda pemadatan gagal |
| `remaining_at_call_ms` | sisa deadline saat panggilan dimulai | tidak pernah ≤ 0 (panggilan tidak dimulai) |

## 7. Acceptance criteria

- [ ] Dua `handle()` di chat berbeda berjalan paralel; yang lambat tidak menahan yang lain (test dengan delay 2 s).
- [ ] Query lambat yang diinjeksi tidak menunda `renew()` melewati ambang ADR-0012 (test loop lag + heartbeat).
- [ ] Timeout 90 s → status `interrupted`, tepat satu `notice:timeout` per chat, tidak ada kiriman kedua dari turn itu.
- [ ] Deadline **menjumlah**: 3 panggilan `run()` dalam satu turn tidak pernah melebihi `turn.timeout_seconds` (test dengan provider lambat palsu).
- [ ] Sisa deadline < `min_call_seconds` → tidak ada panggilan LLM baru; turn menjawab dari yang ada atau `interrupted`.
- [ ] `max_retries: 0`: respons 500/429 tidak diulang di jalur turn (test menghitung request).
- [ ] Instance dengan `mode='standby'` menolak `send_text`/`send_file` tanpa mengirim apa pun (test), dan `generation` basi juga ditolak.
- [ ] Tidak ada `with db.connect()` di dalam `async def` (test AST, hanya helper offload yang boleh).
- [ ] `embed.encode` dan jalur mem0 sinkron berjalan di executor; loop lag p95 < 100 ms selama capture batch.
- [ ] Shutdown: task yang melewati `drain_seconds` di-cancel dan ditandai `interrupted`, bukan menggantung.
- [ ] 429 dengan `Retry-After: 30` di jalur turn tidak ditunggu: turn gagal terlihat, bukan menggantung; `Retry-After: 3` diulang sekali dan setiap percobaan menulis baris `llm_calls` sendiri.
- [ ] Percobaan ulang panggilan model tidak menjalankan ulang tool yang sudah dieksekusi pada iterasi itu (test dengan spy).

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Executor jenuh → turn antre di thread | `workers` eksplisit + timeout kerja memetakan ke error terklasifikasi, bukan menggantung; metrik `io_queue_wait_ms` |
| Cancellation di tengah tulis DB | Satu blok offload = satu transaksi; cancel terjadi antar-query, tidak di tengah commit |
| Lock per chat bocor pada banyak chat | LRU `max_chat_locks`; lock tanpa penunggu langsung dilepas |
| Gerbang kirim bocor karena jalur baru | Satu helper `_send_gate()`; test memastikan semua pemanggilan `send_*` lewat helper |
| `mode` basi dibanding lease sebenarnya | Gerbang membandingkan `generation`, bukan hanya `mode`; `mode` hanya optimasi cepat |
| Turn terpotong saat draft sudah bagus | `min_call_seconds` + jawaban dari bagian yang sudah ada; `interrupted` tidak diulang otomatis |
| Retry dimatikan lalu provider sesaat gagal | Retry tetap ada di pekerjaan latar; di jalur turn, kegagalan cepat lebih baik daripada latensi tak terbatas |

## 9. Interaksi dengan ADR lain

- **ADR-0012** — `generation` dan `mode` adalah dua sisi satu keputusan: lease menentukan siapa primer, ADR ini memastikan hanya primer yang berbicara.
- **ADR-0013** — status `interrupted` dan `notice` sudah didefinisikan; ADR ini hanya memberi pemicu deadline dan shutdown.
- **ADR-0011** — angka drain/grace tidak diduplikasi; urutan fase shutdown tetap milik ADR-0011.
- **ADR-0003** — worker kognisi memakai executor yang sama dan tetap memakai retry (di luar jalur turn).
- **ADR-0019** — `turn_id`, `llm_calls_per_turn`, dan counter `standby_refused`/`turn_timeout_total` didefinisikan di sana.

## 10. Referensi kode

- `monoagens/engine/agent.py:143,413,448,1066` — lock global, `handle()`, titik tunggu lock, gather shutdown.
- `monoagens/engine/db.py:53,95` — `create_engine` dan `connect()` sinkron (seam offload).
- `monoagens/engine/embed.py:53-62` — `encode` sinkron.
- `monoagens/engine/jobs/supervisor.py:62,111` — penulisan `mode` primary/standby.
- `monoagens/engine/jobs/lease.py:56-101` — `acquire`/`renew` (pemilik `generation`).
- `monoagens/engine/guard/sandbox.py:168` — preseden `asyncio.wait_for` untuk pekerjaan berbatas.
- `monoagens/engine/llm.py:122-128` — client `AsyncOpenAI(timeout=...)`; `:165-169` — `run_budget_seconds` dihitung per `run()` (dasar pemisahan dua lapis).
- `monoagens/gateways/cli/gateway.py:81`, `monoagens/gateways/whatsapp/gateway.py:124` — dua pemanggil `handle()`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0017-turn-isolation-timeout.md`.*
