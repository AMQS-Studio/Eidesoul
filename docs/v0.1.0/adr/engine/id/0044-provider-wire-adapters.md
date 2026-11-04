> Terjemahan turunan — jangan sunting; sumber: engine/0044-provider-wire-adapters.md (sha256: e98a1ece66ed)

# ADR-0044: Adapter Wire Provider — Gemini Native Lebih Dulu, Satu Seam

- **Status:** accepted
- **Tanggal:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0027 (clean by default), ADR-0031 (struktur modul), ADR-0033 (core netral), dan ADR-0041 (konfigurasi voice); menggantikan asumsi bahwa setiap provider harus berbicara wire OpenAI dengan batas adapter protokol.
- **Supersedes:** —
- **Terkait:** ADR-0027 (distribusi), ADR-0031 (struktur modul), ADR-0033 (core netral), ADR-0041 (konfigurasi voice), ADR-0045 (sampling mode)
- **Implementasi:** implemented — Gemini native `generateContent`, response ternormalisasi, sampling/safety bergerbang capability, dan fixture test sudah live; Anthropic native tetap ditunda oleh ADR ini.
- **Catatan lokasi:** seam provider mengikat loop LLM engine, jadi ADR ini duduk di `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0044-provider-wire-adapters.md`.

---

## 1. Konteks

Engine sekarang punya satu jalur client: `LLM.__init__` membangun `AsyncOpenAI`, dan setiap request
lewat `client.chat.completions.create`. Paket provider hanya menyimpan fakta endpoint dan auth. Itu
bekerja untuk wire OpenAI, tetapi menyembunyikan kontrol native:

| Permukaan | Perilaku saat ini | Kesenjangan |
| --- | --- | --- |
| Gemini | shim kompatibilitas OpenAI dari Google | tanpa `safetySettings`, tanpa `topK`, tanpa alasan blok native |
| Anthropic | lapisan kompatibilitas OpenAI-SDK Anthropic | tanpa prompt caching, tanpa ringkasan thinking, semantik tool terbatas |
| Provider OpenAI-compatible | wire OpenAI native | sudah benar; tidak perlu client native |
| Sampling | hanya `temperature` | `top_p`, `top_k`, dan `min_p` belum bisa dikonfigurasi |
| Safety | tidak ada switch request-level | moderasi provider tidak bisa dialamatkan engine |
| Error | bentuk exception OpenAI | hasil blok dan safety native tidak terwakili |

Arahan owner: kontrol provider harus eksplisit. Native bila API native membuka knob yang dibutuhkan
engine, dan satu wire protocol, bukan satu client per merek. Permintaannya bukan menulis loop terpisah
untuk setiap provider; permintaannya adalah berhenti menyembunyikan kemampuan native di balik shim.

Engine tetap netral terhadap provider. Nama provider dipilih di `engine.llm.provider`; engine tidak
pernah bercabang pada nama itu. Adapter dipilih dari wire protocol yang tercatat di provider record,
dan adapter hanya memiliki terjemahan request/response.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Kontrol native saat dibutuhkan | Gemini bisa menerima `safetySettings`, `topK`, dan alasan blok native |
| Satu loop turn | Setiap adapter menormalkan ke bentuk teks, tool call, usage, dan finish yang sama |
| Satu wire family, bukan satu merek | Provider OpenAI-compatible tetap memakai client yang ada |
| Tanpa cabang nama provider di core | `llm.py` meminta capability dari adapter; tidak membandingkan nama |
| Config generik | Sampling dan safety adalah field engine; nilai yang tidak didukung gagal saat load |
| Tanpa drop diam-diam | Opsi yang tidak bisa dikirim adapter adalah error konfigurasi, bukan diabaikan |
| Tanpa loop baru | Retry, deadline, budget, checkpoint, dan metrik tetap di `llm.py` |
| Bisa diuji tanpa network | Fixture request dan response mencakup setiap adapter |
| Permukaan dependensi kecil | Adapter native memakai stack HTTP yang ada; tidak perlu SDK provider |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan semua provider di shim OpenAI** | jalur saat ini | Ditolak: safety Gemini dan finish reason native tetap tidak terjangkau |
| **B. Client native per merek** | SDK Google, Anthropic, OpenAI, loop terpisah | Ditolak: menduplikasi tool, retry, deadline, dan accounting |
| **C. Adapter per wire protocol** | wire OpenAI, Gemini `generateContent`, Anthropic Messages | **Dipilih** |
| **D. Native untuk setiap provider** | satu client per provider meski wire-nya sama | Ditolak: biaya perawatan tanpa kontrol tambahan |
| **E. Proxy atau sidecar** | proses terpisah menerjemahkan API native | Ditolak: komponen runtime baru hanya untuk pemetaan request/response |
| **F. Opsi provider bebas** | kirim mapping arbitrer ke provider | Ditolak: tanpa validasi, tanpa batas capability, dan konvensi config kedua |

## 4. Keputusan

### 4.1 Tiga wire family

Provider record mendapat nilai `wire`:

| Wire | Provider | Adapter native |
| --- | --- | --- |
| `openai` | OpenAI, OpenRouter, Groq, DeepSeek, Ollama, OpenCode, custom | tidak berubah; `AsyncOpenAI` |
| `gemini` | Google Gemini | **adapter native pertama** |
| `anthropic` | Anthropic Claude | adapter native saat fiturnya dibutuhkan |

Nilai wire adalah data di provider record. Itu bukan cabang di `llm.py` pada nama provider.

### 4.2 Batas adapter

Adapter hanya memiliki:

- konstruksi payload request untuk satu panggilan;
- normalisasi response ke teks, tool call, usage, dan finish reason;
- laporan capability untuk opsi yang bisa dikirim;
- klasifikasi error native yang bisa dipetakan loop LLM.

Adapter tidak memiliki retry, deadline turn, budget panggilan, checkpoint, penulisan `llm_calls`, atau
registry tool. Semua itu tetap di loop LLM yang ada.

Response ternormalisasi adalah nilai internal dengan setidaknya:

| Field | Arti |
| --- | --- |
| `text` | teks assistant, kosong bila response hanya tool call |
| `tool_calls` | panggilan ternormalisasi dengan nama, argumen, dan id |
| `usage` | jumlah token input dan output bila provider melaporkannya |
| `finish_reason` | nilai ternormalisasi dan nilai native |
| `blocked` | true bila provider menolak atau tidak mengembalikan kandidat |

Response yang diblokir adalah hasil kelas satu, bukan exception dengan string. Turn bisa melaporkan
penolakan provider tanpa berpura-pura tool call atau jawaban terjadi.

### 4.3 Gemini native

Adapter Gemini memakai `generateContent`, bukan endpoint kompatibilitas OpenAI:

- bentuk endpoint: `v1beta/models/{model}:generateContent`;
- auth: variabel key yang ada di header `x-goog-api-key`;
- pesan: `contents` dengan role dan parts;
- system prompt: `systemInstruction`;
- tool: `functionDeclarations` dan hasil `functionCall`;
- sampling: `generationConfig.temperature`, `topP`, `topK`, dan max output token;
- safety: `safetySettings` dengan kategori harm yang bisa diatur saat engine meminta `off`;
- response: kandidat, finish reason, usage metadata, dan alasan blok.

Safety `off` bukan janji model akan patuh. Itu menghapus ambang API untuk kategori yang dikirim
adapter; perilaku model dan ketentuan provider tetap berlaku. Penolakan provider tetap error yang
terlihat. Gemini mendokumentasikan bahwa filter yang bisa diatur default-nya off untuk Gemini 2.5 dan
setelahnya, sementara core harm seperti child safety selalu diblokir dan tidak bisa diatur.

Provider lain tidak mengekspos kontrol request-level yang sama:

| Provider | Safety request-level | Yang ditambah adapter native |
| --- | --- | --- |
| OpenAI | tidak ada threshold di Chat Completions; moderation adalah classifier terpisah atau skor inline | Responses API dan semantik tool native, bukan tombol off |
| Anthropic | tidak ada threshold; refusal adalah `stop_reason: refusal` | caching, thinking, tool native, bukan tombol off |
| OpenRouter | guardrail hanya menambah aturan block/redact dan deteksi prompt injection | routing dan kontrol organisasi, bukan cara mematikan kebijakan provider upstream |
| Groq | model moderation adalah panggilan terpisah | tidak ada threshold safety request-level |
| DeepSeek | permukaan Chat Completions publik tidak mendokumentasikan threshold safety | tidak ada threshold safety request-level |
| Ollama | inferensi lokal; tidak ada lapisan moderasi API | opsi sampling native dan kontrol lokal, bukan filter provider |
| Gemini | kategori dan threshold `safetySettings` | satu-satunya adapter potongan pertama yang membawa threshold safety |

API native tidak otomatis layak dibuat adapter. Ia layak hanya bila mengekspos kontrol yang dibutuhkan
engine, atau bila lapisan kompatibilitas menghalangi fitur yang dibutuhkan.

### 4.4 Anthropic native, ditunda

Adapter Anthropic bukan bagian dari potongan pertama. Lapisan kompatibilitas tetap sampai owner
membutuhkan prompt caching native, ringkasan thinking, atau semantik tool native. Saat itu terjadi, ia
mengikuti batas adapter yang sama: Messages API, system top-level, tool `input_schema`, blok `tool_use`
dan `tool_result`, dan accounting usage native.

### 4.5 Provider compatible tetap di wire OpenAI

OpenRouter, Groq, DeepSeek, Ollama, OpenCode, dan endpoint custom tetap memakai client OpenAI. Client
native untuk provider ini tidak menambah kontrol yang dibutuhkan engine, dan jalur kompatibilitas sudah
membawa tool serta bentuk pesan yang kompatibel.

### 4.6 Config sampling dan safety generik

Engine mengekspos field generik; adapter memvalidasi apa yang bisa dikirim. Batasnya mengikuti
pemisahan soul/engine:

| Key | Default | Arti |
| --- | --- | --- |
| `soul.voice.sampling.daily_temperature` | `0.9` | temperature chat daily |
| `soul.voice.sampling.professional_temperature` | `0.3` | temperature mode task |
| `soul.voice.sampling.top_p` | kosong | tidak diisi berarti default provider |
| `soul.voice.sampling.top_k` | kosong | hanya Gemini di potongan pertama |
| `soul.voice.sampling.min_p` | kosong | hanya bila adapter melaporkan dukungan |
| `engine.llm.utility_temperature` | `0.2` | decision, ringkasan, dan ekstraksi |
| `soul.voice.safety` | `inherit` | `inherit` atau `off`; `off` digerbangi adapter |

Nilai daily, professional, dan sampling adalah pilihan perilaku, jadi tinggal di soul. Temperature
utility adalah panggilan operasional (decision, digest, ekstraksi), jadi tetap di engine. `engine.llm`
menyimpan provider, model, timeout, retry, budget, dan reasoning effort; tidak ada salinan kedua
temperature perilaku.

Field numerik kosong tidak dikirim ke request. Nilai non-kosong yang tidak bisa dikirim adapter
terpilih adalah error konfigurasi saat load, bukan no-op diam-diam. Penolakan provider setelah request
valid adalah error runtime dengan alasan native.

`soul.voice.safety: off` hanya diterima bila adapter melaporkan capability. Untuk Gemini, `off`
memetakan ke threshold `off` saat ini (nama API yang menggantikan `BLOCK_NONE`) untuk kategori harm yang
bisa diatur; core harm tetap diblokir. Untuk adapter tanpa capability, load gagal dengan pesan yang
menyebut provider dan nilai yang tidak didukung. `inherit` berarti default provider; tidak ada nilai
`engine.llm.safety` kedua.

Nilai daily dan professional dimiliki ADR-0045; ADR ini mendefinisikan transport nilainya, bukan
kebijakan karakter.

### 4.7 Tanpa kosakata nama provider di engine

`llm.py` menerima wire dan adapter; ia tidak mengimpor modul provider berdasarkan merek dan tidak menguji
string provider. Paket provider boleh menyimpan adapter dan fakta endpoint, tetapi tidak mengimpor modul
engine.

Aturan bahwa wire protocol berbeda membutuhkan adapter, bukan entry data, tetap dipertahankan. Yang
berubah adalah adapter sekarang boleh membawa kode request/response, bukan hanya metadata endpoint.

### 4.8 Non-streaming lebih dulu

Adapter native pertama non-streaming. Loop sekarang sudah mengonsumsi response lengkap dan
mencatatnya; streaming adalah keputusan terpisah dengan kontrak delivery dan pembatalannya sendiri.

## 5. Konfigurasi

Selector provider yang ada tetap:

```yaml
engine:
  llm:
    provider: google
    model: <pilihan operator>
    utility_temperature: 0.2

soul:
  voice:
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    safety: inherit
```

`provider` dan `model` wajib dan tidak punya default kode. Nilai sampling adalah default, bukan
kebijakan tetap: operator boleh mengubahnya. Nilai yang tidak bisa dibawa wire terpilih ditolak saat
config dimuat.

## 6. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Wire family | 3 |
| Adapter native di potongan pertama | 1 |
| Perbandingan nama provider di kode engine | 0 |
| Nilai sampling tidak didukung yang di-drop diam-diam | 0 |
| Response native yang dinormalkan ke satu bentuk | ya |
| Panggilan network live di test adapter | 0 |
| Test wire OpenAI yang berubah perilakunya | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 7. Kriteria penerimaan

- [x] `ProviderDefaults` membawa wire dan `llm.py` memilih adapter tanpa cabang nama provider.
- [x] Satu tipe response ternormalisasi membawa teks, tool call, usage, finish reason, dan status blocked.
- [x] Gemini bisa mengirim `safetySettings` saat `soul.voice.safety: off` dan `topK` saat dikonfigurasi.
- [x] Alasan blok Gemini dan kandidat kosong terlihat oleh turn, bukan dilaporkan sebagai jawaban.
- [x] Provider OpenAI-compatible mempertahankan request dan perilaku tool yang ada.
- [x] Anthropic tetap di lapisan kompatibilitas sampai adapter native diterima.
- [x] `top_p`, `top_k`, dan `min_p` hanya dikirim bila didukung; nilai tidak didukung gagal saat load.
- [x] Test adapter memakai fixture request dan response dan tidak memanggil endpoint live.
- [x] Error provider native tetap mencatat satu baris `llm_calls` dengan purpose yang benar.
- [x] Temperature daily, professional, dan utility bisa dikonfigurasi operator.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Drift API native | Setiap adapter punya fixture test dan bentuk request berversi; drift gagal di test, bukan produksi |
| Dua dialek tool call | Normalkan kedua dialek di batas adapter dan uji nama tool, argumen, serta id hasil |
| Penolakan safety native | Error provider tetap terlihat; `off` bukan jaminan kepatuhan |
| Sampling tidak didukung | Validasi saat load dan sebut provider serta field; jangan drop diam-diam |
| Dependensi HTTP baru | Pakai stack HTTP yang ada bila mungkin; bila dependensi langsung ditambah, catat baris lisensinya |
| Accounting token berbeda | Pakai objek usage native bila ada; pertahankan fallback nilai kosong yang ada |
| Logika retry ganda | Adapter tidak pernah retry; `llm.py` memiliki retry dan deadline |
| Cabang nama provider kembali | Provider record dan capability adapter adalah satu-satunya permukaan dispatch |

## 9. Interaksi dengan ADR lain

- **ADR-0027**: Gemini native tidak dipasang default; provider tetap pilihan operator.
- **ADR-0031**: batas adapter tetap di samping paket provider dan seam LLM; budget modul engine tidak berubah.
- **ADR-0033**: tidak ada kosakata bahasa, budaya, transport, atau vendor yang masuk mekanisme; konstanta
  kategori Gemini tinggal di adapter Gemini.
- **ADR-0041**: konfigurasi voice tidak terpengaruh; voice layer tetap mengonsumsi engine.
- **ADR-0043**: riset dan sitasi tidak berubah; provider native tidak menjadi sumber kebenaran.
- **ADR-0045**: default temperature daily dan professional didefinisikan di sana; ADR ini membawanya ke
  adapter.

## 10. Referensi kode

- `monoagens/providers/base.py:12-18` — `ProviderDefaults` dan field `wire` mendatang.
- `monoagens/providers/__init__.py:19-54` — registry provider dan `resolve`.
- `monoagens/providers/gemini.py:1-13` — entry kompatibilitas OpenAI saat ini.
- `monoagens/providers/anthropic.py:1-15` — lapisan kompatibilitas dan catatannya.
- `monoagens/engine/llm.py:173-183` — konstruksi `AsyncOpenAI`.
- `monoagens/engine/llm.py:287-323` — satu panggilan provider dan pencatatannya.
- `monoagens/engine/llm.py:368-379` — kwargs request OpenAI dan `temperature`.
- `monoagens/engine/llm.py:590-625` — panggilan `complete` tanpa tool dan temperature-nya.
- `monoagens/engine/config.py:147-160` — `LLMConfig`.
- `monoagens/admin/templates.py:100` — temperature engine yang dihasilkan.
- `pyproject.toml:9-28` — set dependensi.
- `tests/test_providers.py` — cakupan registry provider.
- `tests/test_llm.py` — cakupan loop LLM yang ada.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0044-provider-wire-adapters.md`.*
