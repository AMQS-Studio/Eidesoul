> Terjemahan turunan — jangan sunting; sumber: engine/0025-turn-cost.md (sha256: 415ecf3594be)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0025: Biaya Turn — Prefix Stabil, Gating `polish`, dan Anggaran Panggilan

- **Status:** accepted
- **Implementasi:** implemented — bukti: satu baris `llm_calls` per percobaan di `llm.py` (revisi 8), `max_calls_per_turn` dengan notice wrap-up, dua pesan sistem + hash prefix dengan fallback merge, bagian prompt stabil/volatil, `tests/test_cost.py`
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** satu turn memanggil LLM beberapa kali (decision, lingkar balasan, `polish`/`regulate`) dan seluruh blok prompt — termasuk blok yang berubah tiap menit (`self` memuat mode, uptime, lease; ditambah `memory`, `emotion`, `topic`, `currents`) — dirangkai ulang menjadi satu pesan sistem; tidak ada batas jumlah panggilan per turn selain `run_budget_seconds` (240 s).
- **Supersedes:** —
- **Terkait:** ADR-0013 (siklus turn), ADR-0015 (payload bertingkat), ADR-0017 (isolasi & batas waktu), ADR-0019 (observability), ADR-0020 (episode & prosedur), ADR-0021 (kontrak keluaran tool)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0025-turn-cost.md`.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| Semua blok prompt dirangkai menjadi **satu** pesan sistem, ditutup baris bahasa + `Date: ...` | `monoagens/engine/turn/prompts.py:187,355-389` |
| Blok `self` memuat `Mode`, `uptime`, `lease` — berubah tanpa perubahan soul/config | `monoagens/engine/turn/prompts.py:150-186` (`:170`) |
| Blok `topic`/`currents`/`style`/`person_style`/`skills` masuk `COMPETING` dan urutannya bergantung salience | `monoagens/engine/turn/salience.py:20-24,48-71`, `monoagens/engine/turn/prompts.py:373-385` |
| Panggilan LLM per turn: decision, lingkar balasan dengan tool, lalu dua panggilan perapian | `monoagens/engine/agent.py:503,758,810,829` |
| `polish` dan `regulate` tidak punya pemicu: dipanggil setiap turn (kecuali kosong) | `monoagens/engine/character/character.py:122,293` |
| Tells deterministik sudah ada tapi belum dipakai sebagai gerbang | `monoagens/engine/character/character.py:62,105` |
| Penjaga panggilan berulang `repeat >= 3` untuk tool yang sama | `monoagens/engine/llm.py:246-255` |
| Anggaran hari ini = waktu (`run_budget_seconds`), bukan jumlah panggilan | `monoagens/engine/llm.py:160-200`, `monoagens/engine/config.py:79` |
| Afinitas/sesi hanya lewat header provider | `monoagens/engine/llm.py:156,176,187,231`, `monoagens/providers/openai.py:15` |

Konsekuensinya: prefix prompt **berubah setiap turn**, jadi prompt caching provider praktis tidak pernah kena; penambahan panggilan `polish`/`regulate` terjadi bahkan ketika draft sudah bersih atau ketika `llm.run` berakhir error; dan tidak ada plafon biaya yang bisa dijelaskan ke operator.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Biaya dan latensi per turn | Panggilan tambahan harus punya alasan deterministik |
| Cache provider | Prefix stabil byte-identik adalah syarat, bukan optimisasi opsional |
| Kejujuran | Plafon panggilan berhenti dengan notice, bukan potong senyap |
| Tanpa LLM untuk memutuskan LLM | Gating `polish`/`regulate` memakai sinyal deterministik |
| Satu sumber | Token accounting tetap milik ADR-0019 |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Status quo** | Satu pesan sistem, polish/regulate selalu | Ditolak: cache tidak pernah kena, biaya tidak terlihat |
| **B. Serahkan caching ke provider** | Tanpa mengubah urutan blok | Ditolak: prefix tetap berubah tiap turn |
| **C. Dua pesan sistem + pemicu deterministik + plafon panggilan** | Stabil dipisah, biaya dibatasi | **Dipilih** |
| **D. Batching decision + balasan** | Satu panggilan untuk dua tugas | Ditunda: mengubah kualitas keputusan; dicatat sebagai pekerjaan kualitas, bukan biaya |

## 4. Keputusan

### 4.1 Dua pesan sistem: stabil lalu volatil

- **Stabil** (`system[0]`): `base`, `character`, `roles`, `safety`, `person_style`, dan aturan pinned Lapis 1 (ADR-0015). Tidak boleh memuat nilai yang berubah tanpa perubahan soul/roles/config; byte-identik antar turn, hash dicatat.
- **Volatil** (`system[1]`): `self` (mode/uptime/lease/surface/brain), `topic`, `currents`, `memory`, `emotion`, `vibe`, `style`, `skills`, `away`, `actions`, ditambah baris bahasa dan `Date`.
- Urutan tetap deterministik: `system[0] → system[1] → riwayat → pesan pengguna`. `Date` tidak lagi menempel di akhir rangkaian, tetapi tinggal di blok volatil.
- Provider yang tidak menerima dua pesan sistem: keduanya digabung dengan pemisah tetap. Cache hit hilang, determinisme tetap.

### 4.2 Hash prefix

`prompt_prefix_hash` = hash dari `system[0]` + skema tool (urut) + nama tool yang aktif. Ia berubah hanya bila soul/roles/config/tool aktif berubah. Dipakai sebagai metrik dan penanda cache, **bukan** sebagai izin atau syarat kebenaran.

### 4.3 `polish`/`regulate` digerbangi pemicu deterministik

- `polish` dijalankan hanya bila draft memicu pemeriksaan tell (`ASSISTANT_TELL`/`MARKDOWN`, `monoagens/engine/character/character.py:62,105`).
- `regulate` dijalankan hanya bila `polish` mengubah teks.
- Tidak dijalankan bila draft kosong atau bila `llm.run` mengakhiri turn dengan `error:`.
- Total maksimum satu panggilan perapian per turn.

### 4.4 Plafon panggilan per turn

`engine.llm.max_calls_per_turn` (default **6**) menghitung **semua** panggilan LLM dalam satu turn: decision + lingkar balasan + perapian. Bila plafon habis: lingkar tool berhenti, turn ditutup dengan jawaban yang sudah ada + satu `notice:budget` (pola pesan yang sudah ada di `monoagens/engine/llm.py:170-183`), tanpa retry dan tanpa potong senyap. Episode tetap dinilai (ADR-0020).

### 4.5 Token accounting tetap milik ADR-0019

Setiap panggilan sudah dicatat di `llm_calls`; ADR ini hanya menambahkan `turn_id` ↔ `prompt_prefix_hash` supaya biaya bisa dibaca per turn dan per jenis panggilan. Tidak ada tabel baru.

### 4.6 `run_budget_seconds` bukan anggaran turn

Ia tetap batas pekerjaan latar (ADR-0017 §5). Anggaran turn adalah waktu (`turn.timeout_seconds`, ADR-0017 §4.3) + panggilan (§4.4), bukan 240 detik per lingkar.

## 5. Konfigurasi

```yaml
engine:
  llm:
    prompt_cache: true        # susun prefix stabil + kirim header sesi
    max_calls_per_turn: 6     # decision + lingkar balasan + perapian
  character:
    polish: auto              # off | auto (auto = hanya bila pemicu deterministik menyala)
    regulate: auto            # hanya dijalankan bila polish mengubah teks
  prompt:
    prefix_hash: true         # hitung dan catat hash prefix stabil
```

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| `prompt_prefix_changes_total` | 0 selama soul/roles/config/tool aktif tidak berubah |
| `calls_per_turn` p50/p95 | p50 ≤ 3, p95 ≤ 6 |
| `tokens_per_turn` p95 | < 20 000 |
| `polish_skipped_total{alasan}` | terlihat, bukan nol-diam |
| `turn_budget_hit_total` | < 1% turn |
| `prompt_cache_hit_ratio` | tersedia bila provider melaporkan; tanpa klaim bila tidak |
| `time_to_first_send_ms` p95 | < 6 s |

## 7. Acceptance criteria

- [ ] Dua turn berurutan menghasilkan `system[0]` byte-identik meski uptime, mode, dan memori berubah (test hash).
- [ ] `Date`, uptime, dan lease tidak muncul di `system[0]` (test memeriksa isi blok stabil).
- [ ] `polish` tidak dipanggil saat draft kosong atau saat `llm.run` mengembalikan `error:` (test menghitung panggilan LLM).
- [ ] `regulate` hanya dipanggil bila `polish` mengubah teks.
- [ ] `max_calls_per_turn` tercapai → turn ditutup rapi + satu `notice:budget`; tidak ada retry dan tidak ada potong senyap.
- [ ] Setiap panggilan turn tercatat di `llm_calls` dengan `turn_id` dan `prompt_prefix_hash` (ADR-0019).

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Sebagian provider menolak dua pesan sistem | Penggabungan dengan pemisah tetap; yang hilang hanya cache hit |
| Aturan pinned basi karena prefix di-cache | Hash berubah saat aturan berubah → invalidasi otomatis; cache bukan sumber kebenaran |
| `polish: auto` melewatkan perbaikan yang perlu | Pemicu deterministik + metrik `polish_skipped_total` ditinjau lewat `/status` |
| Plafon panggilan memotong tugas panjang | Notice + episode tetap dinilai (ADR-0020); tugas yang perlu lebih banyak panggilan naik ke otomasi (ADR-0014) |
| Determinisme diklaim tanpa bukti | Acceptance menuntut test byte-identik, bukan penilaian manual |

## 9. Interaksi dengan ADR lain

- **ADR-0015**: Lapis 1 pinned adalah isi `system[0]`; Lapis 2 (index + `info`) tetap di blok volatil/alat, bukan di prefix stabil.
- **ADR-0017**: `turn.timeout_seconds` adalah anggaran waktu; `llm.max_retries` mengikuti tabel retry di sana (amandemen §4.6).
- **ADR-0019**: `llm_calls` tetap satu-satunya tempat token dicatat; `prompt_prefix_hash` dikaitkan ke `turn_id`.
- **ADR-0020**: turn yang kena plafon tetap menutup episode dan boleh dinilai; kegagalan biaya bukan kegagalan belajar.
- **ADR-0021**: mode ringkas mengurangi token keluaran tool, yang ikut menentukan `tokens_per_turn`.

## 10. Referensi kode

- `monoagens/engine/turn/prompts.py:187` — `system_prompt` (tempat pemisahan stabil/volatil).
- `monoagens/engine/turn/prompts.py:355-389` — perakitan blok, `salience.select`, join, baris bahasa + `Date`.
- `monoagens/engine/turn/prompts.py:150-186` — `self_block` (mode/uptime/lease; kandidat pertama pindah ke blok volatil).
- `monoagens/engine/turn/prompts.py:141-147` — `roles_block`.
- `monoagens/engine/turn/salience.py:20-24,48-71` — `COMPETING`/`ORDER`/`select`.
- `monoagens/engine/agent.py:503,758,810,829` — decision, lingkar balasan, `polish`, `regulate`.
- `monoagens/engine/character/character.py:62,105,122,293` — tell deterministik dan dua fungsi perapian.
- `monoagens/engine/llm.py:160-200` — lingkar dengan `run_budget_seconds` dan pesan anggaran.
- `monoagens/engine/llm.py:246-255` — penjaga panggilan tool berulang (`repeat >= 3`).
- `monoagens/engine/llm.py:156,176,187,231` — `session_headers` per request.
- `monoagens/providers/openai.py:15` — `session_header="x-opencode-session"` (afinitas provider).
- `monoagens/engine/config.py:69-81,161-163,202-204` — `LLMConfig`, `PromptConfig`, `CharacterConfig`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0025-turn-cost.md`.*
