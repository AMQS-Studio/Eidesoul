> Terjemahan turunan — jangan sunting; sumber: engine/0024-tool-input-contract.md (sha256: 0db96db87443)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0024: Kontrak Input Tool — Validasi Argumen, Handler Async, dan `env` Shell

- **Status:** accepted
- **Implementasi:** implemented — bukti: subset keyword skema di `registry` + validasi coroutine, `argument_errors`/urutan nama→izin→argumen→safety di `tools/__init__.py`, whitelist `shell.env` terhitung, `tests/test_tools_contract.py`
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** satu pintu eksekusi tool memeriksa nama, izin, dan safety, tetapi argumen diteruskan apa adanya ke handler; handler sinkron tidak ditolak saat boot; dan `shell` mewarisi seluruh environment proses — termasuk API key dan DSN.
- **Supersedes:** —
- **Terkait:** ADR-0007 (kelas izin, pola sensitif, §4.8 kredensial), ADR-0015 (manifest kapabilitas), ADR-0016 (skema kanonik), ADR-0019 (metrik), ADR-0021 (kontrak keluaran tool)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0024-tool-input-contract.md`.

---

## 1. Konteks

| Fakta | Bukti |
|---|---|
| `execute` menjalankan urutan nama → izin → `fn` kosong → safety/approval → eksekusi; argumen tidak pernah diperiksa | `eidesoul/engine/tools/__init__.py:182-239` |
| Handler dipanggil dengan `await spec.fn(args, ctx)` | `eidesoul/engine/tools/__init__.py:235` |
| Command juga di-`await` | `eidesoul/engine/agent.py:986` |
| Skema argumen hanya diperiksa **bentuknya** saat discovery (`parameters` dict, `type: object`, `properties` objek) | `eidesoul/engine/registry.py:45-53`, `eidesoul/engine/registry.py:104-108` |
| `validate()` memeriksa nama/alias/keluarga/deskripsi, tidak pernah memeriksa jenis `fn` | `eidesoul/engine/registry.py:70-108` |
| `shell` menjalankan `create_subprocess_shell` tanpa `env=` → child mewarisi environment proses | `eidesoul/engine/tools/shell.py:10-26` |
| `sandbox` sudah punya pola env minimal (container + CLI klien) | `eidesoul/engine/tools/sandbox.py:18-20,83-85,155` |
| Tidak ada `inspect.iscoroutinefunction` di seluruh `eidesoul/` | grep repositori |

Akibat hari ini: argumen bertipe salah baru gagal **di tengah handler**, sesudah efek samping mungkin terjadi; skema yang salah bentuk hanya ketahuan saat tool dipakai; dan `shell` bisa membaca `OPENAI_API_KEY`/DSN hanya karena ia ada di environment induk.

## 2. Decision drivers

| Driver | Konsekuensi |
|---|---|
| Determinisme sebelum efek samping | Argumen tidak sah ditolak sebelum guard LLM dan sebelum `fn` |
| Fail closed saat boot | Skema tidak sah dan handler sinkron menggagalkan start, bukan runtime |
| Nol dependency baru | Spirit `AGENTS.md` §Capability spine: dependency hanya untuk hal yang benar-benar perlu |
| Satu pintu | Validasi hanya di `execute()`/dispatch, bukan di dalam 13 handler |
| Rahasia tidak ikut ke child | `shell` menerima environment yang dihitung, bukan diwarisi |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkas | Nilai |
|---|---|---|
| **A. Biarkan** | Validasi diserahkan ke tiap handler | Ditolak: tersebar, mudah lupa, gagal setelah efek samping |
| **B. `jsonschema`** | Validator pihak ketiga | Ditolak: dependency baru untuk subset yang kita pakai sendiri |
| **C. Validator subset stdlib + coroutine check + env whitelist** | Satu tempat, tanpa dependency | **Dipilih** |
| **D. Validasi di handler** | Tiap tool memeriksa sendiri | Ditolak: duplikasi di 13 tempat, tanpa jaminan konsisten |

## 4. Keputusan

### 4.1 Urutan di satu pintu

`nama → izin → argumen → safety/approval → eksekusi`. Argumen tidak sah tidak boleh membuat baris approval dan tidak boleh memanggil classifier guard: keduanya mahal dan tidak berguna untuk input yang tidak akan dieksekusi.

### 4.2 Subset JSON Schema yang didukung

`type`, `properties`, `required`, `enum`, `items`, `minimum`/`maximum`, `minLength`/`maxLength`, `pattern`, `additionalProperties`. Keyword di luar daftar ini **menggagalkan boot**, supaya subset ini benar-benar berlaku dan tidak ada skema yang diam-diam diabaikan.

### 4.3 `additionalProperties` absen = `false`

Deviasi sadar dari JSON Schema: kunci asing ditolak secara default; handler yang memang menerima kunci bebas menulis `additionalProperties: true`. Alasannya jalur nyata — `shell` menerima `command` dan `timeout`, dan kunci tambahan yang tak dikenal adalah penyelundupan argumen.

### 4.4 Bentuk hasil validasi

`error: invalid arguments for <tool>: <field> (<alasan>)` — deterministik, tanpa LLM, dipotong `MAX_ERROR` (500 karakter), menyebut maksimum `tools.max_errors` field. `ctx.used` tidak ditulis untuk panggilan yang ditolak.

### 4.5 Handler wajib coroutine

`validate()` menolak `fn` non-coroutine untuk tool **dan** command; pesan menyebut berkas (`<source>.py: fn must be async`). Alasan: kedua pemanggil memakai `await` (`eidesoul/engine/tools/__init__.py:235`, `eidesoul/engine/agent.py:986`), jadi handler sinkron adalah bug yang lebih murah ditemukan saat boot.

### 4.6 `env` shell dihitung, bukan diwarisi

`shell` memakai whitelist `engine.shell.env` (default `PATH, HOME, LANG, TZ, XDG_RUNTIME_DIR, TMPDIR, TERM`) mengikuti pola `client_env()` di `eidesoul/engine/tools/sandbox.py:83-85`. Wildcard `*` ditolak saat boot; isi `.env` tidak pernah diteruskan ke child; variabel di luar whitelist tidak ikut meski ada di environment proses. `sandbox` tidak berubah.

### 4.7 Batas dengan kredensial

ADR ini menutup **jalur env**. Membaca rahasia dari berkas (`printenv`, `.env`, `/proc/*/environ`, kunci SSH) bukan urusan ADR ini, melainkan kelas pola di ADR-0007 §4.8.

## 5. Konfigurasi

```yaml
engine:
  tools:
    args: strict               # strict: validasi §4.2 + additionalProperties default false
    max_errors: 5              # jumlah field yang disebut dalam satu pesan error
  shell:
    env: [PATH, HOME, LANG, TZ, XDG_RUNTIME_DIR, TMPDIR, TERM]
```

Tidak ada `tools.args: off`: mematikan validasi berarti membuka kembali lubang yang ditutup ADR ini.

## 6. Metrik dan gate

| Metrik | Target awal |
|---|---|
| `tool_args_rejected_total{tool}` | ada, per tool |
| `tool_schema_invalid_total` (boot) | 0 — boot gagal bila > 0 |
| `handler_sync_rejected_total` (boot) | 0 — boot gagal bila > 0 |
| `shell_env_names` | = panjang whitelist; tidak pernah memuat `*` |
| `tool_input_error_ratio` | < 2% dari panggilan tool |

## 7. Acceptance criteria

- [ ] Argumen bertipe salah → `error:` sebelum `fn` dipanggil (test: spy membuktikan `fn` tidak berjalan).
- [ ] Kunci asing ditolak; handler dengan `additionalProperties: true` menerimanya.
- [ ] Keyword di luar §4.2 di dalam `parameters` → boot gagal dengan pesan menyebut berkas.
- [ ] Handler sinkron pada tool **dan** command → boot gagal.
- [ ] `shell` tidak melihat `OPENAI_API_KEY` meski variabel itu ada di environment proses (test end-to-end).
- [ ] `engine.shell.env: ["*"]` → boot gagal.
- [ ] Pesan error ≤ 500 karakter dan menyebut nama tool serta field.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Subset menyimpang dari JSON Schema | Keyword asing ditolak saat boot; §4.2 adalah kontraknya |
| Skema lama terlalu longgar | `_schema_errors` sudah menuntut `type: object` + `properties`; tiap skema diaudit saat cutover |
| Whitelist memutus tool yang butuh variabel lain | Operator menambah nama; wildcard tetap ditolak |
| Validasi menambah latensi | Hanya penelusuran dict; tanpa dependency, tanpa I/O |
| Penolakan argumen membingungkan model | Pesan menyebut field dan alasan; model bisa memperbaiki argumen dalam satu iterasi |

## 9. Interaksi dengan ADR lain

- **ADR-0007**: satu pintu izin tidak berubah; §4.8 menambahkan kelas pola kredensial yang melengkapi §4.6 di sini.
- **ADR-0021**: kontrak **keluaran** tool; ADR ini kontrak **masukan**. Keduanya memakai `Handler` yang sama dan tidak boleh punya dua sumber skema.
- **ADR-0015**: manifest menampilkan `parameters` apa adanya; validator memakai field yang sama, jadi tidak ada skema kedua.
- **ADR-0016**: tidak ada perubahan skema database.
- **ADR-0019**: metrik baru lewat counter in-process, tanpa tabel baru.

## 10. Referensi kode

- `eidesoul/engine/tools/__init__.py:182-239` — `execute`, satu pintu (tempat validasi argumen disisipkan); `:235` `await spec.fn`.
- `eidesoul/engine/tools/__init__.py:17-19` — `MAX_OUTPUT`/`MAX_ERROR`/`TRUNCATED`.
- `eidesoul/engine/registry.py:45-53` — `_schema_errors` (bentuk skema).
- `eidesoul/engine/registry.py:70-108` — `validate()` (tempat pemeriksaan coroutine ditambahkan).
- `eidesoul/engine/registry.py:148-182` — `discover`/`discover_many`.
- `eidesoul/engine/registry.py:31` — `Handler.parameters`.
- `eidesoul/engine/agent.py:986` — dispatch command `await handler.fn(ctx)`.
- `eidesoul/engine/tools/shell.py:10-26` — `create_subprocess_shell` tanpa `env`.
- `eidesoul/engine/tools/sandbox.py:18-20,83-85,155` — pola whitelist env yang dipakai ulang.
- `eidesoul/engine/config.py:98-108` — `SandboxConfig`, acuan bentuk blok config `shell`.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0024-tool-input-contract.md`.*
