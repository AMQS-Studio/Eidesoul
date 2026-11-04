> Terjemahan turunan — jangan sunting; sumber: engine/0045-character-without-local-packs.md (sha256: a0c95b9a2e15)

# ADR-0045: Karakter Tanpa Pack Lokal — Soul, Contoh, Tanpa Gerbang Gender, Tanpa Tabel Level

- **Status:** accepted
- **Tanggal:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Konteks:** mengikuti ADR-0008 (aturan), ADR-0025 (biaya turn), ADR-0033 (core netral), ADR-0034 (prompt parts), ADR-0038 (privasi), ADR-0041 (konfigurasi voice), ADR-0043 (kontrak riset), dan ADR-0044 (adapter wire provider); menghapus katalog karakter runtime, selector `soul.style`, pembedaan gender, dan tabel level karakter, serta memindahkan tuning voice ke `soul.voice`.
- **Supersedes:** ADR-0041 (lokasi konfigurasi dan premis `soul.style` saja; semantik master/off-by-default tetap)
- **Terkait:** ADR-0008 (integritas memori), ADR-0025 (biaya turn), ADR-0033 (core netral), ADR-0034 (prompt parts), ADR-0038 (privasi), ADR-0041 (konfigurasi voice), ADR-0043 (kontrak riset), ADR-0044 (adapter wire provider)
- **Implementasi:** draft — keputusan dicatat; penghapusan pack, perubahan prompt, sampling mode, dan test belum diimplementasikan di ADR ini.
- **Catatan lokasi:** perubahan mengikat turn engine, jadi ADR ini duduk di `adr/engine/`. Salinan Indonesia di `docs/v0.1.0/adr/engine/id/0045-character-without-local-packs.md`.

---

## 1. Konteks

Layer karakter sekarang mencampur empat pekerjaan berbeda:

| Pekerjaan | Permukaan saat ini | Masalah |
| --- | --- | --- |
| Persona | `soul.yaml` `background` dan `persona` | sumber yang benar, tetapi hanya sebagian dari prompt karakter |
| Gerakan kanonik | `library/patterns/*.json` dipilih per turn | katalog lokal yang tidak dirawat pada skala sekarang |
| Fakta publik | `library/facts/*.json` dipilih per turn | ADR-0043 menghapusnya dari runtime |
| Batas register | `toxicity` 0-4 dan `adult_memes` 0-3 | tabel level numerik yang mengubah suara menjadi kebijakan |
| Target gender | `target` di pattern dan `soul.gender` | pembedaan yang tidak diinginkan owner |
| Polish | satu panggilan `LLM.complete` pada draft robotik | berguna, tetapi terikat pada tabel level yang sama |
| Rewrite level | `regulate()` setelah draft | panggilan model kedua yang bisa meratakan suara |

Arahan owner eksplisit:

- karakter berasal dari `soul.yaml` dan model, bukan pack lokal;
- beberapa contoh nyata di soul lebih kuat daripada katalog baris besar;
- tidak ada pembedaan gender di jalur karakter;
- tidak ada tabel toxicity/adult dan tidak ada rewrite level;
- chat daily boleh lebih ekspresif; mode professional tetap benar-benar membantu;
- `affect`, `humanize`, level privasi, dan guard tool mempertahankan kontraknya sendiri.

ADR ini hanya tentang tabel level **karakter**. Ia tidak menghapus level privasi L0-L4 dari ADR-0038,
dan tidak menghapus sandbox atau kebijakan approval tool.

## 2. Decision drivers

| Driver | Konsekuensi |
| --- | --- |
| Satu sumber karakter | persona dan contoh di `soul.yaml` adalah satu-satunya data karakter |
| Tanpa katalog lokal | `library/patterns` dan `library/facts` keluar dari runtime |
| Tanpa resolver | `soul.style`, `library resolve`, dan vektor pack dihapus, bukan disimpan sebagai config mati |
| Tanpa pembedaan gender | tidak ada field gender, baris gender di prompt, atau target gender |
| Tanpa tabel level | tidak ada nilai toxicity/adult, tidak ada `over_level()`, tidak ada `regulate()` |
| Daily ekspresif | sampling daily lebih tinggi dan dipimpin persona |
| Professional berguna | mode professional dipimpin task, temperature lebih rendah, tanpa polish |
| Sampling bisa dikonfigurasi | temperature daily, professional, utility, dan polish adalah nilai operator |
| Lexicon hanya detector | `library/lexicon` menyimpan daftar kata deterministik, bukan contoh prompt |
| Tanpa dependensi karakter baru | tidak ada library karakter eksternal yang diperkenalkan |

## 3. Opsi yang dipertimbangkan

| Opsi | Ringkasan | Nilai |
| --- | --- | --- |
| **A. Pertahankan pack dan level lokal** | lanjut memilih baris pattern dan fact berdasarkan register dan level | Ditolak: owner tidak akan merawat katalog; ia membatasi suara dan menduplikasi model |
| **B. Ganti pack dengan library karakter eksternal** | konsumsi dataset roleplay/persona saat runtime | Ditolak: tidak ada drop-in Indonesia; lisensi, bahasa, dan format dataset tidak cocok untuk prompt runtime |
| **C. Soul plus contoh plus model** | persona, beberapa contoh, memori, dan model terpilih | **Dipilih** |
| **D. Pertahankan target gender** | klasifikasi target sebelum memilih gerakan | Ditolak: owner tidak menginginkan pembedaan itu |
| **E. Pertahankan level tetapi buat configurable** | ekspos tabel 0-4 yang sama | Ditolak: tabelnya yang bermasalah, bukan hanya nilainya |
| **F. Hapus level, pertahankan rewrite** | pertahankan `regulate()` tanpa tabel | Ditolak: rewrite tanpa aturan tetap meratakan suara |
| **G. Satu temperature global** | pertahankan `engine.llm.temperature` untuk semua mode | Ditolak: daily dan professional butuh sampling berbeda |
| **H. Karakter hanya prompt** | tanpa panggilan karakter sama sekali | Ditolak: polish masih menghapus assistant tell dari draft robotik |

## 4. Keputusan

### 4.1 Satu sumber karakter

`soul.yaml` adalah satu-satunya sumber karakter:

| Field | Peran |
| --- | --- |
| `description` | identitas satu baris |
| `background` | latar dan sejarah yang stabil |
| `persona` | suara, register, jangkar budaya, aturan reaksi |
| `examples` | tiga sampai lima pertukaran pendek `user`/`soul` |
| `rules` | aturan perilaku dari ADR-0008, bukan gaya |

`render_persona()` sudah mengeluarkan `## Dialogue examples`. Contoh adalah jangkar few-shot; mereka
bukan database, pack, atau memori kedua. Mereka tidak boleh memuat nama, nomor telepon, URL, atau
transkrip mentah pihak ketiga. Pertukaran sintetis yang menangkap ritme lebih disukai.

Model membawa prior budaya yang stabil. Klaim current, spesifik, atau numerik tetap mengikuti ADR-0043:
cari saat bukti dibutuhkan, abstain saat search gagal.

### 4.2 Hapus katalog karakter lokal

Runtime tidak lagi memuat `library/patterns` atau `library/facts`.

Dihapus:

- `select_patterns()` dan `select_facts()` dari loop turn;
- `character/patterns.py` dan `character/knowledge.py`;
- field `patterns` dan `facts` di `PromptContext`;
- blok `## Room patterns` dan `## Known facts`;
- entry package data untuk kedua tree.

File tidak dipindahkan ke lokasi runtime lain. Fixture test boleh membawa pertukaran sintetis kecil,
tetapi tidak ada pack yang dikirim dan dimuat per turn. File data dihapus setelah test yang
bergantung padanya diganti.

### 4.3 Hapus `soul.style`

`SoulStyle` hanya berisi selector pack dan limitnya, jadi ia dihapus bersama pack:

- `patterns`, `facts`, `registers`, `max_patterns`, dan `max_facts` keluar dari `soul.yaml`;
- `SoulStyle`, `style_block()`, dan `_style()` keluar dari `soul.py`;
- `eidesoul library resolve` dan `admin/library.py` dipensiunkan;
- `init` berhenti menulis blok `style:` dan berhenti menjalankan resolver;
- blok `style:` tidak disimpan sebagai permukaan kompatibilitas kosong.

Bagian `engine.style` yang terpisah dari ADR-0041 adalah field berbeda: itu profil balasan voice layer.
Ia pindah ke `soul.voice.style` di §4.9; section engine dihapus, bukan disimpan paralel.

### 4.4 Hapus pembedaan gender

Jalur karakter tidak mengklasifikasi atau menargetkan gender:

- `soul.gender` dan validasi `GENDERS` dihapus;
- prompt decision tidak lagi mencetak baris gender;
- nilai `target` di pattern hilang bersama katalog pattern;
- key lama `gender_gate`, `gender_confidence`, dan `gender_avatar` dihapus dari config yang dihasilkan;
- tidak ada classifier pengganti.

`soul.pronouns` tetap field identitas: ia mengatur referensi diri, bukan kelas target. Profil tanpa
pronouns memakai default katalog bahasa.

### 4.5 Hapus tabel level karakter

Berikut dihapus dari jalur karakter:

| Dihapus | Alasan |
| --- | --- |
| `CharacterConfig.toxicity` | tabel register 0-4 |
| `CharacterConfig.adult_memes` | tabel meme adult 0-3 |
| `CharacterConfig.roast_jabs` | cap berbentuk level tanpa pemilik lain |
| `CharacterConfig.max_questions` | pemicu rewrite berbentuk level |
| `effective_limits()` | aksesor tabel |
| `over_level()` dan `severity()` | scanner marker |
| `regulate()` dan `too_many_questions()` | panggilan rewrite kedua |
| `library/severity` | pack severity kata kotor |
| `library/lexicon` `level_words` | peta level yang dipakai scan karakter |
| `character_block()` | blok prompt level/register |

`character.py` hanya menyimpan jalur polish: deteksi draft robotik atau kepanjangan, satu panggilan
`LLM.complete`, pertahankan fakta dan hasil tool, dan kembalikan draft asli bila hasilnya mencurigakan.
`CharacterConfig` menyimpan `enabled`, `max_chars`, `max_tokens`, dan `temperature`.

Prompt dasar tetap menyatakan batas jujur yang sudah ada di sana: tidak mengarang fakta, tidak mengklaim
aksi tool, tidak doxing, tidak mengancam. ADR ini tidak menambah lapisan moderasi baru.

Dua knob non-level tetap ada di `soul.yaml`:

```yaml
voice:
  profanity: free      # free | avoid
  safety: inherit      # inherit | off
```

`voice.profanity` adalah instruksi prompt, bukan scanner dan bukan tabel. `free` memberi tahu soul bahwa
ia boleh mengumpat saat thread sudah melakukannya; `avoid` memintanya tidak. Tidak ada skala 0-4, tidak
ada pack severity per kata, dan tidak ada rewrite.

`voice.safety` adalah selector kebijakan per soul. `inherit` memakai default provider; `off` meminta
setting native tanpa filter dari adapter. Tidak ada salinan `engine.llm.safety`: soul adalah satu-satunya
tempat perilaku ini dipilih. `off` tetap membutuhkan adapter yang melaporkan capability; jika tidak,
config gagal saat load, sesuai ADR-0044. Provider tetap bisa menolak; override menghapus ambang request,
bukan perilaku model atau ketentuan provider.

### 4.6 Sampling daily dan professional

Decision sudah memisahkan `daily` dan `professional` (`decision.py`). ADR ini membuat perbedaan sampling
eksplisit dan bisa dikonfigurasi. Sampling perilaku tinggal di soul; panggilan utility operasional tetap
di engine:

| Key | Default | Dipakai oleh |
| --- | ---: | --- |
| `soul.voice.sampling.daily_temperature` | `0.9` | draft daily |
| `soul.voice.sampling.professional_temperature` | `0.3` | draft professional |
| `soul.voice.sampling.top_p` | kosong | semua wire yang mendukung |
| `soul.voice.sampling.top_k` | kosong | Gemini native lebih dulu |
| `soul.voice.sampling.min_p` | kosong | hanya bila adapter mendukung |
| `soul.voice.character.temperature` | `0.7` | polish daily |
| `engine.llm.utility_temperature` | `0.2` | decision, ringkasan, ekstraksi |

Pemisahannya disengaja: nilai yang mengubah cara soul berbicara tinggal di `soul.yaml`; nilai yang
menggambarkan panggilan operasional tinggal di `engine.llm`. Tidak ada salinan kedua nilai daily atau
professional di `engine.llm`.

Nilai perilaku adalah default, bukan kebijakan tetap. Nilai kosong berarti default provider. Nilai
non-kosong yang tidak didukung wire terpilih gagal saat load, sesuai ADR-0044.

Mode daily adalah default untuk percakapan biasa. Mode professional dipilih untuk task nyata dan bukan
default global. Professional punya default yang membantu: tanpa polish, pekerjaan tuntas dengan tool,
temperature lebih rendah, dan aturan pelaporan jujur yang sama. Bila owner nanti ingin professional
sebagai default semua chat, itu keputusan terpisah.

### 4.7 Lexicon hanya detector

`library/lexicon` hanya bertahan untuk deteksi deterministik yang dibutuhkan jalur engine lain:

| Dipertahankan | Dipakai oleh |
| --- | --- |
| `assistant_tells` | pemicu polish |
| `negations` | kontradiksi memori |
| `stopwords` | salience dan drift |
| `injection` | guard dan output tool |
| `affirmations` | deteksi jawaban privasi |
| `laughter`, `particles`, `serious_registers` | profil gaya dan konteks detector |

Dihapus dari kontrak lexicon:

- `prompt_examples` dan setiap panggilan `lexicon.example()` di jalur karakter dan prompt;
- `level_words` bila hanya peta level karakter. Jalur privasi boleh mempertahankan pemetaan numeriknya
  sendiri dengan key yang jelas terpisah; itu bukan level karakter.

Prompt tidak lagi meminjam kata formal, pembuka umpatan, daftar referensi diri, atau contoh polish dari
lexicon. Itu materi persona dan tempatnya di `soul.yaml` atau tidak sama sekali.

### 4.8 Layer yang tidak berubah

- `affect` dan delapan prototipe VAD tetap.
- `humanize` tetap; ia mengubah delivery, bukan kata.
- Level privasi L0-L4 dan kebijakan disclosure tetap.
- Guard tool, sandbox, dan kebijakan approval tetap di engine.
- **Implementasi** voice layer tetap di luar cakupan; konfigurasinya pindah di §4.9.

### 4.9 Tuning voice tinggal di `soul.yaml`

Semua tuning karakter dan voice pindah ke satu blok `soul.voice`. Ini membalik opsi E ADR-0041: tuning
voice adalah data per soul, bukan mekanisme global engine. Engine tetap memiliki implementasinya; soul
memiliki setelannya.

```yaml
soul:
  voice:
    enabled: false
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    affect:
      enabled: true
      volatility: expressive
      grudge_half_life_hours: 1.5
      vibe:
        enabled: true
        attack: 0.35
        baseline_influence: 0.05
        hold_seconds: 1.2
        tick_seconds: 1.0
    style:
      enabled: true
      max_chars: 800
    character:
      enabled: false
      max_chars: 400
      max_tokens: 500
      temperature: 0.7
    humanize:
      enabled: true
      cpms: 25.0
      min_delay: 0.5
      max_delay: 5.0
      between_delay: 0.7
      max_bubbles: 3
      reaction_stickers: []
    profanity: free
    safety: inherit
```

Section top-level lama `engine.affect`, `engine.style`, `engine.character`, dan `engine.humanize` dihapus.
`engine.voice` tidak diperkenalkan; semantik satu section/master dari ADR-0041 berlaku di `soul.voice`.
`engine.guard` **tidak** pindah: itu batas keamanan, bukan tuning karakter.

Bila `soul.voice` tidak ada, default di atas berlaku. Sub-section inert selama `soul.voice.enabled`
`false`, kecuali `sampling`, `profanity`, dan `safety` yang berada di level request dan tidak digerbangi
master.
`soul.voice.style` adalah profil balasan statistik yang dulu di `engine.style`; ia bukan selector pack
`soul.style` yang dihapus.

## 5. Konfigurasi

```yaml
engine:
  llm:
    utility_temperature: 0.2

soul:
  voice:
    enabled: false
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    affect: {enabled: true}
    style: {enabled: true}
    character: {enabled: false}
    humanize: {enabled: true}
    profanity: free
    safety: inherit
```

Key engine yang dihapus: section `affect`, `style`, `character`, dan `humanize`. Key karakter yang
dihapus: `toxicity`, `adult_memes`, `roast_jabs`, `max_questions`. Key soul yang dihapus: `gender` dan
blok pack `soul.style` lama. Key host config yang dihapus: `gender_gate`, `gender_confidence`,
`gender_avatar`. `voice.profanity` dan `voice.safety` dipertahankan, bukan field level.

`soul.yaml` mempertahankan:

```yaml
name: ...
language: ...
locale: ...
timezone: ...
pronouns: ...
background: |
  ...
persona: |
  ...
voice:
  enabled: false
  sampling:
    daily_temperature: 0.9
    professional_temperature: 0.3
    top_p: 0.95
    top_k: 40
    min_p: null
  affect:
    enabled: true
  style:
    enabled: true
  character:
    enabled: false
  humanize:
    enabled: true
  profanity: free
  safety: inherit
examples:
  - user: ...
    soul: ...
rules: []
```

Blok `voice` yang tidak ada berarti default di §4.9: `enabled: false`, setiap sub-section `enabled: true`,
`profanity: avoid`, dan `safety: inherit`. Nilai soul diselesaikan sebelum request LLM; `off` tetap
digerbangi capability adapter dari ADR-0044.

## 6. Metrik dan gate

| Metrik | Target |
| --- | ---: |
| Pack karakter runtime yang dimuat | 0 |
| Panggilan `select_patterns()` / `select_facts()` | 0 |
| Field `soul.style` | 0 |
| Field gender di jalur karakter | 0 |
| Field level karakter | 0 |
| Section `soul.voice` | 5 (sampling, affect, style, character, humanize) |
| Knob master `soul.voice` | 3 (enabled, profanity, safety) |
| Section voice top-level engine | 0 |
| Panggilan rewrite level per turn | 0 |
| Key temperature daily dan professional | 2 |
| Panggilan polish dengan temperature terkonfigurasi | 1 |
| Contoh prompt yang dibaca dari lexicon | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 7. Kriteria penerimaan

- [ ] Tidak ada jalur runtime yang membaca `library/patterns` atau `library/facts`.
- [ ] `select_patterns()` dan `select_facts()` tidak ada lagi.
- [ ] `soul.style` dan `eidesoul library resolve` tidak ada lagi.
- [ ] Prompt karakter tidak punya blok room-pattern atau known-facts.
- [ ] Jalur karakter tidak punya field gender, baris gender di prompt, atau target gender.
- [ ] Jalur karakter tidak punya tabel toxicity/adult, pack severity, scan level, atau rewrite level.
- [ ] `soul.voice.profanity` dan `soul.voice.safety` bisa dikonfigurasi per soul.
- [ ] `engine.affect`, `engine.style`, `engine.character`, dan `engine.humanize` tidak ada lagi.
- [ ] `soul.voice` punya sub-section sampling, affect, style, character, dan humanize.
- [ ] Tidak ada temperature daily atau professional yang tersisa di `engine.llm`.
- [ ] `voice.safety` adalah satu-satunya selector safety dan `off` gagal bila tidak didukung.
- [ ] Blok `voice` yang tidak ada diselesaikan menjadi `profanity: avoid` dan `safety: inherit`.
- [ ] `character.py` tetap memoles draft robotik dan mempertahankan fakta serta hasil tool.
- [ ] Temperature daily dan professional bisa dikonfigurasi operator.
- [ ] Mode professional tetap dipilih untuk task, bukan default percakapan biasa.
- [ ] `affect`, `humanize`, level privasi, dan guard tool tetap bekerja.
- [ ] Tidak ada library karakter eksternal atau dependensi runtime baru.

## 8. Risiko dan mitigasi

| Risiko | Mitigasi |
| --- | --- |
| Persona drift tanpa pack | Persona `soul.yaml` plus contoh adalah satu sumber; test memastikan contoh masuk prompt |
| Model terlalu seragam | Temperature daily bisa dikonfigurasi dan persona membawa ritme |
| Professional terlalu datar | Default professional eksplisit; prompt task sudah melaporkan hasil dan bukti |
| Tool call kurang andal | Professional memakai temperature lebih rendah; test tool call tetap pada budget yang ada |
| Penghapusan level mengubah perilaku | Ini disengaja; tidak ada tabel kompatibilitas yang disimpan |
| Gender kembali lewat field baru | Test netral memastikan jalur karakter tidak punya field atau baris prompt gender |
| Test pack rusak | Test yang bergantung pada pack diganti dengan test soul dan mode berbasis fixture |
| Lexicon menjadi katalog tersembunyi | Hanya daftar detector yang tersisa; key prompt-example dan level karakter dihapus |
| Level privasi tertukar dengan level karakter | ADR-0038 mempertahankan L0-L4; ADR ini hanya menghapus tabel karakter |
| `soul.yaml` terlalu besar | Voice satu blok nested; persona dan contoh tetap satu-satunya prosa panjang |
| Guard dipindah ke data persona | `engine.guard` tetap di config engine; test schema memastikannya |
| Safety per soul melewati setelan engine | Presedensi eksplisit; `off` tetap butuh dukungan adapter native dan dicatat |

## 9. Interaksi dengan ADR lain

- **ADR-0008**: `rules` tetap aturan perilaku; itu bukan katalog gaya.
- **ADR-0025**: polish tetap panggilan bergerbang; menghapus `regulate()` menurunkan jumlah panggilan terburuk.
- **ADR-0033**: tidak ada kosakata budaya, gender, atau vendor yang masuk mekanisme; konten persona tetap di
  `soul.yaml`.
- **ADR-0034**: prompt part `character` dihapus bersama bloknya; registry mempertahankan part yang masih
  menghasilkan teks.
- **ADR-0038**: level privasi dan disclosure tetap; hanya tabel karakter yang dihapus.
- **ADR-0041**: semantik satu section/master dan off-by-default tetap; ADR-0045 memindahkan lokasi
  dari `engine.voice` ke `soul.voice`, menghapus blok pack `soul.style`, dan menghapus pemilihan pack
  dari bagian karakter.
- **ADR-0043**: fakta publik pindah ke prior model dan websearch; fakta tidak lagi masuk blok karakter.
- **ADR-0044**: temperature mode dibawa melalui adapter provider; nilai daily dan professional divalidasi
  terhadap wire terpilih. `soul.voice.safety` diselesaikan sebelum request; gate capability adapter tetap berlaku.

## 10. Referensi kode

- `eidesoul/engine/character/patterns.py:1-27` — katalog pattern lokal dan helper seleksinya.
- `eidesoul/engine/character/knowledge.py:1-30` — katalog fakta lokal.
- `eidesoul/engine/character/character.py:190-268` — `severity()`, `effective_limits()`, dan `over_level()`.
- `eidesoul/engine/character/character.py:310-360` — `regulate()` dan jalur rewrite-nya.
- `eidesoul/engine/character/lexicon.py:23-47` — key lexicon, termasuk `level_words` dan contoh prompt.
- `eidesoul/engine/soul.py:49-55` — `SoulStyle`.
- `eidesoul/engine/soul.py:95-109` — `style_block()`.
- `eidesoul/engine/soul.py:255-285` — pemuatan soul dan assignment `style`.
- `eidesoul/engine/turn/loop.py:16-17` — import `select_facts` dan `select_patterns`.
- `eidesoul/engine/turn/loop.py:393-414` — seleksi pattern dan fact per turn.
- `eidesoul/engine/turn/loop.py:534-579` — polish daily dan regulasi level.
- `eidesoul/engine/turn/prompts.py:171-214` — `character_block()` dan teks level.
- `eidesoul/engine/turn/prompts.py:442-449` — prompt part karakter.
- `eidesoul/engine/turn/decision.py:25-26` — kontrak keputusan daily/professional.
- `eidesoul/engine/turn/decision.py:392-397` — prompt identitas dengan baris gender.
- `eidesoul/engine/config.py:582-614` — `AffectConfig`, `StyleConfig`, `CharacterConfig`, dan
  `HumanizeConfig`.
- `eidesoul/engine/config.py:1724-1732` — parsing config karakter.
- `eidesoul/engine/soul.py:58-72` — field `Soul` yang akan membawa `voice`.
- `eidesoul/engine/soul.py:255-285` — pemuatan soul dan assignment `voice` mendatang.
- `eidesoul/__main__.py:102-111` — konstruksi `AffectStore`, `StyleStore`, dan `VibeRunner`.
- `eidesoul/admin/library.py:1-27` — resolver pack.
- `eidesoul/admin/templates.py:389` — blok style soul yang dihasilkan.
- `eidesoul/__main__.py:600` — panggilan `library resolve`.
- `tests/test_library.py` — cakupan seleksi pack.
- `tests/test_resolver.py` — cakupan resolver.
- `tests/test_character.py` — cakupan polish dan regulate.
- `tests/test_prompt_contract.py` — registry prompt dan cakupan register.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0045-character-without-local-packs.md`.*
