> Terjemahan turunan — jangan sunting; sumber: engine/0019-observability.md (sha256: 4a0d57c71034)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0019: Observability — Log Terstruktur, Token, dan `/status`

- **Status:** accepted
- **Implementasi:** sebagian — ada: logging + command `/status`; sisa: `turn_id`, log `text|json`, tabel `llm_calls`
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P2-11 — hanya ada `logging.basicConfig` teks bebas tanpa korelasi; `llm.run` tidak membaca `response.usage` sehingga token/biaya tidak pernah terukur; tidak ada counter, antrean, atau latensi yang bisa dilihat operator tanpa menebak dari log
- **Supersedes:** —
- **Terkait:** ADR-0013 (`turns`, `turn_id`), ADR-0003 (`cognition_jobs`, queue depth), ADR-0014 (`automation_runs`), ADR-0011 (shutdown/ledger), ADR-0015 (redaksi), ADR-0016 (DDL `llm_calls`), ADR-0017 (timeout/`standby_refused`)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0019-observability.md`.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Log hanya teks bebas, tanpa id korelasi | `__main__.py:22` `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(message)s")` |
| Token tidak pernah dibaca | `llm.py:150-200` hanya mengembalikan `content`; tidak ada `response.usage` |
| Tidak ada metrik | tidak ada counter/latensi di `eidesoul/`; `ablate.replay_metrics` hanya analisis offline |
| Diagnosa hari ini | membaca log dan menebak; tidak bisa menjawab "turn ini berapa token" atau "antrean kognisi berapa" |

Ini bukan kerapian: tanpa token terukur, keputusan biaya di ADR-0014/0015 (budget, `run_budget_seconds`, pemadatan) berjalan tanpa umpan balik; tanpa `turn_id`, satu keluhan pengguna tidak bisa dilacak ke satu turn.

## 2. Decision drivers

- Diagnosa harus bisa dilakukan **tanpa membaca isi pesan** (privasi ADR-0006/0008, redaksi ADR-0015 §4.7).
- Biaya LLM harus terukur per panggilan dan per turn, bukan perasaan.
- Tanpa dependency baru dan tanpa HTTP: AGENTS melarang gateway HTTP; operator memakai command dan log.
- Korelasi harus bertahan lintas proses untuk pekerjaan latar (job kognisi, automation run).

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Log terstruktur + counter in-process + `/status` + token accounting** | stdlib saja | kecil-sedang | tidak ada export Prometheus (disengaja) | **Dipilih** |
| **B. OTel sekarang (extra)** | SDK + exporter | sedang | dependency dan asumsi infra yang tidak dimiliki pengguna v0.1.0 | Ditunda |
| **C. Rapikan log saja** | format lebih baik | kecil | token, antrean, dan latensi tetap tidak terukur | Ditolak |

## 4. Keputusan

### 4.1 Format log dapat dipilih, field wajib seragam

- `engine.logging.format: text | json` (default `text`). `json` memakai formatter stdlib (tanpa dependency), satu objek per baris.
- Field wajib pada setiap baris: `ts`, `level`, `event` (kunci stabil, bukan kalimat bebas), `logger`; opsional: `turn_id`, `chat_id`, `job_id`, `run_id`, `gateway`, `duration_ms`.
- **Tidak ada isi pesan, teks memori, kutipan arsip, atau nama orang** di log — hanya id dan angka. Ditegakkan test (lihat §7).

### 4.2 `turn_id` sebagai benang merah

- Dibuat di awal `handle()` (dipakai juga sebagai `turns.id`), diteruskan lewat `contextvars` sehingga setiap log di dalam turn membawanya tanpa parameter tambahan.
- Pekerjaan latar membawa `job_id`/`run_id` (ADR-0003/0014) dan **bukan** `turn_id` turn lain; tidak ada pencampuran konteks antar-task.

### 4.3 Token accounting per panggilan

- `llm.run`, `llm.complete`, dan panggilan latar membaca `response.usage` (prompt/completion) dan menulis **satu baris** `llm_calls` per panggilan: `turn_id` (null untuk latar), `job_id`/`run_id`, `purpose` (`decision`/`reply`/`polish`/`digest`/`extract`/`reflect`/`research`), `model`, `tokens_in`, `tokens_out`, `latency_ms`, `ok`.
- Bentuk kolom, index, dan retensinya **milik ADR-0016** (§4.6); ADR ini hanya menetapkan isi dan pemakaiannya.
- Tidak ada tabel harga di engine (model tidak pernah dipin; AGENTS). Biaya dihitung operator dari `model` + token.

### 4.4 Counter in-process, bukan exporter

Counter yang wajib ada (naik di memori, dibaca `/status`):

| Counter | Sumber |
|---|---|
| `turns_total`, `turn_errors_total`, `turn_timeout_total` | ADR-0013/0017 |
| `standby_refused_total` | ADR-0017 §4.4 |
| `llm_calls_total`, `llm_tokens_in_total`, `llm_tokens_out_total` | ADR-0019 §4.3 |
| `queue_depth` (dibaca dari `cognition_jobs`) | ADR-0003 |
| `rich_missing_total`, `directory_missing_total`, `participants_unknown_total` | ADR-0018 |
| `memory_hits_total`, `memory_staged_total` | ADR-0005/0008 |
| `automation_runs_total`, `automation_missed_total` | ADR-0014 |
| `turn_latency_ms` p50/p95 (ring `window_seconds`) | ADR-0013 |

### 4.5 `/status` (owner) sebagai satu-satunya permukaan

- Command `status` yang sudah ada diperluas: mode/primary, `generation`, uptime, versi skema, counter di §4.4, dan bucket kasar (mis. "antrean: sedang") — angka mentah hanya untuk role owner.
- Tidak ada server HTTP, tidak ada endpoint `/metrics`. Integrasi monitoring eksternal membaca log JSON (`turn_id`, `event`) dan tabel `llm_calls`.
- Redaksi: `status` tidak pernah menampilkan DSN, kunci, atau isi memori (ADR-0015 §4.7 tetap berlaku).

## 5. Konfigurasi

```yaml
engine:
  logging:
    format: text            # text | json
    level: INFO
  metrics:
    enabled: true
    window_seconds: 300     # jendela ring untuk p50/p95 latensi turn
    retention_days: 90      # baris `llm_calls` lebih tua dihapus job purge ADR-0005
```

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `log_json_valid` | setiap baris mode json dapat di-parse | 100% pada smoke |
| `log_content_leak` | baris log yang memuat teks pesan/memori | **0** (test memeriksa record) |
| `llm_usage_missing` | panggilan LLM yang selesai tanpa baris `llm_calls` | 0 |
| `turn_latency_p95_ms` | p95 latensi turn dalam jendela | dilaporkan, bukan gate keras |
| `status_administrator_only` | `/status` dari role non-administrator | hanya bucket kasar |

## 7. Acceptance criteria

- [ ] `engine.logging.format=json`: setiap baris valid JSON dengan field wajib §4.1.
- [ ] Setiap log di dalam turn membawa `turn_id` yang sama; pekerjaan latar membawa `job_id`/`run_id`, tidak membawa `turn_id` turn lain.
- [ ] Test: satu turn berisi pesan rahasia → tidak ada satu pun record log yang memuat teks itu.
- [ ] Setiap panggilan LLM yang selesai menghasilkan tepat satu baris `llm_calls` dengan token > 0 (kecuali provider tidak mengembalikan usage → `ok=false`, `tokens_*` null, dan tercatat).
- [ ] `/status` menampilkan mode, `generation`, counter, dan bucket antrean; role non-administrator hanya menerima bucket kasar.
- [ ] `turn_latency_ms` p50/p95 dapat dibaca setelah minimal 5 turn.
- [ ] Tidak ada dependency baru di `pyproject.toml` untuk ADR ini.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Log membocorkan isi percakapan | aturan §4.1 + test `log_content_leak=0`; teks hanya boleh masuk tabel/arsip |
| Cardinality meledak (`chat_id` sebagai label) | `chat_id` hanya field log, tidak pernah label metrik; counter berbasis agregat |
| `contextvars` bocor antar-task | `turn_id` di-set ulang di awal setiap task; pekerjaan latar membersihkan konteks |
| Token tidak tersedia dari provider | baris tetap ditulis dengan `ok=false`; tidak ada estimasi palsu |
| Overhead log JSON | format `text` tetap default; `json` untuk operator yang butuh |

## 9. Interaksi dengan ADR lain

- **ADR-0016** — pemilik DDL `llm_calls` (§4.6); ADR ini tidak menulis DDL.
- **ADR-0013** — `turn_id` adalah id turn; status turn tetap satu-satunya sumber kebenaran.
- **ADR-0003** — `queue_depth` dibaca dari `cognition_jobs`, bukan counter yang disalin.
- **ADR-0014** — `automation_runs` sudah mencatat run; counter menambahkan agregat, bukan duplikasi data.
- **ADR-0015** — redaksi `settings`/`status` mengikuti aturan yang sama.
- **ADR-0017** — `turn_timeout_total` dan `standby_refused_total` lahir di sana, dibaca di sini.

## 10. Referensi kode

- `eidesoul/__main__.py:22` — satu-satunya konfigurasi logging saat ini.
- `eidesoul/engine/llm.py:150-200` — `run()` mengembalikan `content`; titik pembacaan `response.usage`.
- `eidesoul/engine/agent.py:413` — awal `handle()` (tempat `turn_id` dibuat).
- `eidesoul/engine/schema.py` — tabel `turns`/`cognition_jobs` (sumber `turn_id` dan `queue_depth`).
- `eidesoul/engine/commands/status.py` — command `status` yang diperluas.
- `eidesoul/engine/db.py:24` — `redact_dsn` (dipakai redaksi `/status`).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0019-observability.md`.*
