> Terjemahan turunan — jangan sunting; sumber: engine/0013-turn-lifecycle.md (sha256: 053818f8bb67)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0013: Turn Lifecycle, Resume, Backlog & Notice

- **Status:** accepted
- **Implementasi:** draft — belum ada `turns`/`turn_steps`/tabel notice
- **Tanggal:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0011 (crash safety) dan ADR-0012 (lease safety); pertanyaan: kalau bot mati di tengah turn, apakah dilanjutkan?
- **Supersedes:** —
- **Terkait:** ADR-0011 (durability), ADR-0012 (fencing), ADR-0003 (worker), ADR-0004 (labeling)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0013-turn-lifecycle.md`.
- **Amandemen:** 2026-11-02 — normalisasi kerangka (ADR-0009 §4.4): menambah Opsi yang dipertimbangkan, Keputusan (ringkasan), dan Interaksi dengan ADR lain; penomoran 3–14 bergeser ke 5–17. 2026-11-03 — audit: rentang `supervisor.py` diperbaiki menjadi `36-129`; path `eidesoul/engine/gateways` dibetulkan menjadi `eidesoul/gateways/`; kunci notice diseragamkan ke `resume_notice.after_seconds`; baris basi soal ADR-0009 diselaraskan. 2026-11-03 — audit: status turn `pending` (tidak ada di enum) diganti `received`/`running`/`sending`/`interrupted`; klaim reaper memakai `clock_timestamp()`; ledger memakai `scope_key`. 2026-11-03 — `send`/`send_to` tidak lagi disebut "tool"; keduanya aksi engine (gateway), dan `send_to` sempat dianggap sinonim — koreksi 2026-11-03: `send` dan `send_to` adalah **tool gateway** (`gateways/whatsapp/tools/`), bukan langkah engine; yang perlu dibedakan adalah langkah otomasi ADR-0014.

---

## 1. Konteks

- `handle()` adalah coroutine in-process tanpa checkpoint (`agent.py:413-926`). Proses mati = turn hilang.
- Pesan biasanya sudah tersimpan di `messages`, tetapi tidak ada yang memproses ulang.
- Balasan yang sudah terkirim tidak bisa ditarik; balasan sebagian meninggalkan sisa yang tak pernah dikirim.
- Indikator typing menghilang sendiri; `answered`/`quiet`/`backlog` hidup di memori (`agent.py:145-147`).
- `spawn()` capture memori hilang saat crash (diperbaiki ADR-0003/0011).

Prinsip yang disepakati:

> **Setiap pesan masuk adalah task. Selesai hanya kalau balasannya sudah terkirim (`delivered`). Yang selesai tidak diulang; yang belum selesai dilanjutkan.**

## 2. Decision drivers

1. **Tidak ada jawaban yang hilang** untuk pesan yang sudah diterima.
2. **Tidak ada balasan ganda** saat resume.
3. **Tidak mengulang efek samping** yang sudah terjadi.
4. **UX jujur**: pengguna diberi tahu saat bot akan/baru saja berhenti.
5. **Tidak membanjiri** chat setelah downtime.
6. **Batas jelas** antara chat murni dan aksi ber efek samping.

## 3. Opsi yang dipertimbangkan

Tidak ada alternatif lain yang tercatat saat keputusan diambil; pilihan yang diambil beserta konsekuensinya dibahas langsung di Definisi, Skema, dan State machine.

## 4. Keputusan

- Setiap pesan masuk adalah task tersendiri dengan baris `turns` ber-`message_id` sebagai idempotency key; turn hanya selesai saat `delivered` (lihat Definisi, Skema).
- Resume dimulai dari checkpoint `turns.step`; langkah yang sudah punya output tersimpan tidak diulang (lihat State machine, Resume).
- Tool idempoten boleh dijalankan ulang; aksi non-idempoten yang sudah `executed` membuat turn `interrupted`, bukan diulang (lihat Resume).
- Pengiriman per chunk: resume hanya mengirim chunk yang belum `sent`; status `unknown` mengikuti kebijakan `partial_delivery` (lihat Resume).
- Shutdown berjalan `running → draining → noticed → stopped`, dengan satu notice per chat per `shutdown_epoch` (lihat Shutdown, Notice).
- Notice ber-`kind='notice:*'` bukan jawaban dan tidak pernah menandai turn `delivered` (lihat Notice).
- Backlog diproses dengan pacing dan fairness sesuai `backlog_policy` (lihat Backlog, Konfigurasi).

## 5. Definisi

| Istilah | Arti |
|---|---|
| **Diterima** | Pesan sudah dipersist (`messages` + `turns(status='received')`) |
| **Selesai** | `turns.status='delivered'` (semua chunk terkirim) |
| **Checkpoint** | `turns.step` terakhir yang tersimpan; resume mulai dari sini |
| **Notice** | Pesan bot `kind='notice:*'`; **bukan** jawaban, tidak menyelesaikan turn |
| **Epoch** | `shutdown_epoch`/`boot_epoch` untuk idempotensi notice |

## 6. Skema

```sql
turns(
  id               text primary key,   -- message_id masuk (idempotency key)
  chat_id          text not null,
  person_id        text not null,
  status           text not null,      -- received|running|sending|delivered|interrupted|expired
  step             text not null,      -- received|decided|drafted|reviewed|sending|delivered
  attempt          int  not null default 0,
  lease_owner      text not null default '',
  lease_until      double precision not null default 0,
  generation       bigint not null default 0,
  decision_json    jsonb,
  draft_text       text,
  reviewed_text    text,
  planned_chunks   int not null default 0,
  delivered_chunks int not null default 0,
  shutdown_epoch   bigint not null default 0,
  boot_epoch       bigint not null default 0,
  last_error       text not null default '',
  created_at       double precision not null,
  updated_at       double precision not null
)

turn_actions(
  turn_id    text not null,
  step_index int  not null,
  tool       text not null,
  args_hash  text not null,
  result     text,
  status     text not null,   -- executed|failed
  primary key (turn_id, step_index)
)

outbound_messages(
  scope_key   text not null,      -- turn:<turn_id> | automation:<run_id>
  chunk_index int  not null,
  gateway     text not null,
  chat_id     text not null,
  provider_id text not null default '',
  status      text not null,  -- intent|sent|unknown
  generation  bigint not null default 0,
  primary key (scope_key, chunk_index)
)
```

`messages` + `turns` + `memory_events` + `memories(pending)` + `cognition_jobs` ditulis **satu transaksi** (ADR-0011).

## 7. State machine

| Step | Setelah | Disimpan |
|---|---|---|
| `received` | Pesan diterima | — |
| `decided` | Decision LLM | `decision_json` |
| `drafted` | `llm.run` + tool loop | `draft_text` |
| `reviewed` | `polish`/`regulate`/privacy review | `reviewed_text` |
| `sending` | Mulai kirim bubble | `delivered_chunks` per chunk |
| `delivered` | Semua chunk terkirim | **terminal** |
| `interrupted` | Tidak bisa dilanjutkan | alasan + notice |
| `expired` | Terlalu tua untuk diresume | alasan |

### 7.1 Pembatalan: `/stop` (turunan task)

`/stop` membatalkan **tugas yang sedang berjalan di room ini** — bukan mematikan instance, bukan
membisukan room.

- **Scope:** `(chat_id, task/episode berjalan)`; turn di room lain tidak tersentuh, dan tidak ada `/stop`
  lintas room.
- **Efek:** task `handle()` di-cancel, typing dihentikan, turn yang belum `delivered` ditandai
  `interrupted` dengan alasan `user_stop`, dan **satu** notice dari katalog. Kiriman yang sudah terkirim
  tidak ditarik dan tetap tercatat di ledger.
- **Tidak** mengubah `mode`/lease (itu `/freeze`), tidak memasang quiet period (mekanisme itu **dihapus**,
  lihat akhir §7.1), dan tidak menghapus backlog.
- **Episode** yang dihentikan ditutup sebagai `interrupted`; `distill` tetap boleh berjalan karena bahan
  belajarnya sudah ada.
- **Wewenang:** penanya di room itu boleh menghentikan tugasnya sendiri; administrator boleh
  menghentikan tugas mana pun **di room yang sama**. Tidak ada jalur lintas room.
- **Idempoten, tanpa LLM:** bila tidak ada tugas berjalan, jawab singkat "tidak ada tugas berjalan".
- **Bisa tiba saat turn berjalan:** `/stop` adalah command (di luar `turn_lock`), jadi ia justru dibaca
  ketika turn sedang berjalan — itu gunanya.
- **Cadangan:** capture memori yang sudah di-`spawn` tidak dibatalkan (durabel dan tidak terlihat
  pengguna); hanya pekerjaan yang menghasilkan balasan yang dihentikan.

**Perilaku lama dihapus, bukan dipindahkan.** `/stop [menit]` yang membisukan balasan di room ternyata
tidak berguna: pesan tetap dicatat, hanya balasan yang ditahan, dan tidak ada yang membutuhkannya. Ia
dihapus bersama seluruh mekanismenya:

- `eidesoul/engine/commands/stop.py` (command beserta alias `diam`);
- `Agent.quiet` dan cabang `agent.py:439-440` ("message recorded without a reply");
- `reply.quiet_seconds` (config, `config.yaml`, template admin);
- kunci i18n `command.stop.*`;
- entri `stop` di inventaris registry (`tests/test_registry.py:52`);
- aturan prompt `prompts.py:234` tentang "quiet period" — diganti dengan aturan yang jujur: diam adalah
  aksi, dan satu-satunya diam yang sah adalah `/stop` yang membatalkan tugas.

Nama `stop` dibebaskan untuk pembatalan task; tidak ada alias `diam`.

## 8. Resume

### 8.1 Kapan

Setelah lease diperoleh (ADR-0012), jalankan recovery pass sekali:

```sql
SELECT ... FROM turns
WHERE status IN ('received','running','sending') AND lease_until < clock_timestamp()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT :n
```

Kelayakan resume:

- `age < max_age_seconds` (default 900),
- `attempt < max_attempts` (default 2),
- tidak ada aksi non-idempoten yang sudah `executed` (lihat `turn_actions`),
- belum melewati `stale_after_seconds` untuk kebijakan `consolidate`.

### 8.2 Dari mana

- Mulai dari `turns.step`; langkah yang sudah punya output tersimpan tidak diulang.
- **Tool idempoten** (`read`, `web_search`, `chat_history`, `memory` baca): boleh dijalankan ulang.
- **Tool ber efek samping** (gateway `send`/`send_to`, serta `shell`, `sandbox`, `settings`): tidak dijalankan ulang; hasil diambil dari `turn_actions` dan dimasukkan kembali ke konteks LLM.
- Aksi non-idempoten yang sudah `executed` dan turn belum selesai → `interrupted`, bukan diulang.

### 8.3 Pengiriman

- Sebelum kirim: `outbound_messages.status='intent'`.
- Sesudah kirim: `sent` + `provider_id`.
- Resume mengirim **hanya chunk yang belum `sent`**.
- `unknown` (crash antara kirim dan catat) mengikuti `partial_delivery`:
  - `skip` (default): jangan kirim ulang — hindari dobel; risiko satu bubble hilang;
  - `resend`: kirim ulang — risiko dobel.
- Semua chunk `sent` tetapi turn masih `sending` → recovery menandai `delivered` (tidak mengirim apa pun).

## 9. Shutdown

```
running → draining → noticed → stopped
```

1. **Draining** — stop intake; `engine_control.shutting_down=true`; turn in-flight diberi `drain_seconds`.
2. **Noticed** — chat dengan turn belum selesai menerima **satu notice per chat per `shutdown_epoch`**.
3. **Stopped** — worker berhenti, lease dirilis, proses keluar.

Batas: `grace_seconds > drain_seconds + notice_seconds`; `grace_seconds < stop_grace_period` orkestrator; divalidasi saat boot (fail-fast).

Race yang dijaga: event yang sudah masuk antrean tetapi belum dipersist → **persist dulu** lalu dicoba selesaikan; kalau tidak sempat, notice lalu turn ditandai `interrupted` (bahan resume). Jangan drop.

## 10. Notice

| Jenis | Kapan | Frekuensi |
|---|---|---|
| `notice:shutdown` | Sebelum keluar rapi | Satu per chat per `shutdown_epoch` |
| `notice:resumed` | Setelah aktif, jika downtime > `resume_notice.after_seconds` | Satu per chat per `boot_epoch` |
| `notice:interrupted` | Turn tidak bisa dilanjutkan | Maksimal satu per chat, lalu diam |

Aturan:

- Notice dicatat sebagai pesan bot dengan `kind`, masuk katalog `t()`.
- Notice **tidak** menandai `delivered`; status turn tetap `received`/`running`/`sending`, atau `interrupted`.
- Kalau notice gagal: `notice_failed`, dicoba lagi sesuai `notice_retry`; kalau tetap gagal, dilaporkan di `/status`.
- Nada: “koneksiku terputus”, bukan “aku shutdown”.

## 11. Backlog

| Kebijakan | Perilaku | Kapan |
|---|---|---|
| `per_message` | Balas satu per satu berurutan | Backlog pendek |
| `per_message_then_consolidate` | Satu per satu sampai batas, lalu satu balasan gabungan | **Default** |
| `consolidate` | Satu balasan untuk seluruh backlog | Backlog panjang |
| `last_only` | Balas pesan terakhir, sisanya konteks | Backlog basi |

- Batas: `max_resume_per_chat` (5) lalu lanjut berpacing; `max_resume_per_minute` (10) global.
- Fairness: round-robin antar chat.
- Umur: `< max_age_seconds` → resume penuh; `< stale_after_seconds` → `consolidate`; di atas itu → `expired` (opsional satu notice).
- Turn kategori `action` tidak digabung; diproses sendiri-sendiri.

## 12. Konfigurasi

```yaml
turns:
  resume: on
  max_attempts: 2
  max_age_seconds: 900
  stale_after_seconds: 21600
  partial_delivery: skip          # skip | resend
  max_resume_per_chat: 5
  max_resume_per_minute: 10
  backlog_policy: per_message_then_consolidate
  shutdown:
    grace_seconds: 25             # total anggaran shutdown (drain 15 + notice 5 + slack 5)
    drain_seconds: 15             # waktu menyelesaikan turn in-flight
    notice_seconds: 5             # waktu mengirim notice
    notice_per_chat: true         # satu notice per chat per epoch
    notice_retry: { attempts: 2, backoff_seconds: 1 }
  resume_notice:
    after_seconds: 300            # kirim notice "resumed" hanya jika downtime > ini
```

Makna kunci:

- `grace_seconds` — total anggaran shutdown; harus `< stop_grace_period` orkestrator.
- `drain_seconds` — kesempatan turn selesai sebelum menyerah; makin kecil → makin banyak turn `interrupted`.
- `notice_seconds` — anggaran mengirim notice; makin kecil → makin banyak `notice_failed`.
- `notice_per_chat` — hindari spam; satu pengumuman per chat per episode.
- `notice_retry` — percobaan ulang notice dalam jendela; habis → `notice_failed`.
- `resume_notice.after_seconds` — ambang downtime sebelum bot mengumumkan “aku kembali”.

## 13. Metrik dan gate

| Metrik | Target |
|---|---|
| Turn diterima tapi tidak selesai | 0 setelah resume |
| Balasan ganda akibat replay | 0 |
| Efek samping non-idempoten diulang | 0 |
| Notice dikirim per chat per epoch | ≤ 1 |
| Turn `expired` | terukur, bukan nol paksa |
| Durasi shutdown | < `grace_seconds` |

## 14. Acceptance criteria

- [ ] Setiap pesan diterima punya baris `turns`; `delivered` hanya setelah semua chunk terkirim.
- [ ] Turn selesai tidak pernah diproses ulang (idempotency `message_id`).
- [ ] Resume mulai dari checkpoint; output LLM dan hasil tool tidak diulang.
- [ ] Aksi non-idempoten yang sudah `executed` → `interrupted`, bukan diulang.
- [ ] Pengiriman per chunk: hanya chunk belum `sent` yang dikirim saat resume.
- [ ] Shutdown: satu notice per chat per epoch; turn tidak ditandai `delivered`.
- [ ] Event yang sudah diterima saat shutdown tetap dipersist dan diberi notice.
- [ ] Backlog: pacing + fairness + kebijakan `per_message_then_consolidate` bekerja.
- [ ] Notice tidak pernah menandai turn `delivered`.
- [ ] `grace_seconds < stop_grace_period` divalidasi saat boot.
- [ ] `/stop` membatalkan tugas berjalan di room itu saja; turn di room lain tidak terpengaruh.
- [ ] Turn yang dibatalkan menjadi `interrupted(user_stop)` + satu notice; kiriman yang sudah terjadi tetap di ledger dan tidak ditarik.
- [ ] `/stop` tidak mengubah `mode`/lease dan tidak memasang quiet period; `/freeze` tetap instance-level.
- [ ] Tidak ada sisa mekanisme quiet: `command.stop.*`, `Agent.quiet`, `reply.quiet_seconds`, dan entri registry `stop` sudah hilang (test grep + registry inventory).
- [ ] `/stop` tanpa tugas berjalan → jawaban singkat, bukan error; `/stop` lintas room ditolak.

## 15. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Replay memicu efek samping dobel | Klasifikasi tool + `turn_actions` + generation (ADR-0012) |
| Notice dianggap jawaban | `kind='notice:*'`; tidak menandai `delivered` |
| Spam notice | Satu per chat per epoch; `resume_notice.after_seconds` |
| Backlog membanjir | Batas per chat/menit; `consolidate`; pacing humanize |
| Turn tua diresume | `max_age` dan `stale_after` |
| Crash saat resume | `attempt` + `max_attempts`, lalu `interrupted` |

## 16. Interaksi dengan ADR lain

- **ADR-0011 (durability)** — `messages` + `turns` + `memory_events` + `memories(pending)` + `cognition_jobs` ditulis dalam satu transaksi; durability turn bersandar pada aturan itu.
- **ADR-0012 (fencing)** — lease dan generation melindungi resume dari dua worker; kolom `lease_owner`, `lease_until`, dan `generation` pada `turns` mengikuti skema fencing tersebut.
- **ADR-0003 (worker)** — perbaikan capture memori saat crash membuat state turn bertahan melewati restart.
- **ADR-0004 (labeling)** — tercatat pada header `Terkait:`; tidak ada ketergantungan teknis yang dirinci di ADR ini.
- **ADR-0009 (rumah ADR)** — sudah diputuskan: berkas ini ikut migrasi menjadi `docs/v0.1.0/adr/engine/0013-turn-lifecycle.md`; tidak ada perubahan pada keputusan turn.

## 17. Referensi kode

- `eidesoul/engine/agent.py:413-926` — `handle` tanpa checkpoint.
- `eidesoul/engine/agent.py:145-147` — `answered`/`backlog`/`quiet` in-memory.
- `eidesoul/engine/agent.py:881-905` — loop pengiriman chunk dan penandaan `answered`.
- `eidesoul/engine/supervisor.py:36-129` — lifecycle shutdown/primary.
- `eidesoul/gateways/` — intake yang harus dihentikan saat draining (di luar core; core tidak mengimpornya).
- `eidesoul/engine/tools/__init__.py:182-240` — satu pintu eksekusi (titik klasifikasi idempotensi).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0013-turn-lifecycle.md`.*
