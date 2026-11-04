> Terjemahan turunan — jangan sunting; sumber: engine/0034-prompt-parts.md (sha256: fbda400d8cba)
> Ditulis tangan (bukan mesin), mengikuti sumber English baris demi baris.

# ADR-0034: Prompt Parts — Satu Registry untuk Metadata Blok, Bukan Kontrak Tool Kedua

- **Status:** accepted
- **Tanggal:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** lanjutan ADR-0015 (anggaran prompt dan indeks kapabilitas), ADR-0025 (prefix stabil dan cache prompt), ADR-0027/0028 (unit plugin dan kapabilitas clean-by-default), ADR-0033 (core netral dan `prompt_section`)
- **Supersedes:** —
- **Terkait:** ADR-0031 (tata letak modul dan anggaran pertumbuhan), ADR-0024 (kontrak input tool), ADR-0018 (seam gateway)
- **Implementasi:** partial — `PromptPart`/`PromptContext`/`CORE_PARTS`, salience metadata, dan registrasi prompt plugin ada di `turn/prompts.py`, `turn/salience.py`, dan `turn/plugin_registration.py`; test mencakup kemurnian stable, prompt plugin, dan kontrak prompt. Scan tree plugin di neutral gate belum ditambahkan.
- **Catatan lokasi:** keputusan engine (ia memiliki perakitan prompt dan permukaan ekstensi untuk blok), jadi ia duduk di bawah `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0034-prompt-parts.md`.

---

## 1. Konteks

`system_prompt()` sudah menerima string yang sudah dirender dan hanya merakitnya (`turn/prompts.py:244`). Sisi produsen juga sama jujurnya: store mengembalikan teks (`AffectStore.prompt`, `StyleStore.profile`, konteks memori), dan agent menyerahkannya. Yang tidak dimiliki perakitan adalah satu deskripsi untuk satu blok. Diukur pada 2026-11-04:

- Ada enam belas blok. Lima blok stabil dibangun di `stable_blocks` (`prompts.py:400`): `base`, `character`, `roles`, `safety`, `person_style`. Sebelas blok volatil dibangun di `volatile_blocks` (`prompts.py:407`): `self`, `topic`, `away`, `emotion`, `vibe`, `style`, `research`, `memory`, `actions`, `skills`, `capabilities`.
- Enam belas nama yang sama muncul kedua kali di `turn/salience.py`: `PINNED` (`:11`), `MANDATORY` (alias historis, `:12`), `COMPETING` (`:13`), dan `ORDER` (`:26`). `select()` mengindeks `ORDER` (`:87`) dan memfilter dengan keanggotaan di `PINNED`/`COMPETING` (`:75`, `:90`).
- Agent membawa salinan ketiga secara implisit: dua belas argumen keyword bernama di call site (`agent.py:1445-1464`) dan panggilan produsen yang mengisinya (`agent.py:1368-1456`).
- Menambah satu blok karena itu menyentuh tiga daftar di `salience.py`, signature dan kedua kamus di `prompts.py`, call site di `agent.py`, dan `tests/test_salience.py` (yang mengimpor `COMPETING`, `MANDATORY`, dan `ORDER` secara langsung). Menghapus satu blok adalah suntingan yang sama terbalik.
- Gateway sudah menyumbang teks lewat `prompt_section()` (ADR-0033 §4.5), dibatasi 1200 karakter (`inbound.py:220`). Plugin tidak punya permukaan prompt sama sekali: `register()`-nya hanya mengembalikan tool dan command (`plugins.py:505-520`), jadi kapabilitas yang butuh instruksi model-facing harus menyunting core.
- Pemisahan itu ada alasannya: `Prompt.stable` dan `Prompt.volatile` dikirim sebagai dua pesan system (`llm.py:232-237`, ADR-0025), sehingga prefix stabil di-cache dan separuh volatil berubah per turn. Blok mana masuk mana adalah keputusan cache, bukan keputusan format.
- Strukturnya bekerja dan teruji (`tests/test_prompt_contract.py` mem-pin properti per body dan hash prefix stabil). Masalahnya adalah jumlah tempat yang harus sepakat soal satu nama, dan tidak adanya titik ekstensi.

## 2. Decision drivers

1. **Satu pemilik per fakta.** Nama, kestabilan, urutan, dan bobot salience sebuah blok hidup di satu entri, bukan di tiga berkas.
2. **Pemisahan cache tetap eksplisit.** `stable` adalah field yang dideklarasikan; perakit tidak pernah menebaknya dari nama atau dari asal teks.
3. **Urutan deterministik.** Hash prefix tidak boleh bergantung pada urutan import atau urutan sisip dictionary.
4. **Ekstensi menambah data, bukan suntingan core** (ADR-0027, ADR-0028, ADR-0033). Blok prompt plugin dideklarasikan dan diverifikasi; ia tidak pernah menyunting `prompts.py`.
5. **Blok prompt bukan tool.** Tidak ada cek permission, tidak ada entri katalog untuk user, tidak ada panggilan async, tidak ada `ToolContext`.
6. **Gagal tertutup.** Nama duplikat, urutan duplikat, deklarasi plugin yang tidak dikenal, dan teks di luar batas adalah kegagalan boot dengan nama yang disebut.
7. **`prompts.py` tetap murni.** Ia tidak boleh mengimpor store, agent, atau gateway; konteks adalah data yang diserahkan.

## 3. Opsi yang dipertimbangkan

**A. Pertahankan kamus string dan daftar nama.** Ditolak: bukti di §1 adalah biayanya, dan setiap plugin atau blok body baru harus menyunting core atau tidak mendapat tempat.

**B. Jadikan `prompts.py` registry `Handler`.** Ditolak: `Handler` adalah kontrak tool/command — `fn(args, ctx)` async, parameter JSON, permission role, `execute()`, dan katalog yang terlihat user (`registry.py`). Blok prompt tidak memiliki satu pun dari itu; memaksakannya lewat `execute` menciptakan permission tak bermakna dan folder discovery kedua. Ia juga menaruh teks blok di belakang jalur approval, yang salah secara konstruksi.

**C. Store mendorong teksnya ke `prompts.py`.** Ditolak: ia membalik dependensi (`turn/` akan mengimpor `stores/`), menjadikan tipe blok registry global yang bisa berubah, dan memindahkan keputusan stabil/volatil ke store — satu-satunya tempat yang tidak bisa tahu kontrak cache.

**D. Registry metadata `PromptPart` dengan konteks eksplisit (dipilih).** Registry memiliki metadata dan produsen; agent membangun satu konteks per turn; salience membaca field, bukan nama.

**E. Prompt plugin lewat `prompt_section` saja.** Ditolak sebagai jawaban tunggal: `prompt_section` adalah fakta transport body dan terikat pada instance gateway. Blok plugin adalah instruksi kapabilitas tanpa transport; ia butuh permukaan statis dan terdeklarasi sendiri.

## 4. Keputusan

### 4.1 Satu blok adalah satu `PromptPart`

```python
@dataclass(frozen=True)
class PromptPart:
    name: str                 # ^[a-z][a-z0-9_]{0,63}$, unik di daftar yang dirakit
    stable: bool              # masuk prefix yang di-cache (ADR-0025)
    order: int                # unik; blok stabil diurutkan sebelum blok volatil
    bonus: float = 0.0        # bobot salience, ADR-0015
    pinned: bool = False      # tidak pernah dibuang oleh anggaran
    produce: Callable[[PromptContext], str] | None = None
    text: str = ""            # khusus bagian ekstensi: teks statis, tanpa callable
```

`produce` dan `text` saling eksklusif: blok core punya produsen; blok ekstensi punya teks literal. Blok yang mengembalikan `""` dibuang sebelum seleksi, persis seperti string kosong dibuang hari ini.

### 4.2 `PromptContext` adalah dependensi data yang eksplisit

`PromptContext` adalah dataclass beku yang field-nya adalah gabungan input semua blok: `config`, `soul`, `roles`, `runtime`, `query`, `thread_id`, `person_id`, `direct`, `body_section`, `language`, `limits`, `patterns`, `facts`, `affect`, `style`, `vibe`, `topic`, `away`, `memory`, `actions`, `skills`, `tools`, `commands`, `bodies`. Ia dibangun sekali per turn oleh agent dan tidak pernah berubah.

Blok baru yang butuh input baru menambah satu field di `PromptContext` dan satu baris di lokasi konstruksi agent. Registry tidak menyembunyikan dependensi data; ia hanya menghapus duplikasi nama.

Produsen bersifat sinkron dan bebas efek samping. Engine tidak pernah menunggu (`await`) sebuah blok.

### 4.3 Registry hidup di `turn/prompts.py`

`CORE_PARTS: tuple[PromptPart, ...]` duduk di samping `system_prompt()` di modul yang sama (444 baris hari ini; registry dan helper tetap jauh di bawah cap 800, jadi tidak ada modul baru dan pohon ADR-0031 tidak berubah). Daftar yang dirakit bersifat per agent: `CORE_PARTS + plugin_parts`, dengan kontribusi body sebagai blok core bernama `body`.

`system_prompt(config, roles, *, context, parts=...)` mengembalikan tipe `Prompt` yang sama:

- `stable` = gabungan blok `stable=True`, dalam `order`;
- `volatile` = gabungan blok `stable=False` yang lolos seleksi, dalam `order`, diikuti tail tetap (`language_line`, `Date` dari `soul.timezone`) yang harus selalu terakhir;
- blok stabil tidak pernah dipilih keluar dan tidak pernah dipindah setelah blok volatil.

### 4.4 Salience membaca metadata, bukan nama

`salience.select(parts, query, ...)` menerima daftar `(PromptPart, text)` dan membuang blok non-pinned terlemah sampai anggaran muat. `PINNED`, `MANDATORY`, `COMPETING`, dan `ORDER` dihapus. `MANDATORY` tidak bertahan sebagai alias; kontraknya adalah field `pinned`.

### 4.5 Ekstensi: plugin mendeklarasikan, tidak menyunting core

Plugin menyumbang blok prompt statis lewat registrasi yang sudah ada:

- `plugin.yaml` mendeklarasikan `provides.prompt: ["<slug>", ...]`;
- `register()` mengembalikan `{"tools": [...], "commands": [...], "prompts": [PromptPart, ...]}`;
- blok ekstensi wajib `stable=True`, `produce=None`, `text` tidak kosong, `len(text) <= 1200`, dan `name` ber-namespace `plugin:<plugin>:<slug>`; `order` diberikan loader setelah blok core, sehingga plugin tidak bisa mengurutkan ulang blok core;
- teks bersifat literal: tanpa placeholder format, tanpa data user, tanpa secret, tanpa interpolasi runtime. Tidak ada callable, jadi tidak ada konteks yang bisa dibaca;
- tabrakan, teks kosong, teks melebihi batas, callable pada blok ekstensi, atau nama di luar `provides.prompt` adalah `PluginError` sebelum plugin masuk jalur turn.

Gateway tidak mendaftarkan blok tambahan. Satu kontribusinya tetap `prompt_section()` (ADR-0033 §4.5), dimodelkan sebagai blok `body`: `stable=True`, `pinned=True`, `order` tepat setelah `base`, dibatasi 1200 karakter. Satu kontribusi body, satu jalur.

### 4.6 Cek boot

Registry menolak, dengan menyebut bloknya: `name` duplikat, `order` duplikat, nama tidak valid, blok core yang punya `produce` dan `text` sekaligus atau tidak punya keduanya, blok ekstensi yang tidak statis, dan blok ekstensi yang namanya tidak ada di `provides.prompt`. Error dikumpulkan; pesan pertama bukan satu-satunya.

### 4.7 Yang sengaja tidak diubah

Produsen tetap di tempatnya: `AffectStore.prompt`, `StyleStore.profile`, `self_block`, `roles_block`, `_capability_index`, dan renderer memori mempertahankan signature-nya. Agent tetap mengambil input per turn, karena hanya agent yang memegang turn. Registry menghapus tiga daftar nama dan signature enam belas argumen; ia tidak memindahkan logika store ke `turn/`.

## 5. Konfigurasi

Tidak ada key engine baru. Satu-satunya data baru adalah key manifest plugin `provides.prompt` (mapping `provides` ADR-0028 sudah menerima daftar bernama setelah ADR-0033 §4.6; ADR ini menetapkan maknanya). Batas ekstensi adalah satu konstanta `PROMPT_PART_MAX = 1200`, nilai yang sama dengan `PROMPT_SECTION_MAX`.

## 6. Metrik dan gate

Hitungan yang harus didorong ke nol, diukur 2026-11-04:

| Spesialisasi | Jumlah | Di mana |
| --- | --- | --- |
| daftar nama blok | 3 | `PINNED`/`MANDATORY`/`COMPETING`/`ORDER` di `turn/salience.py` |
| blok tanpa `PromptPart` | 16 | `stable_blocks` + `volatile_blocks` di `turn/prompts.py` |
| literal nama blok di signature `system_prompt` | 12 | `agent.py:1445-1464` |
| permukaan prompt plugin | 0 | `register()` hanya mengembalikan tool/command |

Gate-nya:

1. `turn/salience.py` tidak memuat nama blok dan tidak memuat simbol `PINNED|MANDATORY|COMPETING|ORDER`; `tests/test_salience.py` membangun kasusnya dari fixture `PromptPart`.
2. Setiap blok core punya tepat satu `PromptPart`; nama di `CORE_PARTS` sama dengan himpunan yang diharapkan tes, tanpa nama atau `order` duplikat.
3. Blok stabil menghasilkan teks yang identik byte demi byte untuk dua konteks per turn yang berbeda (tanggal, pesan, memori berbeda) — cek berjalan di `tests/test_prompt_parts.py`; blok stabil yang membaca data per turn menggagalkan suite.
4. `bodies` punya tepat satu blok (`body`), ia satu-satunya blok `pinned` yang teksnya berasal dari `prompt_section`, dan dibatasi 1200.
5. Blok ekstensi bersifat statis, ber-namespace, di bawah batas, dan tidak ada di registry core; nama duplikat atau tidak dikenal ditolak.
6. `tests/test_prompt_contract.py` tetap mem-pin properti per body dan hash prefix stabil yang dinormalisasi; hash hanya berubah ketika teks atau urutan blok stabil berubah secara sengaja.
7. `scripts/neutral.py --check` juga menolak pohon prompt plugin yang memuat huruf non-ASCII atau kata budaya terlarang, memakai cakupan yang sama seperti core (pohon plugin dipindai lewat manifest-nya).

## 7. Kriteria penerimaan

- Menambah blok adalah satu entri `PromptPart` plus produsennya; tidak ada berkas lain yang berubah. Menghapus blok adalah kebalikannya.
- `turn/salience.py` hanya memuat algoritma seleksi; tidak ada nama blok.
- `system_prompt` menerima konteks dan daftar part; enam belas argumen keyword hilang.
- Prefix stabil hanya dirakit dari blok `stable=True`, dalam `order`, dan `prompt_prefix_hash` hanya berubah bersama blok-blok itu.
- Plugin bisa menambah satu blok instruksi statis lewat `provides.prompt` dan `register()["prompts"]`, tanpa suntingan core, dan blok itu muncul di prompt setelah blok core.
- Plugin yang mendeklarasikan callable, nama duplikat, atau 1201 karakter gagal registrasi dengan satu error bernama.
- Jalur gateway tidak berubah: `prompt_section()` tetap satu-satunya kontribusi body, dibatasi dan statis.
- `pytest -q`, gate netral, dan suite kontrak prompt tetap hijau.

## 8. Risiko dan mitigasi

- **Blok stabil membaca data per turn dan merusak cache.** Mitigasi: cek kesetaraan dua konteks di suite; `stable` dideklarasikan, tidak pernah disimpulkan.
- **Registry global bocor antar agent dalam satu proses.** Mitigasi: daftar yang dirakit bersifat per agent; `CORE_PARTS` adalah data modul yang immutable dan blok ekstensi disalin saat boot.
- **Plugin memakai prompt sebagai permukaan injeksi.** Mitigasi: blok ekstensi adalah teks literal tanpa callable dan tanpa konteks, dibatasi, English-only lewat gate netral, ber-namespace, dan dideklarasikan di manifest sebelum registrasi.
- **Perubahan urutan merusak semua cache.** Mitigasi: `order` adalah data di satu berkas; mengubah blok stabil atau urutannya adalah diff yang disengaja, dicatat oleh hash kontrak prompt.
- **Registry menjadi god object.** Mitigasi: konteks adalah dataclass beku dengan field bernama; blok yang butuh input baru membuat kebutuhan itu terlihat sebagai field baru dan baris agent baru, bukan lookup tersembunyi.
- **Dua jalur ekstensi (section body dan part plugin) membingungkan penulis.** Mitigasi: pemisahannya berdasarkan sifat — fakta transport milik body; instruksi kapabilitas milik plugin; keduanya tidak bisa memakai jalur yang lain.

## 9. Interaksi dengan ADR lain

- ADR-0025: pemisahan stabil/volatil dan hash prefix adalah alasan `stable` dan `order` menjadi field.
- ADR-0015: anggaran dan indeks kapabilitas tetap; salience kini membaca `pinned`/`bonus` dari metadata.
- ADR-0027/0028: plugin tetap memiliki kode dan manifest-nya; `provides.prompt` adalah data, diverifikasi seperti manifest lainnya, dan digest mencakup produsen teks (tidak ada — teksnya data di pohon kode).
- ADR-0031: tidak ada modul baru dan tidak ada berkas top-level baru; `prompts.py` tetap punya satu alasan berubah (perakitan) dan registry tetap di bawah cap pertumbuhan.
- ADR-0033 §4.5: `prompt_section` bertahan tanpa perubahan dan dimodelkan sebagai blok `body`; gateway tidak mendapat permukaan kedua.
- ADR-0024: blok prompt bukan handler dan tidak pernah masuk `execute` atau validator argumen.

## 10. Referensi kode

- `eidesoul/engine/turn/prompts.py:16,45,244,400,407,428` — `Prompt`, `_capability_index`, `system_prompt`, dua kamus blok, dan tail volatil tetap.
- `eidesoul/engine/turn/salience.py:11,12,13,26,53,68,75,87,90` — daftar nama dan setiap penggunaan keanggotaan/indeks.
- `eidesoul/engine/agent.py:1101-1107,1184,1368-1372,1445-1464` — produsen emosi/vibe, panggilan decision, `character_block`, dan panggilan perakitan enam belas argumen.
- `eidesoul/engine/llm.py:232-237` — dua pesan system yang menjadikan pemisahan sebagai keputusan cache.
- `eidesoul/engine/inbound.py:220` — `prompt_section`, batasnya, dan kontribusi body.
- `eidesoul/engine/plugins.py:261-268,505-520` — parsing `provides` dan `register()`; titik ekstensi yang didefinisikan ADR ini.
- `tests/test_salience.py:5,18,44,83-84` — tes yang mengimpor daftar nama dan akan dibangun ulang dari fixture.
- `tests/test_prompt_contract.py`, `tests/data/prompt_cases.yaml` — kontrak properti per body dan hash stabil yang harus tetap lolos.

---

*ADR ini disimpan di `docs/v0.1.0/adr/engine/id/0034-prompt-parts.md` sebagai salinan; sumber English di `docs/v0.1.0/adr/engine/0034-prompt-parts.md`.*
