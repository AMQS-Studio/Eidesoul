> Terjemahan turunan — jangan sunting; sumber: engine/0018-gateway-contract.md (sha256: b296b5a70595)
> Teks ini ditulis manusia; baris yang tidak bisa diterjemahkan mesin disalin apa adanya ke sumber English, jadi sumber bercampur dua bahasa.

# ADR-0018: Kontrak Gateway — Core, Rich, Directory

- **Status:** accepted
- **Implementasi:** implemented — bukti: `GatewayCore`/`Rich`/`Directory`, `send_sticker`, `participants`, boot check + `gateway_capabilities`, tes degradasi, body harness, dan refusal `participants()` terpasang di `chat_history`/`send_to` (menggantikan cek cross-chat lama)
- **Tanggal:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P1-6 — satu Protocol berisi 16 method mencampur transport, direktori, kebijakan, dan data; dua method tidak pernah dipakai engine; setiap gateway wajib menstub semuanya; `send_reaction` sebenarnya mengirim sticker; ADR-0007 butuh `participants(thread_id)` yang tidak ada di kontrak
- **Supersedes:** —
- **Terkait:** ADR-0007 (`rooms_of`, `participants`, fail closed), ADR-0015 (index kapabilitas, `self_handles`, `render`, `prompt_hints`), ADR-0013 (jalur kirim), ADR-0011 (ledger kiriman)
- **Catatan lokasi:** ADR hidup di `docs/v0.1.0/adr/engine/`; salinan Indonesia di `docs/v0.1.0/adr/engine/id/0018-gateway-contract.md`.

---

## 1. Konteks

`inbound.py:31-72` mendefinisikan satu `Protocol` dengan 16 method. Isinya tiga urusan berbeda:

| Kelompok | Method | Dipakai engine? |
|---|---|---|
| Transport | `send_text`, `send_file`, `send_reaction`, `typing`, `prepare_media`, `render` | ya, kecuali `send_reaction` (dipakai `agent.py:354`) |
| Direktori/identitas | `chat_name`, `self_handles`, `resolve_target` | ya (`chat_name` 8×, `self_handles` 3×) |
| Direktori mati | `list_targets`, `data_paths` | **tidak pernah** |
| Kebijakan/topologi | `prompt_hints` | ya (2×) |
| Lifecycle | `run`, `stop`, `tools`, `commands` | ya |

Tiga akibat:

1. **Stub wajib.** `cli/gateway.py:88-140` harus menyediakan keenam belas method; separuhnya `pass` atau `return []`. Gateway baru membayar biaya yang sama untuk kemampuan yang tidak ia punya.
2. **Permukaan mati.** `list_targets` dan `data_paths` tidak dipanggil siapa pun di `eidesoul/` — kontrak yang tidak dipakai tetapi mengikat.
3. **Nama yang berbohong.** `send_reaction` mengirim **sticker** (`whatsapp/gateway.py:348-356` membaca `reaction_stickers`), bukan reaksi ke pesan.
4. **Seam yang belum ada.** ADR-0007 §4.5 mewajibkan `participants(thread_id)` sebagai sumber kebenaran keanggotaan; method itu tidak ada di kontrak sehingga implementasinya akan lahir sebagai atribut liar per gateway.

## 2. Decision drivers

- Gateway adalah **appendage**: transport saja, tidak boleh memaksa kemampuan yang tidak ia punya.
- Kemampuan yang tidak ada harus **gagal terlihat** (degradasi eksplisit), bukan `pass` yang diam-diam.
- Cross-room wajib fail closed: tanpa bukti keanggotaan, baca ditolak (ADR-0007).
- Satu kontrak yang bisa diuji: gateway yang tidak memenuhi Core gagal saat boot, bukan saat pesan pertama.

## 3. Opsi yang dipertimbangkan

| Opsi | Isi | Biaya | Risiko | Verdict |
|---|---|---|---|---|
| **A. Core + Rich + Directory** | tiga protocol; engine mengecek dengan `isinstance` (`runtime_checkable`) | sedang (dua gateway disentuh) | penulis gateway perlu membaca tiga nama | **Dipilih** |
| **B. Satu Protocol + `capabilities: frozenset[str]`** | stringly-typed flag | kecil | tetap wajib menstub; typo flag lolos sampai runtime | Ditolak |
| **C. Minimal** | buang dua method mati, rename satu | kecil | stub wajib dan `participants` tetap tidak punya rumah | Ditolak |

## 4. Keputusan

### 4.1 Tiga protocol

| Protocol | Isi | Wajib? |
|---|---|---|
| `GatewayCore` | `name`, `tools`, `commands`, `run`, `stop`, `send_text`, `render`, `prompt_hints`, `self_handles`, `chat_name` (default: `thread_id`) | **wajib** |
| `Rich` | `send_file`, `send_sticker`, `typing`, `prepare_media` | opsional |
| `Directory` | `resolve_target(query)`, `participants(thread_id) -> set[str] \| None` | opsional |

- `list_targets` dan `data_paths` **dihapus** dari kontrak (tidak ada pemanggil).
- `send_reaction` **diganti nama** menjadi `send_sticker` — satu nama untuk satu perilaku.
- `tools`/`commands` tetap di Core karena engine merakit katalog dari gateway (ADR-0015 §4.2).

### 4.2 Degradasi eksplisit

| Keadaan | Perilaku |
|---|---|
| Gateway tanpa `Rich` | balasan media jatuh ke teks (nama berkas + isi bila teks); `typing` dilewati; sticker tidak dikirim |
| Gateway tanpa `Directory` | nama target tidak bisa diresolusi → soul meminta id; pembacaan lintas room **ditolak** (fail closed) |
| `participants()` mengembalikan `None` | dianggap "tidak diketahui" → tolak lintas room, jangan menebak (ADR-0007) |
| `participants()` mengembalikan himpunan | dipakai apa adanya sebagai bukti keanggotaan penanya |

### 4.3 Pemeriksaan saat boot

- Engine memeriksa setiap gateway yang didaftarkan memenuhi `GatewayCore`; gagal → error boot dengan nama method yang hilang.
- `Rich`/`Directory` **tidak** diperiksa; ketiadaannya sah dan memicu degradasi di atas.
- Hasil pemeriksaan dicatat sebagai `gateway_capabilities` di log boot (ADR-0019) — operator tahu apa yang hilang sebelum pengguna menemukannya.

### 4.4 Jalur kirim tetap satu

- Semua kiriman lewat `send` (ADR-0007 register) atau `send_text` langsung dari agent; `Rich` tidak menambah jalur izin.
- Gerbang primary (ADR-0017 §4.4) berlaku sebelum `send_text`/`send_file`; `Rich` tidak boleh melewatinya.
- Ledger `outbound_messages` (ADR-0013) mencatat semua kiriman, termasuk yang jatuh ke teks karena tidak ada `Rich`.

## 5. Konfigurasi

Tidak ada kunci konfigurasi baru dalam ADR ini. Kapabilitas gateway adalah sifat kode, bukan setelan operator.

## 6. Metrik dan gate

| Metrik | Definisi | Gate |
|---|---|---|
| `gateway_core_ok` | gateway lulus pemeriksaan Core saat boot | 1 untuk setiap gateway terdaftar |
| `rich_missing_total` | balasan media yang jatuh ke teks | terlihat, bukan nol-diam |
| `directory_missing_total` | pembacaan lintas room yang ditolak karena tanpa `Directory` | 0 pada WhatsApp; boleh > 0 pada CLI |
| `participants_unknown_total` | `participants()` mengembalikan `None` saat lintas room | ditolak, tidak pernah ditebak |

## 7. Acceptance criteria

- [ ] Gateway yang hanya memenuhi `GatewayCore` dapat menjalankan turn teks penuh (test dengan gateway palsu).
- [ ] Gateway tanpa `Rich`: balasan media menjadi teks, `typing` dilewati, tidak ada exception.
- [ ] Gateway tanpa `Directory`: lintas room ditolak, bukan ditebak.
- [ ] `participants()` mengembalikan `None` → tolak; mengembalikan himpunan → izin mengikuti himpunan.
- [ ] Gateway yang tidak memenuhi Core gagal saat boot, dengan nama method yang hilang di pesan error.
- [ ] Tidak ada referensi `send_reaction`, `list_targets`, atau `data_paths` tersisa di `eidesoul/engine/` maupun `eidesoul/gateways/`.
- [ ] WhatsApp mengimplementasi `Rich` + `Directory`; CLI mengimplementasi Core (dan `Rich` sebisanya), tanpa stub kosong untuk yang tidak dipakai.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| `isinstance` pada `runtime_checkable` hanya memeriksa nama method, bukan signature | test kontrak memanggil method dengan argumen nyata; signature diperiksa lewat pesan error saat boot |
| Penulis gateway bingung tiga protocol | tabel §4.1 di ADR ini + satu contoh minimal di docstring `inbound.py` |
| Degradasi menyembunyikan fitur yang sebenarnya bisa dipakai | counter `rich_missing_total` dan `directory_missing_total` di `/status` |
| Perubahan kontrak memecah gateway pihak ketiga | kontrak belum dirilis (pra-0.1.0); tidak ada shim (ADR-0014/AGENTS) |

## 9. Interaksi dengan ADR lain

- **ADR-0007** — `participants(thread_id)` menjadi method resmi, dan ketiadaannya otomatis fail closed.
- **ADR-0015** — `self_handles`, `render`, `prompt_hints` tetap Core karena index kapabilitas dan format balasan bergantung padanya.
- **ADR-0013** — jalur kirim dan ledger tidak berubah; hanya permukaan kontrak yang dipersempit.
- **ADR-0017** — gerbang primary berlaku di semua jalur kirim, termasuk `Rich`.
- **ADR-0011** — kegagalan kirim tetap masuk ledger/dead-letter seperti sebelumnya.

## 10. Referensi kode

- `eidesoul/engine/inbound.py:31-72` — Protocol 16 method (sebelum dipecah).
- `eidesoul/engine/agent.py:311,335,354` — pemanggilan `send_text`, `send_file`, `send_reaction`.
- `eidesoul/engine/agent.py:763,977` — `prompt_hints` di payload.
- `eidesoul/gateways/cli/gateway.py:88-140` — stub enam belas method (bukti biaya kontrak gemuk).
- `eidesoul/gateways/whatsapp/gateway.py:348-356` — `send_reaction` yang mengirim sticker.
- `eidesoul/engine/access/permissions.py:48` — `command_allowed` (gerbang command, tidak berubah).

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/0018-gateway-contract.md`.*
