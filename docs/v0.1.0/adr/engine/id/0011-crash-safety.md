> Terjemahan turunan — jangan sunting; sumber: engine/0011-crash-safety.md (sha256: d6ec6e9bdf8d)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0011: Crash Safety & Durability — Antrean Transaksional, Klaim Job, Shutdown Rapi

- **Status:** accepted
- **Implementasi:** partial — ada: intake transaksional (`HistoryStore.intake`), claim/reaper turn dengan backoff + dead-letter, ledger kirim, shutdown bertahap, recovery, `tests/test_turn_shutdown.py`; sisa: claim/retry `cognition_jobs` (ADR-0003, W3) dan tulisan memori dalam transaksi intake yang sama (W3)
- **Tanggal:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0001 (canonical), ADR-0002 (capture), ADR-0003 (worker C/D), ADR-0004 (labeling fallback)
- **Supersedes:** —
- **Terkait:** ADR-0012 (lease safety), ADR-0013 (turn lifecycle & resume)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0011-crash-safety.md`.
- **Amandemen:** 2026-11-02 — normalisasi kerangka (ADR-0009 §4.4): menambah bagian Interaksi dengan ADR lain; Referensi bergeser ke §10. 2026-11-03 — audit: rentang `supervisor.py:36-140` diperbaiki menjadi `36-129`. 2026-11-03 — audit: klaim waktu klaim job memakai `clock_timestamp()` (sesuai ADR-0012), dan ledger memakai `scope_key` sehingga kiriman otomasi tercakup.

---

## 1. Konteks

- Pesan disimpan sinkron ke `messages`, tetapi capture memori memakai `spawn()` fire-and-forget tanpa retry, status, atau dead-letter (`agent.py:917-921`).
- Tidak ada antrean durable: kalau proses mati, pekerjaan memori hilang; kalau worker mati setelah klaim, pekerjaan menggantung.
- Pekerjaan yang dijadwalkan tidak tahan restart: `answered`, `backlog`, `quiet` hidup di memori (`agent.py:145-147`).
- Saat DB bermasalah, `WorkingMemory.append` menangkap error dan lanjut (`context.py:210-225`): pesan hanya ada di memori lalu hilang — fail-open yang bertentangan dengan janji “menyimpan”.
- Perintah `shell` memakai `create_subprocess_shell` tanpa process group (`tools/shell.py:13-18`); anak proses bisa menjadi yatim saat shutdown.
- Lease dirilis saat `deactivate()`, tetapi crash tidak memberi kesempatan itu — recovery bergantung pada TTL dan keamanan takeover (ADR-0012).

## 2. Decision drivers

1. **Tidak ada pekerjaan hilang** saat proses mati, selama DB sehat.
2. **Tidak ada pemrosesan ganda** saat dua runner bekerja.
3. **Pemulihan otomatis** setelah restart, tanpa intervensi manual.
4. **Observability**: backlog, dead-letter, dan kegagalan terlihat.
5. **Kesederhanaan**: satu tabel pekerjaan, satu pintu tulis.
6. **Shutdown rapi** dalam anggaran waktu orkestrator.

## 3. Opsi yang dipertimbangkan

| Opsi | Bentuk | Kelebihan | Kekurangan | Status |
|---|---|---|---|---|
| **A. Status quo** | `spawn()` per pekerjaan | Tidak ada perubahan | Hilang diam-diam; tanpa retry/status | Ditolak |
| **B. Antrean tanpa klaim** | Tabel antrean, satu worker | Sederhana | Dua runner bisa memproses item yang sama; tanpa lease/reaper | Ditolak |
| **C. Antrean transaksional + klaim + ledger + shutdown rapi** | Enqueue satu transaksi; `SKIP LOCKED`; lease/reaper; outbound ledger; fail-fast saat archive gagal | Tahan crash; idempotent; terlihat | Menambah tabel, worker, reaper, metrik | **Dipilih** |

## 4. Keputusan

### 4.1 Enqueue transaksional

Satu transaksi menulis:

```
messages          (pesan masuk)
turns             (status='received')      -- ADR-0013
memory_events     (kandidat pasca hard exclusion)
memories          (state='pending', level NULL)  -- ADR-0004
cognition_jobs    (kind='capture', ...)    -- ADR-0003
```

Kalau transaksi gagal, turn **tidak** diproses. Tidak ada fail-open untuk archive: lebih baik gagal cepat dan membiarkan transport mengulang daripada mengklaim “tersimpan” padahal tidak.

### 4.2 Klaim job

```sql
SELECT ... FROM cognition_jobs
WHERE status='queued' AND next_attempt_at <= clock_timestamp()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT :n
```

- `claimed_by`, `lease_until` diisi saat klaim.
- **Reaper**: `lease_until` lewat → `queued` lagi.
- Retry dengan backoff; setelah `dead_letter_after` → status `dead` + alert.
- `idempotency_key` unik per pekerjaan; penulisan canonical via `MemoryStore` (upsert).

### 4.3 Ledger pengiriman

`outbound_messages(scope_key, chunk_index, gateway, chat_id, provider_id, status)` — status `intent|sent|unknown`; `scope_key` = `turn:<id>` atau `automation:<run_id>`, sehingga kiriman otomasi memakai jalur idempotensi yang sama (detail di ADR-0013).

### 4.4 Shutdown rapi

```
running → draining → noticed → stopped
```

1. Stop intake; `engine_control.shutting_down = true`.
2. Drain: turn in-flight diberi `drain_seconds` untuk selesai.
3. Notice: chat dengan turn belum selesai menerima satu notice (ADR-0013).
4. Worker berhenti mengambil job baru; job in-flight diselesaikan atau dikembalikan ke antrean.
5. Release lease; proses keluar.

Batas: `grace_seconds > drain_seconds + notice_seconds` dan `grace_seconds < stop_grace_period` orkestrator. Validasi ini **fail-fast saat boot**, bukan diam-diam terpotong.

### 4.5 Pemulihan saat start

- Setelah lease diperoleh (ADR-0012), jalankan recovery pass: klaim turn/job yang `lease_until` lewat.
- Job dikembalikan ke antrean oleh reaper; turn diresume oleh ADR-0013.

### 4.6 Higiene proses

- `shell`/subprocess: `start_new_session=True` + kill process group saat shutdown.
- Sandbox: container diberi label (`eidesoul.worker`, `eidesoul.turn`) untuk pembersihan; `--rm` tetap.
- Worker tidak memblokir event loop: DB dan MiniLM lewat thread/executor.

### 4.7 Yang tidak durable dan harus diputuskan

| Artefak | Keputusan |
|---|---|
| `quiet` (`/stop`) | Persist ke DB (`state` per chat) |
| `answered` (dedupe burst) | Persist sebagai bagian dari `turns` (`message_id` unik) |
| `backlog` (away count) | Turunkan dari `messages`/`turns`, bukan in-memory |

## 5. Konfigurasi

```yaml
worker:
  retry: { attempts: 5, backoff_seconds: 2, dead_letter_after: 5 }
  reaper: { every_seconds: 30, batch: 100 }
  queue_cap: 5000
turns:
  shutdown:
    grace_seconds: 25
    drain_seconds: 15
    notice_seconds: 5
    notice_per_chat: true
    notice_retry: { attempts: 2, backoff_seconds: 1 }
```

## 6. Metrik dan gate

| Metrik | Target |
|---|---|
| Job hilang saat crash | 0 |
| Pemrosesan ganda per item | 0 |
| Dead-letter per hari | mendekati 0 |
| `grace_seconds` vs `stop_grace_period` | validasi boot gagal jika terlanggar |
| Anak proses yatim setelah shutdown | 0 |

## 7. Acceptance criteria

- [ ] Enqueue memori/turn satu transaksi dengan pesan; tidak ada fail-open archive.
- [ ] Job diklaim dengan `SKIP LOCKED` + lease; reaper mengembalikan klaim mati.
- [ ] Idempotency key mencegah duplikasi pada replay.
- [ ] Outbound ledger mencegah pengiriman ulang chunk.
- [ ] Shutdown mengikuti fase dan mematuhi batas `grace_seconds`.
- [ ] Validasi `grace_seconds < stop_grace_period` gagal saat boot.
- [ ] `quiet`/`answered`/`backlog` dipersist atau dihilangkan dari desain.
- [ ] Subprocess dimatikan per process group; container dibersihkan.
- [ ] Status backlog, dead-letter, dan stale claim terlihat di `/status`.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| DB down saat enqueue | Fail-fast; turn tidak diproses; transport mengulang |
| Reaper terlalu agresif | `lease_until` cukup panjang; klaim ulang idempotent |
| Ledger tidak lengkap | Status `unknown` + kebijakan `skip`/`resend` (ADR-0013) |
| Shutdown terpotong orkestrator | Validasi konfigurasi saat boot; metrik durasi shutdown |
| Antrean menumpuk | Backpressure + degrade mode + alert (ADR-0003) |

## 9. Interaksi dengan ADR lain

- **ADR-0001** — canonical: penulisan canonical lewat `MemoryStore` (upsert) saat klaim job (§4.2).
- **ADR-0002** — capture: capture memori yang semula `spawn()` fire-and-forget (§1) kini menjadi pekerjaan antrean transaksional.
- **ADR-0003** — antrean pekerjaan dan klaim job yang sama: `cognition_jobs(kind='capture')` ditulis dalam transaksi enqueue (§4.1), diklaim via `SKIP LOCKED` + lease (§4.2), dengan backpressure/alert saat antrean menumpuk (§8).
- **ADR-0004** — labeling fallback: kandidat memori ditulis sebagai `memories(state='pending', level NULL)` pada transaksi enqueue (§4.1).
- **ADR-0012** — lease safety: recovery bergantung pada TTL dan keamanan takeover; lease diperoleh lebih dulu sebelum recovery pass (§4.5).
- **ADR-0013** — turn lifecycle & resume: `turns(status='received')` pada enqueue (§4.1), ledger pengiriman (§4.3), notice shutdown (§4.4), resume turn pada recovery pass (§4.5), dan kebijakan `skip`/`resend` ledger (§8).

## 10. Referensi kode

- `eidesoul/engine/agent.py:917-921` — spawn capture.
- `eidesoul/engine/context.py:210-225` — `WorkingMemory.append` fail-open.
- `eidesoul/engine/db.py:95-145` — `connect`, `upsert`.
- `eidesoul/engine/supervisor.py:36-129` — lifecycle primary/standby.
- `eidesoul/engine/lease.py:103-129` — renew/release.
- `eidesoul/engine/tools/shell.py:13-18` — subprocess tanpa process group.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0011-crash-safety.md`.*
